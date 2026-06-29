#!/usr/bin/env python3
"""JSpeak settings GUI for Windows (Tkinter).

Mirrors the Linux GTK settings window: a scrollable dark panel of titled cards
(Model / Language / Typing / Hotkey / Glow overlay / Dictionary / API key) with
inline hints, a colour picker + intensity slider for the glow, and a live
"Preview glow" that spawns the overlay with the *current* (unsaved) values.

Edits the same config.json the daemon reads; mode/language/typing/dictionary/key
changes apply on your next dictation (the daemon reloads config each time). Glow
colour/size/enable apply after JSpeak restarts (e.g. next login)."""
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import colorchooser, ttk

import appconfig
import audiodevices
import groq_core
import hotkeys
import version


def _device_pairs(current=""):
    """[(value, label)] for the microphone picker. '' is the system default; a
    saved-but-missing device is kept so saving doesn't silently switch it."""
    pairs = [("", "System default")] + [(n, n) for n in audiodevices.list_inputs()]
    if current and current not in [v for v, _l in pairs]:
        pairs.append((current, f"{current} (not connected)"))
    return pairs

# The native UI font per platform so the window matches the rest of the OS
# (this Tk window is shared by Windows and macOS). Segoe UI is Windows-only and
# falls back to an ugly default on macOS, so pick Helvetica Neue there.
UI_FONT = "Helvetica Neue" if sys.platform == "darwin" else "Segoe UI"

# ---- palette (matches settings.py CSS) ----
BG = "#0e0c15"
CARD = "#181523"
FIELD = "#120f1c"
FG = "#e8e4f2"
SUB = "#908aa6"
HINT = "#847c9e"
SECTION = "#b48cff"
ACCENT = "#a855f7"
ACCENT_HI = "#b76dff"
OK = "#7ee0a8"
BORDER = "#262134"

MODES = [("quick", "Quick"), ("smart", "Smart"), ("accurate", "Accurate"),
         ("max", "Max")]
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
TYPE_METHODS = [("auto", "Type letter-by-letter (recommended)"),
                ("clipboard", "Paste all at once (instant)")]


def _label_for(pairs, value):
    return dict(pairs).get(value, pairs[0][1])


def _value_for(pairs, label):
    for v, lbl in pairs:
        if lbl == label:
            return v
    return pairs[0][0]


class Settings:
    def __init__(self):
        self.cfg = appconfig.load_config()
        self.preview_proc = None
        self._canvases = []
        self.root = tk.Tk()
        self.root.title("JSpeak Settings")
        self.root.configure(bg=BG)
        self.root.geometry("580x720")
        self.root.minsize(480, 560)

        self._init_ttk()

        c = self.cfg

        # ---- header (title + subtitle) ---------------------------------
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=22, pady=(18, 2))
        tk.Label(header, text="JSpeak", bg=BG, fg="#ffffff",
                 font=(UI_FONT, 20, "bold")).pack(anchor="w")
        tk.Label(header, text=f"Push-to-talk dictation  ·  hold Ctrl+Shift  ·  "
                 f"v{version.__version__}", bg=BG, fg=SUB,
                 font=(UI_FONT, 9)).pack(anchor="w")
        tk.Frame(header, bg=BORDER, height=1).pack(fill="x", pady=(14, 0))

        # ---- tabbed notebook -------------------------------------------
        self.nb = ttk.Notebook(self.root, style="JS.TNotebook")
        self.nb.pack(fill="both", expand=True)
        self.root.bind_all("<MouseWheel>", self._on_wheel)

        # ---- General ----
        pad = self._scroll_tab("General")
        card = self._card(pad, "MODEL")
        self.mode = self._option(card, "Speed / quality", MODES,
                                 c.get("mode", "quick"), on_change=self._mode_changed)
        self.mode_desc = self._hint(card, "")
        self._mode_changed()

        card = self._card(pad, "LANGUAGE")
        self.language = self._option(card, "Spoken language", LANGUAGES,
                                     c.get("language", "auto"))
        self._hint(card, "Auto-detect handles most cases. Forcing a language "
                         "improves accuracy on short clips.")
        self.type_method = self._option(card, "Text insertion", TYPE_METHODS,
                                        c.get("type_method", "auto"))
        self._hint(card, "Letter-by-letter animates the text in (uses the delay "
                         "in Dictation). Paste all at once inserts everything "
                         "instantly via the clipboard - restored afterwards.")

        card = self._card(pad, "GROQ API KEY")
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x")
        self.api = tk.Entry(row, bg=FIELD, fg=FG, insertbackground=FG,
                            relief="flat", show="*")
        self.api.insert(0, c.get("groq_api_key", ""))
        self.api.pack(side="left", fill="x", expand=True, ipady=4)
        self._show = tk.BooleanVar(value=False)
        tk.Checkbutton(row, text="Show", variable=self._show,
                       command=lambda: self.api.config(
                           show="" if self._show.get() else "*"),
                       bg=CARD, fg=SUB, selectcolor=FIELD, activebackground=CARD,
                       activeforeground=FG, font=(UI_FONT, 9)).pack(
            side="left", padx=(8, 0))
        tk.Button(row, text="Test", command=self._test_key, relief="flat",
                  bg=FIELD, fg=FG, activebackground=BORDER, font=(UI_FONT, 9),
                  cursor="hand2").pack(side="left", padx=(8, 0))
        self._hint(card, "Get a free key at console.groq.com/keys - it stays on "
                         "your machine and is never shared. Use Test to check it.")

        # ---- Dictation ----
        pad = self._scroll_tab("Dictation")
        card = self._card(pad, "TYPING")
        self.key_delay = self._spin(card, "Delay between letters (ms)",
                                    c.get("key_delay_ms", 10), 0, 200)
        self._hint(card, "Lower = faster typing. 10 is very fast; 0-3 is "
                         "near-instant. Ignored when pasting all at once.")
        self.append_space = self._check(card, "Add trailing space",
                                        c.get("append_space", True))
        self.uncensored = self._check(card, "Uncensored (literature mode)",
                                      c.get("uncensored", True))
        self._hint(card, "Transcribes profanity verbatim and never softens or "
                         "masks it.")

        card = self._card(pad, "MICROPHONE")
        self.input_device = self._option(
            card, "Input device", _device_pairs(c.get("input_device", "")),
            c.get("input_device", "") or "")
        self._hint(card, "Which microphone to record from. System default "
                         "follows whatever the OS is set to.")

        card = self._card(pad, "CLEANUP")
        self.cleanup_enabled = self._check(card, "LLM cleanup (off = fastest, raw)",
                                           c.get("cleanup_enabled", True))
        self.voice_commands = self._check(
            card, "Voice formatting commands",
            c.get("voice_commands", {}).get("enabled", True))
        self._hint(card, "Say 'new line', 'new paragraph', 'new bullet', or "
                         "'scratch that' (deletes the previous sentence).")
        self.trim_silence = self._check(card, "Trim silence before upload",
                                        c.get("trim_silence", True))
        self.max_tokens = self._spin(card, "Max output length (tokens)",
                                     c.get("max_tokens", 4096), 256, 16384)
        self._hint(card, "Custom cleanup instructions - your own style/formatting "
                         "rules (e.g. 'British spelling', 'bullet lists', 'keep it "
                         "lowercase'). Applied only when LLM cleanup is on.")
        self.custom_instructions = self._textarea(
            card, c.get("custom_instructions", "") or "")

        card = self._card(pad, "DICTIONARY")
        d = c.get("dictionary", {})
        self._hint(card, "Terms - names/words it mishears (one per line). Bias "
                         "recognition and keep their spelling.")
        self.terms = self._textarea(card, "\n".join(d.get("terms", []) or []))
        self._hint(card, "Replacements - one per line as  wrong = right")
        rep = d.get("replacements", {}) or {}
        self.reps = self._textarea(
            card, "\n".join(f"{k} = {v}" for k, v in rep.items()))

        # ---- Hotkey ----
        pad = self._scroll_tab("Hotkey")
        card = self._card(pad, "HOTKEY")
        spec = hotkeys.parse_spec(c)
        self.mod_vars = {}
        mod_row = tk.Frame(card, bg=CARD)
        mod_row.pack(fill="x", pady=(2, 4))
        tk.Label(mod_row, text="Modifiers", bg=CARD, fg=FG,
                 font=(UI_FONT, 9)).pack(side="left")
        for m in hotkeys.MODIFIERS:
            var = tk.BooleanVar(value=m in spec["mods"])
            self.mod_vars[m] = var
            tk.Checkbutton(mod_row, text=m.capitalize(), variable=var, bg=CARD,
                           fg=FG, selectcolor=FIELD, activebackground=CARD,
                           activeforeground=FG, font=(UI_FONT, 9)).pack(
                side="left", padx=(8, 0))
        key_pairs = [("", "None")] + [
            (k, k.upper() if len(k) == 1 else k.capitalize())
            for k in hotkeys.REGULAR_KEYS]
        self.hk_key = self._option(card, "Extra key (optional)", key_pairs,
                                   spec["key"] or "")
        self.activation = self._option(
            card, "Activation",
            [("hold", "Hold to talk"), ("toggle", "Toggle (press to start/stop)")],
            "toggle" if spec["toggle"] else "hold")
        self.min_hold = self._spin(card, "Hold before recording (ms)",
                                   c.get("min_hold_ms", 300), 50, 2000)
        self.min_record = self._spin(card, "Discard if shorter than (ms)",
                                     c.get("min_record_ms", 350), 100, 3000)
        self.cancel_on_esc = self._check(card, "Esc cancels a recording",
                                         c.get("cancel_on_esc", True))
        self._hint(card, "Hold: record while held. Toggle: press once to start, "
                         "again to stop. Press Esc mid-recording to discard it.")

        # ---- Glow ----
        pad = self._scroll_tab("Glow")
        card = self._card(pad, "GLOW OVERLAY")
        ov = c.get("overlay", {})
        self.ov_enabled = self._check(card, "Enabled", ov.get("enabled", True))
        self.ov_color = ov.get("color", "#a855f7")
        color_row = self._row(card, "Glow colour")
        self.color_btn = tk.Button(
            color_row, text="  Colour  ", command=self._pick_color, relief="flat",
            bg=self.ov_color, fg="#ffffff", activebackground=self.ov_color,
            font=(UI_FONT, 9, "bold"), cursor="hand2")
        self.color_btn.pack(side="right")
        self.ov_alpha = tk.DoubleVar(value=ov.get("max_alpha", 0.55))
        alpha_row = self._row(card, "Intensity")
        scale = tk.Scale(alpha_row, from_=0.0, to=1.0, resolution=0.05,
                         orient="horizontal", variable=self.ov_alpha,
                         bg=CARD, fg=FG, troughcolor=FIELD, highlightthickness=0,
                         activebackground=ACCENT, length=180)
        scale.pack(side="right")
        self.ov_thick = self._spin(card, "Thickness (px)",
                                   ov.get("thickness_px", 110), 10, 400)
        self.ov_reduce = self._check(card, "Reduce motion (low-end mode)",
                                     ov.get("reduce_motion", True))
        self._hint(card, "On (default): static glow, zero CPU while recording - "
                         "best on weak hardware. Off: gentle breathing pulse.")
        prev = tk.Button(card, text="Preview glow", command=self._preview,
                         relief="flat", bg=FIELD, fg=FG, activebackground=BORDER,
                         font=(UI_FONT, 9), cursor="hand2")
        prev.pack(anchor="w", pady=(6, 0))
        self._hint(card, "Previews the current (unsaved) colour, intensity and "
                         "thickness for a couple of seconds.")

        # ---- Footer ----
        footer = tk.Frame(self.root, bg=BG)
        footer.pack(fill="x", side="bottom")
        self.status = tk.Label(footer, text="", bg=BG, fg=OK,
                               font=(UI_FONT, 9))
        self.status.pack(side="left", padx=22, pady=12)
        tk.Button(footer, text="Save & Apply", command=self.save, bg=ACCENT,
                  fg="#ffffff", relief="flat", font=(UI_FONT, 10, "bold"),
                  padx=18, pady=6, activebackground=ACCENT_HI,
                  cursor="hand2").pack(side="right", padx=(0, 22), pady=12)
        tk.Button(footer, text="Close", command=self._close, bg=FIELD, fg=FG,
                  relief="flat", font=(UI_FONT, 10), padx=14, pady=6,
                  activebackground=BORDER, cursor="hand2").pack(
            side="right", padx=(0, 8), pady=12)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    # ---- ttk dark styling for OptionMenu/Scrollbar ----
    def _init_ttk(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TMenubutton", background=FIELD, foreground=FG,
                        relief="flat", padding=6, arrowcolor=SECTION)
        style.map("TMenubutton", background=[("active", BORDER)])
        style.configure("Vertical.TScrollbar", background=CARD,
                        troughcolor=BG, arrowcolor=SUB, bordercolor=BG)
        # segmented-looking notebook tabs (matches the GTK stack switcher)
        style.configure("JS.TNotebook", background=BG, borderwidth=0,
                        tabmargins=[22, 12, 22, 0])
        style.configure("JS.TNotebook.Tab", background=FIELD, foreground=SUB,
                        padding=[18, 8], borderwidth=0,
                        font=(UI_FONT, 10, "bold"))
        style.map("JS.TNotebook.Tab",
                  background=[("selected", ACCENT), ("active", BORDER)],
                  foreground=[("selected", "#ffffff"), ("active", FG)])
        try:
            style.layout("JS.TNotebook.Tab", style.layout("TNotebook.Tab"))
        except Exception:
            pass

    # ---- scrollable notebook tab ----
    def _scroll_tab(self, title):
        outer = tk.Frame(self.nb, bg=BG)
        self.nb.add(outer, text=title)
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, bg=BG)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(win, width=e.width))
        self._canvases.append(canvas)
        pad = tk.Frame(inner, bg=BG)
        pad.pack(fill="both", expand=True, padx=22, pady=(4, 16))
        return pad

    def _on_wheel(self, e):
        try:
            cur = self.nb.index(self.nb.select())
            self._canvases[cur].yview_scroll(int(-e.delta / 120), "units")
        except Exception:
            pass

    # ---- widget helpers ----
    def _card(self, parent, title):
        tk.Label(parent, text=title, bg=BG, fg=SECTION,
                 font=(UI_FONT, 9, "bold")).pack(anchor="w", pady=(14, 2))
        card = tk.Frame(parent, bg=CARD, highlightbackground=BORDER,
                        highlightthickness=1)
        card.pack(fill="x")
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=14, pady=12)
        return inner

    def _row(self, card, label):
        """A label + right-aligned control row. Returns the row frame so callers
        parent the control directly to it. (Packing a control into a row via
        ``in_`` while its real parent is the card leaves the row stacked above
        the control, which swallows its clicks on macOS - the control shows but
        won't interact.)"""
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", pady=(6, 0))
        tk.Label(row, text=label, bg=CARD, fg=FG,
                 font=(UI_FONT, 9)).pack(side="left")
        return row

    def _hint(self, card, text):
        h = tk.Label(card, text=text, bg=CARD, fg=HINT, font=(UI_FONT, 8),
                     justify="left", wraplength=440, anchor="w")
        h.pack(fill="x", anchor="w", pady=(4, 0))
        return h

    def _option(self, card, label, pairs, value, on_change=None):
        var = tk.StringVar(value=_label_for(pairs, value))
        row = self._row(card, label)
        om = ttk.OptionMenu(row, var, var.get(), *[p[1] for p in pairs],
                            style="TMenubutton")
        try:
            menu = om["menu"]
            menu.configure(bg=FIELD, fg=FG, activebackground=ACCENT,
                           activeforeground="#ffffff", relief="flat", bd=0)
        except Exception:
            pass
        om.pack(side="right")
        if on_change:
            var.trace_add("write", lambda *_: on_change())
        return (var, pairs)

    def _spin(self, card, label, value, lo, hi):
        var = tk.IntVar(value=int(value))
        row = self._row(card, label)
        sp = tk.Spinbox(row, from_=lo, to=hi, textvariable=var, width=7,
                        bg=FIELD, fg=FG, insertbackground=FG, relief="flat",
                        buttonbackground=CARD, justify="right")
        sp.pack(side="right")
        return var

    def _check(self, card, label, value):
        var = tk.BooleanVar(value=bool(value))
        tk.Checkbutton(card, text=label, variable=var, bg=CARD, fg=FG,
                       selectcolor=FIELD, activebackground=CARD,
                       activeforeground=FG, font=(UI_FONT, 9),
                       anchor="w").pack(fill="x", anchor="w", pady=(6, 0))
        return var

    def _textarea(self, card, text):
        t = tk.Text(card, height=3, bg=FIELD, fg=FG, insertbackground=FG,
                    relief="flat", highlightbackground=BORDER,
                    highlightthickness=1, font=(UI_FONT, 9), wrap="word")
        t.pack(fill="x", pady=(2, 6))
        t.insert("1.0", text)
        return t

    # ---- actions ----
    def _mode_changed(self):
        code = _value_for(self.mode[1], self.mode[0].get())
        self.mode_desc.config(text=MODE_DESC.get(code, ""))

    def _test_key(self):
        key = self.api.get().strip()
        self.status.config(text="Testing key…", fg=OK)

        def work():
            ok, msg = groq_core.validate_key(key)
            self.root.after(
                0, lambda: self.status.config(
                    text=("✓ " if ok else "✗ ") + msg, fg=OK if ok else "#e8a0a0"))

        threading.Thread(target=work, daemon=True).start()

    def _pick_color(self):
        rgb, hexv = colorchooser.askcolor(color=self.ov_color,
                                          title="Glow colour")
        if hexv:
            self.ov_color = hexv
            self.color_btn.config(bg=hexv, activebackground=hexv)

    def _preview(self):
        self._kill_preview()
        env = os.environ.copy()
        env["JSPEAK_OVERLAY_COLOR"] = self.ov_color
        env["JSPEAK_OVERLAY_ALPHA"] = str(round(self.ov_alpha.get(), 2))
        env["JSPEAK_OVERLAY_THICKNESS"] = str(int(self.ov_thick.get()))
        env["JSPEAK_OVERLAY_REDUCE_MOTION"] = "1" if self.ov_reduce.get() else "0"
        # CREATE_NO_WINDOW is Windows-only; on macOS (which reuses this Tk GUI)
        # there is no console to hide, so don't pass it there.
        flags = 0x08000000 if sys.platform == "win32" else 0
        try:
            self.preview_proc = subprocess.Popen(
                [sys.executable, "--overlay"], stdin=subprocess.PIPE, env=env,
                creationflags=flags)
        except Exception as e:
            self.status.config(text=f"Preview failed: {e}", fg="#e8a0a0")
            return
        self._send_preview("rec")
        self.root.after(1600, lambda: self._send_preview("off"))
        self.root.after(2200, self._kill_preview)
        self.status.config(text="Previewing glow…", fg=OK)

    def _send_preview(self, cmd):
        p = self.preview_proc
        if p and p.poll() is None:
            try:
                p.stdin.write((cmd + "\n").encode())
                p.stdin.flush()
            except Exception:
                pass

    def _kill_preview(self):
        p = self.preview_proc
        if p and p.poll() is None:
            try:
                p.stdin.write(b"quit\n")
                p.stdin.flush()
                time.sleep(0.05)
            except Exception:
                pass
            try:
                p.terminate()
            except Exception:
                pass
        self.preview_proc = None

    def save(self):
        c = self.cfg
        c["groq_api_key"] = self.api.get().strip()
        c["mode"] = _value_for(self.mode[1], self.mode[0].get())
        c["language"] = _value_for(self.language[1], self.language[0].get())
        c["type_method"] = _value_for(self.type_method[1], self.type_method[0].get())
        c["key_delay_ms"] = int(self.key_delay.get())
        c["min_hold_ms"] = int(self.min_hold.get())
        c["min_record_ms"] = int(self.min_record.get())
        c["append_space"] = bool(self.append_space.get())
        c["uncensored"] = bool(self.uncensored.get())
        c["cleanup_enabled"] = bool(self.cleanup_enabled.get())
        c["trim_silence"] = bool(self.trim_silence.get())
        c["cancel_on_esc"] = bool(self.cancel_on_esc.get())
        c["input_device"] = _value_for(self.input_device[1], self.input_device[0].get())
        c["max_tokens"] = int(self.max_tokens.get())
        c["custom_instructions"] = self.custom_instructions.get("1.0", "end").strip()
        c["hotkey"] = {
            "modifiers": [m for m, v in self.mod_vars.items() if v.get()],
            "key": _value_for(self.hk_key[1], self.hk_key[0].get()) or None,
        }
        c["activation"] = _value_for(self.activation[1], self.activation[0].get())
        c.setdefault("voice_commands", {})
        c["voice_commands"]["enabled"] = bool(self.voice_commands.get())
        c.setdefault("overlay", {})
        c["overlay"]["enabled"] = bool(self.ov_enabled.get())
        c["overlay"]["color"] = self.ov_color
        c["overlay"]["max_alpha"] = round(self.ov_alpha.get(), 2)
        c["overlay"]["thickness_px"] = int(self.ov_thick.get())
        c["overlay"]["reduce_motion"] = bool(self.ov_reduce.get())

        terms = [t.strip() for t in self.terms.get("1.0", "end").splitlines()
                 if t.strip()]
        reps = {}
        for line in self.reps.get("1.0", "end").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                if k.strip():
                    reps[k.strip()] = v.strip()
        c.setdefault("dictionary", {})
        c["dictionary"]["terms"] = terms
        c["dictionary"]["replacements"] = reps

        try:
            appconfig.save_config(c)
            try:
                os.chmod(appconfig.CONFIG_PATH, 0o600)
            except Exception:
                pass
        except Exception as e:
            self.status.config(text=f"Save failed: {e}", fg="#e8a0a0")
            return
        self.status.config(
            text="Saved. Text/model settings apply on your next dictation; "
                 "glow colour/size apply after restart.", fg=OK)

    def _close(self):
        self._kill_preview()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    Settings().run()


if __name__ == "__main__":
    main()
