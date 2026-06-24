"""Rolling history of recent dictations so a result is never lost to a focus
slip or a wrong active window.

Stored as JSON in the config dir and capped to the most recent ``MAX`` entries.
The daemons append after a successful type; the tray's "Copy last dictation"
action and the ``--copy-last`` CLI role read entries back. Every operation is
best-effort and never raises - losing history must never break dictation.
"""
import json
import os
import tempfile
import threading
import time

import jpaths

MAX = 50
_lock = threading.Lock()


def _path():
    return jpaths.config_dir() / "history.json"


def _load_raw():
    """Load the stored entries (oldest first), tolerating a missing or corrupt
    file by returning an empty list."""
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [e for e in data
            if isinstance(e, dict) and isinstance(e.get("text"), str)]


def add(text):
    """Append a dictation, keeping only the newest ``MAX``. Blank text is
    ignored. Writes atomically so a crash can't corrupt the file."""
    if not text or not text.strip():
        return
    entry = {"text": text, "ts": time.time()}
    with _lock:
        items = _load_raw()
        items.append(entry)
        items = items[-MAX:]
        path = _path()
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                                       prefix=".history-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            tmp = None
        except OSError:
            pass
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass


def recent(n=10):
    """The newest ``n`` entries, newest first: ``[{"text", "ts"}, ...]``."""
    items = _load_raw()
    items.reverse()
    return items[:max(0, n)]


def latest():
    """The most recent dictation text, or ``None`` if the history is empty."""
    items = _load_raw()
    return items[-1]["text"] if items else None


def clear():
    """Forget all stored dictations."""
    with _lock:
        try:
            os.unlink(_path())
        except OSError:
            pass
