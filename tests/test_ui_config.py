"""Portable settings: app folder first, documented fallback when read-only."""

from __future__ import annotations

import json
import os
import stat

import pytest
from ui.config import DEFAULTS, Config


def test_uses_app_folder_when_writable(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    cfg = Config(tmp_path)
    cfg.update(lang="en", destination="D:/steam")
    assert (tmp_path / "config.json").is_file()
    assert cfg.used_fallback is False

    again = Config(tmp_path)
    assert again.get("lang") == "en"
    assert again.get("destination") == "D:/steam"


def test_survives_corrupt_json(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    (tmp_path / "config.json").write_text("{not json", encoding="utf-8")
    cfg = Config(tmp_path)
    assert cfg.get("lang") == DEFAULTS["lang"]


def test_ignores_wrong_types(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    (tmp_path / "config.json").write_text(
        json.dumps({"lang": "de", "backup": "yes", "source": 5}), encoding="utf-8"
    )
    cfg = Config(tmp_path)
    assert cfg.get("lang") == "ru"
    assert cfg.get("backup") is True
    assert cfg.get("source") == ""


@pytest.mark.skipif(
    os.name == "nt", reason="chmod не делает папку только для чтения в Windows"
)
def test_read_only_app_folder_falls_back(tmp_path, monkeypatch):
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    notices: list[str] = []
    cfg = Config(app_dir, on_notice=notices.append)
    os.chmod(app_dir, stat.S_IRUSR | stat.S_IXUSR)
    try:
        cfg.update(lang="en")
    finally:
        os.chmod(app_dir, stat.S_IRWXU)
    fallback = local / "P3R-Save-Transfer" / "config.json"
    assert fallback.is_file()
    assert cfg.used_fallback is True
    assert notices and notices[-1].endswith("config.json")
    assert json.loads(fallback.read_text(encoding="utf-8"))["lang"] == "en"


def test_fallback_is_read_when_app_folder_has_none(tmp_path, monkeypatch):
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    fallback = local / "P3R-Save-Transfer" / "config.json"
    fallback.parent.mkdir(parents=True)
    fallback.write_text(json.dumps({"lang": "en"}), encoding="utf-8")
    cfg = Config(tmp_path / "app")
    assert cfg.get("lang") == "en"
    assert cfg.used_fallback is True


def test_save_failure_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))

    def explode(*_args, **_kwargs):
        raise OSError("nope")

    cfg = Config(tmp_path)
    monkeypatch.setattr(Config, "_write_atomic", staticmethod(explode))
    cfg.update(lang="en")  # must not raise
    assert cfg.get("lang") == "en"


def test_defaults_are_complete():
    assert set(DEFAULTS) >= {"lang", "source", "destination", "steam_id", "backup", "dry_run"}
