"""
"TV only" mode. Turning the laptop panel really off stops Windows screen capture, so instead:
the backlight goes to 0 and a plain black, click-through window covers the screen. Screen capture
skips that window (WDA_EXCLUDEFROMCAPTURE), so the TV keeps showing the desktop.
"""
from __future__ import annotations

import ctypes
import subprocess
import threading
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32")
kernel32 = ctypes.WinDLL("kernel32")

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

WS_POPUP = 0x80000000
WS_EX_TOPMOST, WS_EX_LAYERED, WS_EX_TRANSPARENT = 0x8, 0x80000, 0x20
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80, 0x08000000
WM_DESTROY, WM_PAINT, WM_TIMER, WM_CLOSE, WM_APP = 0x2, 0xF, 0x113, 0x10, 0x8000
WDA_NONE, WDA_EXCLUDEFROMCAPTURE = 0x0, 0x11
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x2, 0x10, 0x40
DT_CENTER, DT_SINGLELINE, DT_VCENTER, DT_RTLREADING = 0x1, 0x20, 0x4, 0x20000
MSG_SHOW, MSG_HIDE = WM_APP + 1, WM_APP + 2


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", wintypes.HDC), ("fErase", wintypes.BOOL), ("rcPaint", wintypes.RECT),
                ("fRestore", wintypes.BOOL), ("fIncUpdate", wintypes.BOOL), ("rgbReserved", ctypes.c_byte * 32)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


kernel32.GetModuleHandleW.restype = wintypes.HMODULE
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, wintypes.UINT]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.BeginPaint.restype = wintypes.HDC
user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH]
user32.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.RECT), wintypes.UINT]
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HMONITOR
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
gdi32.GetStockObject.restype = wintypes.HGDIOBJ
gdi32.CreateFontW.restype = wintypes.HFONT
gdi32.CreateFontW.argtypes = [ctypes.c_int] * 5 + [wintypes.DWORD] * 8 + [wintypes.LPCWSTR]
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]


def _powershell(cmd: str) -> str:
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                       capture_output=True, text=True, timeout=15, creationflags=0x08000000)
    return r.stdout.strip()


def get_brightness() -> int | None:
    """Laptop panel brightness 0-100, or None when the screen has no software brightness control."""
    try:
        out = _powershell("(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness).CurrentBrightness")
        return int(out.split()[0])
    except Exception:
        return None


def set_brightness(level: int) -> None:
    _powershell(f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{int(level)})")


def exclude_from_capture(hwnd: int, exclude: bool = True) -> bool:
    """Hide a window from screen capture (it stays visible on the laptop)."""
    return bool(user32.SetWindowDisplayAffinity(wintypes.HWND(hwnd), WDA_EXCLUDEFROMCAPTURE if exclude else WDA_NONE))


class Blackout:
    """Black, click-through, capture-excluded window covering the primary monitor."""

    def __init__(self):
        self.hwnd = None
        self.visible = False
        self.supported = False
        self._ready = threading.Event()
        self._proc = WNDPROC(self._wndproc)  # keep a reference so it isn't garbage collected
        threading.Thread(target=self._run, daemon=True, name="sharek-blackout").start()
        self._ready.wait(3)

    # -- public (any thread)
    def show(self) -> bool:
        if not self.supported or not self.hwnd:
            return False
        user32.PostMessageW(self.hwnd, MSG_SHOW, 0, 0)
        return True

    def hide(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, MSG_HIDE, 0, 0)

    def close(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    # -- window thread
    def _run(self) -> None:
        try:
            user32.SetThreadDpiAwarenessContext(wintypes.HANDLE(-4))  # per-monitor v2: real pixels
        except Exception:
            pass
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.hbrBackground = gdi32.GetStockObject(4)  # BLACK_BRUSH
        wc.lpszClassName = "SharekBlackout"
        user32.RegisterClassExW(ctypes.byref(wc))

        mon = user32.MonitorFromPoint(wintypes.POINT(0, 0), 1)  # primary
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(mon, ctypes.byref(mi))
        r = mi.rcMonitor
        self.hwnd = user32.CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            "SharekBlackout", "Sharek", WS_POPUP, r.left, r.top, r.right - r.left, r.bottom - r.top,
            None, None, hinst, None)
        user32.SetLayeredWindowAttributes(self.hwnd, 0, 255, 2)  # opaque, but still click-through
        # Without capture exclusion the TV would go black too, so the feature is off on old Windows.
        self.supported = bool(user32.SetWindowDisplayAffinity(self.hwnd, WDA_EXCLUDEFROMCAPTURE))
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _topmost(self, hwnd) -> None:
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)

    def _wndproc(self, hwnd, msg, wp, lp):
        if msg == MSG_SHOW:
            self.visible = True
            user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
            self._topmost(hwnd)
            user32.SetTimer(hwnd, 1, 1000, None)  # stay above full-screen players
            return 0
        if msg == MSG_HIDE:
            self.visible = False
            user32.KillTimer(hwnd, 1)
            user32.ShowWindow(hwnd, SW_HIDE)
            return 0
        if msg == WM_TIMER:
            if self.visible:
                self._topmost(hwnd)
            return 0
        if msg == WM_PAINT:
            ps = PAINTSTRUCT()
            hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
            rc = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rc))
            user32.FillRect(hdc, ctypes.byref(rc), gdi32.GetStockObject(4))
            user32.EndPaint(hwnd, ctypes.byref(ps))
            return 0
        if msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wp, lp)
