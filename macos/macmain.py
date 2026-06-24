#!/usr/bin/env python3
"""JSpeak dictation daemon for macOS.

Same behavior as the Linux and Windows daemons (hold Ctrl+Shift -> record ->
Groq transcribe + clean -> type into the focused app), implemented with
cross-platform APIs: pynput for the global hotkey + typing, sounddevice for
audio, a click-through AppKit overlay (separate process, controlled over a
pipe), and a pystray menu-bar icon.

macOS threading note: pynput's global key listener manages its own run loop on
its own thread, while pystray must own the *main* thread (it runs an
NSApplication). So the hotkey daemon runs on a background thread and the menu-bar
icon owns main - the inverse of the Windows daemon.

Permissions: macOS gates global key capture and synthetic typing behind
Accessibility and Input Monitoring. Grant JSpeak both in
System Settings -> Privacy & Security the first time you run it.
"""
import io
import os
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import appconfig
import audio
import audiodevices
import groq_core
import history
import hotkeys
import jpaths
import notify
import updater
import version
from groq_core import MODES, transcribe_and_clean

LOG_PATH = jpaths.config_dir() / "jspeak.log"
LAUNCH_AGENT_LABEL = "com.jspeak.agent"


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Audio capture (sounddevice; one always-open stream gated by a flag)
# ---------------------------------------------------------------------------
class Recorder:
    def __init__(self, rate=16000, trim=True, device=""):
        import sounddevice as sd
        import numpy as np
        self._np = np
        self.rate = rate
        self.trim = trim
        self.frames = []
        self.active = False
        idx = audiodevices.sounddevice_index(device)
        try:
            self.stream = sd.InputStream(samplerate=rate, channels=1,
                                         dtype="int16", device=idx,
                                         callback=self._cb)
        except Exception as e:
            if idx is not None:               # fall back to the system default
                log(f"input device {device!r} unavailable ({e}); using default")
                self.stream = sd.InputStream(samplerate=rate, channels=1,
                                             dtype="int16", callback=self._cb)
            else:
                raise
        self.stream.start()

    def _cb(self, indata, frames, t, status):
        if self.active:
            self.frames.append(indata.copy())

    def start(self):
        self.frames = []
        self.active = True               # always-open stream: no start latency

    def discard(self):
        self.active = False
        self.frames = []

    def stop(self):
        self.active = False
        if not self.frames:
            return None, 0
        data = self._np.concatenate(self.frames).tobytes()
        if self.trim:
            data = audio.trim_silence(data, self.rate)
        ms = int(len(data) / 2 / self.rate * 1000)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.rate)
            w.writeframes(data)
        return buf.getvalue(), ms


# ---------------------------------------------------------------------------
# Clipboard (pbcopy/pbpaste) for paste-all-at-once
# ---------------------------------------------------------------------------
def _clip_get():
    try:
        return subprocess.check_output(["pbpaste"]).decode("utf-8")
    except Exception:
        return None


def _clip_set(text):
    try:
        p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
        p.communicate(text.encode("utf-8"))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Typing
# ---------------------------------------------------------------------------
def type_text(text, key_delay_ms, method, kbd):
    if not text:
        return
    if method == "clipboard":
        saved = _clip_get()
        _clip_set(text)
        from pynput.keyboard import Key
        with kbd.pressed(Key.cmd):            # Cmd+V on macOS
            kbd.press("v")
            kbd.release("v")
        time.sleep(0.18)
        if saved is not None:
            _clip_set(saved)
        return
    # per-letter (handles any Unicode incl. RTL)
    delay = max(0, key_delay_ms) / 1000.0
    for ch in text:
        kbd.type(ch)
        if delay:
            time.sleep(delay)


# ---------------------------------------------------------------------------
# Overlay control (separate process, commands over stdin)
# ---------------------------------------------------------------------------
class Overlay:
    def __init__(self, cfg):
        oc = cfg.get("overlay", {})
        self.enabled = oc.get("enabled", True)
        self.color = oc.get("color", "#a855f7")
        self.max_alpha = oc.get("max_alpha", 0.55)
        self.thickness = oc.get("thickness_px", 110)
        self.reduce_motion = oc.get("reduce_motion", True)
        self.proc = None

    def start(self):
        if not self.enabled:
            return
        env = os.environ.copy()
        env["JSPEAK_OVERLAY_COLOR"] = str(self.color)
        env["JSPEAK_OVERLAY_ALPHA"] = str(self.max_alpha)
        env["JSPEAK_OVERLAY_THICKNESS"] = str(self.thickness)
        env["JSPEAK_OVERLAY_REDUCE_MOTION"] = "1" if self.reduce_motion else "0"
        try:
            self.proc = subprocess.Popen(
                [sys.executable, "--overlay"], stdin=subprocess.PIPE, env=env)
        except Exception as e:
            log(f"overlay unavailable: {e}")
            self.proc = None

    def _send(self, cmd):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write((cmd + "\n").encode())
                self.proc.stdin.flush()
            except Exception:
                pass

    def recording(self):
        if self.enabled and (self.proc is None or self.proc.poll() is not None):
            self.start()
            time.sleep(0.15)
        self._send("rec")

    def processing(self):
        self._send("proc")

    def error(self):
        self._send("error")

    def off(self):
        self._send("off")

    def stop(self):
        self._send("quit")
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Global hotkey state machine (pynput)
# ---------------------------------------------------------------------------
class HotkeyDaemon:
    IDLE, ARMED, RECORDING, ABORTED = range(4)

    def __init__(self, cfg):
        from pynput import keyboard
        self.keyboard = keyboard
        self.cfg = cfg
        self.min_hold = cfg.get("min_hold_ms", 300) / 1000.0
        self.min_record = cfg.get("min_record_ms", 350) / 1000.0
        self.cancel_on_esc = cfg.get("cancel_on_esc", True)
        self.spec = hotkeys.parse_spec(cfg)
        self.tracker = hotkeys.ChordTracker(self.spec)
        self.toggle = self.spec["toggle"]
        self.chord_label = hotkeys.describe(self.spec)
        self.lock = threading.Lock()
        self._was_satisfied = False
        self.state = self.IDLE
        self.arm_timer = None
        self.rec_started = 0.0
        self.recorder = Recorder(cfg.get("sample_rate", 16000),
                                 trim=cfg.get("trim_silence", True),
                                 device=cfg.get("input_device", ""))
        self.overlay = Overlay(cfg)
        self.overlay.start()
        self.kbd = keyboard.Controller()
        self._modmap = self._build_modmap(keyboard.Key)

    @staticmethod
    def _build_modmap(K):
        pairs = {
            "ctrl": ("ctrl", "ctrl_l", "ctrl_r"),
            "shift": ("shift", "shift_l", "shift_r"),
            "alt": ("alt", "alt_l", "alt_r", "alt_gr"),
            "super": ("cmd", "cmd_l", "cmd_r"),     # macOS Command key
        }
        m = {}
        for name, attrs in pairs.items():
            for attr in attrs:
                key = getattr(K, attr, None)
                if key is not None:
                    m[key] = name
        return m

    def _canon(self, key):
        """pynput key -> canonical hotkey name (or None if not a chord key)."""
        name = self._modmap.get(key)
        if name:
            return name
        kn = getattr(key, "name", None)            # 'space', 'f1', ...
        if kn in hotkeys.REGULAR_KEYS:
            return kn
        ch = getattr(key, "char", None)
        if ch and ch.lower() in hotkeys.REGULAR_KEYS:
            return ch.lower()
        return None

    def on_press(self, key):
        with self.lock:
            if (self.cancel_on_esc and key == self.keyboard.Key.esc
                    and self.state in (self.ARMED, self.RECORDING)):
                self._cancel()
                return
            name = self._canon(key)
            relevant = self.tracker.set(name, True) if name else False
            if self.toggle:
                sat = self.tracker.satisfied
                if sat and not self._was_satisfied:
                    if self.state == self.RECORDING:
                        self._stop_and_process()
                    elif self.state == self.IDLE:
                        self._toggle_start()
                self._was_satisfied = sat
                return
            if relevant:
                if self.tracker.satisfied and self.state == self.IDLE:
                    self.state = self.ARMED
                    self.recorder.start()           # pre-roll, no clipping
                    self.rec_started = time.time()
                    self.arm_timer = threading.Timer(self.min_hold, self._arm_fire)
                    self.arm_timer.start()
            elif self.state == self.ARMED:           # a real key -> a shortcut
                self.state = self.ABORTED
                self._cancel_timer()
                self.recorder.discard()

    def on_release(self, key):
        with self.lock:
            name = self._canon(key)
            if name:
                self.tracker.set(name, False)
            if self.toggle:
                self._was_satisfied = self.tracker.satisfied
                return
            if name and not self.tracker.satisfied:
                if self.state == self.RECORDING:
                    self._stop_and_process()
                elif self.state in (self.ARMED, self.ABORTED):
                    self.state = self.IDLE
                    self._cancel_timer()
                    self.recorder.discard()

    def _cancel_timer(self):
        if self.arm_timer:
            self.arm_timer.cancel()
            self.arm_timer = None

    def _cancel(self):
        """Discard an armed/in-progress take without transcribing. Hold mode
        parks in ABORTED until the chord is released; toggle mode resets to
        IDLE. Caller holds self.lock."""
        was = self.state
        self._cancel_timer()
        self.recorder.discard()
        self.state = self.IDLE if self.toggle else self.ABORTED
        if self.toggle:
            self._was_satisfied = self.tracker.satisfied
        if was == self.RECORDING:
            log("  cancelled (Esc)")
        self.overlay.off()

    def _toggle_start(self):
        self.recorder.start()
        self.rec_started = time.time()
        self.state = self.RECORDING
        log(f"recording... (press {self.chord_label} again to stop)")
        self.overlay.recording()

    def _arm_fire(self):
        with self.lock:
            if self.state == self.ARMED and self.tracker.satisfied:
                self.state = self.RECORDING       # pre-roll already capturing
                log(f"recording... (release {self.chord_label} to stop)")
                self.overlay.recording()

    def _stop_and_process(self):
        held = time.time() - self.rec_started
        wav, ms = self.recorder.stop()
        self.state = self.IDLE
        if not wav or held < self.min_record:
            log(f"  discarded (too short, {int(held*1000)}ms)")
            self.overlay.off()
            return
        self.overlay.processing()
        threading.Thread(target=self._process, args=(wav, ms), daemon=True).start()

    def _process(self, wav, ms):
        try:
            cfg = appconfig.load_config()   # reload so settings apply live
            t0 = time.time()
            final = transcribe_and_clean(cfg, wav, log=log)
            if not final:
                self.overlay.off()
                return
            log(f"  typing: {final!r}  ({time.time()-t0:.1f}s, {ms}ms audio)")
            type_text(final, cfg.get("key_delay_ms", 10),
                      cfg.get("type_method", "auto"), self.kbd)
            history.add(final)
            self.overlay.off()
        except Exception as e:
            log(f"  ERROR: {e}")
            self.overlay.error()
            notify.notify("JSpeak", groq_core.classify_error(e))

    def run(self):
        log(f"JSpeak v{version.__version__} ready (macOS). "
            f"mode={self.cfg.get('mode')} models={MODES[self.cfg.get('mode','quick')]}")
        verb = "Press" if self.toggle else "Hold"
        log(f"{verb} {self.chord_label} and speak.")
        with self.keyboard.Listener(on_press=self.on_press,
                                    on_release=self.on_release) as listener:
            listener.join()


def maybe_first_run(cfg):
    if cfg.get("groq_api_key", "").strip():
        return
    log("No Groq API key set. Opening settings.")
    try:
        subprocess.Popen([sys.executable, "--settings"])
    except Exception:
        pass


def ensure_autostart():
    """Register the app to launch on login via a LaunchAgent plist. Idempotent:
    only rewrites the plist when the executable path changed (e.g. after an
    update relocates the binary). Takes effect at the next login."""
    try:
        exe = sys.executable
        agents = Path.home() / "Library" / "LaunchAgents"
        agents.mkdir(parents=True, exist_ok=True)
        plist = agents / f"{LAUNCH_AGENT_LABEL}.plist"
        content = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n'
            '<dict>\n'
            f'  <key>Label</key>\n  <string>{LAUNCH_AGENT_LABEL}</string>\n'
            '  <key>ProgramArguments</key>\n'
            f'  <array>\n    <string>{exe}</string>\n  </array>\n'
            '  <key>RunAtLoad</key>\n  <true/>\n'
            '  <key>ProcessType</key>\n  <string>Interactive</string>\n'
            '</dict>\n'
            '</plist>\n'
        )
        if not plist.exists() or plist.read_text() != content:
            plist.write_text(content)
    except Exception as e:
        log(f"autostart registration skipped: {e}")


def main():
    cfg = appconfig.load_config()
    ensure_autostart()
    updater.check_in_background()
    maybe_first_run(cfg)
    daemon = HotkeyDaemon(cfg)

    def quit_app():
        log("quit requested from tray")
        try:
            daemon.overlay.stop()
        except Exception:
            pass
        os._exit(0)

    # Hotkey listener runs on its own thread (pynput manages its run loop); the
    # menu-bar icon must own the main thread on macOS.
    threading.Thread(target=daemon.run, daemon=True).start()

    try:
        import mactray
        mactray.run(quit_app)            # blocks on the main thread
    except Exception as e:
        log(f"tray unavailable: {e}")
        threading.Event().wait()         # keep the process alive for the listener


if __name__ == "__main__":
    main()
