#!/usr/bin/env python3
"""JSpeak system-tray icon for Windows.

Puts JSpeak in the notification area so it stops "hiding in the taskbar": a
purple mic icon you can right-click for Settings / Open log / Quit, or
double-click to jump straight into Settings. Runs on its own thread via pystray
so it never blocks the hotkey daemon; if pystray/Pillow are unavailable it
degrades silently and the daemon keeps running headless.
"""
import os
import subprocess
import sys
import threading

import jpaths
import notify
import version

ACCENT = (168, 85, 247)        # #a855f7
DARK = (20, 18, 26)            # #14121a


def _make_icon_image(size=64):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # rounded dark tile with a purple glow ring + simple mic glyph
    d.rounded_rectangle([2, 2, size - 3, size - 3], radius=size // 5,
                        fill=DARK + (255,), outline=ACCENT + (255,), width=3)
    cx = size // 2
    cap_w = size // 4
    d.rounded_rectangle([cx - cap_w // 2, size // 4, cx + cap_w // 2,
                         int(size * 0.58)], radius=cap_w // 2,
                        fill=ACCENT + (255,))
    # stand + base
    d.line([cx, int(size * 0.58), cx, int(size * 0.74)], fill=ACCENT + (255,),
           width=max(2, size // 18))
    d.line([cx - cap_w // 2, int(size * 0.74), cx + cap_w // 2,
            int(size * 0.74)], fill=ACCENT + (255,), width=max(2, size // 18))
    return img


def _spawn(role):
    flags = 0x08000000  # CREATE_NO_WINDOW
    try:
        subprocess.Popen([sys.executable, role], creationflags=flags)
    except Exception:
        pass


def _open_logs():
    try:
        os.startfile(str(jpaths.config_dir()))   # noqa: P204 (Windows only)
    except Exception:
        pass


def _copy_last():
    import clip
    import history
    clip.copy(history.latest())


def start(on_quit):
    """Start the tray icon on a daemon thread. `on_quit` is called when the
    user picks Quit (the daemon uses it to tear down and exit)."""
    try:
        import pystray
    except Exception:
        return None

    def _quit(icon, _item):
        try:
            icon.stop()
        finally:
            on_quit()

    menu = pystray.Menu(
        pystray.MenuItem("Settings", lambda i, it: _spawn("--settings"),
                         default=True),
        pystray.MenuItem("Copy last dictation", lambda i, it: _copy_last()),
        pystray.MenuItem("Open log folder", lambda i, it: _open_logs()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit JSpeak", _quit),
    )
    icon = pystray.Icon("jspeak", _make_icon_image(),
                        f"JSpeak v{version.__version__}", menu)

    def _balloon(title, message):
        try:
            icon.notify(message, title)
        except Exception:
            pass

    notify.set_windows_notifier(_balloon)
    threading.Thread(target=icon.run, daemon=True).start()
    return icon
