#!/usr/bin/env python3
"""JSpeak settings GUI for Windows (PySide6 / Qt).

A ground-up replacement for the old Tkinter settings window. The Tk version
looked flat and fought the platform (grey OptionMenus, a hand-rolled canvas
scroller, checkboxes that ignored the dark theme); this one is a proper Qt app:
a left sidebar that switches between pages, cards with real hierarchy, custom
painted toggle switches, and a sticky action bar - all driven by one stylesheet.

It edits the same config.json the daemon reads, so behaviour is unchanged:
mode/language/typing/dictionary/hotkey changes apply on your next dictation (the
daemon reloads config each time); glow colour/size/enable apply after JSpeak
restarts. "Test" validates the Groq key on a worker thread; "Preview glow"
spawns the real overlay with the current (unsaved) values, exactly as before.

macOS keeps the Tk window (see app.py); this module is Windows-only.
"""
import os
import subprocess
import sys

from PySide6.QtCore import Qt, QSize, Signal, QThread, QTimer
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QColorDialog, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QPlainTextEdit,
    QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QStackedWidget,
    QVBoxLayout, QWidget)

import appconfig
import audiodevices
import groq_core
import hotkeys
import version


def _device_pairs():
    """[(value, label)] for the microphone picker - '' is the system default."""
    return [("", "System default")] + [(n, n) for n in audiodevices.list_inputs()]

# ---- palette (shared with settings.py / the overlay) ----------------------
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
ERR = "#e8a0a0"
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
ACTIVATIONS = [("hold", "Hold to talk"),
               ("toggle", "Toggle (press to start/stop)")]

# Pages shown in the sidebar: (glyph, title, subtitle).
PAGES = [
    ("⚙", "General", "Model, language and your Groq API key"),
    ("✎", "Dictation", "Typing, cleanup and your personal dictionary"),
    ("⌨", "Hotkey", "The chord that starts and stops recording"),
    ("✦", "Glow", "The screen-edge glow shown while recording"),
]


# ---------------------------------------------------------------------------
# Custom painted on/off switch - replaces the dark-theme-defying Tk checkbox.
# ---------------------------------------------------------------------------
class Switch(QCheckBox):
    """A pill toggle that paints itself, so it actually reads as on/off on a
    dark surface. Behaves like a QCheckBox (isChecked/setChecked/toggled)."""

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setChecked(bool(checked))
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(46, 26)

    def sizeHint(self):
        return QSize(46, 26)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        radius = r.height() / 2
        on = self.isChecked()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(ACCENT) if on else QColor(FIELD))
        p.drawRoundedRect(r, radius, radius)
        if not on:                       # quiet outline so "off" still has form
            p.setBrush(Qt.NoBrush)
            p.setPen(QColor(BORDER))
            p.drawRoundedRect(r, radius, radius)
        d = r.height() - 8
        x = (r.right() - d - 3) if on else (r.left() + 4)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ffffff") if on else QColor(SUB))
        p.drawEllipse(int(x), int(r.top() + 4), int(d), int(d))


# ---------------------------------------------------------------------------
# Groq key validation on a worker thread (keeps the UI responsive).
# ---------------------------------------------------------------------------
class KeyTester(QThread):
    done = Signal(bool, str)

    def __init__(self, key):
        super().__init__()
        self._key = key

    def run(self):
        try:
            ok, msg = groq_core.validate_key(self._key)
        except Exception as e:                       # never crash the GUI
            ok, msg = False, str(e)
        self.done.emit(bool(ok), str(msg))


def _stylesheet():
    return f"""
    QWidget {{ color: {FG}; font-family: "Segoe UI"; font-size: 13px; }}
    QMainWindow, #root {{ background: {BG}; }}

    /* sidebar */
    #sidebar {{ background: {FIELD}; border-right: 1px solid {BORDER}; }}
    #wordmark {{ color: #ffffff; font-size: 22px; font-weight: 700; }}
    #wordmarkSub {{ color: {SUB}; font-size: 11px; }}
    QListWidget#nav {{ background: transparent; border: 0; outline: 0;
                       padding: 6px 10px; }}
    QListWidget#nav::item {{ color: {SUB}; padding: 11px 14px; border-radius: 9px;
                             margin: 3px 0; }}
    QListWidget#nav::item:hover {{ background: {BORDER}; color: {FG}; }}
    QListWidget#nav::item:selected {{ background: {ACCENT}; color: #ffffff; }}

    /* page header */
    #pageTitle {{ font-size: 20px; font-weight: 700; color: #ffffff; }}
    #pageSubtitle {{ color: {SUB}; font-size: 12px; }}
    #headerRule {{ background: {BORDER}; max-height: 1px; min-height: 1px; }}

    /* cards */
    #section {{ color: {SECTION}; font-size: 11px; font-weight: 700;
                letter-spacing: 1px; }}
    #card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 14px; }}
    #hint {{ color: {HINT}; font-size: 11px; }}
    #modeDesc {{ color: {SUB}; font-size: 12px; }}

    /* inputs */
    QComboBox, QSpinBox, QLineEdit {{
        background: {FIELD}; border: 1px solid {BORDER}; border-radius: 8px;
        padding: 6px 10px; color: {FG}; selection-background-color: {ACCENT}; }}
    QComboBox:hover, QSpinBox:hover, QLineEdit:hover {{ border-color: {ACCENT}; }}
    QComboBox:focus, QSpinBox:focus, QLineEdit:focus {{ border-color: {ACCENT_HI}; }}
    QComboBox::drop-down {{ border: 0; width: 22px; }}
    QComboBox::down-arrow {{ image: none; border-left: 4px solid transparent;
        border-right: 4px solid transparent; border-top: 5px solid {SECTION};
        margin-right: 8px; }}
    QComboBox QAbstractItemView {{ background: {FIELD}; color: {FG};
        border: 1px solid {BORDER}; border-radius: 8px; padding: 4px;
        outline: 0; selection-background-color: {ACCENT};
        selection-color: #ffffff; }}
    QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: 0; }}
    QPlainTextEdit {{ background: {FIELD}; border: 1px solid {BORDER};
        border-radius: 8px; padding: 6px; color: {FG};
        selection-background-color: {ACCENT}; }}
    QPlainTextEdit:focus {{ border-color: {ACCENT_HI}; }}

    /* checkbox row labels */
    QCheckBox#plain {{ color: {FG}; spacing: 10px; }}
    QCheckBox#plain::indicator {{ width: 18px; height: 18px; border-radius: 5px;
        border: 1px solid {BORDER}; background: {FIELD}; }}
    QCheckBox#plain::indicator:checked {{ background: {ACCENT};
        border-color: {ACCENT}; }}

    /* buttons */
    QPushButton {{ background: {FIELD}; color: {FG}; border: 1px solid {BORDER};
        border-radius: 8px; padding: 7px 14px; }}
    QPushButton:hover {{ border-color: {ACCENT}; }}
    QPushButton#primary {{ background: {ACCENT}; color: #ffffff; border: 0;
        font-weight: 700; padding: 9px 20px; }}
    QPushButton#primary:hover {{ background: {ACCENT_HI}; }}

    /* slider */
    QSlider::groove:horizontal {{ height: 6px; background: {FIELD};
        border-radius: 3px; }}
    QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
    QSlider::handle:horizontal {{ background: #ffffff; width: 16px; height: 16px;
        margin: -6px 0; border-radius: 8px; }}

    QScrollArea {{ border: 0; background: {BG}; }}
    #pageBody {{ background: {BG}; }}
    QScrollBar:vertical {{ background: {BG}; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px;
        min-height: 28px; }}
    QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    #footer {{ background: {FIELD}; border-top: 1px solid {BORDER}; }}
    """


class Settings(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = appconfig.load_config()
        self.preview_proc = None
        self._tester = None
        self._combos = {}                # name -> (QComboBox, pairs)

        self.setWindowTitle("JSpeak Settings")
        self.resize(880, 660)
        self.setMinimumSize(740, 560)

        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_sidebar())
        outer.addWidget(self._build_main(), 1)

        self._build_general()
        self._build_dictation()
        self._build_hotkey()
        self._build_glow()

        self.nav.setCurrentRow(0)
        self.setStyleSheet(_stylesheet())

    # ---- shell ----------------------------------------------------------
    def _build_sidebar(self):
        bar = QWidget(objectName="sidebar")
        bar.setFixedWidth(212)
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(18, 22, 14, 16)
        lay.setSpacing(2)
        lay.addWidget(QLabel("JSpeak", objectName="wordmark"))
        lay.addWidget(QLabel(f"v{version.__version__}", objectName="wordmarkSub"))
        lay.addSpacing(18)

        self.nav = QListWidget(objectName="nav")
        self.nav.setFocusPolicy(Qt.NoFocus)
        for glyph, title, _sub in PAGES:
            QListWidgetItem(f"  {glyph}   {title}", self.nav)
        self.nav.currentRowChanged.connect(self._on_page)
        lay.addWidget(self.nav, 1)

        tip = QLabel("Hold your hotkey and speak.\nRelease to type it out.")
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        return bar

    def _build_main(self):
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        header = QWidget()
        hl = QVBoxLayout(header)
        hl.setContentsMargins(30, 22, 30, 14)
        hl.setSpacing(2)
        self.page_title = QLabel(objectName="pageTitle")
        self.page_subtitle = QLabel(objectName="pageSubtitle")
        hl.addWidget(self.page_title)
        hl.addWidget(self.page_subtitle)
        rule = QFrame(objectName="headerRule")
        hl.addSpacing(8)
        hl.addWidget(rule)
        lay.addWidget(header)

        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        lay.addWidget(self._build_footer())
        return wrap

    def _build_footer(self):
        footer = QWidget(objectName="footer")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(30, 12, 30, 12)
        self.status = QLabel("")
        self.status.setStyleSheet(f"color: {OK}; font-size: 12px;")
        fl.addWidget(self.status, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        save = QPushButton("Save && Apply", objectName="primary")
        save.clicked.connect(self.save)
        fl.addWidget(close)
        fl.addWidget(save)
        return footer

    def _new_page(self):
        """A scrollable page; returns the QVBoxLayout to fill with cards.

        The caller adds cards then a trailing stretch (via _end_page) so they
        pin to the top instead of floating in the middle of a tall viewport."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget(objectName="pageBody")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(30, 12, 30, 26)
        lay.setSpacing(8)
        scroll.setWidget(body)
        self.stack.addWidget(scroll)
        return lay

    @staticmethod
    def _end_page(page_layout):
        page_layout.addStretch(1)

    def _on_page(self, row):
        if row < 0:
            return
        self.stack.setCurrentIndex(row)
        _g, title, sub = PAGES[row]
        self.page_title.setText(title)
        self.page_subtitle.setText(sub)

    # ---- card / widget builders ----------------------------------------
    def _card(self, page_layout, title):
        page_layout.addSpacing(8)
        page_layout.addWidget(QLabel(title.upper(), objectName="section"))
        card = QFrame(objectName="card")
        inner = QVBoxLayout(card)
        inner.setContentsMargins(18, 16, 18, 16)
        inner.setSpacing(10)
        page_layout.addWidget(card)
        return inner

    def _row(self, card, label, widget):
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(label)
        rl.addWidget(lbl, 1)
        rl.addWidget(widget, 0, Qt.AlignRight)
        card.addWidget(row)
        return row

    def _hint(self, card, text):
        h = QLabel(text, objectName="hint")
        h.setWordWrap(True)
        card.addWidget(h)
        return h

    def _combo(self, card, name, label, pairs, value):
        combo = QComboBox()
        combo.setMinimumWidth(240)
        for _v, lbl in pairs:
            combo.addItem(lbl)
        idx = next((i for i, (v, _l) in enumerate(pairs) if v == value), 0)
        combo.setCurrentIndex(idx)
        self._combos[name] = (combo, pairs)
        self._row(card, label, combo)
        return combo

    def _combo_value(self, name):
        combo, pairs = self._combos[name]
        return pairs[combo.currentIndex()][0]

    def _spin(self, card, label, value, lo, hi):
        sp = QSpinBox()
        sp.setRange(lo, hi)
        sp.setValue(int(value))
        sp.setFixedWidth(110)
        sp.setAlignment(Qt.AlignRight)
        sp.setButtonSymbols(QSpinBox.NoButtons)
        self._row(card, label, sp)
        return sp

    def _toggle(self, card, label, value):
        sw = Switch(value)
        self._row(card, label, sw)
        return sw

    def _check(self, card, label, value):
        """Inline checkbox for compact boolean rows inside a busy card."""
        cb = QCheckBox(label, objectName="plain")
        cb.setChecked(bool(value))
        card.addWidget(cb)
        return cb

    def _textarea(self, card, text, height=72):
        t = QPlainTextEdit()
        t.setPlainText(text or "")
        t.setFixedHeight(height)
        t.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        card.addWidget(t)
        return t

    # ---- pages ----------------------------------------------------------
    def _build_general(self):
        page = self._new_page()
        c = self.cfg

        card = self._card(page, "Model")
        self.mode = self._combo(card, "mode", "Speed / quality", MODES,
                                c.get("mode", "quick"))
        self.mode_desc = QLabel("", objectName="modeDesc")
        self.mode_desc.setWordWrap(True)
        card.addWidget(self.mode_desc)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self._mode_changed()

        card = self._card(page, "Language")
        self._combo(card, "language", "Spoken language", LANGUAGES,
                    c.get("language", "auto"))
        self._hint(card, "Auto-detect handles most cases. Forcing a language "
                         "improves accuracy on short clips.")
        self._combo(card, "type_method", "Text insertion", TYPE_METHODS,
                    c.get("type_method", "auto"))
        self._hint(card, "Letter-by-letter animates the text in (uses the delay "
                         "in Dictation). Paste all at once inserts everything "
                         "instantly via the clipboard - restored afterwards.")

        card = self._card(page, "Groq API key")
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        self.api = QLineEdit(c.get("groq_api_key", ""))
        self.api.setEchoMode(QLineEdit.Password)
        self.api.setPlaceholderText("gsk_…")
        rl.addWidget(self.api, 1)
        show = QPushButton("Show")
        show.setCheckable(True)
        show.toggled.connect(
            lambda on: self.api.setEchoMode(
                QLineEdit.Normal if on else QLineEdit.Password))
        test = QPushButton("Test")
        test.clicked.connect(self._test_key)
        rl.addWidget(show)
        rl.addWidget(test)
        card.addWidget(row)
        self._hint(card, "Get a free key at console.groq.com/keys - it stays on "
                         "your machine and is never shared. Use Test to check it.")
        self._end_page(page)

    def _build_dictation(self):
        page = self._new_page()
        c = self.cfg

        card = self._card(page, "Typing")
        self.key_delay = self._spin(card, "Delay between letters (ms)",
                                    c.get("key_delay_ms", 10), 0, 200)
        self._hint(card, "Lower = faster typing. 10 is very fast; 0-3 is "
                         "near-instant. Ignored when pasting all at once.")
        self.append_space = self._toggle(card, "Add trailing space",
                                         c.get("append_space", True))
        self.uncensored = self._toggle(card, "Uncensored (literature mode)",
                                       c.get("uncensored", True))
        self._hint(card, "Transcribes profanity verbatim and never softens or "
                         "masks it.")

        card = self._card(page, "Microphone")
        dev = c.get("input_device", "") or ""
        pairs = _device_pairs()
        if dev and dev not in [v for v, _l in pairs]:
            pairs.append((dev, f"{dev} (not connected)"))
        self._combo(card, "input_device", "Input device", pairs, dev)
        self._hint(card, "Which microphone to record from. System default "
                         "follows whatever macOS/Windows is set to.")

        card = self._card(page, "Cleanup")
        self.cleanup_enabled = self._toggle(
            card, "LLM cleanup (off = fastest, raw)",
            c.get("cleanup_enabled", True))
        self.voice_commands = self._toggle(
            card, "Voice formatting commands",
            c.get("voice_commands", {}).get("enabled", True))
        self._hint(card, "Say 'new line', 'new paragraph', 'new bullet', or "
                         "'scratch that' (deletes the previous sentence).")
        self.trim_silence = self._toggle(card, "Trim silence before upload",
                                        c.get("trim_silence", True))
        self.max_tokens = self._spin(card, "Max output length (tokens)",
                                     c.get("max_tokens", 4096), 256, 16384)
        self._hint(card, "Custom cleanup instructions - your own style/formatting "
                         "rules (e.g. 'British spelling', 'bullet lists', 'keep it "
                         "lowercase'). Applied only when LLM cleanup is on.")
        self.custom_instructions = self._textarea(
            card, c.get("custom_instructions", "") or "")

        card = self._card(page, "Dictionary")
        d = c.get("dictionary", {})
        self._hint(card, "Terms - names/words it mishears (one per line). Bias "
                         "recognition and keep their spelling.")
        self.terms = self._textarea(card, "\n".join(d.get("terms", []) or []))
        self._hint(card, "Replacements - one per line as  wrong = right")
        rep = d.get("replacements", {}) or {}
        self.reps = self._textarea(
            card, "\n".join(f"{k} = {v}" for k, v in rep.items()))
        self._end_page(page)

    def _build_hotkey(self):
        page = self._new_page()
        c = self.cfg
        spec = hotkeys.parse_spec(c)

        card = self._card(page, "Hotkey")
        mod_row = QWidget()
        ml = QHBoxLayout(mod_row)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.addWidget(QLabel("Modifiers"), 1)
        self.mod_checks = {}
        for m in hotkeys.MODIFIERS:
            cb = QCheckBox(m.capitalize(), objectName="plain")
            cb.setChecked(m in spec["mods"])
            cb.toggled.connect(self._update_chord)
            self.mod_checks[m] = cb
            ml.addWidget(cb)
        card.addWidget(mod_row)

        key_pairs = [("", "None")] + [
            (k, k.upper() if len(k) == 1 else k.capitalize())
            for k in hotkeys.REGULAR_KEYS]
        kc = self._combo(card, "hk_key", "Extra key (optional)", key_pairs,
                         spec["key"] or "")
        kc.currentIndexChanged.connect(self._update_chord)
        ac = self._combo(card, "activation", "Activation", ACTIVATIONS,
                         "toggle" if spec["toggle"] else "hold")
        ac.currentIndexChanged.connect(self._update_chord)
        self.min_hold = self._spin(card, "Hold before recording (ms)",
                                   c.get("min_hold_ms", 300), 50, 2000)
        self.min_record = self._spin(card, "Discard if shorter than (ms)",
                                     c.get("min_record_ms", 350), 100, 3000)
        self.cancel_on_esc = self._toggle(card, "Esc cancels a recording",
                                          c.get("cancel_on_esc", True))
        self._hint(card, "Hold: record while held. Toggle: press once to start, "
                         "again to stop. Press Esc mid-recording to discard it.")

        self.chord_label = QLabel("")
        self.chord_label.setStyleSheet(
            f"color: {SECTION}; font-size: 15px; font-weight: 700; "
            f"padding: 4px 0;")
        self._row(card, "Current chord", self.chord_label)
        self._update_chord()
        self._end_page(page)

    def _build_glow(self):
        page = self._new_page()
        c = self.cfg
        ov = c.get("overlay", {})

        card = self._card(page, "Glow overlay")
        self.ov_enabled = self._toggle(card, "Enabled", ov.get("enabled", True))

        self.ov_color = ov.get("color", "#a855f7")
        self.color_btn = QPushButton("  Choose colour  ")
        self.color_btn.clicked.connect(self._pick_color)
        self._style_color_btn()
        self._row(card, "Glow colour", self.color_btn)

        self.ov_alpha = QSlider(Qt.Horizontal)
        self.ov_alpha.setRange(0, 100)
        self.ov_alpha.setFixedWidth(200)
        self.ov_alpha.setValue(int(round(float(ov.get("max_alpha", 0.55)) * 100)))
        self._row(card, "Intensity", self.ov_alpha)

        self.ov_thick = self._spin(card, "Thickness (px)",
                                   ov.get("thickness_px", 110), 10, 400)
        self.ov_reduce = self._toggle(card, "Reduce motion (low-end mode)",
                                      ov.get("reduce_motion", True))
        self._hint(card, "On (default): static glow, zero CPU while recording - "
                         "best on weak hardware. Off: gentle breathing pulse.")

        prev = QPushButton("Preview glow")
        prev.clicked.connect(self._preview)
        card.addWidget(prev, 0, Qt.AlignLeft)
        self._hint(card, "Previews the current (unsaved) colour, intensity and "
                         "thickness for a couple of seconds.")
        self._end_page(page)

    # ---- actions --------------------------------------------------------
    def _mode_changed(self):
        self.mode_desc.setText(MODE_DESC.get(self._combo_value("mode"), ""))

    def _update_chord(self):
        spec = {
            "mods": frozenset(m for m, cb in self.mod_checks.items()
                              if cb.isChecked()),
            "key": self._combo_value("hk_key") or None,
            "toggle": self._combo_value("activation") == "toggle",
        }
        self.chord_label.setText(hotkeys.describe(spec))

    def _style_color_btn(self):
        fg = "#ffffff" if QColor(self.ov_color).lightnessF() < 0.6 else "#101010"
        self.color_btn.setStyleSheet(
            f"background: {self.ov_color}; color: {fg}; border: 0; "
            f"border-radius: 8px; padding: 7px 14px; font-weight: 700;")

    def _pick_color(self):
        col = QColorDialog.getColor(QColor(self.ov_color), self, "Glow colour")
        if col.isValid():
            self.ov_color = col.name()
            self._style_color_btn()

    def _test_key(self):
        key = self.api.text().strip()
        self._set_status("Testing key…", OK)
        self._tester = KeyTester(key)
        self._tester.done.connect(self._key_tested)
        self._tester.start()

    def _key_tested(self, ok, msg):
        self._set_status(("✓ " if ok else "✗ ") + msg, OK if ok else ERR)

    def _set_status(self, text, color):
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color}; font-size: 12px;")

    # ---- glow preview (spawns the real overlay, current unsaved values) --
    def _preview(self):
        self._kill_preview()
        env = os.environ.copy()
        env["JSPEAK_OVERLAY_COLOR"] = self.ov_color
        env["JSPEAK_OVERLAY_ALPHA"] = str(round(self.ov_alpha.value() / 100, 2))
        env["JSPEAK_OVERLAY_THICKNESS"] = str(int(self.ov_thick.value()))
        env["JSPEAK_OVERLAY_REDUCE_MOTION"] = "1" if self.ov_reduce.isChecked() else "0"
        flags = 0x08000000 if sys.platform == "win32" else 0   # CREATE_NO_WINDOW
        try:
            self.preview_proc = subprocess.Popen(
                [sys.executable, "--overlay"], stdin=subprocess.PIPE, env=env,
                creationflags=flags)
        except Exception as e:
            self._set_status(f"Preview failed: {e}", ERR)
            return
        self._send_preview("rec")
        QTimer.singleShot(1600, lambda: self._send_preview("off"))
        QTimer.singleShot(2200, self._kill_preview)
        self._set_status("Previewing glow…", OK)

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
            except Exception:
                pass
            try:
                p.terminate()
            except Exception:
                pass
        self.preview_proc = None

    # ---- persist --------------------------------------------------------
    def save(self):
        c = self.cfg
        c["groq_api_key"] = self.api.text().strip()
        c["mode"] = self._combo_value("mode")
        c["language"] = self._combo_value("language")
        c["type_method"] = self._combo_value("type_method")
        c["key_delay_ms"] = int(self.key_delay.value())
        c["min_hold_ms"] = int(self.min_hold.value())
        c["min_record_ms"] = int(self.min_record.value())
        c["append_space"] = self.append_space.isChecked()
        c["uncensored"] = self.uncensored.isChecked()
        c["cleanup_enabled"] = self.cleanup_enabled.isChecked()
        c["trim_silence"] = self.trim_silence.isChecked()
        c["cancel_on_esc"] = self.cancel_on_esc.isChecked()
        c["input_device"] = self._combo_value("input_device")
        c["max_tokens"] = int(self.max_tokens.value())
        c["custom_instructions"] = self.custom_instructions.toPlainText().strip()
        c["hotkey"] = {
            "modifiers": [m for m, cb in self.mod_checks.items()
                          if cb.isChecked()],
            "key": self._combo_value("hk_key") or None,
        }
        c["activation"] = self._combo_value("activation")
        c.setdefault("voice_commands", {})
        c["voice_commands"]["enabled"] = self.voice_commands.isChecked()
        c.setdefault("overlay", {})
        c["overlay"]["enabled"] = self.ov_enabled.isChecked()
        c["overlay"]["color"] = self.ov_color
        c["overlay"]["max_alpha"] = round(self.ov_alpha.value() / 100, 2)
        c["overlay"]["thickness_px"] = int(self.ov_thick.value())
        c["overlay"]["reduce_motion"] = self.ov_reduce.isChecked()

        terms = [t.strip() for t in self.terms.toPlainText().splitlines()
                 if t.strip()]
        reps = {}
        for line in self.reps.toPlainText().splitlines():
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
            self._set_status(f"Save failed: {e}", ERR)
            return
        self._set_status(
            "Saved. Text/model settings apply on your next dictation; "
            "glow colour/size apply after restart.", OK)

    def closeEvent(self, event):
        self._kill_preview()
        super().closeEvent(event)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    win = Settings()
    win.show()
    app.exec()


if __name__ == "__main__":
    main()
