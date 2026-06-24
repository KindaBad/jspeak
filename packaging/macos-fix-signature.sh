#!/usr/bin/env bash
# Repair the code signature of an already-downloaded JSpeak macOS build.
#
# Builds before this fix shipped the bundled Python.framework with python.org's
# code signature while the launcher was ad-hoc signed. On Apple silicon that
# mismatch makes macOS refuse to load the framework:
#
#   Failed to load Python shared library ... library load disallowed by
#   system policy
#
# This script clears the download quarantine flag and re-signs the whole bundle
# ad-hoc with a single identity (plus entitlements that disable library
# validation), which is exactly what the release pipeline now does at build
# time. Run it once against the extracted `jspeak` folder:
#
#   ./macos-fix-signature.sh /path/to/jspeak
#
# With no argument it defaults to ./jspeak next to this script.
set -euo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
  echo "This fix-up only applies to macOS builds." >&2
  exit 1
fi

DIR="${1:-./jspeak}"
if [ ! -x "$DIR/jspeak" ]; then
  echo "Could not find '$DIR/jspeak'. Pass the path to the extracted folder:" >&2
  echo "  $0 /path/to/jspeak" >&2
  exit 1
fi

echo "==> Clearing Gatekeeper quarantine flag"
xattr -dr com.apple.quarantine "$DIR" 2>/dev/null || true

ENT="$(mktemp -t jspeak-entitlements).plist"
trap 'rm -f "$ENT"' EXIT
cat > "$ENT" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.security.cs.disable-library-validation</key>
    <true/>
    <key>com.apple.security.cs.allow-unsigned-executable-memory</key>
    <true/>
    <key>com.apple.security.cs.allow-jit</key>
    <true/>
    <key>com.apple.security.cs.allow-dyld-environment-variables</key>
    <true/>
</dict>
</plist>
PLIST

echo "==> Re-signing every Mach-O ad-hoc (this can take a minute)"
while IFS= read -r -d '' f; do
  if file -b "$f" | grep -q 'Mach-O'; then
    codesign --force --timestamp=none --sign - "$f"
  fi
done < <(find "$DIR" -type f -print0)

echo "==> Rebuilding framework bundle seals"
find "$DIR" -name "*.framework" -type d -prune \
  -exec codesign --force --deep --timestamp=none --sign - {} +

echo "==> Signing the launcher with library-validation disabled"
codesign --force --timestamp=none --entitlements "$ENT" --sign - "$DIR/jspeak"

codesign --verify --strict --verbose=2 "$DIR/jspeak"
echo "==> Done. Launch it with: $DIR/jspeak"
