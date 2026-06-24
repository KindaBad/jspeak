# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for JSpeak on Windows (onedir).

Bundles PySide6/Qt (the settings window), pynput (hotkey + typing), sounddevice
+ numpy (audio), and the default config. The overlay is pure Win32/GDI, so no
GUI toolkit is needed there. Excludes the Linux-only GTK modules and Tkinter
(the macOS/Linux settings paths) since Windows now uses Qt. Produces
dist/jspeak/jspeak.exe - one program that dispatches to
daemon / --overlay / --settings.
"""
from PyInstaller.utils.hooks import collect_all

datas = [("config.default.json", ".")]
binaries = []
hiddenimports = ["pynput", "sounddevice", "numpy", "pystray", "PIL",
                 "winmain", "winoverlay", "winsettings_qt", "wintray",
                 "audio", "hotkeys", "notify", "glowgradient", "groq_core",
                 "appconfig", "jpaths", "updater", "version",
                 "history", "audiodevices", "clip",
                 "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"]

for mod in ("sounddevice", "pynput", "pystray"):
    d, b, h = collect_all(mod)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["app.py"],
    pathex=["shared", "windows"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["gi", "cairo", "overlay", "settings", "jspeak", "winsettings",
              "tkinter", "_tkinter"],
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
    console=False,   # windowed; logs go to %APPDATA%/jspeak/jspeak.log
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="jspeak",
)
