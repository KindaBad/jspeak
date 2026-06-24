#!/usr/bin/env python3
"""JSpeak screen-edge glow overlay (Wayland layer-shell, GTK3).

A persistent, focus-less overlay that paints a soft glow around the screen
edges. Driven by Unix signals from the main jspeak process:

    SIGUSR1  -> recording   (bright, gentle breathing pulse)
    SIGWINCH -> processing  (dim, faster subtle pulse = "working")
    SIGUSR2  -> off         (fade out)
    SIGHUP   -> error        (brief red flash, then off)
    SIGTERM  -> quit

Performance: the glow gradient is rendered ONCE into a cached image surface; each
animation frame is just a clipped blit of that surface at the current alpha (no
per-frame blur, no shadow rasterization), capped at ~24fps. With reduce_motion the
glow holds completely static, so there is no per-frame work at all while
recording. This keeps it light enough for low-end hardware.

KeyboardMode.NONE means it never takes keyboard focus; an empty pointer input
region makes it click-through; and it is only mapped while visible so it can
never block the mouse while idle.
"""
import math
import os
import signal
import sys
import time
import warnings

warnings.filterwarnings("ignore")

try:
    import cairo
except Exception as e:  # pragma: no cover
    sys.stderr.write(f"[overlay] pycairo unavailable, glow disabled: {e}\n")
    sys.exit(0)

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gtk, GtkLayerShell, GLib  # noqa: E402


def hex_rgb(s, default=(168, 85, 247)):
    try:
        s = s.lstrip("#")
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except Exception:
        return default


R, G, B = hex_rgb(os.environ.get("JSPEAK_OVERLAY_COLOR", "#a855f7"))
MAX_ALPHA = float(os.environ.get("JSPEAK_OVERLAY_ALPHA", "0.55"))
THICKNESS = int(os.environ.get("JSPEAK_OVERLAY_THICKNESS", "110"))
REDUCE_MOTION = os.environ.get("JSPEAK_OVERLAY_REDUCE_MOTION", "0") == "1"
FPS_INTERVAL = 42  # ~24fps; smooth enough for a slow glow, light on the CPU
REDRAW_EPS = 0.006  # skip frames whose alpha barely changed

TARGETS = {"off": 0.0, "rec": 1.0, "proc": 0.5, "error": 0.9}

BASE_F = (R / 255, G / 255, B / 255)
ERROR_F = (229 / 255, 70 / 255, 66 / 255)


def _mixf(c1, c2, t):
    return tuple(a + (b - a) * t for a, b in zip(c1, c2))


def build_glow_surface(w, h, depth, base, max_alpha):
    """Bake the edge glow once. A per-edge gradient (luminous lip -> base ->
    deep halo -> transparent); overlapping corners read as a soft vignette."""
    core = _mixf(base, (1.0, 1.0, 1.0), 0.45)
    halo = _mixf(base, (0.11, 0.055, 0.235), 0.45)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)

    def edge(x0, y0, x1, y1, rx, ry, rw, rh):
        g = cairo.LinearGradient(x0, y0, x1, y1)
        g.add_color_stop_rgba(0.00, *core, min(1.0, max_alpha * 1.15))
        g.add_color_stop_rgba(0.16, *base, max_alpha * 0.72)
        g.add_color_stop_rgba(0.45, *halo, max_alpha * 0.34)
        g.add_color_stop_rgba(1.00, *halo, 0.0)
        cr.save()
        cr.rectangle(rx, ry, rw, rh)
        cr.clip()
        cr.set_source(g)
        cr.paint()
        cr.restore()

    edge(0, 0, 0, depth, 0, 0, w, depth)                 # top
    edge(0, h, 0, h - depth, 0, h - depth, w, depth)     # bottom
    edge(0, 0, depth, 0, 0, 0, depth, h)                 # left
    edge(w, 0, w - depth, 0, w - depth, 0, depth, h)     # right
    surf.flush()
    return surf


class Glow:
    def __init__(self):
        self.state = "off"
        self.level = 0.0
        self.phase = 0.0
        self.alpha = 0.0
        self._last_alpha = -1.0
        self.last = time.monotonic()
        self.tick_id = None
        self._cache = {}          # base color -> (surface, (w,h))
        self.depth = 0

        win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        win.set_app_paintable(True)
        win.set_decorated(False)
        visual = win.get_screen().get_rgba_visual()
        if visual is not None:
            win.set_visual(visual)
        win.connect("draw", self.on_draw)
        win.connect("realize", self.apply_click_through)
        win.connect("map-event", lambda *_: (self.apply_click_through(win), False)[1])
        win.connect("size-allocate", lambda *_: self.apply_click_through(win))

        GtkLayerShell.init_for_window(win)
        GtkLayerShell.set_namespace(win, "jspeak-overlay")
        GtkLayerShell.set_layer(win, GtkLayerShell.Layer.OVERLAY)
        for edge in (GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT,
                     GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM):
            GtkLayerShell.set_anchor(win, edge, True)
        GtkLayerShell.set_exclusive_zone(win, -1)
        GtkLayerShell.set_keyboard_mode(win, GtkLayerShell.KeyboardMode.NONE)
        self.win = win
        # Mapped only while visible (see set_state) so it never blocks the mouse.

    def apply_click_through(self, widget):
        try:
            widget.input_shape_combine_region(cairo.Region())
        except Exception as e:
            sys.stderr.write(f"[overlay] click-through unavailable: {e}\n")

    def surface_for(self, w, h):
        key = "error" if self.state == "error" else "base"
        cached = self._cache.get(key)
        if cached and cached[1] == (w, h):
            return cached[0]
        self.depth = min(int(THICKNESS * 1.25), w // 2, h // 2)
        base = ERROR_F if key == "error" else BASE_F
        surf = build_glow_surface(w, h, self.depth, base, MAX_ALPHA)
        self._cache[key] = (surf, (w, h))
        return surf

    def on_draw(self, widget, cr):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        surf = self.surface_for(w, h)
        d = self.depth
        # only ever touch the four edge bands; the center stays untouched
        cr.rectangle(0, 0, w, d)
        cr.rectangle(0, h - d, w, d)
        cr.rectangle(0, 0, d, h)
        cr.rectangle(w - d, 0, d, h)
        cr.clip()
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()                       # clear the bands
        if self.alpha > 0.003:
            cr.set_operator(cairo.OPERATOR_OVER)
            cr.set_source_surface(surf, 0, 0)
            cr.paint_with_alpha(self.alpha)
        return False

    def set_state(self, state):
        if state == "error":
            GLib.timeout_add(850, lambda: (self.set_state("off"), False)[1])
        self.state = state
        if state != "off" and not self.win.get_mapped():
            self.win.show()
            self.apply_click_through(self.win)
        if self.tick_id is None:
            self.last = time.monotonic()
            self.tick_id = GLib.timeout_add(FPS_INTERVAL, self.tick)

    def tick(self):
        now = time.monotonic()
        dt = min(0.06, now - self.last)
        self.last = now
        self.phase += dt

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
            self.win.queue_draw()
            self._last_alpha = self.alpha

        if self.state == "off" and self.level < 0.002:
            self.win.hide()             # unmap entirely -> cannot block the mouse
            self.tick_id = None
            return False
        if not animated and abs(self.level - target) < 0.0015:
            self.tick_id = None         # static hold: stop until next state change
            return False
        return True


def main():
    glow = Glow()
    for sig, st in ((signal.SIGUSR1, "rec"), (signal.SIGWINCH, "proc"),
                    (signal.SIGUSR2, "off"), (signal.SIGHUP, "error")):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig,
                             lambda s=st: (glow.set_state(s) or True))
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM,
                         lambda: (Gtk.main_quit() or True))
    Gtk.main()


if __name__ == "__main__":
    main()
