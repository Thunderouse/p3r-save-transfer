"""The bridge is the only thing the window calls; it must never raise."""

from __future__ import annotations

import hashlib
import os

import pytest
from ui.bridge import Bridge

import synth
from p3r_transfer import codec

USER = "25354ABCDEF01234"
GUID = "8F3A1C2E-0B4D-4E6F-9A11-223344556677"
STEAM_ID = "11111111111111111"


@pytest.fixture
def env(tmp_path, monkeypatch):
    local = tmp_path / "LocalAppData"
    roaming = tmp_path / "Roaming"
    wgs = local / "Packages" / "SEGAofAmericaInc.L0cb6b3aea_s751p9cej88mt" / "SystemAppData" / "wgs"
    user = wgs / USER
    for index in range(3):
        leaf = user / f"0000000{index}-1111-2222-3333-44444444444{index}" / GUID
        leaf.mkdir(parents=True)
        words = {
            synth.word_index("play_time"): (index + 1) * 3600 * 30,
            synth.word_index("level"): 5 + index,
        }
        (leaf / f"CAFE{index}").write_bytes(synth.save(words) + b"\x00" * 2048)
        (leaf / "container.0000").write_bytes(b"\x00" * 8)
    steam = roaming / "Sega" / "P3R" / "Steam" / STEAM_ID
    steam.mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))
    return {"wgs": wgs, "steam": steam, "local": local, "roaming": roaming}


def test_version():
    info = Bridge().version()
    assert info["ok"] and info["version"].count(".") == 2


def test_scan_tree(env):
    result = Bridge().scan(str(env["wgs"]))
    assert result["ok"]
    assert result["count"] == 3
    assert [item["slot"] for item in result["saves"]] == [1, 2, 3]
    assert {item["kind"] for item in result["saves"]} == {"gvas"}
    assert all(item["level"] for item in result["saves"])
    assert result["saves"][0]["play_time"] == 3600


def test_scan_single_file(env):
    path = next(env["wgs"].rglob("CAFE1"))
    result = Bridge().scan(str(path))
    assert result["ok"] and result["count"] == 1
    assert result["source_kind"] == "file"
    assert result["saves"][0]["level"] == 6


def test_scan_steam_folder(env):
    (env["steam"] / "SaveData0004.sav").write_bytes(synth.sealed_save())
    result = Bridge().scan(str(env["steam"].parent))
    assert result["ok"]
    assert result["source_kind"] == "steam"
    assert result["saves"][0]["slot"] == 4
    assert result["saves"][0]["kind"] == "steam"


def test_scan_rejects_junk(tmp_path):
    junk = tmp_path / "junk.sav"
    junk.write_bytes(b"JUNK" + b"\x00" * 64)
    result = Bridge().scan(str(junk))
    assert result["ok"] is False
    assert "0x4B4E554A" in result["error"]


def test_scan_missing_path(tmp_path):
    result = Bridge().scan(str(tmp_path / "nope"))
    assert result["ok"] is False
    assert result["error"]


def test_default_source(env):
    info = Bridge().default_source()
    assert info["ok"] and info["exists"] is True


def test_detect_dest(env):
    info = Bridge().detect_dest()
    assert info["ok"]
    assert [a["id"] for a in info["accounts"]] == [STEAM_ID]
    assert info["path"] == str(env["steam"])


def test_detect_dest_multiple_accounts(env):
    other = env["roaming"] / "Sega" / "P3R" / "Steam" / "11111111111111112"
    other.mkdir()
    info = Bridge().detect_dest("11111111111111112")
    assert [a["id"] for a in info["accounts"]] == ["11111111111111111", "11111111111111112"]
    assert info["path"] == str(other)


def test_plan_flags_conflicts(env):
    source = next(env["wgs"].rglob("CAFE0"))
    (env["steam"] / "SaveData001.sav").write_bytes(b"x")
    plan = Bridge().plan_convert(
        [{"path": str(source), "slot": 1}], str(env["steam"]), {"force": False}
    )
    assert plan["ok"]
    assert plan["conflicts"] == ["SaveData001.sav"]
    assert plan["plan"][0]["exists"] is True


def test_convert_writes_and_keeps_sources(env):
    bridge = Bridge()
    paths = sorted(env["wgs"].rglob("CAFE*"))
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    result = bridge.convert(
        [{"path": str(p), "slot": index + 1} for index, p in enumerate(paths)],
        str(env["steam"]),
        {"backup": True},
    )
    assert result["ok"] and result["converted"] == 3 and result["failed"] == 0
    for index, path in enumerate(paths):
        target = env["steam"] / f"SaveData{index + 1:03d}.sav"
        assert target.exists()
        assert codec.decrypt(target.read_bytes()) == path.read_bytes()
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    assert before == after


def test_convert_dry_run(env):
    source = next(env["wgs"].rglob("CAFE0"))
    result = Bridge().convert(
        [{"path": str(source), "slot": 1}], str(env["steam"]), {"dry_run": True}
    )
    assert result["ok"] and result["dry_run"] is True
    assert list(env["steam"].glob("SaveData*.sav")) == []


def test_convert_force_overwrites_with_backup(env):
    source = next(env["wgs"].rglob("CAFE0"))
    existing = env["steam"] / "SaveData001.sav"
    existing.write_bytes(b"old")
    result = Bridge().convert(
        [{"path": str(source), "slot": 1}], str(env["steam"]), {"backup": True, "force": True}
    )
    assert result["converted"] == 1
    assert result["backup"] and os.path.isdir(result["backup"])
    assert (env["steam"] / "SaveData001.sav").read_bytes() != b"old"


def test_convert_reports_per_file_errors(env, tmp_path):
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (blocked / "SaveData001.sav").mkdir()  # a directory cannot be replaced by a file
    source = next(env["wgs"].rglob("CAFE0"))
    result = Bridge().convert(
        [{"path": str(source), "slot": 1}], str(blocked), {"backup": False}
    )
    assert result["ok"] and result["failed"] == 1
    assert result["items"][0]["status"] == "error"
    assert "занят" in result["items"][0]["error"].lower() or result["items"][0]["error"]


def test_convert_cancel_marks_the_rest(env):
    source = sorted(env["wgs"].rglob("CAFE*"))
    state = {"count": 0}

    def cancel():
        state["count"] += 1
        return state["count"] > 1

    result = Bridge().convert(
        [{"path": str(p), "slot": i + 1} for i, p in enumerate(source)],
        str(env["steam"]),
        {"backup": False},
        should_cancel=cancel,
    )
    assert result["cancelled"] is True
    statuses = [item["status"] for item in result["items"]]
    assert statuses.count("cancelled") >= 2


def test_convert_progress_events(env):
    events = []
    source = [
        {"path": str(p), "slot": i + 1} for i, p in enumerate(sorted(env["wgs"].rglob("CAFE*")))
    ]
    Bridge().convert(source, str(env["steam"]), {"backup": False}, on_progress=events.append)
    assert events[0]["event"] == "start"
    assert events[-1]["event"] == "done"
    assert any(event.get("status") == "ok" for event in events)


def test_convert_without_items(env):
    result = Bridge().convert([], str(env["steam"]), {})
    assert result["ok"] is False
    assert result["error"]


def test_convert_requires_destination(env):
    source = next(env["wgs"].rglob("CAFE0"))
    result = Bridge().convert([{"path": str(source), "slot": 1}], "", {})
    assert result["ok"] is False


def test_open_folder_uses_injected_opener(tmp_path):
    seen = []
    bridge = Bridge(opener=seen.append)
    assert bridge.open_folder(str(tmp_path))["ok"] is True
    assert seen == [str(tmp_path)]


def test_open_folder_missing(tmp_path):
    result = Bridge(opener=lambda _p: None).open_folder(str(tmp_path / "nope"))
    assert result["ok"] is False


def test_invalid_inputs_never_raise():
    bridge = Bridge()
    for bad in (None, "", "   ", 0):
        result = bridge.scan(bad)
        assert isinstance(result, dict) and "ok" in result
    assert isinstance(bridge.detect_dest(), dict)
