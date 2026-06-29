#!/usr/bin/env python3
"""Unified entry point for the JSpeak binary (Linux + Windows + macOS).

One executable, several roles selected by argument so PyInstaller only bundles a
single program. The daemon/overlay/settings implementations differ per OS:

    jspeak                 run the dictation daemon
    jspeak --overlay       run the screen-edge glow overlay (spawned internally)
    jspeak --settings      open the settings GUI
    jspeak --version       print version and exit
    jspeak --check-update  check for and apply an update, then exit
    jspeak --copy-last     copy the most recent dictation to the clipboard
"""
import os
import sys

# Running from source, the modules live in per-area folders (shared/ + the
# platform dirs). Put them on the path so the bare imports below resolve the
# same way they do in a frozen build, where PyInstaller flattens everything into
# one namespace.
if not getattr(sys, "frozen", False):
    _ROOT = os.path.dirname(os.path.abspath(__file__))
    for _area in ("shared", "linux", "windows", "macos"):
        sys.path.insert(0, os.path.join(_ROOT, _area))

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"


def _ensure_ca_bundle():
    """A frozen PyInstaller build ships its own Python with no access to the OS
    trust store, so every HTTPS call (Groq key test, transcription, auto-update)
    fails TLS verification - notably on macOS. Point OpenSSL at certifi's bundled
    CA file so the default SSL context can verify certificates. Harmless when run
    from source (the system store already works); only sets the vars if unset."""
    if os.environ.get("SSL_CERT_FILE"):
        return
    try:
        import certifi
        ca = certifi.where()
    except Exception:
        return
    if ca and os.path.exists(ca):
        os.environ["SSL_CERT_FILE"] = ca
        os.environ.setdefault("REQUESTS_CA_BUNDLE", ca)


_ensure_ca_bundle()


def main():
    args = sys.argv[1:]
    role = args[0] if args else ""

    if role == "--version":
        from version import __version__
        print(__version__)
        return
    if role == "--check-update":
        import updater
        updater.check_and_apply(force_log=True)
        return
    if role == "--copy-last":
        import clip
        import history
        text = history.latest()
        if text and clip.copy(text):
            print("Copied last dictation to clipboard.")
        elif not text:
            print("No dictation history yet.")
        else:
            print("Could not access the clipboard.")
        return

    if role == "--overlay":
        if IS_WIN:
            import winoverlay as ov
        elif IS_MAC:
            import macoverlay as ov
        else:
            import overlay as ov
        ov.main()
    elif role == "--settings":
        # Windows uses the PySide6/Qt settings window (winsettings_qt). macOS
        # keeps the cross-platform Tkinter window (winsettings); Linux keeps its
        # native GTK settings.
        if IS_WIN:
            import winsettings_qt as st
        elif IS_MAC:
            import winsettings as st
        else:
            import settings as st
        st.main()
    else:
        if IS_WIN:
            import winmain as daemon
        elif IS_MAC:
            import macmain as daemon
        else:
            import jspeak as daemon
        daemon.main()


if __name__ == "__main__":
    main()
