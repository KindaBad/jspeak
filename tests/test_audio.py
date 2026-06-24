import array

import audio


def _pcm(values):
    return array.array("h", values).tobytes()


def test_trim_removes_silent_head_and_tail():
    rate = 16000
    silence = [0] * rate                      # 1s silence each side
    loud = [8000, -8000] * (rate // 2)        # 1s loud
    pcm = _pcm(silence + loud + silence)
    out = audio.trim_silence(pcm, rate, pad_ms=50)
    assert len(out) < len(pcm)                # trimmed
    # keeps the loud part plus small padding, well under the full clip
    assert len(out) <= len(_pcm(loud)) + _pcm([0] * (rate // 5)).__len__()


def test_trim_all_silence_returns_unchanged():
    rate = 16000
    pcm = _pcm([0] * rate)
    assert audio.trim_silence(pcm, rate) == pcm


def test_trim_empty_returns_unchanged():
    assert audio.trim_silence(b"", 16000) == b""


def test_trim_never_raises_on_garbage():
    # odd byte count can't form int16 frames cleanly; must not raise
    assert isinstance(audio.trim_silence(b"\x01\x02\x03", 16000), bytes)
