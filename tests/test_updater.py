from pathlib import Path

import updater


def test_unix_swap_replaces_install_and_preserves_siblings(tmp_path):
    # Mirrors the macOS layout: binary in <app dir>/app, config beside it.
    app_root = tmp_path / "jspeak"
    install = app_root / "app"
    install.mkdir(parents=True)
    (install / "jspeak").write_text("OLD BINARY")
    config = app_root / "config.json"
    config.write_text('{"groq_api_key": "keep me"}')

    new_dir = tmp_path / "stage" / "jspeak"
    new_dir.mkdir(parents=True)
    (new_dir / "jspeak").write_text("NEW BINARY")

    updater._unix_swap(new_dir, install)

    assert (install / "jspeak").read_text() == "NEW BINARY"   # swapped in
    assert config.read_text() == '{"groq_api_key": "keep me"}'  # untouched
    assert not (app_root / "app.old").exists()                # backup cleaned
    assert not new_dir.exists()                               # moved into place


def test_parse_version_handles_v_prefix_and_padding():
    assert updater.parse_version("v1.2.3") == (1, 2, 3)
    assert updater.parse_version("1.1") == (1, 1, 0)
    assert updater.parse_version("") == (0, 0, 0)


def test_is_newer():
    assert updater.is_newer("v1.1.0", "1.0.0")
    assert not updater.is_newer("1.0.0", "1.0.0")
    assert not updater.is_newer("1.0.0", "v1.1.0")


def test_latest_release_includes_prereleases(monkeypatch):
    # /releases/latest hides pre-releases, so a beta-only project saw no updates.
    # latest_release must consider pre-releases and return the newest one.
    releases = [
        {"tag_name": "v1.4.2-beta", "prerelease": True, "assets": [
            {"name": updater.ASSET_NAME, "browser_download_url": "http://x/new"}]},
        {"tag_name": "v1.4.1-beta", "prerelease": True, "assets": [
            {"name": updater.ASSET_NAME, "browser_download_url": "http://x/old"}]},
    ]
    monkeypatch.setattr(updater, "_get_json", lambda url: releases)
    tag, url = updater.latest_release()
    assert tag == "v1.4.2-beta"
    assert url == "http://x/new"


def test_latest_release_picks_highest_version_regardless_of_order(monkeypatch):
    releases = [
        {"tag_name": "v1.4.0-beta", "assets": [
            {"name": updater.ASSET_NAME, "browser_download_url": "http://x/a"}]},
        {"tag_name": "v1.4.10-beta", "assets": [
            {"name": updater.ASSET_NAME, "browser_download_url": "http://x/b"}]},
        {"tag_name": "v1.4.2-beta", "assets": [
            {"name": updater.ASSET_NAME, "browser_download_url": "http://x/c"}]},
    ]
    monkeypatch.setattr(updater, "_get_json", lambda url: releases)
    tag, url = updater.latest_release()
    assert tag == "v1.4.10-beta"
    assert url == "http://x/b"


def test_latest_release_skips_releases_without_this_platform_asset(monkeypatch):
    # A newer release still uploading its asset must not shadow an older, usable
    # one (otherwise we'd report a newer version with no download).
    releases = [
        {"tag_name": "v1.5.0-beta", "assets": [
            {"name": "some-other-platform.tar.gz",
             "browser_download_url": "http://x/other"}]},
        {"tag_name": "v1.4.2-beta", "assets": [
            {"name": updater.ASSET_NAME, "browser_download_url": "http://x/good"}]},
    ]
    monkeypatch.setattr(updater, "_get_json", lambda url: releases)
    tag, url = updater.latest_release()
    assert tag == "v1.4.2-beta"
    assert url == "http://x/good"


def _win_script():
    return updater._windows_update_script(
        new_dir=Path(r"C:\stage\extract\jspeak"),
        install=Path(r"C:\Apps\jspeak"),
        tmp=Path(r"C:\Apps\jspeaktmp123"),
        pid=4242,
        log_path=Path(r"C:\Temp\jspeak_update.log"),
    )


def test_windows_update_script_waits_for_launching_pid():
    # The old helper just slept 2s; the fix must poll for the real PID to exit
    # before touching the locked files.
    script = _win_script()
    assert 'set "PID=4242"' in script
    assert 'tasklist /fi "PID eq %PID%"' in script
    assert ":waitexit" in script


def test_windows_update_script_kills_leftover_processes_before_copy():
    # The detached overlay child (also jspeak.exe) is what held the file lock.
    script = _win_script()
    kill_at = script.index(f'taskkill /f /im "{updater.EXE_NAME}"')
    copy_at = script.index("robocopy")
    assert kill_at < copy_at, "must release locks before mirroring files"


def test_windows_update_script_robocopy_has_bounded_retries():
    # Default robocopy retry is /R:1000000 /W:30 which stalls forever on a lock;
    # the helper must cap retries so a genuine failure surfaces instead of hanging.
    script = _win_script()
    assert "/R:10 /W:2" in script


def test_windows_update_script_relaunches_and_cleans_up():
    script = _win_script()
    assert f'set "EXE=C:\\Apps\\jspeak\\{updater.EXE_NAME}"' in script
    assert 'start "" "%EXE%"' in script
    assert 'set "TMP=C:\\Apps\\jspeaktmp123"' in script
    assert 'rmdir /s /q "%TMP%"' in script
