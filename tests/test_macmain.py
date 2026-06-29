"""Tests for the macOS text-injection path (macmain).

macmain lazy-imports pynput/sounddevice inside the daemon/recorder, so the module
imports cleanly on any platform and its typing logic can be exercised headless.
The Cmd+V clipboard branch needs pynput, so it is guarded.
"""
import importlib.util
import sys

import pytest

import macmain

HAS_PYNPUT = importlib.util.find_spec("pynput") is not None


class _FakeKbd:
    """Records what the per-letter path types and any Cmd+V clipboard paste."""

    def __init__(self):
        self.typed = []
        self.pressed_ctx = None
        self.events = []

    def type(self, ch):
        self.typed.append(ch)

    def press(self, key):
        self.events.append(("press", key))

    def release(self, key):
        self.events.append(("release", key))

    def pressed(self, key):
        kbd = self

        class _Ctx:
            def __enter__(self):
                kbd.pressed_ctx = key
                return kbd

            def __exit__(self, *exc):
                return False

        return _Ctx()


# --- platform-independent: per-letter typing --------------------------------

def test_per_letter_types_every_char(monkeypatch):
    monkeypatch.setattr(macmain.time, "sleep", lambda *_: None)
    kbd = _FakeKbd()
    macmain.type_text("héllo", 0, "auto", kbd)
    assert "".join(kbd.typed) == "héllo"


def test_per_letter_respects_key_delay(monkeypatch):
    slept = []
    monkeypatch.setattr(macmain.time, "sleep", lambda d: slept.append(d))
    macmain.type_text("ab", 10, "auto", _FakeKbd())
    assert slept == [0.01, 0.01]                 # 10ms per character


def test_empty_text_is_a_noop(monkeypatch):
    monkeypatch.setattr(macmain.time, "sleep", lambda *_: None)
    kbd = _FakeKbd()
    macmain.type_text("", 0, "auto", kbd)
    assert kbd.typed == []


# --- clipboard branch (Cmd+V) -----------------------------------------------

@pytest.mark.skipif(not HAS_PYNPUT, reason="clipboard path needs pynput.Key")
def test_clipboard_method_sets_clipboard_and_pastes(monkeypatch):
    calls = {}
    monkeypatch.setattr(macmain, "_clip_get", lambda: None)
    monkeypatch.setattr(macmain, "_clip_set",
                        lambda t: calls.__setitem__("text", t))
    monkeypatch.setattr(macmain.time, "sleep", lambda *_: None)
    kbd = _FakeKbd()

    macmain.type_text("paste me", 0, "clipboard", kbd)

    assert calls.get("text") == "paste me"       # text put on the clipboard
    assert ("press", "v") in kbd.events          # Cmd+V issued
    assert ("release", "v") in kbd.events
    assert kbd.pressed_ctx is not None            # Cmd held during the paste


@pytest.mark.skipif(not HAS_PYNPUT, reason="clipboard path needs pynput.Key")
def test_clipboard_method_restores_previous_clipboard(monkeypatch):
    restored = {}
    monkeypatch.setattr(macmain, "_clip_get", lambda: "old contents")
    monkeypatch.setattr(macmain, "_clip_set",
                        lambda t: restored.setdefault("seq", []).append(t))
    monkeypatch.setattr(macmain.time, "sleep", lambda *_: None)

    macmain.type_text("new", 0, "clipboard", _FakeKbd())

    # set to the dictation, then restored to what was there before
    assert restored["seq"] == ["new", "old contents"]
