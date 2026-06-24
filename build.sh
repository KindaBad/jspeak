#!/usr/bin/env bash
# Build the Linux release tarball with PyInstaller.
# Produces dist/jspeak-linux-x86_64.tar.gz
set -euo pipefail
cd "$(dirname "$0")"

PY=./.venv/bin/python
if [ ! -x "$PY" ]; then
  echo "Creating build venv..."
  python3 -m venv --system-site-packages .venv
  PY=./.venv/bin/python
fi
./.venv/bin/pip install --quiet --upgrade pyinstaller pycairo

echo "==> Building with PyInstaller"
rm -rf build dist
./.venv/bin/pyinstaller --noconfirm jspeak.spec

echo "==> Packaging tarball"
( cd dist && tar -czf jspeak-linux-x86_64.tar.gz jspeak )
echo "Done: dist/jspeak-linux-x86_64.tar.gz"
ls -lh dist/jspeak-linux-x86_64.tar.gz
