"""Shared config load/save (used by the Windows daemon and settings; the Linux
daemon has its own with legacy migration)."""
import json
import os
import shutil

import hotkeys
import jpaths
from groq_core import MODES

CONFIG_PATH = jpaths.config_path()
DEFAULT_CONFIG = jpaths.bundle_dir() / "config.default.json"


def ensure_user_config():
    if CONFIG_PATH.exists():
        return
    try:
        shutil.copy(DEFAULT_CONFIG, CONFIG_PATH)
    except Exception:
        CONFIG_PATH.write_text(json.dumps({"groq_api_key": "", "mode": "quick"},
                                          indent=2))


def _clamp(value, lo, hi, default):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def validate(cfg):
    """Coerce a loaded config into a safe shape: known modes, sane numeric
    ranges, a valid hotkey/activation, and well-formed nested dicts. Never
    raises; unknown keys are preserved untouched."""
    if not isinstance(cfg, dict):
        cfg = {}
    cfg.setdefault("groq_api_key", "")
    cfg["mode"] = cfg.get("mode", "quick") if cfg.get("mode", "quick") in MODES else "quick"
    cfg["key_delay_ms"] = _clamp(cfg.get("key_delay_ms", 10), 0, 1000, 10)
    cfg["min_hold_ms"] = _clamp(cfg.get("min_hold_ms", 300), 0, 5000, 300)
    cfg["min_record_ms"] = _clamp(cfg.get("min_record_ms", 350), 0, 10000, 350)
    cfg["sample_rate"] = _clamp(cfg.get("sample_rate", 16000), 8000, 48000, 16000)
    cfg["max_tokens"] = _clamp(cfg.get("max_tokens", 4096), 256, 32768, 4096)
    for flag, dflt in (("uncensored", True), ("append_space", True),
                       ("cleanup_enabled", True), ("trim_silence", True),
                       ("cancel_on_esc", True), ("always_record", False)):
        cfg[flag] = bool(cfg.get(flag, dflt))
    if cfg.get("activation") not in ("hold", "toggle"):
        cfg["activation"] = "hold"

    dev = cfg.get("input_device", "")
    cfg["input_device"] = dev.strip() if isinstance(dev, str) else ""

    ci = cfg.get("custom_instructions", "")
    cfg["custom_instructions"] = ci.strip()[:2000] if isinstance(ci, str) else ""

    # hotkey: keep only recognised modifiers / key; fall back to Ctrl+Shift
    spec = hotkeys.parse_spec(cfg)
    cfg["hotkey"] = {"modifiers": sorted(spec["mods"]), "key": spec["key"]}

    ov = cfg.get("overlay") if isinstance(cfg.get("overlay"), dict) else {}
    ov.setdefault("enabled", True)
    ov.setdefault("color", "#a855f7")
    ov["max_alpha"] = max(0.0, min(1.0, float(ov.get("max_alpha", 0.55) or 0.55)))
    ov["thickness_px"] = _clamp(ov.get("thickness_px", 110), 10, 600, 110)
    ov.setdefault("reduce_motion", True)
    cfg["overlay"] = ov

    d = cfg.get("dictionary") if isinstance(cfg.get("dictionary"), dict) else {}
    d["terms"] = [t for t in (d.get("terms") or []) if isinstance(t, str)]
    reps = d.get("replacements") if isinstance(d.get("replacements"), dict) else {}
    d["replacements"] = {str(k): str(v) for k, v in reps.items()}
    cfg["dictionary"] = d

    vc = cfg.get("voice_commands") if isinstance(cfg.get("voice_commands"), dict) else {}
    vc["enabled"] = bool(vc.get("enabled", True))
    custom = vc.get("custom") if isinstance(vc.get("custom"), dict) else {}
    vc["custom"] = {str(k): str(v) for k, v in custom.items()}
    cfg["voice_commands"] = vc
    return cfg


def load_config():
    ensure_user_config()
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    return validate(cfg)


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
