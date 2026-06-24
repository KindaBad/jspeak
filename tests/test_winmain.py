"""Tests for the Windows text-injection path (winmain).

The event-generation logic is platform-independent and runs everywhere. The
struct byte-sizes and the live SendInput call are Win64-specific, so those are
guarded - but they are the ones that actually catch the 1.4.0-beta regression
where pynput's SendInput truncated the INPUT* on Win64 and crashed with an
"access violation writing 0x0".
"""
import ctypes
import sys

import pytest

import winmain

WINDOWS = sys.platform == "win32"
SIXTYFOUR = ctypes.sizeof(ctypes.c_void_p) == 8


# --- platform-independent: event generation ---------------------------------

def test_ascii_char_is_one_unicode_keypress():
    evs = winmain._unicode_events("A")
    assert len(evs) == 2                                    # down + up
    assert evs[0].u.ki.wScan == ord("A")
    assert evs[0].u.ki.dwFlags == winmain._KEYEVENTF_UNICODE
    assert evs[1].u.ki.dwFlags == (winmain._KEYEVENTF_UNICODE
                                   | winmain._KEYEVENTF_KEYUP)
    # Unicode injection must not carry a virtual-key code.
    assert evs[0].u.ki.wVk == 0


def test_astral_char_emits_utf16_surrogate_pair():
    evs = winmain._unicode_events("\U0001F600")             # U+1F600 😀
    assert len(evs) == 4                                    # 2 units x down/up
    scans = [e.u.ki.wScan for e in evs]
    assert scans == [0xD83D, 0xD83D, 0xDE00, 0xDE00]        # high, high, low, low


def test_key_event_sets_requested_vk_and_flags():
    e = winmain._key_event(vk=winmain._VK_RETURN,
                           flags=winmain._KEYEVENTF_KEYUP)
    assert e.type == winmain._INPUT_KEYBOARD
    assert e.u.ki.wVk == winmain._VK_RETURN
    assert e.u.ki.dwFlags == winmain._KEYEVENTF_KEYUP


def test_empty_send_is_a_noop():
    assert winmain._send([]) == 0                           # never touches windll


def test_type_text_falls_back_to_clipboard_when_injection_fails(monkeypatch):
    # If SendInput inserts nothing (the truncated-pointer crash class, a secure
    # desktop, etc.) the dictation must still land via a clipboard paste rather
    # than being silently dropped.
    pasted = {}
    monkeypatch.setattr(winmain, "_send", lambda events: 0)        # inserts nothing
    monkeypatch.setattr(winmain, "_clip_get", lambda: None)
    monkeypatch.setattr(winmain, "_clip_set",
                        lambda t: pasted.__setitem__("text", t))
    monkeypatch.setattr(winmain, "_paste",
                        lambda: pasted.__setitem__("pasted", True))
    monkeypatch.setattr(winmain.time, "sleep", lambda *_: None)

    winmain.type_text("hello", 0, "auto")

    assert pasted.get("pasted") is True
    assert pasted.get("text") == "hello"


def test_clipboard_method_uses_paste_path(monkeypatch):
    calls = {}
    monkeypatch.setattr(winmain, "_clip_get", lambda: None)
    monkeypatch.setattr(winmain, "_clip_set",
                        lambda t: calls.__setitem__("text", t))
    monkeypatch.setattr(winmain, "_paste",
                        lambda: calls.__setitem__("pasted", True))
    monkeypatch.setattr(winmain.time, "sleep", lambda *_: None)

    winmain.type_text("paste me", 0, "clipboard")

    assert calls.get("pasted") is True
    assert calls.get("text") == "paste me"


# --- Win64-specific: struct layout & live injection -------------------------

@pytest.mark.skipif(not (WINDOWS and SIXTYFOUR), reason="Win64-only ABI sizes")
def test_input_struct_sizes_match_win64_abi():
    # Wrong sizes here mean SendInput gets a bad cbSize and silently fails.
    assert ctypes.sizeof(winmain._KEYBDINPUT) == 24
    assert ctypes.sizeof(winmain._INPUT) == 40


@pytest.mark.skipif(not WINDOWS, reason="exercises the real Win32 SendInput")
def test_sendinput_inserts_every_event():
    # The regression: an unset argtypes truncated the INPUT* on Win64 so
    # SendInput crashed / inserted 0. With argtypes set it inserts them all.
    sent = winmain._send(winmain._unicode_events("A"))
    assert sent == 2


@pytest.mark.skipif(not WINDOWS, reason="Win32 library handles")
def test_send_input_handle_is_isolated_from_pynput_global():
    # The 1.4.1 crash came back because we bound SendInput on the *shared*
    # ctypes.windll.user32 that pynput also mutates. Our handle must be a private
    # WinDLL instance so pynput can't reset the signatures we rely on.
    assert winmain._u32() is not ctypes.windll.user32
    assert winmain._u32().SendInput.argtypes is not None


@pytest.mark.skipif(not (WINDOWS and SIXTYFOUR), reason="Win64 handle widths")
def test_global_alloc_lock_return_pointer_sized_handles():
    # Missing restype here truncated 64-bit handles to 32 bits -> GlobalLock
    # returned NULL -> memmove(NULL) -> "access violation writing 0x0".
    k = winmain._k32()
    assert k.GlobalAlloc.restype is ctypes.c_void_p
    assert k.GlobalLock.restype is ctypes.c_void_p
