"""Microphone discovery and selection, shared by the settings UIs and the
recording daemons.

The user picks a microphone by *name* (stable across reboots, unlike the backend
index). The daemons resolve that name to whatever the active backend needs at
capture time - a ``sounddevice`` index on macOS/Windows, an ALSA PCM string for
``arecord`` on Linux. If the saved device is missing or discovery fails we fall
back to the system default, so an unplugged headset never breaks dictation.
"""
import shutil
import subprocess
import sys

IS_LINUX = sys.platform not in ("win32", "darwin")

# Empty string is the sentinel for "system default" everywhere it's stored.
DEFAULT_DEVICE = ""


def list_inputs():
    """Human-readable input-device names (without the default entry). Best-effort
    and never raises; returns ``[]`` when discovery is unavailable."""
    return _list_linux() if IS_LINUX else _list_sounddevice()


def _list_sounddevice():
    try:
        import sounddevice as sd
    except Exception:
        return []
    seen, out = set(), []
    try:
        for d in sd.query_devices():
            if d.get("max_input_channels", 0) > 0:
                name = (d.get("name") or "").strip()
                if name and name not in seen:
                    seen.add(name)
                    out.append(name)
    except Exception:
        return []
    return out


def _list_linux():
    """Top-level PCM names from ``arecord -L`` (skip the low-level hw entries
    and their indented descriptions)."""
    if not shutil.which("arecord"):
        return []
    try:
        out = subprocess.run(["arecord", "-L"], capture_output=True,
                             text=True, timeout=3).stdout
    except Exception:
        return []
    names = []
    for line in out.splitlines():
        # Device names are flush-left; their descriptions are indented.
        if not line or line[:1].isspace():
            continue
        name = line.strip()
        if not name or name == "null":
            continue
        names.append(name)
    return names


def sounddevice_index(name):
    """Resolve a saved device name to a ``sounddevice`` input index, or ``None``
    for the system default / when the device can't be found."""
    if not name:
        return None
    try:
        import sounddevice as sd
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) > 0 and d.get("name") == name:
                return i
    except Exception:
        pass
    return None
