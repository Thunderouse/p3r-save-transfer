"""The version lives in one place; everything else must agree with it."""

from __future__ import annotations

import re
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    tomllib = None  # type: ignore[assignment]

from p3r_transfer import __version__

ROOT = Path(__file__).resolve().parent.parent


def pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if tomllib is not None:
        return tomllib.loads(text)["project"]["version"]
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match is not None
    return match.group(1)


def test_version_file_is_the_source():
    text = (ROOT / "src" / "p3r_transfer" / "version.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match is not None
    assert match.group(1) == __version__


def test_pyproject_matches_the_code():
    assert pyproject_version() == __version__, (
        "pyproject.toml and src/p3r_transfer/version.py disagree"
    )


def test_version_has_a_semver_shape():
    core, _, pre = __version__.partition("-")
    assert re.fullmatch(r"\d+\.\d+\.\d+", core), __version__
    if pre:
        assert re.fullmatch(r"(rc|beta)\.?\d*", pre), __version__


def test_cli_and_gui_read_the_same_string(monkeypatch):

    from p3r_transfer import cli

    parser = cli.build_parser()
    action = next(a for a in parser._actions if a.dest == "version")
    assert __version__ in action.version


def test_portable_zip_name_matches_the_version():
    """The build script derives the archive name from __version__."""
    script = (ROOT / "tools" / "build_portable.ps1").read_text(encoding="utf-8")
    assert "P3R-Save-Transfer-$version-win64-portable.zip" in script
