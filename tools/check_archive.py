"""Verify that the portable archive is complete and clean.

    py -3 tools/check_archive.py dist\\P3R-Save-Transfer-<version>-win64-portable.zip

Fails (exit 1) when the archive is missing the executables, is missing the
``_internal`` runtime, or contains settings, logs, saves, tests or sources.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

REQUIRED_FILES = {
    "P3R-Save-Transfer.exe",
    "p3r-save-transfer-cli.exe",
    "README.txt",
    "LICENSE",
    "version.txt",
}
FORBIDDEN_PARTS = (
    "config.json",
    ".log",
    ".sav",
    "backup/",
    "_p3r_backup_",
    "tests/",
    "src/",
    "__pycache__/",
    ".git/",
    ".pytest_cache/",
)
MIN_EXE_BYTES = 500_000


def check(path: Path) -> list[str]:
    problems: list[str] = []
    with zipfile.ZipFile(path) as archive:
        # PowerShell 5.1's Compress-Archive may store backslashes as separators
        infos = [info for info in archive.infolist() if not info.is_dir()]
        names = [info.filename.replace("\\", "/") for info in infos]
        sizes = {
            info.filename.replace("\\", "/"): info.file_size for info in infos
        }
        present = {Path(name).name for name in names}

        for required in sorted(REQUIRED_FILES - present):
            problems.append(f"missing file: {required}")
        internal = [name for name in names if name.startswith("_internal/")]
        if not internal:
            problems.append("missing _internal/ runtime")
        for name in names:
            if name.endswith("/"):
                continue
            for forbidden in FORBIDDEN_PARTS:
                if forbidden in name:
                    problems.append(f"forbidden entry: {name}")
        for exe in ("P3R-Save-Transfer.exe", "p3r-save-transfer-cli.exe"):
            matched = [name for name in names if Path(name).name == exe]
            if matched and sizes[matched[0]] < MIN_EXE_BYTES:
                problems.append(f"{exe} looks like a stub ({sizes[matched[0]]} bytes)")
        top_level = sorted({name.split("/")[0] for name in names})
    print(f"archive: {path.name} ({path.stat().st_size} bytes)")
    print(f"top level: {', '.join(top_level)}")
    print(f"entries: {len(names)}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args(argv)
    if not args.archive.is_file():
        print(f"no such archive: {args.archive}")
        return 1
    problems = check(args.archive)
    if problems:
        print("archive check FAILED")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print("archive check OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
