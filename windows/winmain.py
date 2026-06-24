#!/usr/bin/env python3
"""JSpeak dictation daemon for Windows.

Same behavior as the Linux daemon (hold Ctrl+Shift -> record -> Groq transcribe
+ clean -> type into the focused app), implemented with cross-platform/Windows
APIs: pynput for the global hotkey + typing, sounddevice for audio, a Win32
click-through Tk overlay (separate process, controlled over a pipe).
"""
import ctypes
import io
import os
import subprocess
import sys
import threading
import time
import wave

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
# Clipboard (Win32) for paste-all-at-once
# ---------------------------------------------------------------------------
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def _clip_get():
    u, k = _u32(), _k32()
    if not u.OpenClipboard(None):
        return None
    try:
        h = u.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = k.GlobalLock(h)
        if not p:                       # NULL on a truncated/invalid handle
            return None
        try:
            return ctypes.c_wchar_p(p).value
        finally:
            k.GlobalUnlock(h)
    finally:
        u.CloseClipboard()


def _clip_set(text):
    u, k = _u32(), _k32()
    if not u.OpenClipboard(None):
        return
    try:
        u.EmptyClipboard()
        data = (text + "\0").encode("utf-16-le")
        # GlobalAlloc/GlobalLock return pointer-sized handles. They MUST have
        # restype set (see _k32) - otherwise ctypes truncates them to 32 bits on
        # Win64, GlobalLock then returns NULL, and memmove(NULL, ...) faults with
        # exactly the "access violation writing 0x0" this app kept crashing on.
        h = k.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            return
        p = k.GlobalLock(h)
        if not p:
            return
        ctypes.memmove(p, data, len(data))
        k.GlobalUnlock(h)
        u.SetClipboardData(CF_UNICODETEXT, h)
    finally:
        u.CloseClipboard()


# ---------------------------------------------------------------------------
# Native keyboard input (Win32 SendInput)
#
# Text is injected through our own 64-bit-clean SendInput rather than a
# third-party library. pynput's Controller.type() crashed here with an
# "access violation writing 0x0000000000000000" on Win64 (the INPUT pointer was
# truncated to 32 bits because SendInput's argtypes were unset), which made all
# typing fail in 1.4.0-beta.
#
# 1.4.1 set argtypes on ctypes.windll.user32.SendInput, but the crash came back
# in the real daemon: pynput is loaded for the global hotkey listener, and it
# binds SendInput on the *same* process-global windll.user32 with its argtypes
# unset - clobbering our signature out from under us so the INPUT* truncated
# again. (The unit test passed because it never imports pynput.) The robust fix
# is to own *private* WinDLL handles (_u32/_k32) that pynput cannot touch, and
# set argtypes/restype on those. Same reasoning fixes the clipboard handles.
# ---------------------------------------------------------------------------
from ctypes import wintypes

_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_VK_RETURN = 0x0D
_VK_CONTROL = 0x11
_VK_V = 0x56
_ULONG_PTR = ctypes.c_size_t        # pointer-sized: the field pynput mistyped


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", _ULONG_PTR)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", _ULONG_PTR)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    # Full union so sizeof(_INPUT) matches what SendInput expects (MOUSEINPUT is
    # the largest member); a short union would make cbSize wrong.
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


# Private library handles, resolved lazily so this module still imports on any
# OS. These are deliberately separate WinDLL instances from the process-global
# ctypes.windll.* that pynput uses, so pynput can't reset the function
# signatures we depend on.
_user32 = None
_kernel32 = None


def _u32():
    """user32 with every function we call given explicit pointer-clean
    argtypes/restype - the fix for the truncated-handle crashes on Win64."""
    global _user32
    if _user32 is None:
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT),
                                ctypes.c_int)
        u.SendInput.restype = wintypes.UINT
        u.OpenClipboard.argtypes = (wintypes.HWND,)
        u.OpenClipboard.restype = wintypes.BOOL
        u.CloseClipboard.argtypes = ()
        u.CloseClipboard.restype = wintypes.BOOL
        u.EmptyClipboard.argtypes = ()
        u.EmptyClipboard.restype = wintypes.BOOL
        u.GetClipboardData.argtypes = (wintypes.UINT,)
        u.GetClipboardData.restype = wintypes.HANDLE
        u.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
        u.SetClipboardData.restype = wintypes.HANDLE
        _user32 = u
    return _user32


def _k32():
    """kernel32 with GlobalAlloc/Lock/Unlock given pointer-sized restypes so
    their handles aren't truncated to 32 bits on Win64."""
    global _kernel32
    if _kernel32 is None:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
        k.GlobalAlloc.restype = ctypes.c_void_p
        k.GlobalLock.argtypes = (ctypes.c_void_p,)
        k.GlobalLock.restype = ctypes.c_void_p
        k.GlobalUnlock.argtypes = (ctypes.c_void_p,)
        k.GlobalUnlock.restype = wintypes.BOOL
        _kernel32 = k
    return _kernel32


def _key_event(vk=0, scan=0, flags=0):
    inp = _INPUT(type=_INPUT_KEYBOARD)
    inp.u.ki = _KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0,
                           dwExtraInfo=0)
    return inp


def _send(events):
    """Inject the events; returns how many SendInput actually inserted (0 means
    it failed - which is what the truncated-pointer bug produced)."""
    n = len(events)
    if not n:
        return 0
    arr = (_INPUT * n)(*events)
    return _u32().SendInput(n, arr, ctypes.sizeof(_INPUT))


def _unicode_events(ch):
    """Key down+up KEYEVENTF_UNICODE events for one character. Code points above
    the BMP are emitted as a UTF-16 surrogate pair."""
    code = ord(ch)
    units = ([0xD800 + ((code - 0x10000) >> 10),
              0xDC00 + ((code - 0x10000) & 0x3FF)]
             if code > 0xFFFF else [code])
    events = []
    for u in units:
        events.append(_key_event(scan=u, flags=_KEYEVENTF_UNICODE))
        events.append(_key_event(scan=u,
                                 flags=_KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP))
    return events


def _type_char(ch):
    # Enter for newlines (Unicode-injected newlines are unreliable across apps);
    # Unicode SendInput for everything else - any script, no layout dependence.
    # Returns how many events SendInput inserted (0 == nothing went in).
    if ch in ("\n", "\r"):
        return _send([_key_event(vk=_VK_RETURN),
                      _key_event(vk=_VK_RETURN, flags=_KEYEVENTF_KEYUP)])
    return _send(_unicode_events(ch))


def _paste():
    """Ctrl+V via SendInput (Ctrl down, V down, V up, Ctrl up)."""
    _send([_key_event(vk=_VK_CONTROL),
           _key_event(vk=_VK_V),
           _key_event(vk=_VK_V, flags=_KEYEVENTF_KEYUP),
           _key_event(vk=_VK_CONTROL, flags=_KEYEVENTF_KEYUP)])


def _paste_via_clipboard(text):
    """Put text on the clipboard, Ctrl+V it, then restore the prior clipboard."""
    saved = _clip_get()
    _clip_set(text)
    _paste()
    time.sleep(0.18)
    if saved is not None:
        _clip_set(saved)


# ---------------------------------------------------------------------------
# Typing
# ---------------------------------------------------------------------------
def type_text(text, key_delay_ms, method):
    if not text:
        return
    if method == "clipboard":
        _paste_via_clipboard(text)
        return
    # per-letter (handles any Unicode incl. RTL via SendInput unicode)
    delay = max(0, key_delay_ms) / 1000.0
    for i, ch in enumerate(text):
        sent = _type_char(ch)
        # If the very first character injects nothing, key injection is blocked
        # (focus race, secure desktop, ...). Salvage the dictation with one
        # clipboard paste instead of silently dropping it.
        if sent == 0 and i == 0:
            _paste_via_clipboard(text)
            return
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
        flags = 0x08000000  # CREATE_NO_WINDOW
        try:
            self.proc = subprocess.Popen(
                [sys.executable, "--overlay"], stdin=subprocess.PIPE,
                env=env, creationflags=flags)
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
        self._modmap = self._build_modmap(keyboard.Key)

    @staticmethod
    def _build_modmap(K):
        pairs = {
            "ctrl": ("ctrl", "ctrl_l", "ctrl_r"),
            "shift": ("shift", "shift_l", "shift_r"),
            "alt": ("alt", "alt_l", "alt_r", "alt_gr"),
            "super": ("cmd", "cmd_l", "cmd_r"),
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
                      cfg.get("type_method", "auto"))
            history.add(final)
            self.overlay.off()
        except Exception as e:
            log(f"  ERROR: {e}")
            self.overlay.error()
            notify.notify("JSpeak", groq_core.classify_error(e))

    def run(self):
        log(f"JSpeak v{version.__version__} ready (Windows). "
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
        subprocess.Popen([sys.executable, "--settings"],
                         creationflags=0x08000000)
    except Exception:
        pass


def ensure_autostart():
    """Register the app to launch on login (HKCU Run key). Idempotent."""
    try:
        import winreg
        exe = sys.executable
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run",
                            0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "JSpeak", 0, winreg.REG_SZ, f'"{exe}"')
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

    try:
        import wintray
        wintray.start(quit_app)
    except Exception as e:
        log(f"tray unavailable: {e}")

    daemon.run()


if __name__ == "__main__":
    main()
