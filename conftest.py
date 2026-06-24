import os
import sys

# Modules live in per-area folders (shared/ + the platform dirs); add each to
# the path so tests can import them by bare name, matching the frozen build.
_ROOT = os.path.dirname(__file__)
for _area in ("shared", "linux", "windows", "macos"):
    sys.path.insert(0, os.path.join(_ROOT, _area))
