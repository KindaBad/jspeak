"""Buffer-slicing math for the always-on ContinuousRecorder (Linux daemon).

These exercise the ring-buffer logic directly by feeding bytes in-process, so
no `arecord` subprocess is spawned.
"""
import io
import wave

import pytest

jspeak = pytest.importorskip("jspeak")
ContinuousRecorder = jspeak.ContinuousRecorder


def _feed(rec, data):
    """Mimic the reader thread appending a chunk under the lock."""
    with rec._lock:
        rec._buf += data
        rec._total += len(data)
        rec._trim_locked()


def _wav_pcm(wav_bytes):
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        return w.readframes(w.getnframes())


def test_stop_returns_whole_take_when_buffer_shorter_than_preroll():
    rec = ContinuousRecorder(16000, trim=False)
    _feed(rec, b"\x01\x00" * 500)        # 1000 bytes, < preroll
    rec.start()                          # mark clamps to start of buffer
    _feed(rec, b"\x02\x00" * 1000)       # 2000 more
    wav, ms = rec.stop()
    assert len(_wav_pcm(wav)) == 3000
    assert ms == int(3000 / 2 / 16000 * 1000)


def test_start_includes_preroll_before_keypress():
    rec = ContinuousRecorder(16000, trim=False)
    _feed(rec, b"\x00\x00" * 5000)       # 10000 bytes already buffered
    rec.start()                          # preroll = 6400 bytes -> mark at 3600
    _feed(rec, b"\x00\x00" * 1000)       # 2000 bytes after the press
    wav, _ = rec.stop()
    assert len(_wav_pcm(wav)) == 6400 + 2000


def test_trim_never_drops_data_after_an_active_mark():
    rec = ContinuousRecorder(16000, trim=False)
    rec._max_bytes = 4000                # tiny cap to force trimming
    _feed(rec, b"\x00\x00" * 1500)       # 3000 bytes
    rec.start()                          # mark at 0
    _feed(rec, b"\x00\x00" * 1500)       # 6000 total, over the cap
    wav, _ = rec.stop()
    assert len(_wav_pcm(wav)) == 6000    # marked audio survives the cap


def test_trim_caps_buffer_when_no_active_mark():
    rec = ContinuousRecorder(16000, trim=False)
    rec._max_bytes = 4000
    _feed(rec, b"\x00\x00" * 3000)       # 6000 bytes, no mark
    assert len(rec._buf) == 4000         # oldest 2000 dropped
    assert rec._total == 6000


def test_stop_without_start_returns_nothing():
    rec = ContinuousRecorder(16000, trim=False)
    _feed(rec, b"\x00\x00" * 1000)
    assert rec.stop() == (None, 0)


def test_discard_clears_the_mark():
    rec = ContinuousRecorder(16000, trim=False)
    _feed(rec, b"\x00\x00" * 1000)
    rec.start()
    rec.discard()
    assert rec.stop() == (None, 0)
