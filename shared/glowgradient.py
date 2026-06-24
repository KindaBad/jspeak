"""Pure gradient math for the Windows screen-edge glow.

Kept free of any Win32/ctypes imports so it can be unit-tested on any platform.
Produces top-down premultiplied BGRA bytes for one edge strip, matching the
luminous-lip -> base -> halo -> transparent falloff of the Linux cairo overlay.
"""
import numpy as np

ORIENTS = ("top", "bottom", "left", "right")


def _mix(c1, c2, t):
    return tuple(a + (b - a) * t for a, b in zip(c1, c2))


# Locations (0..1 from the luminous lip inward) of the four gradient stops,
# shared by every backend so the glow looks identical on Linux/Windows/macOS.
STOP_LOCATIONS = (0.00, 0.16, 0.45, 1.00)


def stop_colors(base_rgb, max_alpha):
    """The four glow gradient stops as (rgb 0..1, alpha 0..1) at STOP_LOCATIONS:
    a luminous lip -> the base colour -> a dim halo -> transparent. Pure math so
    it can drive the cairo (Linux), GDI (Windows) and AppKit (macOS) overlays
    and be unit-tested anywhere."""
    base = (base_rgb[0] / 255.0, base_rgb[1] / 255.0, base_rgb[2] / 255.0)
    core = _mix(base, (1.0, 1.0, 1.0), 0.45)
    halo = _mix(base, (0.11, 0.055, 0.235), 0.45)
    cols = (core, base, halo, halo)
    alphas = (min(1.0, max_alpha * 1.15), max_alpha * 0.72, max_alpha * 0.34, 0.0)
    return cols, alphas


def gradient_line(depth, base_rgb, max_alpha):
    """Straight (non-premultiplied) RGBA in 0..1, sampled across `depth` steps
    from the luminous screen-edge lip (t=0) to the transparent inner edge."""
    cols, alphas = stop_colors(base_rgb, max_alpha)
    stops = np.array(STOP_LOCATIONS)
    cols = np.array(cols)
    alphas = np.array(alphas)
    t = np.linspace(0.0, 1.0, max(2, depth))
    r = np.interp(t, stops, cols[:, 0])
    g = np.interp(t, stops, cols[:, 1])
    b = np.interp(t, stops, cols[:, 2])
    a = np.interp(t, stops, alphas)
    return r, g, b, a


def bgra_premul(w, h, orient, base_rgb, max_alpha):
    """Top-down premultiplied BGRA bytes for one edge strip (size w x h)."""
    if orient not in ORIENTS:
        raise ValueError(f"bad orient: {orient}")
    depth = h if orient in ("top", "bottom") else w
    r, g, b, a = gradient_line(depth, base_rgb, max_alpha)
    B = (b * a * 255.0)
    G = (g * a * 255.0)
    R = (r * a * 255.0)
    A = (a * 255.0)
    line = np.stack([B, G, R, A], axis=1).clip(0, 255).astype(np.uint8)
    if orient == "top":
        img = np.repeat(line[:, None, :], w, axis=1)
    elif orient == "bottom":
        img = np.repeat(line[::-1][:, None, :], w, axis=1)
    elif orient == "left":
        img = np.repeat(line[None, :, :], h, axis=0)
    else:  # right
        img = np.repeat(line[::-1][None, :, :], h, axis=0)
    return np.ascontiguousarray(img, dtype=np.uint8).tobytes()
