# JSpeak

Push-to-talk dictation for Wayland/Hyprland - a small WisprFlow-style tool.

**Hold `Ctrl+Shift`** (with no other key) and speak. **Release** to:
1. Record your voice (PipeWire/PulseAudio via `arecord`).
2. Transcribe it with **Groq Whisper**.
3. Clean it up (remove filler words, fix grammar, match your register) with a
   fast **Groq LLM**.
4. Type the result into the focused window via `ydotool` (~10 ms/keystroke).

A purple **screen-edge glow** shows while you're recording (bright, gently
pulsing) and dims while it transcribes/types (then fades out) - no beeps. A
short **red flash** means something failed (network/API). The glow only exists
on screen during recording/processing, so it never blocks your mouse.

By default it runs in **uncensored / literature mode** (`uncensored: true`):
profanity is transcribed verbatim and never softened or masked. Dictated text
also gets a trailing space (`append_space: true`) so consecutive dictations
flow. Both are toggleable in settings.

No Wayland display access is needed: keys are read from `/dev/input` and text is
injected through `uinput`, so it works the same under any compositor and from a
background service. Pure Python stdlib - no `pip install` required.

**Cross-platform.** JSpeak also ships for **macOS** (Apple silicon) and
**Windows**. The Groq pipeline, prompts, dictionary, voice commands and the
screen-edge glow behave identically everywhere; only the OS plumbing (key
capture, typing, overlay, autostart) differs. See **[macOS](#macos)** below and
download a build from the [releases page](https://github.com/KindaBad/jspeak/releases).

**Open source (MIT).** Source and prebuilt releases both live in this repo. The
fastest way to install on Linux or macOS is the universal one-liner, which
detects your OS and grabs the matching build:
```bash
curl -fsSL https://raw.githubusercontent.com/KindaBad/jspeak/main/install.sh | bash
```

## Requirements
- Membership in the `input` group (read keys + write uinput).
- `ydotoold` running, socket at `$XDG_RUNTIME_DIR/.ydotool_socket`.
- `arecord` (alsa-utils), `paplay` (libpulse), `python3`.

## Install
```bash
cd ~/jspeak
./setup.sh                          # secures config, checks deps, installs service
systemctl --user start jspeak.service
journalctl --user -u jspeak.service -f   # watch logs
```
It will then autostart on every login.

Debug in the foreground instead:
```bash
./run.sh
```

## macOS

A prebuilt Apple-silicon (arm64) build is published on the
[releases page](https://github.com/KindaBad/jspeak/releases) as
`jspeak-macos-arm64.tar.gz`. The one-line installer is the recommended path - it
downloads with `curl` (no Gatekeeper quarantine), installs under
`~/Library/Application Support`, and needs **no admin password**:

```bash
curl -fsSL https://raw.githubusercontent.com/KindaBad/jspeak/main/install.sh | bash
```

> JSpeak is ad-hoc signed (no paid Apple Developer ID), so a **browser** download
> trips Gatekeeper's "unidentified developer" block - clearing that needs an
> administrator. The `curl` installer sidesteps it because curl downloads aren't
> quarantined. To install by hand instead:
> ```bash
> tar -xzf jspeak-macos-arm64.tar.gz
> xattr -dr com.apple.quarantine jspeak   # clear the quarantine flag (no admin needed)
> ./jspeak/jspeak                          # first run opens Settings for your Groq key
> ```

> **"Failed to load Python shared library … library load disallowed by system
> policy"?** That's an older build whose bundled `Python.framework` was signed
> with a mismatched identity. Builds from v1.4.3-beta on are re-signed at release
> time and don't hit this. To repair a build you already downloaded, run the
> bundled fix-up once: `bash packaging/macos-fix-signature.sh ./jspeak` (or grab
> it from the repo), then launch `./jspeak/jspeak` again.

The app lives in the menu bar (purple mic icon → **Settings / Open log folder /
Quit**) and installs a **LaunchAgent** (`~/Library/LaunchAgents/com.jspeak.agent.plist`)
so it autostarts on login. Config and logs live in
`~/Library/Application Support/jspeak/`. The auto-updater works the same as on
Linux (a newer GitHub release is downloaded and swapped in on startup).

**Permissions (required once).** macOS gates global key capture and synthetic
typing. On first use, grant JSpeak both of these in
**System Settings → Privacy & Security**, then restart it:
- **Input Monitoring** - to detect the Ctrl+Shift hold.
- **Accessibility** - to type the cleaned text into the focused app.

Microphone access is requested the first time it records. The glow overlay is a
borderless, click-through AppKit window pinned above everything, so - like on
Linux - it never steals focus or blocks the mouse.

> Cmd+Shift is a natural hotkey on macOS: set **Hotkey → Modifiers** to
> `super + shift` in Settings (the "super" modifier is the ⌘ Command key).

Running from source on a Mac (for development):
```bash
pip install -r requirements-mac.txt
python app.py            # daemon   (python app.py --settings  for the GUI)
```

## Configuration - `config.json`
- `mode`:
  - `quick` - turbo Whisper + Llama-3.1-8B cleanup. Fastest, cheapest. (default)
  - `smart` - turbo Whisper + Llama-3.3-70B cleanup. Better slang/rewrites.
  - `accurate` - full Whisper + 70B. Best for noisy or hard audio.
  - `max` - full Whisper + GPT-OSS-120B cleanup. Best quality, slowest/priciest.
- `activation` - `hold` (record while the chord is held) or `toggle` (tap once
  to start, tap again to stop) for long dictation.
- `cancel_on_esc` - `true` (default) lets you press **Esc** mid-recording to
  throw the take away without transcribing or typing it.
- `input_device` - microphone name to record from; empty = system default. On
  Linux this is an ALSA PCM (`arecord -D`); on macOS/Windows a device name.
  Pick from the list in Settings.
- `key_delay_ms` - delay between simulated keystrokes (default 10).
- `min_hold_ms` - how long Ctrl+Shift must be held before recording starts.
  This is what stops normal `Ctrl+Shift+<key>` shortcuts from triggering
  dictation: press any other key during the hold window and the gesture aborts.
- `min_record_ms` - recordings shorter than this are discarded as accidental.
- `cleanup_enabled` - `true` (default) runs the LLM cleanup pass; `false` types
  the raw transcript with no LLM call (fastest).
- `custom_instructions` - your own style/formatting rules added to the cleanup
  pass, e.g. `"Use British spelling"`, `"Format lists as bullet points"`, or
  `"Keep everything lowercase"`. Tunes the output's style only - it never makes
  JSpeak answer or translate what you dictated, and it's ignored when
  `cleanup_enabled` is `false`. Editable in Settings under **Dictation**.

After editing config: `systemctl --user restart jspeak.service`.

**Recent dictations.** Every typed result is kept in a small rolling history
(`history.json` in the config dir, last 50). Run `jspeak --copy-last` to put the
most recent one back on the clipboard - handy when focus was on the wrong window.
The installer also adds a *"JSpeak: Copy last dictation"* app-menu entry you can
bind to a key; macOS/Windows expose it in the menu-bar/tray menu.

## How the hotkey avoids false triggers
1. Press Ctrl+Shift → **armed** (timer starts).
2. Press any other key before the timer → **aborted** (it was a shortcut).
3. Hold past `min_hold_ms` with no other key → **recording** (glow turns on).
4. Release Ctrl or Shift → **stop**, transcribe, clean, type (glow fades out).

## Screen-edge glow (`overlay` in config.json)
A GTK3 `wlr-layer-shell` overlay (`linux/overlay.py`) paints a purple edge glow. It
uses `keyboard-mode = none`, so it **never steals focus**, it's click-through,
and it's only mapped while visible so it can't block the mouse.

It's built to be cheap on low-end hardware: the glow gradient is rendered **once**
into a cached image, then each frame is just a clipped blit (no per-frame blur).
- `reduce_motion` (default **true**): static glow → **0% CPU** while recording.
  Set `false` for a gentle breathing pulse (a few % CPU).
- `enabled`: set `false` to turn the glow off entirely.
- `color`: hex, e.g. `#a855f7`.
- `max_alpha`: 0-1 intensity.
- `thickness_px`: how far the glow reaches inward from the edges.

## Languages & RTL (`language` + `type_method` in config.json)
Whisper auto-detects language by default, so you can just speak Arabic, Hebrew,
Spanish, etc. and it transcribes + cleans up in that language (it never
translates). Force a language with an ISO-639-1 code (`ar`, `he`, `en`, `es`, …)
for better accuracy on short clips.

Text insertion (`type_method`):
- `auto` / `wtype` - type letter-by-letter in **any** language including
  right-to-left Arabic & Hebrew (Wayland virtual keyboard). Default.
- `ydotool` - type, Latin/ASCII only, no Wayland needed.
- `clipboard` - **paste everything at once instantly** via Ctrl+V; your previous
  clipboard contents are saved and restored afterward.

## Dictionary (`dictionary` in config.json)
Teach it names/words it mishears:
- `terms`: a list like `["Tyreless", "kindabad", "Hyprland"]`. These are sent to
  Whisper as recognition bias **and** the cleanup model is told to keep their
  exact spelling (and fix close mis-hearings to them).
- `replacements`: explicit fixes applied to the final text, e.g.
  `{ "j speak": "JSpeak" }`. Case-insensitive, whole-word.

## Project layout
```
app.py            unified entry point (daemon / --overlay / --settings / --copy-last)
shared/           platform-agnostic core: Groq pipeline, config, audio, hotkeys, history, updater
linux/            Linux daemon, GTK layer-shell overlay, GTK settings
windows/          Windows daemon, Win32/GDI overlay, Qt settings, tray
macos/            macOS daemon, AppKit overlay, menu-bar tray
tests/            stdlib-only test suite (run with `pytest`)
packaging/        macOS entitlements + signature repair helper
```
Running from source goes through `app.py`, which puts `shared/` and the
platform folders on the import path; frozen builds bundle everything flat.

## License
JSpeak is open source under the [MIT License](LICENSE). You can use, modify, and
redistribute it freely. Contributions are welcome - open an issue or PR.
Your own `config.json` (which holds your Groq API key) is git-ignored and never
part of the source tree.
