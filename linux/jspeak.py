#!/usr/bin/env python3
"""JSpeak - push-to-talk dictation for Wayland/Hyprland.

Hold Ctrl+Shift (with no other key) to record. Release to transcribe via Groq
Whisper, clean up the text with a fast Groq LLM, and type it into the focused
window via ydotool.

No Wayland display is required: keys are read from /dev/input (evdev raw, stdlib
only) and text is injected through uinput by ydotool.
"""

import glob
import json
import os
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from urllib import request as urlrequest, error as urlerror

# ----------------------------------------------------------------------------
# Paths & config
# ----------------------------------------------------------------------------
import audio
import hotkeys
import history
import jpaths
import notify
import updater
import version
import groq_core
from groq_core import MODES, transcribe_and_clean

ROOT = Path(__file__).resolve().parent      # the linux/ source folder
REPO_ROOT = ROOT.parent                      # repo root: app.py, .venv, config
CONFIG_PATH = jpaths.config_path()
DEFAULT_CONFIG = jpaths.bundle_dir() / "config.default.json"

# evdev key codes (linux/input-event-codes.h)
EV_KEY = 0x01
ESC_CODE = 1                 # KEY_ESC - cancels an in-progress recording

EVENT_FMT = "llHHi"          # timeval(sec,usec), type, code, value
EVENT_SIZE = struct.calcsize(EVENT_FMT)

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def ensure_user_config():
    """Create the per-user config on first run (migrating a dev config.json if
    present, else copying the bundled default with a blank API key)."""
    if CONFIG_PATH.exists():
        return
    legacy = REPO_ROOT / "config.json"
    src = legacy if legacy.exists() else DEFAULT_CONFIG
    try:
        shutil.copy(src, CONFIG_PATH)
    except Exception:
        CONFIG_PATH.write_text(json.dumps({"groq_api_key": "", "mode": "quick"},
                                          indent=2))
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def load_config():
    ensure_user_config()
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    import appconfig
    return appconfig.validate(cfg)


# ----------------------------------------------------------------------------
# Screen-edge glow overlay (separate GTK layer-shell process, signal-driven)
# ----------------------------------------------------------------------------
def find_wayland_display():
    """The daemon may run without WAYLAND_DISPLAY in its env; discover it."""
    wd = os.environ.get("WAYLAND_DISPLAY")
    if wd:
        return wd
    xrd = os.environ.get("XDG_RUNTIME_DIR")
    if not xrd:
        return None
    socks = sorted(p for p in glob.glob(os.path.join(xrd, "wayland-*"))
                   if not p.endswith(".lock"))
    return os.path.basename(socks[0]) if socks else None


def _venv_python():
    venv_py = REPO_ROOT / ".venv" / "bin" / "python"
    return str(venv_py) if venv_py.exists() else sys.executable


def overlay_command():
    """How to launch the overlay: the frozen binary with --overlay, or the venv
    python dispatching through app.py from source (so the shared/ modules are on
    the path)."""
    if jpaths.is_frozen():
        return [sys.executable, "--overlay"]
    return [_venv_python(), str(REPO_ROOT / "app.py"), "--overlay"]


def settings_command():
    if jpaths.is_frozen():
        return [sys.executable, "--settings"]
    return [_venv_python(), str(REPO_ROOT / "app.py"), "--settings"]


class Overlay:
    """Controls overlay.py via signals: recording / processing / off."""

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
        wd = find_wayland_display()
        if wd:
            env["WAYLAND_DISPLAY"] = wd
        env["GDK_BACKEND"] = "wayland"          # layer-shell is wayland-only
        env["JSPEAK_OVERLAY_COLOR"] = str(self.color)
        env["JSPEAK_OVERLAY_ALPHA"] = str(self.max_alpha)
        env["JSPEAK_OVERLAY_THICKNESS"] = str(self.thickness)
        env["JSPEAK_OVERLAY_REDUCE_MOTION"] = "1" if self.reduce_motion else "0"
        cmd = overlay_command()
        try:
            self.proc = subprocess.Popen(
                cmd, env=env, stdout=subprocess.DEVNULL,
            )
            log(f"overlay started (display={wd})")
        except Exception as e:
            log(f"overlay unavailable: {e}")
            self.proc = None

    def _send(self, sig):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.send_signal(sig)
            except Exception:
                pass

    def recording(self):
        if self.enabled and (self.proc is None or self.proc.poll() is not None):
            self.start()  # respawn if it died (e.g. compositor wasn't ready)
            time.sleep(0.15)
        self._send(signal.SIGUSR1)

    def processing(self):
        self._send(signal.SIGWINCH)

    def error(self):
        self._send(signal.SIGHUP)   # brief red flash, then auto-off

    def off(self):
        self._send(signal.SIGUSR2)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


# ----------------------------------------------------------------------------
# Recording (arecord raw PCM -> wrap as WAV, robust against kill timing)
# ----------------------------------------------------------------------------
class Recorder:
    def __init__(self, rate, trim=True, device=""):
        self.rate = rate
        self.trim = trim
        self.device = device or ""          # ALSA PCM name; "" = arecord default
        self.proc = None
        self.raw_path = None

    def start(self):
        if self.proc:                       # already running (pre-roll)
            return
        fd, self.raw_path = tempfile.mkstemp(suffix=".raw", prefix="jspeak_")
        os.close(fd)
        cmd = ["arecord", "-q", "-f", "S16_LE", "-c", "1",
               "-r", str(self.rate), "-t", "raw"]
        if self.device:
            cmd += ["-D", self.device]
        cmd.append(self.raw_path)
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def _read_raw(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        raw = b""
        if self.raw_path:
            try:
                with open(self.raw_path, "rb") as f:
                    raw = f.read()
            except OSError:
                pass
            try:
                os.unlink(self.raw_path)
            except OSError:
                pass
        self.proc = None
        self.raw_path = None
        return raw

    def discard(self):
        """Stop and throw away (aborted/too-short pre-roll)."""
        self._read_raw()

    def stop(self):
        """Stop recording and return (WAV bytes, ms) or (None, 0)."""
        raw = self._read_raw()
        if not raw:
            return None, 0
        if self.trim:
            raw = audio.trim_silence(raw, self.rate)
        ms = int(len(raw) / 2 / self.rate * 1000)
        buf = tempfile.SpooledTemporaryFile()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.rate)
            w.writeframes(raw)
        buf.seek(0)
        return buf.read(), ms


# ----------------------------------------------------------------------------
# Typing  (wtype handles all scripts incl. RTL Arabic/Hebrew; ydotool is
# ASCII-only; clipboard pastes anything but overwrites the clipboard)
# ----------------------------------------------------------------------------
def _wayland_env():
    env = os.environ.copy()
    wd = find_wayland_display()
    if wd:
        env["WAYLAND_DISPLAY"] = wd
    return env


def paste_text(text):
    """Insert everything at once via the clipboard, preserving the user's
    existing clipboard contents."""
    env = _wayland_env()
    try:
        saved = subprocess.run(["wl-paste", "--no-newline"], env=env,
                               capture_output=True, timeout=2).stdout
    except Exception:
        saved = None
    subprocess.run(["wl-copy"], input=text.encode(), env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # paste with Ctrl+V (keycodes: leftctrl=29, v=47)
    subprocess.run(["ydotool", "key", "29:1", "47:1", "47:0", "29:0"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.18)  # let the target consume the paste before we restore
    if saved:
        subprocess.run(["wl-copy"], input=saved, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def type_text(text, key_delay_ms, method="auto"):
    if not text:
        return
    if method == "auto":
        method = "wtype" if shutil.which("wtype") else "ydotool"

    if method == "wtype" and shutil.which("wtype"):
        subprocess.run(
            ["wtype", "-d", str(key_delay_ms), "--", text],
            env=_wayland_env(),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    elif method == "clipboard" and shutil.which("wl-copy"):
        paste_text(text)
    else:  # ydotool (ASCII / Latin only)
        subprocess.run(
            ["ydotool", "type", "--key-delay", str(key_delay_ms), "--", text],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


# ----------------------------------------------------------------------------
# Processing pipeline (runs in a worker thread so key loop stays responsive)
# ----------------------------------------------------------------------------
def process_audio(_cfg_unused, wav_bytes, dur_ms, overlay):
    cfg = load_config()                     # reload so settings apply live
    try:
        t0 = time.time()
        final = transcribe_and_clean(cfg, wav_bytes, log=log)
        if not final:
            overlay.off()
            return
        log(f"  typing: {final!r}  ({time.time()-t0:.1f}s, {dur_ms}ms audio)")
        type_text(final, cfg.get("key_delay_ms", 10),
                  method=cfg.get("type_method", "auto"))
        history.add(final)
        overlay.off()
    except urlerror.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        log(f"  HTTP {e.code} from Groq: {body}")
        overlay.error()
        notify.notify("JSpeak", groq_core.classify_error(e))
    except Exception as e:
        log(f"  ERROR: {e}")
        overlay.error()
        notify.notify("JSpeak", groq_core.classify_error(e))


# ----------------------------------------------------------------------------
# Keyboard listener / state machine
# ----------------------------------------------------------------------------
IDLE, ARMED, RECORDING, ABORTED = "IDLE", "ARMED", "RECORDING", "ABORTED"


class KeyListener:
    def __init__(self, cfg, overlay):
        self.cfg = cfg
        self.overlay = overlay
        self.min_hold = cfg.get("min_hold_ms", 300) / 1000.0
        self.min_record = cfg.get("min_record_ms", 350) / 1000.0
        self.cancel_on_esc = cfg.get("cancel_on_esc", True)
        self.spec = hotkeys.parse_spec(cfg)
        self.tracker = hotkeys.ChordTracker(self.spec)
        self.toggle = self.spec["toggle"]
        self.chord_label = hotkeys.describe(self.spec)
        self.devices = {}  # path -> fd
        self.state = IDLE
        self.armed_at = 0.0
        self.rec_started_at = 0.0
        self._was_satisfied = False
        self.recorder = Recorder(cfg.get("sample_rate", 16000),
                                 trim=cfg.get("trim_silence", True),
                                 device=cfg.get("input_device", ""))
        self._last_scan = 0.0

    # -- device management --------------------------------------------------
    def scan_devices(self):
        paths = set()
        for p in glob.glob("/dev/input/by-path/*kbd*"):
            try:
                paths.add(os.path.realpath(p))
            except OSError:
                pass
        # open new
        for path in paths - set(self.devices):
            try:
                self.devices[path] = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError as e:
                log(f"cannot open {path}: {e}")
        if not self.devices:
            log("WARNING: no keyboard devices found under /dev/input/by-path/*kbd*")

    def _drop(self, path):
        fd = self.devices.pop(path, None)
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    # -- state transitions --------------------------------------------------
    def begin_recording(self):
        # pre-roll already started at ARM; just promote to RECORDING.
        self.state = RECORDING
        log(f"recording... (release {self.chord_label} to stop)")
        self.overlay.recording()

    def toggle_start(self):
        self.recorder.start()
        self.rec_started_at = time.time()
        self.state = RECORDING
        log(f"recording... (press {self.chord_label} again to stop)")
        self.overlay.recording()

    def end_recording(self):
        held = time.time() - self.rec_started_at
        wav, dur_ms = self.recorder.stop()
        self.state = IDLE
        if not wav or held < self.min_record:
            log(f"  discarded (too short, {int(held*1000)}ms)")
            self.overlay.off()
            return
        self.overlay.processing()
        threading.Thread(
            target=process_audio,
            args=(self.cfg, wav, dur_ms, self.overlay), daemon=True
        ).start()

    def cancel_recording(self):
        """Throw away an armed/in-progress take without transcribing. In hold
        mode we drop to ABORTED so the eventual chord release doesn't re-fire;
        in toggle mode we go straight back to IDLE."""
        was = self.state
        self.recorder.discard()
        self.state = IDLE if self.toggle else ABORTED
        if self.toggle:
            self._was_satisfied = self.tracker.satisfied
        if was == RECORDING:
            log("  cancelled (Esc)")
        self.overlay.off()

    def handle_event(self, code, value):
        # value: 1=press, 0=release, 2=autorepeat
        if value == 2:
            return
        if (self.cancel_on_esc and value == 1 and code == ESC_CODE
                and self.state in (ARMED, RECORDING)):
            self.cancel_recording()
            return
        name = hotkeys.EVDEV_CODE_TO_NAME.get(code)
        is_press = value == 1
        relevant = self.tracker.set(name, is_press) if name else False

        if self.toggle:
            sat = self.tracker.satisfied
            if sat and not self._was_satisfied:        # rising edge of the chord
                if self.state == RECORDING:
                    self.end_recording()
                elif self.state == IDLE:
                    self.toggle_start()
            self._was_satisfied = sat
            return

        # ---- hold mode ----
        if relevant and is_press:
            if self.tracker.satisfied and self.state == IDLE:
                self.state = ARMED
                self.armed_at = time.time()
                self.recorder.start()              # pre-roll to avoid clipping
                self.rec_started_at = time.time()
        elif relevant and not is_press:            # a chord key released
            if not self.tracker.satisfied:
                if self.state == RECORDING:
                    self.end_recording()
                elif self.state in (ARMED, ABORTED):
                    self.state = IDLE
                    self.recorder.discard()
        elif is_press and not relevant:
            # a real other key during ARMED means this was a shortcut, not dictation
            if self.state == ARMED:
                self.state = ABORTED
                self.recorder.discard()

    # -- main loop ----------------------------------------------------------
    def run(self):
        self.scan_devices()
        if not self.devices:
            log("No keyboards to listen on. Are you in the 'input' group?")
            sys.exit(1)
        log(f"JSpeak ready. mode={self.cfg['mode']}  "
            f"models={MODES[self.cfg['mode']]}")
        verb = "Press" if self.toggle else "Hold"
        log(f"{verb} {self.chord_label} and speak."
            + ("  (Esc cancels)" if self.cancel_on_esc else ""))
        while True:
            timeout = None
            if self.state == ARMED:
                timeout = max(0.0, self.min_hold - (time.time() - self.armed_at))
            elif time.time() - self._last_scan > 3.0:
                timeout = 1.0  # periodic device rescan

            try:
                r, _, _ = select.select(list(self.devices.values()), [], [], timeout)
            except (ValueError, OSError):
                r = []

            for fd in r:
                path = next((p for p, f in self.devices.items() if f == fd), None)
                try:
                    data = os.read(fd, EVENT_SIZE * 64)
                except OSError:
                    if path:
                        self._drop(path)
                    continue
                for off in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
                    _, _, etype, code, value = struct.unpack(
                        EVENT_FMT, data[off:off + EVENT_SIZE])
                    if etype == EV_KEY:
                        self.handle_event(code, value)

            # arm -> record after threshold with the chord still held (hold mode)
            if not self.toggle and self.state == ARMED and self.tracker.satisfied \
                    and (time.time() - self.armed_at) >= self.min_hold:
                self.begin_recording()

            if time.time() - self._last_scan > 3.0:
                self.scan_devices()
                self._last_scan = time.time()


def maybe_first_run(cfg):
    """If no API key is configured, point the user at settings (and open it
    once on a desktop session) instead of failing silently."""
    if cfg.get("groq_api_key", "").strip():
        return
    log("No Groq API key set. Opening settings - paste your key from "
        "https://console.groq.com/keys")
    try:
        subprocess.Popen(settings_command(), env=_wayland_env(),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def main():
    cfg = load_config()
    log(f"JSpeak v{version.__version__}  (config: {CONFIG_PATH})")
    updater.check_in_background()
    overlay = Overlay(cfg)
    overlay.start()
    maybe_first_run(cfg)
    try:
        KeyListener(cfg, overlay).run()
    except KeyboardInterrupt:
        log("bye")
    finally:
        overlay.stop()


if __name__ == "__main__":
    main()
