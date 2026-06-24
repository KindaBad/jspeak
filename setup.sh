#!/usr/bin/env bash
# JSpeak setup: lock down config, check dependencies, install + enable the
# systemd --user service so it autostarts on login.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Securing config.json (contains your API key)"
# Fresh clones have no config.json (it's git-ignored); seed it from the default
# so there's something to edit, then lock it down to the owner.
[ -f config.json ] || cp config.default.json config.json
chmod 600 config.json

echo "==> Checking overlay toolkit (GTK3 + gtk-layer-shell)"
python3 -c "import gi; gi.require_version('Gtk','3.0'); gi.require_version('GtkLayerShell','0.1'); from gi.repository import Gtk, GtkLayerShell; print('    ok: gtk-layer-shell overlay available')" \
  || echo "    WARNING: overlay deps missing (python-gobject, gtk3, gtk-layer-shell). Dictation still works; set overlay.enabled=false in config.json to silence."

echo "==> Checking dependencies"
for bin in arecord ydotool ydotoold python3; do
  if command -v "$bin" >/dev/null; then
    echo "    ok: $bin"
  else
    echo "    MISSING: $bin"
  fi
done
if ! pgrep -x ydotoold >/dev/null; then
  echo "    WARNING: ydotoold is not running. Start it (and on login) with:"
  echo "      systemctl --user enable --now ydotool   # if your distro ships the unit"
fi
if ! id -nG | grep -qw input; then
  echo "    WARNING: you are not in the 'input' group; key capture will fail."
  echo "      sudo usermod -aG input \$USER   # then log out/in"
fi

echo "==> Installing systemd --user service"
mkdir -p ~/.config/systemd/user
cp jspeak.service ~/.config/systemd/user/jspeak.service
systemctl --user daemon-reload
systemctl --user enable jspeak.service

echo "==> Installing settings launcher + app menu entry"
chmod +x jspeak-settings
mkdir -p ~/.local/share/applications
# Generate the launcher with this checkout's absolute path so it resolves
# wherever the repo lives (the committed .desktop is only a template).
sed "s|^Exec=.*|Exec=$(pwd -P)/jspeak-settings|" jspeak-settings.desktop \
  > ~/.local/share/applications/jspeak-settings.desktop
update-desktop-database ~/.local/share/applications 2>/dev/null || true

echo
echo "Done. Start it now with:   systemctl --user start jspeak.service"
echo "Settings GUI:              ./jspeak-settings   (or 'JSpeak Settings' in your launcher)"
echo "Live logs:                 journalctl --user -u jspeak.service -f"
echo "Foreground (debug):        ./run.sh"
