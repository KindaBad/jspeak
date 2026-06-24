#!/usr/bin/env python3
"""JSpeak screen-edge glow overlay for Windows (layered windows + GDI).

A soft purple glow painted around the screen edges, matching the Linux GTK
overlay as closely as Win32 allows. Four borderless, topmost, click-through,
no-activate windows hug the edges. Each one is a *per-pixel alpha* layered
window (UpdateLayeredWindow + AC_SRC_ALPHA): the glow fades from a luminous lip
at the screen edge through the base colour to a transparent inner edge, exactly
like the cairo gradient on Linux. This is why the old flat-fill Tk strips looked
like dull grey rectangles -- a single window-wide alpha cannot make a gradient.

The gradient bitmap is baked ONCE per state (base / error) with numpy; each
animation frame only changes the layered window's global SourceConstantAlpha, so
the breathing pulse is essentially free. State is driven by single-line commands
on stdin from the daemon: rec / proc / error / off / quit. Click-through +
no-activate mean it never steals focus or blocks the mouse.
"""
import ctypes
import math
import os
import sys
import threading
import time
from ctypes import wintypes

import glowgradient

# --- window styles ---------------------------------------------------------
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4

SM_CXSCREEN = 0
SM_CYSCREEN = 1

ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
BI_RGB = 0
DIB_RGB_COLORS = 0

WM_APP = 0x8000
WM_TIMER = 0x0113
WM_QUIT = 0x0012

# command name -> WM_APP wParam code
CMD_CODES = {"rec": 1, "proc": 2, "error": 3, "off": 4, "quit": 5}
CODE_CMDS = {v: k for k, v in CMD_CODES.items()}

TARGETS = {"off": 0.0, "rec": 1.0, "proc": 0.5, "error": 0.9}
FPS_INTERVAL_MS = 33          # ~30fps; smooth glow, light on the CPU
REDRAW_EPS = 0.006            # skip frames whose alpha barely changed


def hex_rgb(s, default=(168, 85, 247)):
    try:
        s = (s or "").lstrip("#")
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except Exception:
        return default


COLOR = hex_rgb(os.environ.get("JSPEAK_OVERLAY_COLOR", "#a855f7"))
ERROR_COLOR = (229, 70, 66)
MAX_ALPHA = float(os.environ.get("JSPEAK_OVERLAY_ALPHA", "0.55"))
THICK = int(os.environ.get("JSPEAK_OVERLAY_THICKNESS", "110"))
REDUCE_MOTION = os.environ.get("JSPEAK_OVERLAY_REDUCE_MOTION", "0") == "1"


# ---------------------------------------------------------------------------
# Win32 prototypes (typed so 64-bit handles survive)
# ---------------------------------------------------------------------------
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte),
                ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte),
                ("AlphaFormat", ctypes.c_byte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD),
                ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD), ("pt", POINT)]


user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.UpdateLayeredWindow.restype = wintypes.BOOL
user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND, wintypes.HDC, ctypes.POINTER(POINT), ctypes.POINTER(SIZE),
    wintypes.HDC, ctypes.POINTER(POINT), wintypes.COLORREF,
    ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                      wintypes.WPARAM, wintypes.LPARAM]
user32.SetTimer.restype = ULONG_PTR
user32.SetTimer.argtypes = [wintypes.HWND, ULONG_PTR, wintypes.UINT, wintypes.LPVOID]
user32.KillTimer.argtypes = [wintypes.HWND, ULONG_PTR]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]

gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]


def set_dpi_aware():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


def _make_dib(memdc, w, h, pixel_bytes):
    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = w
    bmi.biHeight = -h          # top-down
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = BI_RGB
    bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(memdc, ctypes.byref(bmi), DIB_RGB_COLORS,
                                  ctypes.byref(bits), None, 0)
    if hbmp and bits:
        ctypes.memmove(bits, pixel_bytes, len(pixel_bytes))
    return hbmp


class Strip:
    def __init__(self, screen_dc, orient, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.shown = False
        exstyle = (WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
                   | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)
        self.hwnd = user32.CreateWindowExW(
            exstyle, "Static", None, WS_POPUP, x, y, w, h,
            None, None, None, None)
        self.memdc = gdi32.CreateCompatibleDC(screen_dc)
        self.hbmp_base = _make_dib(self.memdc, w, h,
                                   glowgradient.bgra_premul(w, h, orient, COLOR, MAX_ALPHA))
        self.hbmp_err = _make_dib(self.memdc, w, h,
                                  glowgradient.bgra_premul(w, h, orient, ERROR_COLOR, MAX_ALPHA))
        self._error = False
        gdi32.SelectObject(self.memdc, self.hbmp_base)

    def set_error(self, error):
        if error != self._error:
            self._error = error
            gdi32.SelectObject(self.memdc, self.hbmp_err if error else self.hbmp_base)

    def update(self, const_alpha):
        ca = max(0, min(255, int(round(const_alpha * 255))))
        if ca <= 0:
            self.hide()
            return
        if not self.shown:
            user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
            self.shown = True
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, ca, AC_SRC_ALPHA)
        dst = POINT(self.x, self.y)
        src = POINT(0, 0)
        size = SIZE(self.w, self.h)
        user32.UpdateLayeredWindow(
            self.hwnd, None, ctypes.byref(dst), ctypes.byref(size),
            self.memdc, ctypes.byref(src), 0, ctypes.byref(blend), ULW_ALPHA)

    def hide(self):
        if self.shown:
            user32.ShowWindow(self.hwnd, SW_HIDE)
            self.shown = False


# ---------------------------------------------------------------------------
# Animation state machine (mirrors overlay.py Glow.tick)
# ---------------------------------------------------------------------------
class Glow:
    def __init__(self):
        set_dpi_aware()
        sw = user32.GetSystemMetrics(SM_CXSCREEN)
        sh = user32.GetSystemMetrics(SM_CYSCREEN)
        t = max(8, min(THICK, sw // 2, sh // 2))
        screen_dc = user32.GetDC(None)
        # top, bottom, left, right
        specs = [
            ("top", 0, 0, sw, t),
            ("bottom", 0, sh - t, sw, t),
            ("left", 0, 0, t, sh),
            ("right", sw - t, 0, t, sh),
        ]
        self.strips = [Strip(screen_dc, o, x, y, w, h)
                       for (o, x, y, w, h) in specs]
        user32.ReleaseDC(None, screen_dc)

        self.state = "off"
        self.level = 0.0
        self.phase = 0.0
        self.alpha = 0.0
        self._last_alpha = -1.0
        self.last = time.monotonic()
        self.error_until = 0.0
        self.running = False           # animation timer active
        self.timer_id = 0

    # --- timer control ---
    def ensure_running(self):
        if not self.running:
            self.running = True
            self.last = time.monotonic()
            self.timer_id = user32.SetTimer(None, 0, FPS_INTERVAL_MS, None)

    def stop_timer(self):
        if self.running and self.timer_id:
            user32.KillTimer(None, self.timer_id)
        self.running = False
        self.timer_id = 0

    # --- commands ---
    def on_command(self, cmd):
        if cmd == "error":
            self.error_until = time.monotonic() + 0.85
        for s in self.strips:
            s.set_error(cmd == "error")
        if cmd in TARGETS:
            self.state = cmd
            self.ensure_running()

    def tick(self):
        now = time.monotonic()
        dt = min(0.06, now - self.last)
        self.last = now
        self.phase += dt

        if self.state == "error" and now >= self.error_until:
            self.state = "off"
            for s in self.strips:
                s.set_error(False)

        target = TARGETS[self.state]
        tau = 0.22 if target > self.level else 0.16
        self.level += (target - self.level) * min(1.0, dt / tau)

        animated = self.state == "error" or (
            self.state in ("rec", "proc") and not REDUCE_MOTION)
        if not animated:
            pulse = 1.0
        elif self.state == "rec":
            p = self.phase
            pulse = (0.82 + 0.13 * math.sin(2 * math.pi * p / 1.9)
                     + 0.05 * math.sin(2 * math.pi * p / 0.83))
        elif self.state == "proc":
            p = self.phase
            pulse = (0.80 + 0.14 * math.sin(2 * math.pi * p / 1.05)
                     + 0.04 * math.sin(2 * math.pi * p / 0.47))
        else:  # error
            pulse = 0.68 + 0.32 * math.sin(2 * math.pi * self.phase / 0.32)

        self.alpha = max(0.0, min(1.0, self.level * pulse))
        if abs(self.alpha - self._last_alpha) > REDRAW_EPS:
            for s in self.strips:
                s.update(self.alpha)
            self._last_alpha = self.alpha

        if self.state == "off" and self.level < 0.002:
            for s in self.strips:
                s.hide()
            self._last_alpha = 0.0
            self.stop_timer()
            return
        if not animated and abs(self.level - target) < 0.0015:
            self.stop_timer()          # static hold: stop until next state change


# ---------------------------------------------------------------------------
# stdin reader -> posts commands onto the GUI thread's message queue
# ---------------------------------------------------------------------------
def stdin_reader(main_tid):
    for raw in sys.stdin:
        cmd = raw.strip()
        code = CMD_CODES.get(cmd)
        if code:
            user32.PostThreadMessageW(main_tid, WM_APP, code, 0)


def main():
    glow = Glow()
    main_tid = kernel32.GetCurrentThreadId()
    threading.Thread(target=stdin_reader, args=(main_tid,), daemon=True).start()

    msg = MSG()
    while True:
        ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if ret == 0 or ret == -1:      # WM_QUIT or error
            break
        if msg.message == WM_TIMER and not msg.hwnd:
            glow.tick()
        elif msg.message == WM_APP:
            cmd = CODE_CMDS.get(msg.wParam)
            if cmd == "quit":
                break
            elif cmd:
                glow.on_command(cmd)
        else:
            user32.DispatchMessageW(ctypes.byref(msg))


if __name__ == "__main__":
    main()
