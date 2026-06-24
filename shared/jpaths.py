"""Filesystem locations that work both when running from source and when
running as a frozen (PyInstaller) binary."""
import os
import sys
from pathlib import Path


def config_dir():
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    p = Path(base) / "jspeak"
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_path():
    return config_dir() / "config.json"


def bundle_dir():
    """Directory holding bundled data files (config.default.json, etc.).
    PyInstaller unpacks data into sys._MEIPASS; from source this module lives in
    shared/, so the data files sit one level up in the repo root."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    return Path(__file__).resolve().parent.parent


def is_frozen():
    return getattr(sys, "frozen", False)
