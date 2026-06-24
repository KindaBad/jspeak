# JSpeak

**Push-to-talk dictation.** Hold **Ctrl+Shift**, speak, release. Your speech is
transcribed, cleaned up (filler words removed, grammar fixed, your tone and slang
kept), and typed into whatever window you are in. A subtle glow shows around your
screen while it listens.

Powered by [Groq](https://groq.com) (fast Whisper plus LLM cleanup). Works in many
languages including right-to-left Arabic and Hebrew.

## Install

### Linux & macOS (one command)
```bash
curl -fsSL https://raw.githubusercontent.com/KindaBad/jspeak/main/install.sh | bash
```
The installer detects your OS and installs the matching build.

**Linux** needs a Wayland compositor with wlr-layer-shell (Hyprland, sway, river,
and so on) and these tools: `ydotool` (+ `ydotoold` running), `wtype`,
`wl-clipboard`, `alsa-utils`. The installer tells you if anything is missing, and
the one command to join the `input` group.

### Windows
1. Download `jspeak-windows-x86_64.zip` from the
   [latest release](https://github.com/KindaBad/jspeak/releases/latest).
2. Extract the folder anywhere (for example your Documents).
3. Run `jspeak.exe`. It registers itself to start on login and opens Settings.
   A purple mic icon sits in the notification area (system tray): right-click it
   for Settings, the log folder, or Quit, or double-click to open Settings.

### macOS notes (Apple silicon)
The command above installs JSpeak under `~/Library/Application Support`, starts
it, and sets it to launch on login - **no admin password needed**. JSpeak is
ad-hoc signed (there
is no paid Apple Developer ID), so a *browser* download trips Gatekeeper's
"unidentified developer" block, which only an administrator can clear. Installing
with `curl` avoids that entirely: curl downloads aren't quarantined.

Then:
1. A purple mic icon sits in the menu bar: click it for Settings, the log folder,
   or Quit.
2. Grant **Input Monitoring** and **Accessibility** to JSpeak in
   **System Settings → Privacy & Security** (needed to detect the hotkey and type
   the result), then relaunch it from the menu bar. Tip: ⌘+Shift works well as the
   hotkey - set Modifiers to `super + shift` in Settings.

<details>
<summary>Manual install (if you'd rather not pipe to bash)</summary>

Download `jspeak-macos-arm64.tar.gz` from the
[latest release](https://github.com/KindaBad/jspeak/releases/latest), then in a
terminal:
```bash
tar -xzf jspeak-macos-arm64.tar.gz
xattr -dr com.apple.quarantine jspeak   # clear the Gatekeeper download flag
./jspeak/jspeak
```
Clearing the quarantine flag from the terminal works without admin rights -
double-clicking the binary in Finder does not.
</details>

On first run, paste a **free Groq API key** from
[console.groq.com/keys](https://console.groq.com/keys). Then hold Ctrl+Shift and talk.

## Features
- **Four quality modes.** Quick (fastest), Smart, Accurate, and **Max** (best
  Whisper + the strongest cleanup model) - pick speed vs. quality in Settings.
- **Fast and accurate.** Groq Whisper plus an LLM that removes "um/uh", fixes
  grammar, and matches your register (keeps slang casual, makes formal text clean).
- **Hold or toggle.** Hold the chord to talk, or switch to toggle mode (tap to
  start, tap to stop) for long dictation. **Press Esc to cancel** a take.
- **Pick your mic.** Choose any input device in Settings, or follow the system default.
- **Copy last dictation.** Lost a result to the wrong window? Grab it again from
  the menu-bar/tray menu (or `jspeak --copy-last`).
- **Any language**, including RTL Arabic and Hebrew.
- **Custom dictionary.** Teach it names and words it mishears.
- **Your text, your way.** Type letter by letter or paste instantly.
- **Lightweight.** The screen glow uses about 0% CPU by default.
- **Auto-updates.** Checks here for new versions and updates itself.
- **Private.** Audio goes only to Groq with your own key. Nothing else leaves your
  machine.

## Uninstall
**Linux:**
```bash
systemctl --user disable --now jspeak.service
rm -rf ~/.local/share/jspeak ~/.config/jspeak \
       ~/.config/systemd/user/jspeak.service \
       ~/.local/share/applications/jspeak-settings.desktop \
       ~/.local/share/applications/jspeak-copy-last.desktop
```
**Windows:** delete the extracted folder, remove the `JSpeak` entry from
`Win+R` -> `shell:startup` (or the registry Run key), and delete
`%APPDATA%\jspeak`.

**macOS:** quit it from the menu bar, then:
```bash
launchctl bootout "gui/$(id -u)/com.jspeak.agent" 2>/dev/null || true
rm -f ~/Library/LaunchAgents/com.jspeak.agent.plist
rm -rf "~/Library/Application Support/jspeak"
```
(The installer keeps everything - binary, config, logs - under
`~/Library/Application Support/jspeak`.)

---
Open source under the [MIT License](https://github.com/KindaBad/jspeak/blob/main/LICENSE). (c) KindaBad.
