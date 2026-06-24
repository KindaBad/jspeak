import clip


def test_copy_empty_is_noop():
    assert clip.copy("") is False
    assert clip.copy(None) is False


def test_copy_cmd_reports_failure_when_tool_missing(monkeypatch):
    # wl-copy / pbcopy absent -> FileNotFoundError, swallowed into False.
    def _boom(*a, **k):
        raise FileNotFoundError("no such tool")

    monkeypatch.setattr(clip.subprocess, "run", _boom)
    assert clip._copy_cmd(["wl-copy"], "hi") is False


def test_copy_cmd_success(monkeypatch):
    class _Done:
        returncode = 0

    captured = {}

    def _run(cmd, **kw):
        captured["cmd"] = cmd
        captured["input"] = kw.get("input")
        return _Done

    monkeypatch.setattr(clip.subprocess, "run", _run)
    assert clip._copy_cmd(["pbcopy"], "héllo") is True
    assert captured["cmd"] == ["pbcopy"]
    assert captured["input"] == "héllo".encode("utf-8")
