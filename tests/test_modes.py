import json

import groq_core as gc


def test_max_mode_uses_best_stt_and_strongest_cleanup():
    stt, cleanup = gc.MODES["max"]
    assert stt == "whisper-large-v3"               # most accurate STT
    assert cleanup == "openai/gpt-oss-120b"        # strongest cleanup model


def test_all_modes_have_two_models():
    for name, pair in gc.MODES.items():
        assert len(pair) == 2, name
        assert all(isinstance(m, str) and m for m in pair), name


def _payload_for(monkeypatch, model):
    captured = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": "clean"}}]}).encode()

    def _fake_open(req, timeout):
        captured["body"] = json.loads(req.data.decode())
        return _Resp()

    monkeypatch.setattr(gc, "_urlopen_retry", _fake_open)
    gc.cleanup("key", model, "raw text")
    return captured["body"]


def test_gpt_oss_cleanup_sets_low_reasoning_effort(monkeypatch):
    body = _payload_for(monkeypatch, "openai/gpt-oss-120b")
    assert body["reasoning_effort"] == "low"
    assert body["model"] == "openai/gpt-oss-120b"


def test_llama_cleanup_has_no_reasoning_effort(monkeypatch):
    # The param is gpt-oss-only; sending it to Llama would 400.
    body = _payload_for(monkeypatch, "llama-3.3-70b-versatile")
    assert "reasoning_effort" not in body
