"""Behaviour around files that are not saves (release 1.0 contract).

A save folder is a normal user folder: notes, ``desktop.ini``, ``Thumbs.db``,
half-downloaded files, backups, a truncated save.  None of that may abort a
scan or a batch conversion, and none of it may be silently ignored either -
every skipped file is reported with a stable reason code.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import synth
from p3r_transfer import cli, discovery

USER_ID = "25354ABCDEF01234"
CONTAINER = "8F3A1C2E-0B4D-4E6F-9A11-223344556677"
LEAF_GUID = "00000001-1111-2222-3333-444444444411"


def run(argv, capsys):
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def build_dirty(tmp_path, monkeypatch, *, valid: int = 2) -> tuple[Path, Path]:
    """The exact scenario from the bug report, generated from scratch."""
    wgs = tmp_path / "LocalAppData" / discovery.WGS_RELATIVE
    leaf = wgs / USER_ID / LEAF_GUID / CONTAINER
    leaf.mkdir(parents=True)
    for index in range(1, valid + 1):
        (leaf / f"C0FFEE0{index}").write_bytes(synth.full_save())
    (leaf / "container.1").write_bytes(b"\x00" * 64)
    (leaf / "desktop.ini").write_bytes(b"[.ShellClassInfo]\r\n")
    (leaf / "Thumbs.db").write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 60)
    (leaf / "notes.txt").write_text("my notes\n", encoding="utf-8")
    (leaf / "empty.sav").write_bytes(b"")
    (leaf / "SaveData003.sav").write_bytes(b"NOT A SAVE" + bytes(range(40)))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    return wgs, leaf


def skipped_map(out_dir: Path) -> dict[str, Path]:
    return {path.name: path for path in out_dir.iterdir()} if out_dir.exists() else {}


# 1 --------------------------------------------------------------------------
def test_dirty_directory_soft_mode(tmp_path, monkeypatch, capsys):
    wgs, _ = build_dirty(tmp_path, monkeypatch)
    out_dir = tmp_path / "out"
    code, out, err = run(["convert-all", "--wgs", str(wgs), "--out-dir", str(out_dir)], capsys)

    assert code == cli.EXIT_PARTIAL, err
    assert sorted(p.name for p in out_dir.glob("SaveData*.sav")) == [
        "SaveData001.sav",
        "SaveData002.sav",
    ]
    assert "Сконвертировано 2, пропущено 5." in out
    for name, reason in (
        ("notes.txt", "not-a-save"),
        ("SaveData003.sav", "not-a-save"),
        ("desktop.ini", "os-junk"),
        ("Thumbs.db", "os-junk"),
        ("empty.sav", "empty"),
    ):
        assert name in out
        assert reason in out
    # container.* is bookkeeping, not a problem
    assert "container.1" not in out


def test_skipped_reasons_are_exposed_in_json(tmp_path, monkeypatch, capsys):
    wgs, _ = build_dirty(tmp_path, monkeypatch)
    code, out, _ = run(["scan", "--wgs", str(wgs), "--json"], capsys)
    assert code == cli.EXIT_OK
    payload = json.loads(out)
    counts = payload["counts"]
    assert counts["saves"] == 2
    assert counts["skipped"] == 5
    assert counts["metadata"] == 1
    assert counts["by_reason"] == {"empty": 1, "not-a-save": 2, "os-junk": 2}
    reasons = {item["reason"] for item in payload["skipped"]}
    assert {"not-a-save", "os-junk", "empty"} <= reasons
    for item in payload["skipped"]:
        assert item["category"] == "skipped"
        assert item["path"] and not item["path"].startswith("/")
        assert ":" not in item["path"][:2]  # no "C:" / drive letters
    assert all(not record["path"].startswith("/") for record in payload["saves"])


# 2 --------------------------------------------------------------------------
def test_strict_mode_refuses_and_writes_nothing(tmp_path, monkeypatch, capsys):
    wgs, _ = build_dirty(tmp_path, monkeypatch)
    out_dir = tmp_path / "out"
    code, out, err = run(
        ["convert-all", "--wgs", str(wgs), "--out-dir", str(out_dir), "--strict"], capsys
    )
    assert code == cli.EXIT_FATAL
    assert "Строгий режим" in err
    assert "not-a-save" in out
    assert not out_dir.exists()


# 3 --------------------------------------------------------------------------
def test_convert_explicit_foreign_file_is_an_error(tmp_path, monkeypatch, capsys):
    _, leaf = build_dirty(tmp_path, monkeypatch)
    code, out, err = run(
        ["convert", "--in", str(leaf / "notes.txt"), "--out-dir", str(tmp_path / "out")], capsys
    )
    assert code == cli.EXIT_FATAL
    assert "0x" in err
    assert "не сохранение P3R" in err
    assert "6d 79 20 6e" in err  # first bytes of "my notes\n" in hex


# 4 --------------------------------------------------------------------------
def test_empty_directory_is_partial_not_a_traceback(tmp_path, monkeypatch, capsys):
    empty = tmp_path / "wgs"
    empty.mkdir()
    code, _, err = run(
        ["convert-all", "--wgs", str(empty), "--out-dir", str(tmp_path / "out")], capsys
    )
    assert code == cli.EXIT_PARTIAL
    assert "Сохранений не найдено" in err
    assert "Traceback" not in err


# 5 --------------------------------------------------------------------------
def test_dry_run_writes_nothing_at_all(tmp_path, monkeypatch, capsys):
    wgs, _ = build_dirty(tmp_path, monkeypatch)
    out_dir = tmp_path / "out"
    code, out, _ = run(
        ["convert-all", "--wgs", str(wgs), "--out-dir", str(out_dir), "--dry-run"], capsys
    )
    assert code == cli.EXIT_PARTIAL
    assert "файлы не записаны" in out
    assert not out_dir.exists()
    assert not list(tmp_path.rglob("_p3r_backup_*"))


# 6 --------------------------------------------------------------------------
def test_cyrillic_spaces_and_long_paths(tmp_path, monkeypatch, capsys):
    wgs = tmp_path / "wgs"
    deep = wgs / USER_ID / "очень длинная папка с пробелами" / CONTAINER
    for index in range(6):
        deep = deep / ("сегмент_" + "x" * 40 + str(index))
    deep.mkdir(parents=True)
    assert len(str(deep)) > 260
    (deep / "SaveData001.sav").write_bytes(synth.full_save())
    (deep / "мои заметки.txt").write_bytes(b"hello")

    code, out, _ = run(["convert-all", "--wgs", str(wgs), "--out-dir", str(tmp_path / "out")], capsys)
    assert code == cli.EXIT_PARTIAL
    assert "Сконвертировано 1, пропущено 1." in out
    assert "not-a-save" in out
    assert (tmp_path / "out" / "SaveData001.sav").exists()


# 7 --------------------------------------------------------------------------
@pytest.mark.skipif(os.name == "nt", reason="POSIX chmod; Windows has no mode bits")
def test_locked_file_is_unreadable_and_others_still_convert(tmp_path, monkeypatch, capsys):
    wgs, leaf = build_dirty(tmp_path, monkeypatch)
    locked = leaf / "locked.sav"
    locked.write_bytes(synth.full_save())
    os.chmod(locked, 0)
    try:
        code, out, _ = run(
            ["convert-all", "--wgs", str(wgs), "--out-dir", str(tmp_path / "out")], capsys
        )
    finally:
        os.chmod(locked, 0o600)

    assert code == cli.EXIT_PARTIAL
    assert "unreadable" in out
    assert "Сконвертировано 2, пропущено 6." in out


# 8 --------------------------------------------------------------------------
def test_too_large_file_is_refused_before_reading(tmp_path, monkeypatch, capsys):
    wgs, leaf = build_dirty(tmp_path, monkeypatch)
    big = leaf / "video.mp4"
    big.write_bytes(b"\x00\x00\x00\x18ftyp")
    os.truncate(big, discovery.MAX_SAVE_BYTES + 1024)
    code, out, _ = run(["scan", "--wgs", str(wgs)], capsys)
    assert code == cli.EXIT_OK
    assert "too-large" in out
    assert "32.00 МБ" in out


def test_include_skipped_tries_but_never_crashes(tmp_path, monkeypatch, capsys):
    wgs, _ = build_dirty(tmp_path, monkeypatch)
    out_dir = tmp_path / "out"
    code, out, err = run(
        [
            "convert-all",
            "--wgs",
            str(wgs),
            "--out-dir",
            str(out_dir),
            "--include-skipped",
            "--no-backup",
        ],
        capsys,
    )
    assert code == cli.EXIT_PARTIAL
    assert "Traceback" not in err
    assert sorted(p.name for p in out_dir.glob("SaveData*.sav")) == [
        "SaveData001.sav",
        "SaveData002.sav",
    ]


def test_explain_prints_magic_and_decision(tmp_path, monkeypatch, capsys):
    _, leaf = build_dirty(tmp_path, monkeypatch)
    code, out, err = run(["explain", str(leaf / "notes.txt")], capsys)
    assert code == cli.EXIT_OK
    assert "0x6E20796D" in out
    assert "Первые 16 байт" in out
    assert "not-a-save" in out
    assert "пропустить" in out


def test_scan_explain_flag_matches_command(tmp_path, monkeypatch, capsys):
    _, leaf = build_dirty(tmp_path, monkeypatch)
    code, out, _ = run(["scan", "--wgs", str(tmp_path / "wgs"), "--explain", str(leaf / "notes.txt")], capsys)
    assert code == cli.EXIT_OK
    assert "not-a-save" in out


def test_inspect_file_classifies_service_names(tmp_path):
    service = tmp_path / "container.0000"
    service.write_bytes(b"\x00" * 8)
    assert discovery.inspect_file(service) is None


def test_metadata_and_junk_are_counted_not_skipped(tmp_path, monkeypatch):
    wgs, leaf = build_dirty(tmp_path, monkeypatch)
    (leaf / "~$draft.docx").write_bytes(b"\x00" * 32)
    (leaf / "save.crdownload").write_bytes(b"GVAS" + b"\x00" * 40)
    result = discovery.scan_tree(wgs)
    assert len(result.saves) == 2
    assert result.metadata_files == 1
    reasons = result.reason_counts()
    assert reasons["os-junk"] >= 3  # desktop.ini, Thumbs.db, ~$draft.docx
    assert reasons["temp-file"] == 1
