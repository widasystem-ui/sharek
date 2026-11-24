"""
Sharek desktop app: the window (ui/index.html in WebView2), the tray icon and the Ctrl+Alt+S shortcut.
The streaming engine lives in sharek.py.
"""
from __future__ import annotations

import ctypes
import json
import os
import socket
import sys
import threading
import time
from ctypes import wintypes

import sharek

APP_VERSION = "1.0"
HOTKEY_LABEL = "Ctrl+Alt+S"
INSTANCE_PORT = 47809  # localhost only: lets a second launch bring the first window back

DEFAULTS = {"res": "1080p", "fps": 60, "quality": "high", "audio_mode": "tv", "fill": True,
            "last_tv": "", "manual_ip": ""}
QUALITY_MBPS = {"medium": 6, "high": 12, "max": 20}
RES_LINES = {"720p": 720, "1080p": 1080, "native": 0}


def resource(*parts: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def friendly_error(e: Exception) -> str:
    if isinstance(e, (socket.timeout, TimeoutError, ConnectionRefusedError, ConnectionResetError)):
        return "ما قدرنا نوصل للتلفزيون"
    if isinstance(e, OSError) and getattr(e, "winerror", None) in (10060, 10061, 10065, 10051):
        return "ما قدرنا نوصل للتلفزيون"
    return str(e) or "صار خطأ غير متوقع"


class SharekApp:
    def __init__(self) -> None:
        sharek.recover_speakers()
        self.settings = sharek.load_settings()
        self._migrate()
        self.lock = threading.RLock()
        self.tvs: list[dict] = []
        self.searching = False
        self.status = {"state": "idle", "message": "", "tv_name": "", "fps": 0, "mbps": 0, "encoder": ""}
        self.streamer: sharek.Streamer | None = None
        self.user_stopped = False
        self.window = None
        self.tray = None
        self.visible = True
        self.told_about_tray = False
        self.quitting = False

    def _migrate(self) -> None:
        s = self.settings
        s["res"] = {"الأصلية": "native"}.get(s.get("res"), s.get("res"))
        s["quality"] = {"متوسطة": "medium", "عالية": "high", "عالية جداً": "max"}.get(s.get("quality"), s.get("quality"))
        for k, v in DEFAULTS.items():
            s.setdefault(k, v)
        if s["res"] not in RES_LINES:
            s["res"] = "1080p"
        if s["quality"] not in QUALITY_MBPS:
            s["quality"] = "high"
        sharek.save_settings(s)

    # ------------------------------------------------------------------ state → UI
    def state(self) -> dict:
        keys = ("res", "fps", "quality", "audio_mode", "fill", "last_tv", "manual_ip")
        return {
            "version": APP_VERSION,
            "hotkey": HOTKEY_LABEL,
            "device_name": sharek.device_name(),
            "searching": self.searching,
            "tvs": self.tvs,
            "settings": {k: self.settings.get(k) for k in keys},
            "status": dict(self.status),
        }

    def push(self) -> None:
        if self.window is not None:
            payload = json.dumps(self.state(), ensure_ascii=False)
            try:
                self.window.evaluate_js(f"window.sharek && window.sharek.onState({payload})")
            except Exception:
                pass
        if self.tray is not None:
            try:
                self.tray.title = self.tray_title()
                self.tray.update_menu()
            except Exception:
                pass

    def set_status(self, **kw) -> None:
        with self.lock:
            if kw.get("state") and kw["state"] != "streaming":
                kw.setdefault("fps", 0)
                kw.setdefault("mbps", 0)
            self.status.update(kw)
        self.push()

    def tray_title(self) -> str:
        s = self.status["state"]
        if s == "streaming":
            return f"شارك — تعرض على {self.status['tv_name']}"
        if s in ("connecting", "waiting_approval"):
            return "شارك — بانتظار التلفزيون"
        return "شارك"

    # ------------------------------------------------------------------ discovery
    def search(self, quiet: bool = False) -> None:
        with self.lock:
            if self.searching:
                return
            self.searching = not quiet
        if not quiet:
            self.push()

        def run():
            found = []
            try:
                found = sharek.discover(1.5)
            finally:
                found.sort(key=lambda t: t["name"])
                changed = found != self.tvs
                self.tvs = found
                was = self.searching
                self.searching = False
                if changed or was:
                    self.push()

        threading.Thread(target=run, daemon=True).start()

    def _auto_search_loop(self) -> None:
        while not self.quitting:
            time.sleep(6)
            if self.visible and self.status["state"] == "idle" and not self.searching:
                self.search(quiet=True)

    # ------------------------------------------------------------------ sharing
    def tv_name(self, ip: str) -> str:
        return next((t["name"] for t in self.tvs if t["ip"] == ip), ip)

    def start(self, ip: str) -> None:
        ip = (ip or "").strip()
        with self.lock:
            if not ip or self.status["state"] not in ("idle", "rejected", "error"):
                return
            self.user_stopped = False
            self.set_status(state="connecting", tv_name=self.tv_name(ip), message="", encoder="")
        threading.Thread(target=self._run_stream, args=(ip,), daemon=True).start()

    def _run_stream(self, ip: str) -> None:
        s = self.settings
        fps = int(s["fps"])
        mbps = QUALITY_MBPS[s["quality"]] * (1.5 if fps >= 60 else 1)
        st = sharek.Streamer(ip, res=RES_LINES[s["res"]], fps=fps, mbps=mbps, audio=s["audio_mode"],
                             fill=bool(s["fill"]), settings=s, on_status=self._on_engine_message,
                             on_phase=self._on_phase)
        self.streamer = st
        try:
            st.start()
            s["last_tv"] = ip
            sharek.save_settings(s)
        except sharek.Cancelled:
            self.set_status(state="idle")
        except sharek.Rejected as e:
            self.set_status(state="rejected", message=str(e))
        except Exception as e:
            if st.cancelled:
                self.set_status(state="idle")
            else:
                self.set_status(state="error", message=friendly_error(e))

    def _on_phase(self, phase: str) -> None:
        if self.streamer and self.streamer.cancelled:
            return
        if phase == "streaming":
            self.set_status(state="streaming", encoder=self.streamer.encoder if self.streamer else "")
            if not self.visible:
                self.notify(f"تعرض الحين على {self.status['tv_name']}")
        else:
            self.set_status(state=phase)

    def _on_engine_message(self, msg: str) -> None:
        # The engine reports why it stopped; the ticker turns an unexpected stop into an error.
        self.last_engine_message = msg

    def stop(self) -> None:
        st = self.streamer
        state = self.status["state"]
        if state in ("connecting", "waiting_approval"):
            if st:
                st.cancel()
            self.set_status(state="idle")
        elif state == "streaming":
            self.user_stopped = True
            if st:
                threading.Thread(target=st.stop, daemon=True).start()
            self.set_status(state="idle")
            if not self.visible:
                self.notify("توقفت المشاركة")

    def reset(self) -> None:
        if self.status["state"] in ("rejected", "error"):
            self.set_status(state="idle", message="")

    def toggle(self) -> None:
        """Shortcut / tray: start sharing to the last TV, or stop."""
        state = self.status["state"]
        if state in ("idle", "rejected", "error"):
            ip = self.settings.get("last_tv") or self.settings.get("manual_ip") or (self.tvs[0]["ip"] if self.tvs else "")
            if not ip:
                self.show()
                self.notify("اختر تلفزيون أول مرة من نافذة شارك")
                return
            self.status["state"] = "idle"
            self.start(ip)
        else:
            self.stop()

    def set_setting(self, key: str, value) -> None:
        if key not in DEFAULTS:
            return
        if key == "fps":
            value = 60 if int(value) >= 60 else 30
        elif key == "fill":
            value = bool(value)
        elif key == "res" and value not in RES_LINES:
            return
        elif key == "quality" and value not in QUALITY_MBPS:
            return
        elif key == "audio_mode" and value not in ("tv", "laptop", "both"):
            return
        self.settings[key] = value
        sharek.save_settings(self.settings)
        if key == "audio_mode" and self.streamer and self.streamer.running:
            threading.Thread(target=self.streamer.set_audio_mode, args=(value,), daemon=True).start()
        self.push()

    def _ticker(self) -> None:
        prev = None
        while not self.quitting:
            time.sleep(1)
            st = self.streamer
            if self.status["state"] != "streaming" or not st:
                prev = None
                continue
            if not st.running:
                if not self.user_stopped:
                    msg = getattr(self, "last_engine_message", "") or "انقطع الاتصال بالتلفزيون"
                    self.set_status(state="error", message=msg)
                    if not self.visible:
                        self.notify(msg)
                continue
            now = time.time()
            f, b = st.stats["frames"], st.stats["bytes"]
            if prev:
                dt = now - prev[0]
                self.status["fps"] = round((f - prev[1]) / dt, 1)
                self.status["mbps"] = round((b - prev[2]) * 8 / dt / 1e6, 2)
                self.push()
            prev = (now, f, b)

    # ------------------------------------------------------------------ window / tray
    def show(self) -> None:
        self.visible = True
        if self.window:
            try:
                self.window.show()
                self.window.restore()
            except Exception:
                pass
        self.search(quiet=True)

    def hide(self) -> None:
        self.visible = False
        if self.window:
            self.window.hide()
        if not self.told_about_tray:
            self.told_about_tray = True
            self.notify(f"شارك شغال جنب الساعة. اضغط {HOTKEY_LABEL} للمشاركة.")

    def minimize(self) -> None:
        if self.window:
            self.window.minimize()

    def notify(self, text: str) -> None:
        if self.tray is not None:
            try:
                self.tray.notify(text, "شارك")
            except Exception:
                pass

    def quit(self) -> None:
        self.quitting = True
        if self.streamer and self.streamer.running:
            self.streamer.stop()
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        if self.window:
            self.window.destroy()

    def _on_closing(self):
        # Closing the window (Alt+F4) keeps Sharek running beside the clock.
        if self.quitting:
            return True
        threading.Thread(target=self.hide, daemon=True).start()
        return False

    def start_tray(self) -> None:
        try:
            import pystray
        except Exception:
            return
        menu = pystray.Menu(
            pystray.MenuItem("إظهار شارك", lambda: self.show(), default=True),
            pystray.MenuItem(lambda item: "إيقاف المشاركة" if self.status["state"] != "idle" and
                             self.status["state"] not in ("rejected", "error") else "ابدأ المشاركة",
                             lambda: self.toggle()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("خروج", lambda: self.quit()),
        )
        self.tray = pystray.Icon("sharek", tray_image(), "شارك", menu)
        self.tray.run_detached()

    def start_background(self) -> None:
        for target in (self._ticker, self._auto_search_loop):
            threading.Thread(target=target, daemon=True).start()
        threading.Thread(target=hotkey_loop, args=(self,), daemon=True).start()
        self.start_tray()
        self.search()


# ---------------------------------------------------------------------- JS bridge
class Api:
    """Methods callable from ui/index.html as window.pywebview.api.*"""

    def __init__(self, app: SharekApp):
        self._app = app

    def get_state(self):
        return self._app.state()

    def search(self):
        self._app.search()

    def start(self, ip):
        self._app.start(ip)

    def stop(self):
        self._app.stop()

    def reset(self):
        self._app.reset()

    def set_setting(self, key, value):
        self._app.set_setting(key, value)

    def minimize(self):
        self._app.minimize()

    def hide(self):
        self._app.hide()


# ---------------------------------------------------------------------- helpers
def tray_image(size: int = 64):
    from PIL import Image, ImageDraw
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    top, bottom = (107, 227, 188), (47, 178, 135)
    for y in range(s):  # vertical gradient tile
        t = y / s
        c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        d.line([(0, y), (s, y)], fill=c + (255,))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.26), fill=255)
    img.putalpha(mask)
    ink = (6, 16, 31, 255)
    w = int(s * 0.07)
    d.rounded_rectangle([s * 0.21, s * 0.27, s * 0.79, s * 0.64], radius=int(s * 0.06), outline=ink, width=w)
    d.line([s * 0.37, s * 0.77, s * 0.63, s * 0.77], fill=ink, width=w)
    for r in (s * 0.09, s * 0.17):
        cx, cy = s * 0.5, s * 0.56
        d.arc([cx - r, cy - r, cx + r, cy + r], 225, 315, fill=ink, width=w)
    return img.resize((size, size), Image.LANCZOS)


def hotkey_loop(app: SharekApp) -> None:
    user32 = ctypes.windll.user32
    MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, WM_HOTKEY = 0x1, 0x2, 0x4000, 0x0312
    if not user32.RegisterHotKey(None, 1, MOD_ALT | MOD_CONTROL | MOD_NOREPEAT, ord("S")):
        sharek.log("hotkey Ctrl+Alt+S is taken by another program")
        return
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message == WM_HOTKEY:
            threading.Thread(target=app.toggle, daemon=True).start()


def single_instance(app_ref: list) -> bool:
    """True if this is the first copy. A second copy asks the first one to show its window."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        srv.bind(("127.0.0.1", INSTANCE_PORT))
    except OSError:
        try:
            with socket.create_connection(("127.0.0.1", INSTANCE_PORT), timeout=1) as c:
                c.sendall(b"show")
        except OSError:
            pass
        return False
    srv.listen(2)

    def serve():
        while True:
            c, _ = srv.accept()
            with c:
                if c.recv(16) == b"show" and app_ref:
                    app_ref[0].show()

    threading.Thread(target=serve, daemon=True).start()
    return True


def centered(w: int, h: int) -> tuple[int | None, int | None]:
    """Window position (in DPI-scaled units) that centres it inside the work area above the taskbar."""
    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        rect = wintypes.RECT()
        user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
        scale = user32.GetDpiForSystem() / 96
        area_w = (rect.right - rect.left) / scale
        area_h = (rect.bottom - rect.top) / scale
        return int(rect.left / scale + (area_w - w) / 2), int(rect.top / scale + max(0, (area_h - h) / 2))
    except Exception:
        return None, None


def main() -> None:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Sharek.Desktop")
    except Exception:
        pass
    ref: list = []
    if not single_instance(ref):
        return
    import webview

    app = SharekApp()
    ref.append(app)
    dark = "#0A1328"
    w, h = 430, 640
    x, y = centered(w, h)
    window = webview.create_window(
        "شارك", url=resource("ui", "index.html"), js_api=Api(app),
        width=w, height=h, x=x, y=y, resizable=False, frameless=True, easy_drag=False,
        background_color=dark,
    )
    app.window = window
    window.events.closing += app._on_closing
    webview.start(app.start_background, private_mode=True)
    app.quitting = True
    if app.streamer and app.streamer.running:
        app.streamer.stop()


if __name__ == "__main__":
    main()
