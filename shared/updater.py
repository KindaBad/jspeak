"""Self-update from the public GitHub releases repo.

On startup the daemon calls check_and_apply() in a background thread. If the
latest GitHub release has a newer version than this build, it downloads the
matching Linux asset, atomically swaps it into place, and re-executes so the
new version takes over - all without user interaction.

Only meaningful for frozen (PyInstaller) builds; running from source is a no-op.
"""
import json
import os
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

from version import __version__, RELEASES_REPO
import jpaths

# List all releases (newest first). We deliberately do NOT use /releases/latest:
# that endpoint silently excludes pre-releases, and JSpeak currently ships only
# beta (pre-release) builds - so it returned nothing and auto-update never fired.
API_RELEASES = f"https://api.github.com/repos/{RELEASES_REPO}/releases?per_page=30"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
if IS_WIN:
    ASSET_NAME = "jspeak-windows-x86_64.zip"
elif IS_MAC:
    ASSET_NAME = "jspeak-macos-arm64.tar.gz"
else:
    ASSET_NAME = "jspeak-linux-x86_64.tar.gz"
EXE_NAME = "jspeak.exe" if IS_WIN else "jspeak"


def _log(msg):
    print(f"[updater] {msg}", flush=True)


def parse_version(s):
    """'v1.2.3' / '1.2.3' -> (1, 2, 3) for comparison."""
    s = (s or "").strip().lstrip("vV")
    parts = []
    for chunk in s.split("."):
        num = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(num) if num else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_newer(remote, local):
    return parse_version(remote) > parse_version(local)


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def latest_release():
    """Newest release that ships this platform's asset, INCLUDING pre-releases
    (betas). Picks the highest version rather than trusting list order, and
    skips drafts and releases whose asset hasn't finished uploading."""
    releases = _get_json(API_RELEASES)
    if not isinstance(releases, list):
        return "", None
    best = None  # (version_tuple, tag, asset_url)
    for rel in releases:
        if rel.get("draft"):
            continue
        tag = rel.get("tag_name", "")
        if not tag:
            continue
        asset_url = next(
            (a.get("browser_download_url") for a in rel.get("assets", [])
             if a.get("name") == ASSET_NAME),
            None,
        )
        if not asset_url:
            continue
        ver = parse_version(tag)
        if best is None or ver > best[0]:
            best = (ver, tag, asset_url)
    if best is None:
        return "", None
    return best[1], best[2]


def _download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            f.write(chunk)


def _install_dir():
    """The PyInstaller onedir folder that contains the jspeak executable."""
    return Path(sys.executable).resolve().parent


def check_and_apply(force_log=False):
    if not jpaths.is_frozen():
        if force_log:
            _log("running from source; updates are a no-op")
        return False
    try:
        tag, asset_url = latest_release()
        if not tag:
            return False
        if not is_newer(tag, __version__):
            if force_log:
                _log(f"up to date (v{__version__}, latest {tag})")
            return False
        if not asset_url:
            _log(f"newer version {tag} found but no {ASSET_NAME} asset")
            return False
        _log(f"updating {__version__} -> {tag}")

        install = _install_dir()
        parent = install.parent
        tmp = Path(tempfile.mkdtemp(dir=str(parent)))
        archive = tmp / ASSET_NAME
        _download(asset_url, archive)
        extract = tmp / "extract"
        extract.mkdir()
        _extract(archive, extract)
        roots = [p for p in extract.iterdir() if p.is_dir()]
        new_dir = roots[0] if roots else extract

        if IS_WIN:
            # Can't overwrite a running .exe; hand off to a helper that waits
            # for us to exit, swaps the folder, and relaunches.
            _windows_swap_and_restart(new_dir, install, tmp)
            return True
        # Unix (Linux/macOS): rename old aside, move new in, re-exec in place.
        # A running executable can be replaced on disk on Unix, so no helper
        # process is needed the way Windows requires.
        _unix_swap(new_dir, install)
        _rmtree(tmp)
        _log("update applied; restarting")
        _restart()
        return True
    except Exception as e:
        _log(f"update check failed: {e}")
        return False


def _unix_swap(new_dir, install):
    """Atomically replace the install directory with the freshly extracted
    ``new_dir``. Only the install dir is touched - siblings such as a
    ``config.json`` or ``history.json`` one level up are left intact, which is
    why the macOS layout keeps the binary in its own ``app`` subdir. A running
    Unix executable can be replaced on disk, so no external helper is needed."""
    install = Path(install)
    backup = install.parent / (install.name + ".old")
    _rmtree(backup)
    os.rename(install, backup)
    os.rename(new_dir, install)
    _rmtree(backup)


def _extract(archive, dest):
    if str(archive).endswith(".zip"):
        import zipfile
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest)
    else:
        with tarfile.open(archive) as tf:
            # filter="data" rejects members with absolute paths or ".." escapes,
            # so a tampered archive can't write outside `dest` (and it silences
            # the 3.12+ extraction deprecation warning).
            tf.extractall(dest, filter="data")


def _windows_update_script(new_dir, install, tmp, pid, log_path):
    """Build the helper batch script that performs the swap from outside this
    (about-to-exit) process. Pure/string-only so it can be unit-tested.

    A running .exe and its loaded DLLs are locked on Windows, so the swap has to
    happen after every jspeak.exe is gone. The daemon also spawns a *detached*
    overlay child (also jspeak.exe) that outlives the main process and keeps the
    files locked, which is why simply sleeping a couple of seconds wasn't enough:
    robocopy hit 'the process cannot access the file because it is being used by
    another process'. So the helper (1) waits for the launching PID to exit,
    (2) force-kills any leftover jspeak.exe so no handle remains, then (3)
    mirrors the new build in, retrying briefly to ride out transient locks (e.g.
    antivirus scanning the freshly written exe).

    Paths are rendered as Windows paths explicitly so the script is identical
    regardless of the host the build runs on."""
    from pathlib import PureWindowsPath
    new_dir = PureWindowsPath(str(new_dir))
    install = PureWindowsPath(str(install))
    tmp = PureWindowsPath(str(tmp))
    log_path = PureWindowsPath(str(log_path))
    exe = install / EXE_NAME
    return (
        "@echo off\r\n"
        "setlocal enableextensions\r\n"
        f'set "PID={pid}"\r\n'
        f'set "SRC={new_dir}"\r\n'
        f'set "DST={install}"\r\n'
        f'set "EXE={exe}"\r\n'
        f'set "TMP={tmp}"\r\n'
        f'set "LOG={log_path}"\r\n'
        'echo update helper started %date% %time% > "%LOG%"\r\n'
        # 1) Wait (up to ~30s) for the launching process to fully exit.
        "set /a tries=0\r\n"
        ":waitexit\r\n"
        'tasklist /fi "PID eq %PID%" 2>nul | find "%PID%" >nul\r\n'
        "if errorlevel 1 goto exited\r\n"
        "set /a tries+=1\r\n"
        "if %tries% geq 30 goto exited\r\n"
        "timeout /t 1 /nobreak >nul\r\n"
        "goto waitexit\r\n"
        ":exited\r\n"
        # 2) Kill any leftover jspeak.exe (overlay/settings) holding file locks.
        f'taskkill /f /im "{EXE_NAME}" >nul 2>&1\r\n'
        "timeout /t 1 /nobreak >nul\r\n"
        # 3) Mirror the new build in, retrying briefly on transient locks.
        'robocopy "%SRC%" "%DST%" /MIR /R:10 /W:2 /NFL /NDL /NJH /NJS /NP >> "%LOG%"\r\n'
        # 4) Relaunch and clean up the staging dir.
        'start "" "%EXE%"\r\n'
        'rmdir /s /q "%TMP%" >nul 2>&1\r\n'
    )


def _windows_swap_and_restart(new_dir, install, tmp):
    """Write the helper script, launch it detached, and exit so it can swap the
    files we currently hold locked."""
    log_path = Path(tempfile.gettempdir()) / "jspeak_update.log"
    bat = Path(tempfile.gettempdir()) / "jspeak_update.bat"
    bat.write_text(_windows_update_script(new_dir, install, tmp,
                                          os.getpid(), log_path))
    import subprocess
    subprocess.Popen(["cmd", "/c", str(bat)],
                     creationflags=0x00000008)  # DETACHED_PROCESS
    _log("update staged; helper will swap files after exit")
    os._exit(0)


def _rmtree(p):
    import shutil
    try:
        shutil.rmtree(p)
    except Exception:
        pass


def _restart():
    exe = _install_dir() / EXE_NAME
    try:
        os.execv(str(exe), [str(exe)] + sys.argv[1:])
    except Exception as e:
        _log(f"restart failed ({e}); exiting for service manager to relaunch")
        os._exit(0)


def check_in_background():
    """Fire-and-forget update check shortly after startup."""
    import threading

    def run():
        time.sleep(3)
        check_and_apply()

    threading.Thread(target=run, daemon=True).start()
