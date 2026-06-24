"""Shared hotkey configuration: normalize a config hotkey into a spec and track
chord state. Platform-agnostic (no evdev/pynput imports here) so it can be
unit-tested anywhere; the Linux and Windows daemons translate their native key
events into the canonical names below and feed this tracker.

Canonical names:
- modifiers: "ctrl", "shift", "alt", "super"
- optional regular key: "space", "a".."z", "0".."9", "f1".."f12"
"""
MODIFIERS = ("ctrl", "shift", "alt", "super")

REGULAR_KEYS = (
    ("space",) + tuple("abcdefghijklmnopqrstuvwxyz") + tuple("0123456789")
    + tuple(f"f{i}" for i in range(1, 13))
)

# Linux evdev codes (linux/input-event-codes.h). Modifiers list both sides.
EVDEV_MODIFIERS = {
    "ctrl": (29, 97), "shift": (42, 54), "alt": (56, 100), "super": (125, 126),
}
EVDEV_REGULAR = {
    "space": 57,
    "a": 30, "b": 48, "c": 46, "d": 32, "e": 18, "f": 33, "g": 34, "h": 35,
    "i": 23, "j": 36, "k": 37, "l": 38, "m": 50, "n": 49, "o": 24, "p": 25,
    "q": 16, "r": 19, "s": 31, "t": 20, "u": 22, "v": 47, "w": 17, "x": 45,
    "y": 21, "z": 44,
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10,
    "0": 11,
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64, "f7": 65,
    "f8": 66, "f9": 67, "f10": 68, "f11": 87, "f12": 88,
}

# evdev code -> canonical name (modifiers map both left/right to the same name)
EVDEV_CODE_TO_NAME = {}
for _name, _codes in EVDEV_MODIFIERS.items():
    for _c in _codes:
        EVDEV_CODE_TO_NAME[_c] = _name
for _name, _c in EVDEV_REGULAR.items():
    EVDEV_CODE_TO_NAME[_c] = _name

DEFAULT_SPEC = {"mods": ("ctrl", "shift"), "key": None, "toggle": False}


def parse_spec(cfg):
    """Read cfg['hotkey'] + cfg['activation'] into a normalized spec dict:
        {"mods": frozenset(...), "key": str|None, "toggle": bool}
    Invalid entries are dropped; an empty result falls back to Ctrl+Shift hold."""
    hk = (cfg or {}).get("hotkey", {}) or {}
    raw_mods = hk.get("modifiers", DEFAULT_SPEC["mods"]) or []
    mods = frozenset(m.lower() for m in raw_mods if m and m.lower() in MODIFIERS)
    key = hk.get("key")
    key = key.lower() if isinstance(key, str) and key.lower() in REGULAR_KEYS else None
    if not mods and not key:
        mods = frozenset(DEFAULT_SPEC["mods"])
    toggle = str((cfg or {}).get("activation", "hold")).lower() == "toggle"
    return {"mods": mods, "key": key, "toggle": toggle}


def describe(spec):
    """Human-readable chord, e.g. 'Ctrl+Shift' or 'Alt+Space (toggle)'."""
    order = {"ctrl": 0, "shift": 1, "alt": 2, "super": 3}
    parts = sorted(spec["mods"], key=lambda m: order.get(m, 9))
    labels = {"ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "super": "Super"}
    out = [labels[m] for m in parts]
    if spec["key"]:
        out.append(spec["key"].upper() if len(spec["key"]) == 1 else spec["key"].capitalize())
    text = "+".join(out) or "Ctrl+Shift"
    return text + (" (toggle)" if spec["toggle"] else "")


class ChordTracker:
    """Tracks which of the spec's keys are currently down and whether the full
    chord is satisfied. The daemon also passes whether any *non-chord* key is
    down, used by hold-mode to abort accidental Ctrl+Shift+<key> shortcuts."""

    def __init__(self, spec):
        self.spec = spec
        self._down = set()           # canonical names currently held
        self.relevant = set(spec["mods"]) | ({spec["key"]} if spec["key"] else set())

    def set(self, name, down):
        """Update a canonical key's state. Returns True if it is part of the
        chord (so the caller knows whether this was a 'real' other key)."""
        if name in self.relevant:
            if down:
                self._down.add(name)
            else:
                self._down.discard(name)
            return True
        return False

    @property
    def satisfied(self):
        return self.relevant.issubset(self._down) and bool(self.relevant)

    def reset(self):
        self._down.clear()
