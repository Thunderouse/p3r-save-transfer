"""Zip the portable folder with forward-slash entry names.

PowerShell 5.1's ``Compress-Archive`` writes ``_internal\\file.dll`` into the
archive. Windows Explorer and 7-Zip cope with that, but the ZIP specification
and every other extractor expect ``/``; a backslash archive unpacks into
literal ``_internal\\file.dll`` names outside Windows.

Usage::

    python tools/make_zip.py dist/P3R-Save-Transfer dist/...-portable.zip
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path


def build(source: Path, target: Path, compresslevel: int = 9) -> int:
    if not source.is_dir():
        raise SystemExit(f"make_zip: нет каталога {source}")
    files = sorted(path for path in source.rglob("*") if path.is_file())
    if not files:
        raise SystemExit(f"make_zip: каталог пуст {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=compresslevel) as archive:
        for path in files:
            archive.write(path, path.relative_to(source).as_posix())
    return len(files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Zip a folder with '/' entry names")
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args(argv)
    count = build(args.source, args.target)
    size = args.target.stat().st_size / (1024 * 1024)
    print(f"{args.target} ({count} entries, {size:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
