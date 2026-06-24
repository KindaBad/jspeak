import appconfig


def test_validate_fills_defaults_on_empty():
    cfg = appconfig.validate({})
    assert cfg["mode"] == "quick"
    assert cfg["activation"] == "hold"
    assert cfg["hotkey"]["modifiers"] == ["ctrl", "shift"]
    assert cfg["cleanup_enabled"] is True
    assert cfg["voice_commands"]["enabled"] is True
    assert cfg["overlay"]["color"] == "#a855f7"
    assert cfg["cancel_on_esc"] is True
    assert cfg["input_device"] == ""


def test_validate_accepts_max_mode():
    assert appconfig.validate({"mode": "max"})["mode"] == "max"


def test_validate_input_device_and_cancel_flag():
    cfg = appconfig.validate({"input_device": "  USB Mic  ",
                              "cancel_on_esc": False})
    assert cfg["input_device"] == "USB Mic"        # stripped
    assert cfg["cancel_on_esc"] is False
    # non-string device coerced to default
    assert appconfig.validate({"input_device": 123})["input_device"] == ""


def test_validate_clamps_numeric_ranges():
    cfg = appconfig.validate({"key_delay_ms": 99999, "min_hold_ms": -5,
                              "max_tokens": 10, "sample_rate": 1})
    assert cfg["key_delay_ms"] == 1000
    assert cfg["min_hold_ms"] == 0
    assert cfg["max_tokens"] == 256
    assert cfg["sample_rate"] == 8000


def test_validate_fixes_bad_mode_and_activation():
    cfg = appconfig.validate({"mode": "ludicrous", "activation": "sideways"})
    assert cfg["mode"] == "quick"
    assert cfg["activation"] == "hold"


def test_validate_coerces_dictionary_types():
    cfg = appconfig.validate({"dictionary": {"terms": ["a", 5, "b"],
                                             "replacements": {"x": 1}}})
    assert cfg["dictionary"]["terms"] == ["a", "b"]
    assert cfg["dictionary"]["replacements"] == {"x": "1"}


def test_validate_clamps_overlay_alpha():
    assert appconfig.validate({"overlay": {"max_alpha": 5}})["overlay"]["max_alpha"] == 1.0
    assert appconfig.validate({"overlay": {"max_alpha": -1}})["overlay"]["max_alpha"] == 0.0


def test_validate_preserves_unknown_keys():
    cfg = appconfig.validate({"some_future_flag": 42})
    assert cfg["some_future_flag"] == 42


def test_validate_custom_instructions_defaults_and_coerces():
    assert appconfig.validate({})["custom_instructions"] == ""
    assert appconfig.validate(
        {"custom_instructions": "  British spelling  "}
    )["custom_instructions"] == "British spelling"
    # non-string is coerced to empty, and the value is length-capped
    assert appconfig.validate({"custom_instructions": 123})["custom_instructions"] == ""
    long = "x" * 5000
    assert len(appconfig.validate({"custom_instructions": long})["custom_instructions"]) == 2000
