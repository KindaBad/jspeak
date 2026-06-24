#!/usr/bin/env bash
# JSpeak universal installer.
#
#   curl -fsSL https://raw.githubusercontent.com/KindaBad/jspeak/main/install.sh | bash
#
# Detects your OS and installs the matching prebuilt release:
#   • Linux  -> ~/.local/share/jspeak  + a systemd --user service
#   • macOS  -> ~/Library/Application Support/jspeak + a LaunchAgent
# Windows users: download jspeak-windows-x86_64.zip from the releases page and
# run jspeak.exe (see the README).
set -euo pipefail

REPO="KindaBad/jspeak"
API="https://api.github.com/repos/$REPO/releases?per_page=30"

say()  { printf '\033[1;35m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

# Resolve the newest release asset URL matching $1, including pre-releases
# (JSpeak currently ships beta builds, which /releases/latest excludes). The
# releases list is already newest-first.
find_asset() {
  local asset="$1" url
  url=$(curl -fsSL "$API" | grep -o "https://[^\"]*$asset" | head -1 || true)
  [ -n "$url" ] || die "Could not find a $asset asset in any recent release of $REPO."
  printf '%s\n' "$url"
}

install_linux() {
  local asset="jspeak-linux-x86_64.tar.gz"
  local dest="$HOME/.local/share/jspeak"

  if [ -z "${WAYLAND_DISPLAY:-}" ] && [ "${XDG_SESSION_TYPE:-}" != "wayland" ]; then
    warn "JSpeak needs a Wayland session with wlr-layer-shell (Hyprland, sway, river, ...)."
  fi

  say "Checking runtime dependencies"
  local missing=()
  for bin in ydotool ydotoold wtype wl-copy arecord; do
    command -v "$bin" >/dev/null || missing+=("$bin")
  done
  if [ "${#missing[@]}" -gt 0 ]; then
    warn "Missing tools: ${missing[*]}"
    echo "    Install them, e.g.:"
    echo "      Arch:   sudo pacman -S ydotool wtype wl-clipboard alsa-utils"
    echo "      Debian: sudo apt install ydotool wtype wl-clipboard alsa-utils"
    echo "      Fedora: sudo dnf install ydotool wtype wl-clipboard alsa-utils"
  fi
  if ! id -nG | grep -qw input; then
    warn "You are not in the 'input' group (needed to read keys + type)."
    echo "    Run:  sudo usermod -aG input \"$USER\"   then log out and back in."
  fi

  local url tmp
  url=$(find_asset "$asset")
  tmp=$(mktemp -d); trap 'rm -rf "$tmp"' RETURN
  say "Downloading $(basename "$url")"
  curl -fsSL "$url" -o "$tmp/$asset"

  say "Installing to $dest"
  mkdir -p "$(dirname "$dest")"
  tar -xzf "$tmp/$asset" -C "$tmp"
  [ -x "$tmp/jspeak/jspeak" ] || die "Unexpected archive layout: $tmp/jspeak/jspeak missing."
  rm -rf "$dest.old" 2>/dev/null || true
  [ -d "$dest" ] && mv "$dest" "$dest.old"
  mv "$tmp/jspeak" "$dest"
  rm -rf "$dest.old" 2>/dev/null || true
  chmod +x "$dest/jspeak"

  if ! pgrep -x ydotoold >/dev/null; then
    warn "ydotoold is not running (needed to type text). Enable it with:"
    echo "    systemctl --user enable --now ydotool 2>/dev/null || ydotoold &"
  fi

  say "Installing the background service"
  mkdir -p "$HOME/.config/systemd/user"
  cat > "$HOME/.config/systemd/user/jspeak.service" <<EOF
[Unit]
Description=JSpeak push-to-talk dictation
After=default.target

[Service]
Type=simple
ExecStart=$dest/jspeak
Environment=YDOTOOL_SOCKET=%t/.ydotool_socket
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
EOF

  mkdir -p "$HOME/.local/share/applications"
  cat > "$HOME/.local/share/applications/jspeak-settings.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=JSpeak Settings
Comment=Configure JSpeak push-to-talk dictation
Exec=$dest/jspeak --settings
Icon=audio-input-microphone
Terminal=false
Categories=Utility;AudioVideo;Settings;
EOF
  cat > "$HOME/.local/share/applications/jspeak-copy-last.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=JSpeak: Copy last dictation
Comment=Copy the most recent dictation back to the clipboard
Exec=$dest/jspeak --copy-last
Icon=edit-copy
Terminal=false
Categories=Utility;
EOF
  update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

  systemctl --user daemon-reload
  systemctl --user enable --now jspeak.service

  say "Done! JSpeak is running and will start on login."
  echo
  echo "  • First time? Open 'JSpeak Settings' and paste your free Groq API key"
  echo "    from https://console.groq.com/keys"
  echo "  • Hold Ctrl+Shift, speak, release to dictate."
  echo "  • Logs:   journalctl --user -u jspeak.service -f"
  echo "  • Update: JSpeak auto-updates; or re-run this installer."
}

install_macos() {
  local asset="jspeak-macos-arm64.tar.gz"
  local app_dir="$HOME/Library/Application Support/jspeak"
  local dest="$app_dir/app"
  local exe="$dest/jspeak"
  local agent_label="com.jspeak.agent"

  if [ "$(uname -m)" != "arm64" ]; then
    warn "JSpeak ships an Apple-silicon (arm64) build; '$(uname -m)' detected."
    warn "It can still run under Rosetta 2, but performance may suffer."
  fi

  local url tmp
  url=$(find_asset "$asset")
  tmp=$(mktemp -d); trap 'rm -rf "$tmp"' RETURN
  say "Downloading $(basename "$url")"
  curl -fsSL "$url" -o "$tmp/$asset"

  say "Unpacking"
  tar -xzf "$tmp/$asset" -C "$tmp"
  [ -x "$tmp/jspeak/jspeak" ] || die "Unexpected archive layout: $tmp/jspeak/jspeak missing."

  # Stop any running copy so we can swap files safely.
  launchctl bootout "gui/$(id -u)/$agent_label" 2>/dev/null || true
  pkill -f "$exe" 2>/dev/null || true

  say "Installing to $dest"
  mkdir -p "$app_dir"
  rm -rf "$dest.old" 2>/dev/null || true
  [ -d "$dest" ] && mv "$dest" "$dest.old"
  mv "$tmp/jspeak" "$dest"
  rm -rf "$dest.old" 2>/dev/null || true
  chmod +x "$exe"
  # curl downloads aren't quarantined; this is normally a no-op safety net.
  xattr -dr com.apple.quarantine "$dest" 2>/dev/null || true

  say "Launching JSpeak"
  "$exe" >/dev/null 2>&1 &
  disown 2>/dev/null || true

  say "Done! JSpeak is installed and running."
  echo
  echo "  • A purple mic icon now sits in your menu bar (click it for Settings)."
  echo "  • First time? Paste your free Groq API key from"
  echo "    https://console.groq.com/keys"
  echo "  • Grant Input Monitoring and Accessibility to JSpeak in"
  echo "    System Settings -> Privacy & Security, then relaunch from the menu bar."
  echo "  • Hold Ctrl+Shift, speak, release to dictate."
  echo "  • JSpeak auto-updates; re-run this installer any time to reinstall."
}

main() {
  command -v curl >/dev/null || die "curl is required but not installed."
  case "$(uname -s)" in
    Linux)  install_linux ;;
    Darwin) install_macos ;;
    MINGW*|MSYS*|CYGWIN*)
      die "Windows: download jspeak-windows-x86_64.zip from https://github.com/$REPO/releases/latest and run jspeak.exe (see the README)." ;;
    *) die "Unsupported OS: $(uname -s). See https://github.com/$REPO for manual install." ;;
  esac
}

main "$@"
