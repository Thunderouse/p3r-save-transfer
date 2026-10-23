"""Fail the build if the git index contains secrets, personal data or binaries.

Scans **tracked files only** (``git ls-files``), so a locally built ``dist/``
folder and the release archive are out of scope - they are shipped as release
assets, not committed.

Run locally or in CI:

    python tools/repo_guard.py            # 0 = clean
    python tools/repo_guard.py --verbose  # also list the files that were checked

Findings are printed as ``path:line: reason`` without the matched text, so a
secret never ends up in the CI log.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Token prefixes are assembled from fragments so this file does not match itself.
GH_PREFIX = "gh" + "p_"
GHO_PREFIX = "gh" + "o_"
PAT_PREFIX = "github" + "_pat_"
AWS_PREFIX = "AK" + "IA"
KEY_HEADER = "-----BEGIN "
KEY_MARKER = "PRIVATE" + " KEY"

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("GitHub token", re.compile(GH_PREFIX + r"[A-Za-z0-9]{30,}")),
    ("GitHub OAuth token", re.compile(GHO_PREFIX + r"[A-Za-z0-9]{30,}")),
    ("GitHub fine-grained token", re.compile(PAT_PREFIX + r"[A-Za-z0-9_]{20,}")),
    ("AWS access key", re.compile(AWS_PREFIX + r"[0-9A-Z]{16}")),
    ("private key block", re.compile(KEY_HEADER + r"[A-Z ]*" + KEY_MARKER + r"-----")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("bearer token", re.compile(r"Authorization:\s*Bearer\s+\S+")),
    ("password literal", re.compile(r"(?i)\bpassword\s*[=:]\s*[^\s\"']+")),
    ("token literal", re.compile(r"(?i)\btoken\s*[=:]\s*[A-Za-z0-9_\-]{12,}")),
]

PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("absolute Windows user path", re.compile(r"[A-Za-z]:\\Users\\")),
    ("SteamID64", re.compile(r"7656119\d{10}")),
    ("unix home/opt path", re.compile(r"(?<![\w.])/(?:opt|home)/[A-Za-z0-9_.-]+")),
    ("IPv4 address", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
]

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
EMAIL_ALLOW = ("users.noreply.github.com", "example.com", "example.org", "noreply.github.com")

FORBIDDEN_NAMES = {"config.json", ".env", "hosts.yml", "id_rsa"}
FORBIDDEN_SUFFIXES = {
    ".sav",
    ".exe",
    ".dll",
    ".zip",
    ".whl",
    ".pdb",
    ".lib",
    ".log",
    ".pem",
    ".key",
    ".tmp",
    ".pyc",
}
BINARY_SUFFIXES = {".png", ".ico", ".ttf", ".woff2", ".jpg", ".jpeg", ".zip", ".gz"}

ALLOWED_IPV4 = {"0.0.0.0", "127.0.0.1", "1.0.0.0"}  # 1.0.0.0 = manifest version


def tracked_files() -> list[Path]:
    try:
        output = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"repo_guard: git ls-files failed: {exc}") from exc
    return [ROOT / name for name in output.split("\0") if name]


def scan_paths(paths: list[Path]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        suffixes = [suffix.lower() for suffix in path.suffixes]
        name = path.name.lower()
        if name in FORBIDDEN_NAMES:
            findings.append(f"{relative}: forbidden file name")
            continue
        if any(suffix in FORBIDDEN_SUFFIXES for suffix in suffixes):
            findings.append(f"{relative}: forbidden extension")
            continue
        if any(suffix in BINARY_SUFFIXES for suffix in suffixes) or name.endswith(".woff2"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            findings.append(f"{relative}: unreadable ({exc})")
            continue
        findings.extend(scan_text(relative, text))
    return findings


def scan_text(relative: str, text: str) -> list[str]:
    findings: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for reason, pattern in SECRET_PATTERNS:
            if pattern.search(line):
                findings.append(f"{relative}:{number}: {reason}")
        for reason, pattern in PII_PATTERNS:
            for match in pattern.finditer(line):
                value = match.group(0)
                if reason == "IPv4 address" and value in ALLOWED_IPV4:
                    continue
                if reason == "IPv4 address":
                    parts = [int(piece) for piece in value.split(".")]
                    if all(0 <= piece <= 255 for piece in parts) and parts[0] == 127:
                        continue
                findings.append(f"{relative}:{number}: {reason}")
        if relative != "LICENSE":
            for match in EMAIL_RE.finditer(line):
                address = match.group(0)
                if any(address.endswith(allowed) for allowed in EMAIL_ALLOW):
                    continue
                findings.append(f"{relative}:{number}: e-mail address")
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan tracked files for secrets/PII")
    parser.add_argument("--verbose", action="store_true", help="list every checked file")
    args = parser.parse_args(argv)

    paths = tracked_files()
    if args.verbose:
        for path in paths:
            print(path.relative_to(ROOT).as_posix())
    findings = scan_paths(paths)
    if findings:
        print(f"repo_guard: {len(findings)} finding(s) in {len(paths)} tracked files")
        for finding in findings:
            print(f"  {finding}")
        return 1
    print(f"repo_guard: clean ({len(paths)} tracked files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
