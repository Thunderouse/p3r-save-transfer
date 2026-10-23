"""The GUI must work with no network at all."""

from __future__ import annotations

import socket
from pathlib import Path

from ui.bridge import Bridge

import synth

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
FORBIDDEN = ("urllib", "requests", "http.client", "socket", "webbrowser", "ftplib")


def test_ui_never_imports_network_modules():
    offenders = []
    for path in UI_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN:
            if f"import {token}" in text or f"from {token}" in text:
                offenders.append(f"{path.name}: {token}")
    assert offenders == []


def test_conversion_works_with_sockets_disabled(tmp_path, monkeypatch):
    local = tmp_path / "LocalAppData"
    roaming = tmp_path / "Roaming"
    leaf = (
        local
        / "Packages"
        / "SEGAofAmericaInc.L0cb6b3aea_s751p9cej88mt"
        / "SystemAppData"
        / "wgs"
        / "user"
        / "00000000-1111-2222-3333-444444444444"
        / "8F3A1C2E-0B4D-4E6F-9A11-223344556677"
    )
    leaf.mkdir(parents=True)
    source = leaf / "CAFE0001"
    source.write_bytes(synth.save({synth.word_index("level"): 9}))
    steam = roaming / "Sega" / "P3R" / "Steam" / "11111111111111111"
    steam.mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))

    def no_network(*_args, **_kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)

    bridge = Bridge()
    scan = bridge.scan(str(local))
    assert scan["ok"] and scan["count"] == 1
    result = bridge.convert(
        [{"path": str(source), "slot": 1}], str(steam), {"backup": True}
    )
    assert result["ok"] and result["converted"] == 1
    assert (steam / "SaveData001.sav").is_file()


def test_assets_are_local(tmp_path):
    """No CDN references in the UI sources: everything ships in assets/."""
    for path in UI_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        assert "http://" not in text and "https://" not in text, path
