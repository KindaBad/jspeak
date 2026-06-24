import history


def _redirect(monkeypatch, tmp_path):
    monkeypatch.setattr(history.jpaths, "config_dir", lambda: tmp_path)


def test_add_and_latest_roundtrip(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    assert history.latest() is None
    history.add("first")
    history.add("second")
    assert history.latest() == "second"


def test_blank_text_is_ignored(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    history.add("   ")
    history.add("")
    history.add(None)
    assert history.latest() is None


def test_recent_is_newest_first_and_capped(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    for i in range(history.MAX + 10):
        history.add(f"entry-{i}")
    items = history.recent(5)
    assert [e["text"] for e in items] == [f"entry-{history.MAX + 10 - 1 - n}"
                                          for n in range(5)]
    # never grows past the cap on disk
    assert len(history._load_raw()) == history.MAX


def test_unicode_preserved(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    history.add("مرحبا بالعالم")
    assert history.latest() == "مرحبا بالعالم"


def test_corrupt_file_is_tolerated(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    (tmp_path / "history.json").write_text("{not json")
    assert history.latest() is None
    history.add("recovered")
    assert history.latest() == "recovered"


def test_clear(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    history.add("gone soon")
    history.clear()
    assert history.latest() is None
