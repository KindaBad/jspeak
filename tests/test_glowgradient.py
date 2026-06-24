import glowgradient as gg


def _px(buf, idx):
    o = idx * 4
    return tuple(buf[o:o + 4])   # B, G, R, A


def test_buffer_size_matches_strip():
    buf = gg.bgra_premul(200, 110, "top", (168, 85, 247), 0.55)
    assert len(buf) == 200 * 110 * 4


def test_premultiplied_invariant_and_transparent_inner_edge():
    w, h = 200, 110
    buf = gg.bgra_premul(w, h, "top", (168, 85, 247), 0.55)
    edge = _px(buf, 0)                      # row 0, col 0 = screen edge
    inner = _px(buf, (h - 1) * w)           # last row, col 0 = inner edge
    b, g, r, a = edge
    assert b <= a and g <= a and r <= a     # required by AC_SRC_ALPHA
    assert a > 0                            # luminous lip
    assert inner == (0, 0, 0, 0)            # fully transparent inner edge


def test_right_orientation_edge_on_last_column():
    w, h = 110, 200
    buf = gg.bgra_premul(w, h, "right", (168, 85, 247), 0.55)
    inner = _px(buf, 0)                     # col 0 = inner
    edge = _px(buf, w - 1)                  # last col = screen edge
    assert inner == (0, 0, 0, 0)
    assert edge[3] > 0


def test_bad_orient_raises():
    import pytest
    with pytest.raises(ValueError):
        gg.bgra_premul(10, 10, "diagonal", (1, 2, 3), 0.5)
