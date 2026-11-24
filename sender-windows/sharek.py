"""
Sharek (شارك) — share this laptop's screen + sound to the Sharek TV app.

Video: Desktop Duplication (ddagrab via PyAV) -> hardware H.264 (QSV/NVENC/AMF/MF) -> TCP.
Audio: WASAPI loopback (soundcard) -> PCM 48 kHz stereo -> TCP.

Run with no arguments for the window, or:
    python sharek.py --list
    python sharek.py --tv 192.168.1.50 [--res 1080] [--fps 60] [--mbps 18] [--audio tv|laptop|both] [--bars]
"""
from __future__ import annotations

import argparse
import atexit
import ctypes
import json
import os
import queue
import socket
import struct
import sys
import threading
import time

DISCOVERY_PORT = 47800
STREAM_PORT = 47801
PROTO_VERSION = 2
APPROVAL_TIMEOUT = 65  # seconds the TV gives the person to answer (+ margin)
T_PING, T_VIDEO, T_AUDIO, T_CONFIG, T_BYE, T_HELLO = 0, 1, 2, 3, 4, 5

AUDIO_RATE = 48000
AUDIO_CH = 2

APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
SETTINGS_DIR = os.path.join(os.environ.get("APPDATA", APP_DIR), "Sharek")
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")


def log(*a) -> None:
    """print() that never fails (pythonw has no console; cp1252 consoles can't show Arabic)."""
    try:
        print(*a, flush=True)
    except Exception:
        pass


# --------------------------------------------------------------------------- settings
def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(s: dict) -> None:
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# --------------------------------------------------------------------------- video encoder (PyAV)
# Low-latency settings per encoder, best first. All of them ship inside the PyAV wheel.
ENCODERS: list[tuple[str, str, dict]] = [
    ("h264_qsv", "nv12", {"preset": "veryfast", "look_ahead": "0", "async_depth": "1", "profile": "main"}),
    ("h264_nvenc", "nv12", {"preset": "p1", "tune": "ull", "zerolatency": "1", "rc": "cbr", "delay": "0",
                            "profile": "main"}),
    ("h264_amf", "nv12", {"usage": "ultralowlatency", "quality": "speed", "rc": "cbr"}),
    ("h264_mf", "nv12", {"hw_encoding": "1", "scenario": "display_remoting", "rate_control": "cbr"}),
    ("libx264", "yuv420p", {"preset": "ultrafast", "tune": "zerolatency", "profile": "main"}),
]


def open_encoder(name: str, pix: str, opts: dict, width: int, height: int, fps: int, mbps: float):
    import av
    from fractions import Fraction
    ctx = av.CodecContext.create(name, "w")
    ctx.width, ctx.height = width, height
    ctx.pix_fmt = pix
    ctx.time_base = Fraction(1, fps)
    ctx.framerate = Fraction(fps, 1)
    ctx.bit_rate = int(mbps * 1e6)
    o = dict(opts)
    o.update({"g": str(fps * 2), "bf": "0", "maxrate": str(int(mbps * 1e6)), "bufsize": str(int(mbps * 5e5))})
    ctx.options = o
    ctx.open()
    return ctx


def pick_encoder(settings: dict) -> tuple[str, str, dict]:
    """First encoder that really works on this PC (the last good one is tried first)."""
    import av
    cached = settings.get("encoder")
    for name, pix, opts in sorted(ENCODERS, key=lambda e: e[0] != cached):
        try:
            ctx = open_encoder(name, pix, opts, 640, 360, 30, 2)
            for i in range(3):
                f = av.VideoFrame(640, 360, pix)
                f.pts = i
                ctx.encode(f)
            ctx.encode(None)
        except Exception:
            continue
        if cached != name:
            settings["encoder"] = name
            save_settings(settings)
        return name, pix, opts
    raise RuntimeError("ما لقينا طريقة لضغط الفيديو على هذا الجهاز")


# --------------------------------------------------------------------------- discovery
def _broadcast_addresses() -> list[str]:
    addrs = {"255.255.255.255"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                addrs.add(".".join(ip.split(".")[:3]) + ".255")
    except Exception:
        pass
    return sorted(addrs)


def discover(timeout: float = 1.5) -> list[dict]:
    """Broadcast on the LAN and collect TVs running Sharek."""
    found: dict[str, dict] = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(0.25)
    try:
        s.bind(("", 0))
        end = time.time() + timeout
        next_send = 0.0
        while time.time() < end:
            if time.time() >= next_send:
                for b in _broadcast_addresses():
                    try:
                        s.sendto(b"SHAREK_DISCOVER", (b, DISCOVERY_PORT))
                    except OSError:
                        pass
                next_send = time.time() + 0.5
            try:
                data, addr = s.recvfrom(1024)
            except socket.timeout:
                continue
            parts = data.decode("utf-8", "ignore").split("|")
            if len(parts) >= 3 and parts[0] == "SHAREK_HERE":
                found[addr[0]] = {"name": parts[1], "ip": addr[0], "port": int(parts[2])}
    finally:
        s.close()
    return list(found.values())


# --------------------------------------------------------------------------- H.264 Annex-B helpers
def nal_units(au: bytes) -> list[bytes]:
    """Split an Annex-B access unit into NAL units (start codes removed)."""
    out, starts = [], []
    i = au.find(b"\x00\x00\x01")
    while i >= 0:
        starts.append(i)
        i = au.find(b"\x00\x00\x01", i + 3)
    for k, st in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(au)
        if k + 1 < len(starts) and au[end - 1] == 0:
            end -= 1  # 4-byte start code
        if end > st + 3:
            out.append(au[st + 3:end])
    return out


class ParamSets:
    """Makes sure every keyframe carries SPS/PPS so the TV can start decoding at any keyframe."""

    START = b"\x00\x00\x00\x01"

    def __init__(self):
        self.sps_pps = b""

    def fix(self, au: bytes, key: bool) -> bytes:
        nals = nal_units(au)
        types = {x[0] & 0x1F for x in nals}
        if 7 in types and 8 in types:
            self.sps_pps = b"".join(self.START + x for x in nals if x[0] & 0x1F in (7, 8))
            return au
        if key and self.sps_pps:
            return self.sps_pps + au
        return au


# --------------------------------------------------------------------------- laptop speakers
# While sound goes to the TV, mute the laptop's own speakers. WASAPI loopback still captures
# the full mix when the endpoint is muted, so the TV keeps getting sound.
class _GUID(ctypes.Structure):
    _fields_ = [("b", ctypes.c_byte * 16)]

    def __init__(self, s: str):
        super().__init__()
        import uuid
        ctypes.memmove(self.b, uuid.UUID(s).bytes_le, 16)


def _vcall(obj, index, *argtypes):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(vtbl[index])


def _speaker_volume():
    ole = ctypes.OleDLL("ole32")
    try:
        ole.CoInitializeEx(None, 0)
    except OSError:
        pass  # COM already initialised on this thread
    P = ctypes.POINTER
    enum, dev, vol = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    ole.CoCreateInstance(ctypes.byref(_GUID("BCDE0395-E52F-467C-8E3D-C4579291692E")), None, 0x17,
                         ctypes.byref(_GUID("A95664D2-9614-4F35-A746-DE8DB63617E6")), ctypes.byref(enum))
    _vcall(enum, 4, ctypes.c_int, ctypes.c_int, P(ctypes.c_void_p))(enum, 0, 0, ctypes.byref(dev))
    _vcall(dev, 3, P(_GUID), ctypes.c_uint, ctypes.c_void_p, P(ctypes.c_void_p))(
        dev, ctypes.byref(_GUID("5CDF2C82-841E-4546-9722-0CF74078229A")), 0x17, None, ctypes.byref(vol))
    return vol


def speakers_muted() -> bool:
    vol = _speaker_volume()
    m = ctypes.c_int()
    _vcall(vol, 15, ctypes.POINTER(ctypes.c_int))(vol, ctypes.byref(m))
    return bool(m.value)


def set_speakers_muted(mute: bool) -> None:
    vol = _speaker_volume()
    _vcall(vol, 14, ctypes.c_int, ctypes.c_void_p)(vol, int(mute), None)


def recover_speakers() -> None:
    """Unmute the laptop if a previous Sharek run muted it and never got to restore it."""
    s = load_settings()
    if s.pop("muted_by_sharek", None):
        try:
            set_speakers_muted(False)
        except Exception:
            pass
        save_settings(s)


class Rejected(Exception):
    pass


class Cancelled(Exception):
    pass


def device_name() -> str:
    return socket.gethostname()


def device_id(settings: dict) -> str:
    """Stable random id so the TV can remember devices it was told to always allow."""
    if not settings.get("device_id"):
        import uuid
        settings["device_id"] = uuid.uuid4().hex
        save_settings(settings)
    return settings["device_id"]


# --------------------------------------------------------------------------- streaming
class Streamer:
    def __init__(self, ip: str, port: int = STREAM_PORT, res: int = 1080, fps: int = 30, mbps: float = 10,
                 audio: str | bool = "tv", monitor: int = 0, on_status=None, settings: dict | None = None,
                 fill: bool = True, on_phase=None):
        self.ip, self.port = ip, port
        self.res, self.fps, self.mbps = res, fps, mbps
        self.fill = fill  # True: stretch to fill the 16:9 TV, False: keep shape with black bars
        # audio: "tv" (TV only, laptop muted), "laptop" (laptop only), "both"
        self.audio_mode = {True: "tv", False: "laptop"}.get(audio, audio) if isinstance(audio, bool) else audio
        self.monitor = monitor
        self.on_status = on_status or log
        self.settings = settings if settings is not None else load_settings()
        self.q: queue.Queue = queue.Queue()
        self.video_backlog = 0
        self.lock = threading.Lock()
        self.running = False
        self.capture = None
        self.sock: socket.socket | None = None
        self.threads: list[threading.Thread] = []
        self.stats = {"frames": 0, "bytes": 0, "dropped": 0}
        self.error: str | None = None
        self.restore_mute: bool | None = None
        self.cancelled = False
        self.encoder = ""
        self.on_phase = on_phase or (lambda phase: None)

    # -- lifecycle
    def start(self) -> None:
        enc, pix, enc_opts = pick_encoder(self.settings)

        self.on_phase("connecting")
        self.sock = socket.create_connection((self.ip, self.port), timeout=5)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
        self.sock.sendall(b"SHRK" + bytes([PROTO_VERSION]))
        hello = json.dumps({"name": device_name(), "id": device_id(self.settings)}, ensure_ascii=False)
        self._send_now(T_HELLO, hello.encode("utf-8"))

        # The TV asks the person watching to accept or reject this device.
        self.on_phase("waiting_approval")
        self.sock.settimeout(APPROVAL_TIMEOUT)
        try:
            answer = self.sock.recv(1)
        except OSError:
            answer = b""
        if self.cancelled:
            self.sock.close()
            raise Cancelled()
        if answer != b"\x01":
            self.sock.close()
            raise Rejected("التلفزيون رفض الطلب" if answer == b"\x00" else "ما وصل رد من التلفزيون")
        self.sock.settimeout(10)

        scale = ""
        if self.res:
            w = {2160: 3840, 1440: 2560, 1080: 1920, 720: 1280}.get(self.res, 1920)
            if self.fill:  # stretch to the TV's 16:9
                scale = f",scale={w}:{self.res}:flags=bicubic,setsar=1"
            else:  # keep the shape, black bars around it
                scale = (f",scale={w}:{self.res}:force_original_aspect_ratio=decrease:force_divisible_by=2"
                         f":flags=bicubic,pad={w}:{self.res}:(ow-iw)/2:(oh-ih)/2")
        # Desktop Duplication on the GPU -> system memory -> scaled -> encoder's pixel format.
        graph = (f"ddagrab=output_idx={self.monitor}:framerate={self.fps}:draw_mouse=1,"
                 f"hwdownload,format=bgra{scale},format={pix}")
        import av
        try:
            self.capture = av.open(graph, format="lavfi")
        except Exception as e:
            self.sock.close()
            raise RuntimeError(f"ما قدرنا نلتقط الشاشة: {e}")
        self.encoder, self.pix, self.enc_opts = enc, pix, enc_opts
        self.running = True
        for target in (self._video_loop, self._send_loop):
            t = threading.Thread(target=target, daemon=True)
            t.start()
            self.threads.append(t)
        self.on_phase("streaming")
        self.on_status(f"متصل بـ {self.ip} • {enc}")

    def cancel(self) -> None:
        """Abort while still connecting / waiting for the TV to approve."""
        self.cancelled = True
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass

    def stop(self, reason: str | None = None) -> None:
        if not self.running:
            return
        self.running = False
        try:
            if self.sock:
                self.sock.settimeout(1)
                self.sock.sendall(struct.pack(">BI", T_BYE, 0))
        except Exception:
            pass
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self._unmute_laptop()
        self.q.put(None)
        self.on_status(reason or "تم الإيقاف")

    # -- audio output
    def set_audio_mode(self, mode: str) -> None:
        """Switch where sound plays: "tv", "laptop" or "both". Safe to call while streaming."""
        self.audio_mode = mode
        if not self.running:
            return
        if mode == "tv":
            if self.restore_mute is None:
                self._mute_laptop()
        else:
            self._unmute_laptop()

    def _mute_laptop(self) -> None:
        try:
            self.restore_mute = speakers_muted()
            set_speakers_muted(True)
            # Remember it on disk so a crash or kill can't leave the laptop muted forever.
            if not self.restore_mute:
                self.settings["muted_by_sharek"] = True; save_settings(self.settings)
            atexit.register(self._unmute_laptop)
        except Exception as e:
            log("mute failed:", e)

    def _unmute_laptop(self) -> None:
        if self.restore_mute is None:
            return
        try:
            set_speakers_muted(self.restore_mute)
            self.settings.pop("muted_by_sharek", None); save_settings(self.settings)
        except Exception as e:
            log("unmute failed:", e)
        self.restore_mute = None

    # -- packet helpers
    def _send_now(self, t: int, payload: bytes) -> None:
        assert self.sock
        self.sock.sendall(struct.pack(">BI", t, len(payload)) + payload)

    def _push(self, t: int, payload: bytes, video: bool = False) -> None:
        if video:
            with self.lock:
                self.video_backlog += 1
        self.q.put((t, payload, video))

    # -- threads
    def _capture_loop(self, frames: "queue.Queue") -> None:
        """Grabs and scales frames; runs beside the encoder so the two overlap."""
        try:
            stream = self.capture.streams.video[0]
            stream.thread_type = "AUTO"
            for frame in self.capture.decode(stream):
                if not self.running:
                    break
                if frames.full():  # encoder is behind: drop the oldest, keep latency low
                    try:
                        frames.get_nowait()
                        self.stats["dropped"] += 1
                    except queue.Empty:
                        pass
                frames.put(frame)
        except Exception as e:
            if self.running:
                self.error = str(e)
        finally:
            frames.put(None)
            try:
                self.capture.close()
            except Exception:
                pass

    def _video_loop(self) -> None:
        ctx = None
        params = ParamSets()
        waiting_key = False
        index = 0
        t0 = time.monotonic()
        frames: queue.Queue = queue.Queue(maxsize=2)
        threading.Thread(target=self._capture_loop, args=(frames,), daemon=True).start()
        try:
            while self.running:
                frame = frames.get()
                if frame is None:
                    break
                if ctx is None:
                    ctx = open_encoder(self.encoder, self.pix, self.enc_opts, frame.width, frame.height,
                                       self.fps, self.mbps)
                    # Audio is always set up so the output can be switched live.
                    cfg = struct.pack(">HHBIB", frame.width, frame.height, self.fps, AUDIO_RATE, AUDIO_CH)
                    self._push(T_CONFIG, cfg)
                    self.set_audio_mode(self.audio_mode)
                    t = threading.Thread(target=self._audio_loop, daemon=True)
                    t.start()
                    self.threads.append(t)
                frame.pts = index
                frame.time_base = ctx.time_base
                index += 1
                for pkt in ctx.encode(frame):
                    key = bool(pkt.is_keyframe)
                    au = params.fix(bytes(pkt), key)
                    with self.lock:
                        backlog = self.video_backlog
                    # Network can't keep up (>0.5 s queued): skip until the next keyframe.
                    if backlog > self.fps // 2:
                        waiting_key = True
                    if waiting_key and not key:
                        self.stats["dropped"] += 1
                        continue
                    waiting_key = False
                    ts = int((time.monotonic() - t0) * 1e6)
                    self._push(T_VIDEO, struct.pack(">Q", ts) + au, video=True)
            if self.running:
                self.stop("توقف التقاط الشاشة" + (f": {self.error}" if self.error else ""))
        except Exception as e:
            if self.running:
                self.error = str(e)
                self.stop(f"خطأ في ضغط الفيديو: {e}")

    def _audio_loop(self) -> None:
        try:
            import numpy as np
            import soundcard as sc
        except Exception as e:
            self.on_status(f"الصوت غير متاح: {e}")
            return
        try:
            # soundcard's COM calls need an initialised apartment on this thread.
            ctypes.windll.ole32.CoInitializeEx(None, 0)
        except Exception:
            pass
        block = AUDIO_RATE // 100  # 10 ms
        while self.running:
            try:
                spk = sc.default_speaker()
                mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
                with mic.recorder(samplerate=AUDIO_RATE, channels=AUDIO_CH, blocksize=block) as rec:
                    current = spk.name
                    checked = time.time()
                    while self.running:
                        data = rec.record(numframes=block)
                        pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
                        if self.audio_mode != "laptop":
                            self._push(T_AUDIO, pcm)
                        # Follow the user switching output device (e.g. headphones).
                        if time.time() - checked > 2:
                            checked = time.time()
                            if sc.default_speaker().name != current:
                                break
            except Exception as e:
                if self.running:
                    self.on_status(f"مشكلة في الصوت: {e}")
                    time.sleep(1)

    def _send_loop(self) -> None:
        try:
            while self.running:
                item = self.q.get()
                if item is None:
                    break
                t, payload, video = item
                if video:
                    with self.lock:
                        self.video_backlog -= 1
                elif t == T_AUDIO and self.q.qsize() > 50:
                    continue  # stale audio, keep latency low
                self.sock.sendall(struct.pack(">BI", t, len(payload)) + payload)
                if video:
                    self.stats["frames"] += 1
                self.stats["bytes"] += len(payload) + 5
        except Exception:
            if self.running:
                self.stop("انقطع الاتصال بالتلفزيون")


# --------------------------------------------------------------------------- CLI
def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Sharek screen sender")
    ap.add_argument("--list", action="store_true", help="find TVs on the network and exit")
    ap.add_argument("--tv", help="TV IP address (skips the window)")
    ap.add_argument("--port", type=int, default=STREAM_PORT)
    ap.add_argument("--res", type=int, default=1080, help="720/1080/1440/2160, 0 = native")
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--mbps", type=float, default=12)
    ap.add_argument("--monitor", type=int, default=0)
    ap.add_argument("--bars", action="store_true", help="keep the screen's shape with black bars")
    ap.add_argument("--audio", choices=["tv", "laptop", "both"], default="tv", help="where sound plays")
    a = ap.parse_args()
    recover_speakers()

    if a.list:
        tvs = discover(2.0)
        for tv in tvs:
            print(f"{tv['ip']}\t{tv['name']}")
        if not tvs:
            print("no Sharek TVs found")
        return
    if a.tv:
        st = Streamer(a.tv, a.port, a.res, a.fps, a.mbps, a.audio, a.monitor, fill=not a.bars)
        st.start()
        try:
            last = (time.time(), 0, 0)
            while st.running:
                time.sleep(2)
                now = time.time()
                f, b = st.stats["frames"], st.stats["bytes"]
                print(f"{(f - last[1]) / (now - last[0]):.1f} fps  {(b - last[2]) * 8 / (now - last[0]) / 1e6:.2f} Mbps"
                      f"  dropped={st.stats['dropped']}", flush=True)
                last = (now, f, b)
        except KeyboardInterrupt:
            st.stop()
        return
    import app  # the window lives in app.py
    app.main()


if __name__ == "__main__":
    main()
