"""Print the project version (used by CI and the release workflow).

    py -3 tools/version.py      -> 0.1.0-beta
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "src" / "p3r_transfer" / "version.py"

_PATTERN = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)


def read_version() -> str:
    text = VERSION_FILE.read_text(encoding="utf-8")
    match = _PATTERN.search(text)
    if match is None:
        raise SystemExit(f"no __version__ in {VERSION_FILE}")
    return match.group(1)


if __name__ == "__main__":
    sys.stdout.write(read_version() + "\n")
