# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for JSpeak on macOS (onedir).

Bundles pynput (hotkey + typing), sounddevice + numpy (audio), pystray + Pillow
(menu-bar icon), the PyObjC AppKit overlay, Tkinter (settings), and the default
config. Excludes the Linux-only GTK modules and the Windows-only modules.
Produces dist/jspeak/jspeak - one program that dispatches to
daemon / --overlay / --settings.
"""
from PyInstaller.utils.hooks import collect_all

datas = [("config.default.json", ".")]
binaries = []
hiddenimports = ["pynput", "sounddevice", "numpy", "pystray", "PIL",
                 "objc", "Foundation", "AppKit", "Quartz", "certifi",
                 "macmain", "macoverlay", "mactray", "winsettings",
                 "audio", "hotkeys", "notify", "glowgradient", "groq_core",
                 "appconfig", "jpaths", "updater", "version",
                 "history", "audiodevices", "clip"]

for mod in ("sounddevice", "pynput", "pystray", "objc",
            "Foundation", "AppKit", "Quartz", "certifi"):
    d, b, h = collect_all(mod)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["app.py"],
    pathex=["shared", "macos", "windows"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["gi", "cairo", "overlay", "settings", "jspeak",
              "winmain", "winoverlay", "wintray", "winsettings_qt", "PySide6"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="jspeak",
    debug=False,
    strip=False,
    upx=False,
    console=False,   # windowed; logs go to ~/Library/Application Support/jspeak
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="jspeak",
)
