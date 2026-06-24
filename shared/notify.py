"""Cross-platform desktop notifications, so failures explain themselves instead
of only flashing the red glow. Best-effort: never raises."""
import shutil
import subprocess
import sys

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

_win_notifier = None   # set by wintray once the tray icon is running


def set_windows_notifier(fn):
    """wintray registers a callable (title, message) -> None that pops a tray
    balloon. Until then Windows notifications are a no-op (logged elsewhere)."""
    global _win_notifier
    _win_notifier = fn


def _osascript_str(s):
    """Quote a Python string as an AppleScript string literal."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def notify(title, message):
    try:
        if IS_WIN:
            if _win_notifier:
                _win_notifier(title, message)
        elif IS_MAC:
            script = (f"display notification {_osascript_str(message)} "
                      f"with title {_osascript_str(title)}")
            subprocess.Popen(["osascript", "-e", script],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif shutil.which("notify-send"):
            subprocess.Popen(
                ["notify-send", "-a", "JSpeak", "-u", "normal", title, message],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
