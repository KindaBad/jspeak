import hotkeys


def test_parse_spec_defaults_to_ctrl_shift_hold():
    spec = hotkeys.parse_spec({})
    assert spec["mods"] == frozenset({"ctrl", "shift"})
    assert spec["key"] is None
    assert spec["toggle"] is False


def test_parse_spec_custom_modifiers_and_toggle():
    spec = hotkeys.parse_spec(
        {"hotkey": {"modifiers": ["alt", "super"], "key": "space"},
         "activation": "toggle"})
    assert spec["mods"] == frozenset({"alt", "super"})
    assert spec["key"] == "space"
    assert spec["toggle"] is True


def test_parse_spec_drops_invalid_and_falls_back():
    spec = hotkeys.parse_spec({"hotkey": {"modifiers": ["meta", "ctrl"], "key": "!"}})
    assert spec["mods"] == frozenset({"ctrl"})   # 'meta' dropped
    assert spec["key"] is None                   # '!' not a known key


def test_parse_spec_empty_modifiers_with_key_is_allowed():
    spec = hotkeys.parse_spec({"hotkey": {"modifiers": [], "key": "f9"}})
    assert spec["mods"] == frozenset()
    assert spec["key"] == "f9"


def test_describe():
    assert hotkeys.describe(hotkeys.parse_spec({})) == "Ctrl+Shift"
    spec = hotkeys.parse_spec({"hotkey": {"modifiers": ["alt"], "key": "space"},
                               "activation": "toggle"})
    assert hotkeys.describe(spec) == "Alt+Space (toggle)"


def test_chord_tracker_satisfied():
    spec = hotkeys.parse_spec({"hotkey": {"modifiers": ["ctrl", "shift"]}})
    t = hotkeys.ChordTracker(spec)
    assert t.set("ctrl", True) is True
    assert not t.satisfied
    t.set("shift", True)
    assert t.satisfied
    t.set("shift", False)
    assert not t.satisfied


def test_chord_tracker_ignores_irrelevant_keys():
    spec = hotkeys.parse_spec({"hotkey": {"modifiers": ["ctrl"], "key": "z"}})
    t = hotkeys.ChordTracker(spec)
    assert t.set("alt", True) is False     # not part of the chord
    t.set("ctrl", True)
    t.set("z", True)
    assert t.satisfied


def test_evdev_code_to_name_maps_both_sides():
    assert hotkeys.EVDEV_CODE_TO_NAME[29] == "ctrl"
    assert hotkeys.EVDEV_CODE_TO_NAME[97] == "ctrl"
    assert hotkeys.EVDEV_CODE_TO_NAME[57] == "space"
    assert hotkeys.EVDEV_CODE_TO_NAME[44] == "z"
