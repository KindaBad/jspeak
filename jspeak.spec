# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for JSpeak (Linux onedir bundle).

Bundles the GTK3 stack (via gi), the GtkLayerShell typelib + shared library
(loaded dynamically, so PyInstaller can't auto-detect them), pycairo, and the
default config. Produces dist/jspeak/jspeak - a single executable that dispatches
to daemon / --overlay / --settings.
"""
import glob
import os
from PyInstaller.utils.hooks import collect_all

datas = [("config.default.json", ".")]
binaries = []
hiddenimports = ["cairo", "gi", "audio", "hotkeys", "notify", "groq_core",
                 "appconfig", "jpaths", "updater", "version",
                 "history", "audiodevices", "clip"]

# Pull in the whole gi / GTK stack.
for mod in ("gi",):
    d, b, h = collect_all(mod)
    datas += d
    binaries += b
    hiddenimports += h

# GObject-Introspection typelibs that are loaded dynamically by name.
TYPELIB_DIR = "/usr/lib/girepository-1.0"
for name in ("GtkLayerShell-0.1", "cairo-1.0"):
    p = os.path.join(TYPELIB_DIR, name + ".typelib")
    if os.path.exists(p):
        datas.append((p, "gi_typelibs"))

# The gtk-layer-shell shared library (dlopened via the typelib).
for so in glob.glob("/usr/lib/libgtk-layer-shell.so*"):
    binaries.append((so, "."))

a = Analysis(
    ["app.py"],
    pathex=["shared", "linux"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "winmain", "winoverlay", "winsettings", "wintray",
              "glowgradient", "pynput", "sounddevice", "pystray", "PIL"],
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
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="jspeak",
)
