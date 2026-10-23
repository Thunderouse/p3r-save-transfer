"""Append asset checksums to the release notes template.

    py -3 tools/make_release_notes.py --version 0.1.0-beta \\
        --zip-sha <sha> --sha-sha <sha> --out notes.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "docs" / "RELEASE_NOTES.md"


def build(version: str, zip_sha: str, sha_sha: str) -> str:
    archive = f"P3R-Save-Transfer-{version}-win64-portable.zip"
    body = TEMPLATE.read_text(encoding="utf-8").rstrip()
    return (
        f"{body}\n\n"
        "## SHA-256\n\n"
        "```\n"
        f"{zip_sha}  {archive}\n"
        f"{sha_sha}  {archive}.sha256\n"
        "```\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--zip-sha", required=True)
    parser.add_argument("--sha-sha", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    args.out.write_text(build(args.version, args.zip_sha, args.sha_sha), encoding="utf-8")
    print(f"{args.out} ({args.out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
