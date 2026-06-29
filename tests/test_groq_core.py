import array
import io
import socket
import wave
from urllib import error as urlerror

import pytest

import groq_core as gc


def _wav(amplitude, ms=500, rate=16000):
    """A mono 16-bit WAV whose samples alternate +/-amplitude."""
    n = int(rate * ms / 1000)
    samples = array.array("h", [amplitude if i % 2 else -amplitude
                                for i in range(n)])
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


# ---- apply_replacements ----
def test_apply_replacements_is_case_insensitive_whole_word():
    out = gc.apply_replacements("i love Groq and grOQ", {"groq": "Groq"})
    assert out == "i love Groq and Groq"


def test_apply_replacements_respects_word_boundaries():
    assert gc.apply_replacements("scattergun", {"cat": "dog"}) == "scattergun"


# ---- apply_voice_commands ----
def test_voice_new_line_and_paragraph():
    out = gc.apply_voice_commands("line one new line line two")
    assert out == "line one\nline two"
    out = gc.apply_voice_commands("para one new paragraph para two")
    assert out == "para one\n\npara two"


def test_voice_scratch_that_removes_previous_sentence():
    out = gc.apply_voice_commands("Keep this. Drop this scratch that")
    assert "Drop this" not in out
    assert out.startswith("Keep this.")


def test_voice_custom_mapping_and_disabled():
    out = gc.apply_voice_commands("say smiley now", mapping={"smiley": ":)"})
    assert ":)" in out
    assert gc.apply_voice_commands("new line", enabled=False) == "new line"


# ---- _unwrap_model_output ----
def test_unwrap_strips_surrounding_quotes():
    assert gc._unwrap_model_output('"Hello there."') == "Hello there."
    assert gc._unwrap_model_output("'yo'") == "yo"


def test_unwrap_strips_fenced_block_with_language_tag():
    assert gc._unwrap_model_output("```text\nHello there.\n```") == "Hello there."
    assert gc._unwrap_model_output("```\njust text\n```") == "just text"


def test_unwrap_leaves_clean_text_and_inner_quotes_untouched():
    assert gc._unwrap_model_output("Hello there.") == "Hello there."
    # a quote that is not wrapping the whole string must survive
    assert gc._unwrap_model_output('He said "hi" loudly') == 'He said "hi" loudly'


# ---- transcribe_and_clean pipeline ----
def _cfg(**over):
    base = {"groq_api_key": "k", "mode": "quick", "append_space": False,
            "cleanup_enabled": True, "voice_commands": {"enabled": True}}
    base.update(over)
    return base


def test_pipeline_runs_cleanup(monkeypatch):
    monkeypatch.setattr(gc, "transcribe", lambda *a, **k: "um hello there")
    monkeypatch.setattr(gc, "cleanup", lambda *a, **k: "Hello there.")
    assert gc.transcribe_and_clean(_cfg(), b"wav") == "Hello there."


def test_pipeline_skips_cleanup_fast_path(monkeypatch):
    monkeypatch.setattr(gc, "transcribe", lambda *a, **k: "raw words")

    def _boom(*a, **k):
        raise AssertionError("cleanup must not be called on the fast path")

    monkeypatch.setattr(gc, "cleanup", _boom)
    out = gc.transcribe_and_clean(_cfg(cleanup_enabled=False), b"wav")
    assert out == "raw words"


def test_pipeline_empty_transcript_returns_none(monkeypatch):
    monkeypatch.setattr(gc, "transcribe", lambda *a, **k: "")
    assert gc.transcribe_and_clean(_cfg(), b"wav") is None


def test_pipeline_applies_voice_commands_and_trailing_space(monkeypatch):
    monkeypatch.setattr(gc, "transcribe", lambda *a, **k: "x")
    monkeypatch.setattr(gc, "cleanup", lambda *a, **k: "a new line b")
    out = gc.transcribe_and_clean(_cfg(append_space=True), b"wav")
    assert out == "a\nb "


def test_pipeline_no_trailing_space_when_text_ends_in_whitespace(monkeypatch):
    # If the cleaned text already ends in whitespace, don't dangle a space.
    monkeypatch.setattr(gc, "transcribe", lambda *a, **k: "x")
    monkeypatch.setattr(gc, "cleanup", lambda *a, **k: "done\n")
    out = gc.transcribe_and_clean(
        _cfg(append_space=True, voice_commands={"enabled": False}), b"wav")
    assert out == "done\n"
    assert not out.endswith(" ")


def test_pipeline_passes_custom_instructions_to_cleanup(monkeypatch):
    seen = {}

    def _capture(api_key, model, transcript, terms=None, max_tokens=4096,
                 custom_instructions=""):
        seen["ci"] = custom_instructions
        return "clean"

    monkeypatch.setattr(gc, "transcribe", lambda *a, **k: "raw")
    monkeypatch.setattr(gc, "cleanup", _capture)
    gc.transcribe_and_clean(_cfg(custom_instructions="Use British spelling"), b"wav")
    assert seen["ci"] == "Use British spelling"


def test_pipeline_falls_back_to_raw_when_cleanup_errors(monkeypatch):
    monkeypatch.setattr(gc, "transcribe", lambda *a, **k: "raw text")

    def _err(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(gc, "cleanup", _err)
    assert gc.transcribe_and_clean(_cfg(), b"wav") == "raw text"


# ---- retry ----
def test_urlopen_retry_recovers_after_transient(monkeypatch):
    monkeypatch.setattr(gc.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def flaky(req, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urlerror.URLError("temporary")
        return "ok"

    monkeypatch.setattr(gc.urlrequest, "urlopen", flaky)
    fake_req = object()
    assert gc._urlopen_retry(fake_req, timeout=1, retries=2) == "ok"
    assert calls["n"] == 3


def test_urlopen_retry_gives_up_and_raises(monkeypatch):
    monkeypatch.setattr(gc.time, "sleep", lambda *_: None)

    def always_timeout(req, timeout):
        raise socket.timeout("nope")

    monkeypatch.setattr(gc.urlrequest, "urlopen", always_timeout)
    with pytest.raises(socket.timeout):
        gc._urlopen_retry(object(), timeout=1, retries=2)


# ---- classify_error ----
def test_classify_error_messages():
    e401 = urlerror.HTTPError("u", 401, "unauth", None, None)
    assert "key" in gc.classify_error(e401).lower()
    e429 = urlerror.HTTPError("u", 429, "rate", None, None)
    assert "rate" in gc.classify_error(e429).lower() or "quota" in gc.classify_error(e429).lower()
    assert "connection" in gc.classify_error(urlerror.URLError("x")).lower()
    assert gc.classify_error(ValueError("?"))   # non-empty generic message


def test_is_hallucination_drops_subtitle_credits():
    import groq_core as gc
    assert gc.is_hallucination("Subtitles by the Amara.org community") is True
    assert gc.is_hallucination("  subtitles by the amara.org community ") is True
    assert gc.is_hallucination("Thanks for watching!") is True
    assert gc.is_hallucination("") is True
    assert gc.is_hallucination("   ") is True


def test_is_hallucination_keeps_real_dictation():
    import groq_core as gc
    assert gc.is_hallucination("what is the capital of france") is False
    assert gc.is_hallucination("write me a python function") is False
    # the marker inside a long genuine sentence is not treated as a credit
    long = "thanks for watching the demo, now let's walk through the whole codebase together"
    assert gc.is_hallucination(long) is False


# ---- wav_peak / silent-mic gate ----
def test_wav_peak_reads_amplitude():
    assert gc.wav_peak(_wav(5000)) == 5000
    assert gc.wav_peak(_wav(0)) == 0


def test_wav_peak_bad_bytes_returns_none():
    assert gc.wav_peak(b"not a wav") is None


def test_pipeline_rejects_silent_audio_before_transcribing(monkeypatch):
    # A dead/muted mic must raise a clear mic error, never reach Whisper, and
    # never type hallucinated boilerplate.
    def _boom(*a, **k):
        raise AssertionError("transcribe must not be called on silent audio")
    monkeypatch.setattr(gc, "transcribe", _boom)
    cfg = {"groq_api_key": "k", "mode": "quick"}
    with pytest.raises(gc.GroqError) as ei:
        gc.transcribe_and_clean(cfg, _wav(10), log=lambda *_: None)
    assert ei.value.kind == "mic"


def test_pipeline_allows_audible_audio(monkeypatch):
    monkeypatch.setattr(gc, "transcribe", lambda *a, **k: "hello world")
    monkeypatch.setattr(gc, "cleanup", lambda *a, **k: "hello world")
    cfg = {"groq_api_key": "k", "mode": "quick", "cleanup_enabled": True}
    out = gc.transcribe_and_clean(cfg, _wav(8000), log=lambda *_: None)
    assert "hello world" in out
