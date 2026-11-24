"""Fake TV for testing the sender on the laptop alone.

Answers discovery, accepts the stream, and writes video to out.h264 / audio to out.pcm
so they can be checked with ffprobe. Usage: python test_receiver.py [seconds]
"""
import socket
import struct
import sys
import threading
import time

DISCOVERY_PORT, STREAM_PORT = 47800, 47801


def discovery():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", DISCOVERY_PORT))
    while True:
        data, addr = s.recvfrom(512)
        if data.startswith(b"SHAREK_DISCOVER"):
            s.sendto(f"SHAREK_HERE|Test TV|{STREAM_PORT}".encode(), addr)


def read_exact(c, n):
    b = bytearray()
    while len(b) < n:
        chunk = c.recv(n - len(b))
        if not chunk:
            raise EOFError
        b += chunk
    return bytes(b)


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 8
    threading.Thread(target=discovery, daemon=True).start()
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("", STREAM_PORT))
    srv.listen(1)
    print("fake TV listening", flush=True)
    c, addr = srv.accept()
    print("connected", addr, read_exact(c, 5), flush=True)
    t, ln = struct.unpack(">BI", read_exact(c, 5))
    print("hello", read_exact(c, ln).decode("utf-8"), flush=True)
    c.sendall(bytes([1]))  # approve
    counts = {}
    first_video = None
    keyframes = 0
    end = None
    with open("out.h264", "wb") as v, open("out.pcm", "wb") as a:
        try:
            while True:
                t, ln = struct.unpack(">BI", read_exact(c, 5))
                p = read_exact(c, ln)
                counts[t] = counts.get(t, 0) + 1
                if t == 1:
                    if first_video is None:
                        first_video = time.time()
                        end = first_video + secs
                    au = p[8:]
                    if b"\x00\x00\x00\x01\x67" in au[:64] or b"\x00\x00\x01\x67" in au[:64]:
                        keyframes += 1
                    v.write(au)
                elif t == 2:
                    a.write(p)
                elif t == 3:
                    print("config", struct.unpack(">HHBIB", p), flush=True)
                elif t == 5:
                    print("hello from", p.decode(), flush=True)
                elif t == 4:
                    print("bye", flush=True)
                    break
                if end and time.time() > end:
                    break
        except EOFError:
            print("sender closed", flush=True)
    dur = (time.time() - first_video) if first_video else 0
    print(f"packets={counts} keyframes_with_sps={keyframes} video_fps={counts.get(1, 0) / max(dur, 0.001):.1f}",
          flush=True)
    c.close()


if __name__ == "__main__":
    main()
