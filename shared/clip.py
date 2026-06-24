"""Set the system clipboard to a string. Used by the tray's "Copy last
dictation" action and the ``--copy-last`` CLI role.

Deliberately tiny and dependency-free (no pynput / sounddevice imports) so the
short-lived ``--copy-last`` process starts instantly. Returns True on success,
False on any failure - callers treat it as best-effort.
"""
import subprocess
import sys


def copy(text):
    if not text:
        return False
    if sys.platform == "win32":
        return _copy_win(text)
    if sys.platform == "darwin":
        return _copy_cmd(["pbcopy"], text)
    return _copy_cmd(["wl-copy"], text)


def _copy_cmd(cmd, text):
    try:
        return subprocess.run(
            cmd, input=text.encode("utf-8"),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
        ).returncode == 0
    except Exception:
        return False


def _copy_win(text):
    """Win32 clipboard set, with pointer-clean ctypes signatures so the global
    handles aren't truncated on Win64 (the same care winmain takes)."""
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return False
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    u = ctypes.WinDLL("user32", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    u.OpenClipboard.argtypes = (wintypes.HWND,)
    u.OpenClipboard.restype = wintypes.BOOL
    u.EmptyClipboard.restype = wintypes.BOOL
    u.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
    u.SetClipboardData.restype = wintypes.HANDLE
    u.CloseClipboard.restype = wintypes.BOOL
    k.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
    k.GlobalAlloc.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = (ctypes.c_void_p,)
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalUnlock.argtypes = (ctypes.c_void_p,)
    k.GlobalUnlock.restype = wintypes.BOOL
    if not u.OpenClipboard(None):
        return False
    try:
        u.EmptyClipboard()
        data = (text + "\0").encode("utf-16-le")
        h = k.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            return False
        p = k.GlobalLock(h)
        if not p:
            return False
        ctypes.memmove(p, data, len(data))
        k.GlobalUnlock(h)
        u.SetClipboardData(CF_UNICODETEXT, h)
        return True
    finally:
        u.CloseClipboard()
