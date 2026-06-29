#!/usr/bin/env python3
"""JSpeak settings GUI - edit config.json and apply (restart the service).

Run with the bundled venv python so GTK is available:
    ~/jspeak/.venv/bin/python ~/jspeak/app.py --settings
or via the jspeak-settings launcher / app menu entry.
"""
import json
import os
import signal
import subprocess
import sys
import threading
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

import audiodevices
import groq_core
import hotkeys
import jpaths
import version

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = jpaths.config_path()
DEFAULT_CONFIG = jpaths.bundle_dir() / "config.default.json"

MODE_DESC = {
    "quick": "Turbo Whisper + Llama-3.1-8B. Fastest & cheapest.",
    "smart": "Turbo Whisper + Llama-3.3-70B. Better grammar & slang.",
    "accurate": "Full Whisper + 70B. Best for noisy / hard audio.",
    "max": "Full Whisper + GPT-OSS-120B. Best quality, slowest.",
}

LANGUAGES = [
    ("auto", "Auto-detect"), ("en", "English"), ("ar", "Arabic"),
    ("he", "Hebrew"), ("es", "Spanish"), ("fr", "French"), ("de", "German"),
    ("it", "Italian"), ("pt", "Portuguese"), ("ru", "Russian"),
    ("tr", "Turkish"), ("hi", "Hindi"), ("ur", "Urdu"), ("fa", "Persian"),
    ("zh", "Chinese"), ("ja", "Japanese"), ("ko", "Korean"),
]

TYPE_METHODS = [
    ("auto", "Type letter-by-letter (recommended)"),
    ("wtype", "Type - all languages / RTL"),
    ("ydotool", "Type - Latin only (fast)"),
    ("clipboard", "Paste all at once (instant)"),
]

CSS = b"""
.js-root { background-color: #0e0c15; color: #e8e4f2; }

.js-header { background-color: #0e0c15; border-bottom: 1px solid #221d31; }
.js-title { font-size: 23px; font-weight: 800; color: #ffffff; }
.js-sub { color: #908aa6; font-size: 12px; }

/* segmented tab switcher */
.js-tabs { background-color: #161320; border-radius: 13px; padding: 4px; }
.js-tabs button { background: none; background-image: none; color: #948dab;
                  border: none; box-shadow: none; outline: none;
                  border-radius: 9px; padding: 7px 18px; font-weight: 600;
                  font-size: 13px; }
.js-tabs button:hover { color: #f3eefc; background-color: #221d31; }
.js-tabs button:checked { color: #ffffff; background-color: #a855f7;
                          box-shadow: 0 3px 12px rgba(168,85,247,0.40); }
.js-tabs button:checked:hover { background-color: #b76dff; }

.js-section { font-size: 11px; font-weight: 800; color: #b48cff;
              letter-spacing: 1.5px; margin-top: 4px; margin-bottom: 2px; }
.js-card { background-color: #181523; border-radius: 16px;
           padding: 17px; border: 1px solid #262134; }
.js-hint { color: #847c9e; font-size: 11px; }
.js-status { color: #7ee0a8; font-size: 12px; }

.js-footer { background-color: #0e0c15; border-top: 1px solid #221d31; }
.js-save { background-image: none; background-color: #a855f7; color: #ffffff;
           font-weight: 700; border-radius: 11px; padding: 9px 22px;
           border: none; box-shadow: 0 4px 16px rgba(168,85,247,0.38); }
.js-save:hover { background-color: #b76dff; }
.js-save:active { background-color: #9b46e8; }
.js-ghost { background: transparent; color: #c4bbdc; border-radius: 11px;
            padding: 9px 16px; border: 1px solid #342e48; }
.js-ghost:hover { border-color: #5a5078; color: #ffffff;
                  background-color: #181523; }

textview, textview text { background-color: #120f1c; color: #e8e4f2;
                          border-radius: 9px; padding: 2px; }
entry, spinbutton { background-color: #120f1c; color: #e8e4f2;
                    border-radius: 9px; border: 1px solid #2a2438;
                    padding: 4px 6px; }
entry:focus, spinbutton:focus { border-color: #a855f7; }
switch { background-color: #2a2438; border-radius: 14px; }
switch:checked { background-color: #a855f7; }
switch:checked:hover { background-color: #b76dff; }
scale highlight, scale trough highlight { background-color: #a855f7; }
scale slider { background-color: #efeaf8; }
combobox box, combobox button { background-color: #120f1c; color: #e8e4f2;
                                border-radius: 9px; }
checkbutton check:checked { background-color: #a855f7; border-color: #a855f7; }
"""


def load():
    if not CONFIG_PATH.exists():
        try:
            import shutil
            shutil.copy(DEFAULT_CONFIG, CONFIG_PATH)
        except Exception:
            return {"groq_api_key": "", "mode": "quick"}
    with open(CONFIG_PATH) as f:
        return json.load(f)


class Settings(Gtk.Window):
    def __init__(self):
        super().__init__(title="JSpeak Settings")
        self.cfg = load()
        self.set_default_size(580, 720)
        self.set_resizable(True)
        self.get_style_context().add_class("js-root")

        prov = Gtk.CssProvider()
        prov.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(outer)

        # ---- header (title + segmented tabs) -------------------------------
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        header.get_style_context().add_class("js-header")
        header.set_margin_top(18)
        header.set_margin_bottom(14)
        header.set_margin_start(22)
        header.set_margin_end(22)
        title = Gtk.Label(xalign=0)
        title.set_markup("JSpeak")
        title.get_style_context().add_class("js-title")
        header.pack_start(title, False, False, 0)
        sub = Gtk.Label(
            label=f"Push-to-talk dictation · hold Ctrl+Shift · v{version.__version__}",
            xalign=0)
        sub.get_style_context().add_class("js-sub")
        header.pack_start(sub, False, False, 0)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(
            Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_transition_duration(180)
        switcher = Gtk.StackSwitcher()
        switcher.set_stack(self.stack)
        switcher.set_halign(Gtk.Align.CENTER)
        switcher.get_style_context().add_class("js-tabs")
        sw_wrap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        sw_wrap.set_halign(Gtk.Align.CENTER)
        sw_wrap.set_margin_top(14)
        sw_wrap.pack_start(switcher, False, False, 0)
        header.pack_start(sw_wrap, False, False, 0)
        outer.pack_start(header, False, False, 0)

        outer.pack_start(self.stack, True, True, 0)

        # ---- General -------------------------------------------------------
        page = self._page("general", "General")

        card = self._card(page, "MODEL")
        self.mode = Gtk.ComboBoxText()
        for k in ("quick", "smart", "accurate", "max"):
            self.mode.append(k, k.capitalize())
        self.mode.set_active_id(self.cfg.get("mode", "quick"))
        self.mode.connect("changed", self._mode_changed)
        self._row(card, "Speed / quality", self.mode)
        self.mode_desc = Gtk.Label(xalign=0)
        self.mode_desc.get_style_context().add_class("js-hint")
        card.pack_start(self.mode_desc, False, False, 0)
        self._mode_changed(self.mode)

        card = self._card(page, "LANGUAGE")
        self.language = Gtk.ComboBoxText()
        for code, name in LANGUAGES:
            self.language.append(code, name)
        self.language.set_active_id(self.cfg.get("language", "auto"))
        self._row(card, "Spoken language", self.language)
        self._hint(card, "Auto-detect handles most cases. Forcing a language "
                         "improves accuracy on short clips.")
        self.type_method = Gtk.ComboBoxText()
        for code, name in TYPE_METHODS:
            self.type_method.append(code, name)
        self.type_method.set_active_id(self.cfg.get("type_method", "auto"))
        self._row(card, "Text insertion", self.type_method)
        self._hint(card, "Letter-by-letter animates the text in (uses the delay "
                         "in Dictation). Paste all at once inserts everything "
                         "instantly via the clipboard - restored afterwards.")

        card = self._card(page, "GROQ API KEY")
        self.api = Gtk.Entry()
        self.api.set_text(self.cfg.get("groq_api_key", ""))
        self.api.set_visibility(False)
        self.api.set_hexpand(True)
        show = Gtk.ToggleButton(label="Show")
        show.get_style_context().add_class("js-ghost")
        show.connect("toggled", lambda b: self.api.set_visibility(b.get_active()))
        test = Gtk.Button(label="Test")
        test.get_style_context().add_class("js-ghost")
        test.connect("clicked", self._test_key)
        row = Gtk.Box(spacing=8)
        row.pack_start(self.api, True, True, 0)
        row.pack_start(show, False, False, 0)
        row.pack_start(test, False, False, 0)
        card.pack_start(row, False, False, 0)
        self._hint(card, "Get a free key at console.groq.com/keys - it stays on "
                         "your machine and is never shared. Use Test to check it.")

        # ---- Dictation -----------------------------------------------------
        page = self._page("dictation", "Dictation")

        card = self._card(page, "TYPING")
        self.key_delay = self._spin(0, 200, 1, self.cfg.get("key_delay_ms", 10))
        self.key_delay_row = self._row(card, "Delay between letters (ms)",
                                       self.key_delay)
        self._hint(card, "Lower = faster typing. 10 is very fast; 0-3 is near-instant. "
                         "Ignored when pasting all at once.")
        self.type_method.connect("changed", self._sync_delay)
        self._sync_delay(self.type_method)
        self.append_space = Gtk.Switch()
        self.append_space.set_active(self.cfg.get("append_space", True))
        self.append_space.set_halign(Gtk.Align.START)
        self._row(card, "Add trailing space", self.append_space)
        self.uncensored = Gtk.Switch()
        self.uncensored.set_active(self.cfg.get("uncensored", True))
        self.uncensored.set_halign(Gtk.Align.START)
        self._row(card, "Uncensored (literature mode)", self.uncensored)
        self._hint(card, "Transcribes profanity verbatim and never softens or "
                         "masks it.")

        card = self._card(page, "MICROPHONE")
        self.input_device = Gtk.ComboBoxText()
        self.input_device.append("", "System default")
        cur_dev = self.cfg.get("input_device", "") or ""
        dev_names = audiodevices.list_inputs()
        for name in dev_names:
            self.input_device.append(name, name)
        if cur_dev and cur_dev not in dev_names:
            self.input_device.append(cur_dev, f"{cur_dev} (not connected)")
        self.input_device.set_active_id(cur_dev)
        self._row(card, "Input device", self.input_device)
        self._hint(card, "Which microphone to record from. System default "
                         "follows the ALSA default device.")

        card = self._card(page, "CLEANUP")
        self.cleanup_enabled = Gtk.Switch()
        self.cleanup_enabled.set_active(self.cfg.get("cleanup_enabled", True))
        self.cleanup_enabled.set_halign(Gtk.Align.START)
        self._row(card, "LLM cleanup", self.cleanup_enabled)
        self._hint(card, "On: an LLM removes fillers and fixes grammar. Off: "
                         "fastest - type the raw transcript with no LLM pass.")
        self.voice_commands = Gtk.Switch()
        self.voice_commands.set_active(
            self.cfg.get("voice_commands", {}).get("enabled", True))
        self.voice_commands.set_halign(Gtk.Align.START)
        self._row(card, "Voice formatting commands", self.voice_commands)
        self._hint(card, "Say 'new line', 'new paragraph', 'new bullet', or "
                         "'scratch that' (deletes the previous sentence).")
        self.trim_silence = Gtk.Switch()
        self.trim_silence.set_active(self.cfg.get("trim_silence", True))
        self.trim_silence.set_halign(Gtk.Align.START)
        self._row(card, "Trim silence before upload", self.trim_silence)
        self.always_record = Gtk.Switch()
        self.always_record.set_active(self.cfg.get("always_record", False))
        self.always_record.set_halign(Gtk.Align.START)
        self._row(card, "Always-on recording (lower latency)", self.always_record)
        self._hint(card, "Keep the mic capturing continuously and only upload the "
                         "held segment. Removes the start-up lag on each press; "
                         "holds the mic open the whole time. Restart to apply.")
        self.max_tokens = self._spin(256, 16384, 256, self.cfg.get("max_tokens", 4096))
        self._row(card, "Max output length (tokens)", self.max_tokens)
        self._hint(card, "Custom cleanup instructions - your own style/formatting "
                         "rules (e.g. 'British spelling', 'bullet lists', 'keep it "
                         "lowercase'). Applied only when LLM cleanup is on.")
        self.custom_instructions, sw = self._textarea(
            self.cfg.get("custom_instructions", "") or "", 70)
        card.pack_start(sw, False, False, 0)

        card = self._card(page, "DICTIONARY")
        d = self.cfg.get("dictionary", {})
        self._hint(card, "Terms - names/words it mishears (one per line). Bias "
                         "recognition and keep their spelling.")
        self.terms, sw = self._textarea("\n".join(d.get("terms", []) or []), 90)
        card.pack_start(sw, False, False, 0)
        self._hint(card, "Replacements - one per line as  wrong = right")
        rep = d.get("replacements", {}) or {}
        rep_text = "\n".join(f"{k} = {v}" for k, v in rep.items())
        self.reps, sw = self._textarea(rep_text, 90)
        card.pack_start(sw, False, False, 0)

        # ---- Hotkey --------------------------------------------------------
        page = self._page("hotkey", "Hotkey")

        card = self._card(page, "HOTKEY")
        spec = hotkeys.parse_spec(self.cfg)
        self.mod_checks = {}
        mods_row = Gtk.Box(spacing=10)
        modlab = Gtk.Label(label="Modifiers", xalign=0)
        modlab.set_hexpand(True)
        mods_row.pack_start(modlab, True, True, 0)
        for m in hotkeys.MODIFIERS:
            cb = Gtk.CheckButton(label=m.capitalize())
            cb.set_active(m in spec["mods"])
            self.mod_checks[m] = cb
            mods_row.pack_start(cb, False, False, 0)
        card.pack_start(mods_row, False, False, 0)

        self.hk_key = Gtk.ComboBoxText()
        self.hk_key.append("", "None")
        for k in hotkeys.REGULAR_KEYS:
            self.hk_key.append(k, k.upper() if len(k) == 1 else k.capitalize())
        self.hk_key.set_active_id(spec["key"] or "")
        self._row(card, "Extra key (optional)", self.hk_key)

        self.activation = Gtk.ComboBoxText()
        self.activation.append("hold", "Hold to talk")
        self.activation.append("toggle", "Toggle (press to start / stop)")
        self.activation.set_active_id("toggle" if spec["toggle"] else "hold")
        self._row(card, "Activation", self.activation)
        self._hint(card, "Hold: record while the chord is held. Toggle: press "
                         "once to start, again to stop.")

        self.min_hold = self._spin(50, 2000, 10, self.cfg.get("min_hold_ms", 300))
        self._row(card, "Hold before recording (ms)", self.min_hold)
        self.min_record = self._spin(100, 3000, 10, self.cfg.get("min_record_ms", 350))
        self._row(card, "Discard if shorter than (ms)", self.min_record)
        self.cancel_on_esc = Gtk.Switch()
        self.cancel_on_esc.set_active(self.cfg.get("cancel_on_esc", True))
        self.cancel_on_esc.set_halign(Gtk.Align.START)
        self._row(card, "Esc cancels a recording", self.cancel_on_esc)
        self._hint(card, "Higher hold = fewer accidental triggers from "
                         "modifier+<key> shortcuts. Press Esc mid-recording to "
                         "throw the take away.")

        # ---- Glow ----------------------------------------------------------
        page = self._page("glow", "Glow")

        card = self._card(page, "GLOW OVERLAY")
        ov = self.cfg.get("overlay", {})
        self.ov_enabled = Gtk.Switch()
        self.ov_enabled.set_active(ov.get("enabled", True))
        self.ov_enabled.set_halign(Gtk.Align.START)
        self._row(card, "Enabled", self.ov_enabled)
        self.ov_color = Gtk.ColorButton()
        rgba = Gdk.RGBA()
        rgba.parse(ov.get("color", "#a855f7"))
        self.ov_color.set_rgba(rgba)
        self._row(card, "Color", self.ov_color)
        self.ov_alpha = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0.0, 1.0, 0.05)
        self.ov_alpha.set_value(ov.get("max_alpha", 0.55))
        self.ov_alpha.set_hexpand(True)
        self.ov_alpha.set_digits(2)
        self._row(card, "Intensity", self.ov_alpha)
        self.ov_thick = self._spin(10, 400, 5, ov.get("thickness_px", 110))
        self._row(card, "Thickness (px)", self.ov_thick)
        self.ov_reduce = Gtk.Switch()
        self.ov_reduce.set_active(ov.get("reduce_motion", True))
        self.ov_reduce.set_halign(Gtk.Align.START)
        self._row(card, "Reduce motion (low-end mode)", self.ov_reduce)
        self._hint(card, "On (default): static glow, zero CPU while recording - "
                         "best on weak hardware. Off: gentle breathing pulse.")
        preview = Gtk.Button(label="Preview glow")
        preview.get_style_context().add_class("js-ghost")
        preview.connect("clicked", self._preview)
        card.pack_start(preview, False, False, 4)
        self._hint(card, "Preview pulses the currently running glow. Save & Apply "
                         "first to preview new color/intensity.")

        # ---- Footer --------------------------------------------------------
        footer = Gtk.Box(spacing=10)
        footer.get_style_context().add_class("js-footer")
        footer.set_margin_top(12)
        footer.set_margin_bottom(14)
        footer.set_margin_start(22)
        footer.set_margin_end(22)
        self.status = Gtk.Label(xalign=0)
        self.status.get_style_context().add_class("js-status")
        footer.pack_start(self.status, True, True, 0)
        close = Gtk.Button(label="Close")
        close.get_style_context().add_class("js-ghost")
        close.connect("clicked", lambda *_: self.destroy())
        save = Gtk.Button(label="Save & Apply")
        save.get_style_context().add_class("js-save")
        save.connect("clicked", self._save)
        footer.pack_start(close, False, False, 0)
        footer.pack_start(save, False, False, 0)
        outer.pack_start(footer, False, False, 0)

    # ---- widget helpers ----
    def _page(self, name, title):
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        body.set_margin_top(18)
        body.set_margin_bottom(20)
        body.set_margin_start(22)
        body.set_margin_end(22)
        scroller.add(body)
        self.stack.add_titled(scroller, name, title)
        return body

    def _card(self, parent, label):
        sec = Gtk.Label(label=label, xalign=0)
        sec.get_style_context().add_class("js-section")
        parent.pack_start(sec, False, False, 0)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.get_style_context().add_class("js-card")
        parent.pack_start(card, False, False, 0)
        return card

    def _row(self, card, label, widget):
        row = Gtk.Box(spacing=12)
        lab = Gtk.Label(label=label, xalign=0)
        lab.set_hexpand(True)
        row.pack_start(lab, True, True, 0)
        row.pack_start(widget, False, False, 0)
        card.pack_start(row, False, False, 0)
        return row

    def _hint(self, card, text):
        h = Gtk.Label(label=text, xalign=0, wrap=True)
        h.get_style_context().add_class("js-hint")
        card.pack_start(h, False, False, 0)

    def _spin(self, lo, hi, step, val):
        s = Gtk.SpinButton.new_with_range(lo, hi, step)
        s.set_value(val)
        return s

    def _textarea(self, text, height):
        tv = Gtk.TextView()
        tv.set_wrap_mode(Gtk.WrapMode.WORD)
        tv.get_buffer().set_text(text)
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_min_content_height(height)
        sw.add(tv)
        return tv, sw

    def _mode_changed(self, combo):
        self.mode_desc.set_text(MODE_DESC.get(combo.get_active_id(), ""))

    def _sync_delay(self, combo):
        self.key_delay_row.set_sensitive(combo.get_active_id() != "clipboard")

    def _test_key(self, *_):
        key = self.api.get_text().strip()
        self.status.set_text("Testing key…")

        def work():
            ok, msg = groq_core.validate_key(key)
            GLib.idle_add(self.status.set_text, ("✓ " if ok else "✗ ") + msg)

        threading.Thread(target=work, daemon=True).start()

    # ---- actions ----
    def _rgba_hex(self):
        c = self.ov_color.get_rgba()
        return "#%02x%02x%02x" % (round(c.red * 255), round(c.green * 255),
                                  round(c.blue * 255))

    def _buf_text(self, tv):
        b = tv.get_buffer()
        return b.get_text(b.get_start_iter(), b.get_end_iter(), False)

    def _preview(self, *_):
        try:
            out = subprocess.run(["pgrep", "-f", "--", "--overlay"],
                                 capture_output=True, text=True)
            pids = [int(x) for x in out.stdout.split()]
            if not pids:
                self.status.set_text("Glow not running - Save & Apply first.")
                return
            for p in pids:
                os.kill(p, signal.SIGUSR1)

            def off():
                for p in pids:
                    try:
                        os.kill(p, signal.SIGUSR2)
                    except Exception:
                        pass
                return False
            GLib.timeout_add(1600, off)
        except Exception as e:
            self.status.set_text(f"Preview failed: {e}")

    def _save(self, *_):
        cfg = self.cfg
        cfg["mode"] = self.mode.get_active_id()
        cfg["language"] = self.language.get_active_id()
        cfg["type_method"] = self.type_method.get_active_id()
        cfg["append_space"] = self.append_space.get_active()
        cfg["uncensored"] = self.uncensored.get_active()
        cfg["key_delay_ms"] = int(self.key_delay.get_value())
        cfg["min_hold_ms"] = int(self.min_hold.get_value())
        cfg["min_record_ms"] = int(self.min_record.get_value())
        cfg["hotkey"] = {
            "modifiers": [m for m, cb in self.mod_checks.items() if cb.get_active()],
            "key": self.hk_key.get_active_id() or None,
        }
        cfg["activation"] = self.activation.get_active_id()
        cfg["cancel_on_esc"] = self.cancel_on_esc.get_active()
        cfg["input_device"] = self.input_device.get_active_id() or ""
        cfg["cleanup_enabled"] = self.cleanup_enabled.get_active()
        cfg["trim_silence"] = self.trim_silence.get_active()
        cfg["always_record"] = self.always_record.get_active()
        cfg["max_tokens"] = int(self.max_tokens.get_value())
        cfg["custom_instructions"] = self._buf_text(self.custom_instructions).strip()
        cfg.setdefault("voice_commands", {})
        cfg["voice_commands"]["enabled"] = self.voice_commands.get_active()
        cfg.setdefault("overlay", {})
        cfg["overlay"]["enabled"] = self.ov_enabled.get_active()
        cfg["overlay"]["color"] = self._rgba_hex()
        cfg["overlay"]["max_alpha"] = round(self.ov_alpha.get_value(), 2)
        cfg["overlay"]["thickness_px"] = int(self.ov_thick.get_value())
        cfg["overlay"]["reduce_motion"] = self.ov_reduce.get_active()

        terms = [t.strip() for t in self._buf_text(self.terms).splitlines()
                 if t.strip()]
        reps = {}
        for line in self._buf_text(self.reps).splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                if k.strip():
                    reps[k.strip()] = v.strip()
        cfg.setdefault("dictionary", {})
        cfg["dictionary"]["terms"] = terms
        cfg["dictionary"]["replacements"] = reps
        cfg["groq_api_key"] = self.api.get_text().strip()

        try:
            with open(CONFIG_PATH, "w") as f:
                json.dump(cfg, f, indent=2)
            os.chmod(CONFIG_PATH, 0o600)
        except Exception as e:
            self.status.set_text(f"Save failed: {e}")
            return

        r = subprocess.run(
            ["systemctl", "--user", "restart", "jspeak.service"],
            capture_output=True, text=True)
        if r.returncode == 0:
            self.status.set_text("Saved & applied - JSpeak restarted.")
        else:
            self.status.set_text("Saved. Restart failed: "
                                 + (r.stderr.strip() or "see logs"))


def main():
    win = Settings()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
