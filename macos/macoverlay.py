#!/usr/bin/env python3
"""JSpeak screen-edge glow overlay for macOS (AppKit / PyObjC).

A soft purple glow painted around the screen edges, matching the Linux GTK and
Windows GDI overlays. One borderless, transparent, click-through window sits at
the screen-saver window level and covers the whole screen; a custom NSView
paints a four-stop gradient (luminous lip -> base colour -> halo -> transparent)
along each edge with NSGradient. Because the window ignores mouse events it can
cover the screen without ever blocking clicks or stealing focus.

The gradient is drawn once per state (base / error); each animation frame only
changes the window's global alphaValue, so the breathing pulse is essentially
free - exactly like the SourceConstantAlpha trick on Windows. State is driven by
single-line commands on stdin from the daemon: rec / proc / error / off / quit.
The process uses the "prohibited" activation policy so it has no Dock icon and
can never become the active app.
"""
import math
import os
import sys
import threading
import time

from AppKit import (
    NSApplication, NSApplicationActivationPolicyProhibited, NSBackingStoreBuffered,
    NSColor, NSColorSpace, NSGradient, NSScreen, NSView, NSWindow,
    NSWindowStyleMaskBorderless, NSScreenSaverWindowLevel,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorStationary, NSWindowCollectionBehaviorIgnoresCycle,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
)
from Foundation import NSObject, NSTimer
import objc

import glowgradient

# command name -> animation target alpha (mirrors winoverlay/overlay)
TARGETS = {"off": 0.0, "rec": 1.0, "proc": 0.5, "error": 0.9}
FPS_INTERVAL = 1.0 / 30.0          # ~30fps; smooth glow, light on the CPU
REDRAW_EPS = 0.006                 # skip frames whose alpha barely changed
ERROR_COLOR = (229, 70, 66)


def _hex_rgb(s, default=(168, 85, 247)):
    try:
        s = (s or "").lstrip("#")
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except Exception:
        return default


COLOR = _hex_rgb(os.environ.get("JSPEAK_OVERLAY_COLOR", "#a855f7"))
MAX_ALPHA = float(os.environ.get("JSPEAK_OVERLAY_ALPHA", "0.55"))
THICK = int(os.environ.get("JSPEAK_OVERLAY_THICKNESS", "110"))
REDUCE_MOTION = os.environ.get("JSPEAK_OVERLAY_REDUCE_MOTION", "0") == "1"

# NSGradient.drawInRect_angle_ angles so the luminous lip sits on the screen
# edge and fades inward: 0=first colour at left, 90=bottom, 180=right, 270=top.
EDGE_ANGLE = {"top": 270.0, "bottom": 90.0, "left": 0.0, "right": 180.0}


def _build_gradient(base_rgb):
    """An NSGradient from the shared four-stop glow definition."""
    cols, alphas = glowgradient.stop_colors(base_rgb, MAX_ALPHA)
    colors = [
        NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a)
        for (r, g, b), a in zip(cols, alphas)
    ]
    locations = [float(x) for x in glowgradient.STOP_LOCATIONS]
    return NSGradient.alloc().initWithColors_atLocations_colorSpace_(
        colors, locations, NSColorSpace.genericRGBColorSpace())


class GlowView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(GlowView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._base = _build_gradient(COLOR)
        self._err = _build_gradient(ERROR_COLOR)
        self._error = False
        return self

    def setError_(self, error):
        if error != self._error:
            self._error = bool(error)
            self.setNeedsDisplay_(True)

    def drawRect_(self, _dirty):
        bounds = self.bounds()
        w = bounds.size.width
        h = bounds.size.height
        t = max(8.0, min(float(THICK), w / 2.0, h / 2.0))
        grad = self._err if self._error else self._base
        rects = {
            "top": ((0.0, h - t), (w, t)),
            "bottom": ((0.0, 0.0), (w, t)),
            "left": ((0.0, 0.0), (t, h)),
            "right": ((w - t, 0.0), (t, h)),
        }
        for edge, rect in rects.items():
            grad.drawInRect_angle_(rect, EDGE_ANGLE[edge])


class Controller(NSObject):
    def init(self):
        self = objc.super(Controller, self).init()
        if self is None:
            return None
        screen = NSScreen.mainScreen()
        frame = screen.frame()
        style = NSWindowStyleMaskBorderless
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, style, NSBackingStoreBuffered, False)
        win.setOpaque_(False)
        win.setBackgroundColor_(NSColor.clearColor())
        win.setLevel_(NSScreenSaverWindowLevel)
        win.setIgnoresMouseEvents_(True)               # click-through
        win.setHasShadow_(False)
        win.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorIgnoresCycle
            | NSWindowCollectionBehaviorFullScreenAuxiliary)
        win.setAlphaValue_(0.0)
        view = GlowView.alloc().initWithFrame_(
            ((0.0, 0.0), (frame.size.width, frame.size.height)))
        win.setContentView_(view)
        self.window = win
        self.view = view
        self.shown = False

        self.state = "off"
        self.level = 0.0
        self.phase = 0.0
        self.alpha = 0.0
        self._last_alpha = -1.0
        self.last = time.monotonic()
        self.error_until = 0.0
        self.timer = None
        return self

    # --- timer control ---
    def ensure_running(self):
        if self.timer is None:
            self.last = time.monotonic()
            self.timer = (
                NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    FPS_INTERVAL, self, b"tick:", None, True))

    def stop_timer(self):
        if self.timer is not None:
            self.timer.invalidate()
            self.timer = None

    def _show(self):
        if not self.shown:
            self.window.orderFrontRegardless()
            self.shown = True

    def _hide(self):
        if self.shown:
            self.window.orderOut_(None)
            self.shown = False

    # --- commands (always delivered on the main thread) ---
    def handleCommand_(self, cmd):
        cmd = str(cmd)
        if cmd == "quit":
            self.stop_timer()
            NSApplication.sharedApplication().terminate_(None)
            return
        if cmd == "error":
            self.error_until = time.monotonic() + 0.85
        self.view.setError_(cmd == "error")
        if cmd in TARGETS:
            self.state = cmd
            if cmd != "off":
                self._show()
            self.ensure_running()

    def tick_(self, _timer):
        now = time.monotonic()
        dt = min(0.06, now - self.last)
        self.last = now
        self.phase += dt

        if self.state == "error" and now >= self.error_until:
            self.state = "off"
            self.view.setError_(False)

        target = TARGETS[self.state]
        tau = 0.22 if target > self.level else 0.16
        self.level += (target - self.level) * min(1.0, dt / tau)

        animated = self.state == "error" or (
            self.state in ("rec", "proc") and not REDUCE_MOTION)
        if not animated:
            pulse = 1.0
        elif self.state == "rec":
            p = self.phase
            pulse = (0.82 + 0.13 * math.sin(2 * math.pi * p / 1.9)
                     + 0.05 * math.sin(2 * math.pi * p / 0.83))
        elif self.state == "proc":
            p = self.phase
            pulse = (0.80 + 0.14 * math.sin(2 * math.pi * p / 1.05)
                     + 0.04 * math.sin(2 * math.pi * p / 0.47))
        else:  # error
            pulse = 0.68 + 0.32 * math.sin(2 * math.pi * self.phase / 0.32)

        self.alpha = max(0.0, min(1.0, self.level * pulse))
        if abs(self.alpha - self._last_alpha) > REDRAW_EPS:
            self.window.setAlphaValue_(self.alpha)
            self._last_alpha = self.alpha

        if self.state == "off" and self.level < 0.002:
            self.window.setAlphaValue_(0.0)
            self._hide()
            self._last_alpha = 0.0
            self.stop_timer()
            return
        if not animated and abs(self.level - target) < 0.0015:
            self.stop_timer()          # static hold: stop until next state change


def _stdin_reader(controller):
    for raw in sys.stdin:
        cmd = raw.strip()
        if cmd:
            controller.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"handleCommand:", cmd, False)


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyProhibited)
    controller = Controller.alloc().init()
    threading.Thread(target=_stdin_reader, args=(controller,), daemon=True).start()
    app.run()


if __name__ == "__main__":
    main()
