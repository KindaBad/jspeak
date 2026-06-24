#!/usr/bin/env python3
"""JSpeak menu-bar icon for macOS.

Puts JSpeak in the menu bar (status area) with a purple mic icon: click for
Settings / Open log folder / Quit. Uses pystray, which on macOS must own the
main thread (it runs an NSApplication under the hood), so `run()` BLOCKS the
caller - the hotkey daemon runs on a background thread while this owns main.
If pystray/Pillow are unavailable it raises and the daemon falls back to running
headless.
"""
import os
import subprocess
import sys

import jpaths
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
    d.line([cx, int(size * 0.58), cx, int(size * 0.74)], fill=ACCENT + (255,),
           width=max(2, size // 18))
    d.line([cx - cap_w // 2, int(size * 0.74), cx + cap_w // 2,
            int(size * 0.74)], fill=ACCENT + (255,), width=max(2, size // 18))
    return img


def _spawn(role):
    try:
        subprocess.Popen([sys.executable, role])
    except Exception:
        pass


def _open_logs():
    try:
        subprocess.Popen(["open", str(jpaths.config_dir())])
    except Exception:
        pass


def _copy_last():
    import clip
    import history
    clip.copy(history.latest())


def run(on_quit):
    """Run the menu-bar icon on the CURRENT (main) thread. Blocks until Quit is
    chosen, then calls `on_quit`. Raises if pystray is unavailable so the caller
    can fall back to headless operation."""
    import pystray

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
    icon.run()           # blocks on the main thread (required by macOS)
    return icon
