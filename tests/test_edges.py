"""Edge paths that the happy-path tests never touch (kept honest by coverage)."""

from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

import synth
from p3r_transfer import cli, convert, discovery, fsutil
from p3r_transfer.errors import LockedFileError, SaveNotFoundError, TransferError


def run(argv, capsys):
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# ------------------------------------------------------------------- fsutil
def test_read_head_missing_file(tmp_path):
    with pytest.raises(SaveNotFoundError):
        fsutil.read_head(tmp_path / "nope.sav")


def test_read_head_permission_denied(tmp_path, monkeypatch):
    target = tmp_path / "x.sav"
    target.write_bytes(b"GVAS")

    def boom(*_args, **_kwargs):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(fsutil, "open", boom, raising=False)
    with pytest.raises(LockedFileError):
        fsutil.read_head(target)


def test_read_head_oserror_is_wrapped(tmp_path, monkeypatch):
    target = tmp_path / "x.sav"
    target.write_bytes(b"GVAS")

    def boom(*_args, **_kwargs):
        raise OSError(5, "io error")

    monkeypatch.setattr(fsutil, "open", boom, raising=False)
    with pytest.raises(TransferError):
        fsutil.read_head(target)


def test_write_bytes_atomic_creates_parents(tmp_path):
    target = tmp_path / "a" / "b" / "SaveData001.sav"
    fsutil.write_bytes_atomic(target, b"GVAS" + b"\x00" * 40)
    assert target.exists()


def test_write_bytes_atomic_permission_error(tmp_path, monkeypatch):
    target = tmp_path / "x.sav"

    def boom(*_args, **_kwargs):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(fsutil, "open", boom, raising=False)
    with pytest.raises(LockedFileError):
        fsutil.write_bytes_atomic(target, b"GVAS")


def test_free_space_reports_something(tmp_path):
    assert fsutil.free_space(tmp_path) > 0


def test_free_space_unknown_path_returns_zero(monkeypatch):
    class Fake:
        def exists(self):
            return False

    monkeypatch.setattr(fsutil.Path, "exists", lambda self: False)
    assert fsutil.free_space("/nonexistent/for/sure") == 0
    assert Fake().exists() is False


# ---------------------------------------------------------------- discovery
def test_inspect_missing_file(tmp_path):
    verdict = discovery.inspect_file(tmp_path / "gone.sav")
    assert isinstance(verdict, discovery.SkippedFile)
    assert verdict.reason == discovery.SKIP_UNREADABLE


def test_scan_tree_survives_unreadable_directory(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX permissions")
    root = tmp_path / "wgs"
    sub = root / "u" / "locked"
    sub.mkdir(parents=True)
    (sub / "C0FFEE01").write_bytes(synth.full_save())
    os.chmod(sub, 0)
    try:
        result = discovery.scan_tree(root)
    finally:
        os.chmod(sub, 0o755)
    assert result.saves == []


def test_scan_steam_reads_accounts(tmp_path):
    account = tmp_path / "Steam" / "11111111111111111"
    account.mkdir(parents=True)
    (account / "SaveData001.sav").write_bytes(synth.full_save())
    (account / "notes.txt").write_bytes(b"nope")
    result = discovery.scan_steam(tmp_path / "Steam")
    assert len(result.saves) == 1
    assert [item.reason for item in result.skipped] == [discovery.SKIP_NOT_A_SAVE]


def test_scan_result_helpers(tmp_path):
    result = discovery.ScanResult(root=tmp_path)
    other = discovery.ScanResult(
        root=tmp_path,
        skipped=[discovery.SkippedFile(path=tmp_path / "a", reason="empty")],
        metadata=[tmp_path / "container.1"],
    )
    result.extend(other)
    assert result.total_seen == 2
    assert result.reason_summary() == "empty: 1"


# ---------------------------------------------------------------------- cli
def test_explain_missing_file(tmp_path, capsys):
    code, _, err = run(["explain", str(tmp_path / "nope.sav")], capsys)
    assert code == cli.EXIT_FATAL
    assert "не найден" in err


def test_explain_directory(tmp_path, capsys):
    code, _, err = run(["explain", str(tmp_path)], capsys)
    assert code == cli.EXIT_FATAL
    assert "каталог" in err


def test_explain_valid_save(tmp_path, capsys):
    target = tmp_path / "SaveData009.sav"
    target.write_bytes(synth.full_save())
    code, out, _ = run(["explain", str(target)], capsys)
    assert code == cli.EXIT_OK
    assert "save-gvas" in out
    assert "конвертировать" in out


def test_scan_strict_with_only_junk(tmp_path, capsys):
    root = tmp_path / "wgs"
    root.mkdir()
    (root / "notes.txt").write_bytes(b"hello")
    code, out, err = run(["scan", "--wgs", str(root), "--strict"], capsys)
    assert code == cli.EXIT_FATAL
    assert "Строгий режим" in err


def test_convert_signature_backup(tmp_path, capsys):
    source = tmp_path / "in.sav"
    source.write_bytes(synth.full_save())
    out = tmp_path / "out"
    code, out_text, err = run(["convert", "--in", str(source), "--out-dir", str(out)], capsys)
    assert code == cli.EXIT_OK, err
    assert "Бэкап" not in out_text  # nothing to back up on the first write


def test_convert_dry_run_does_not_write(tmp_path, capsys):
    source = tmp_path / "in.sav"
    source.write_bytes(synth.full_save())
    out = tmp_path / "out"
    code, text, err = run(
        ["convert", "--in", str(source), "--out-dir", str(out), "--dry-run"], capsys
    )
    assert code == cli.EXIT_OK, err
    assert not out.exists()


def test_convert_json_shape(tmp_path, capsys):
    source = tmp_path / "in.sav"
    source.write_bytes(synth.full_save())
    code, text, _ = run(
        ["convert", "--in", str(source), "--out-dir", str(tmp_path / "o"), "--json"], capsys
    )
    assert code == cli.EXIT_OK
    payload = json.loads(text)
    assert payload["status"] == "ok"
    assert payload["slot"] == 1


def test_verify_reports_problem(tmp_path, capsys):
    bad = tmp_path / "bad.sav"
    bad.write_bytes(b"XXXX" + b"\x00" * 64)
    code, out, _ = run(["verify", str(bad)], capsys)
    assert code == cli.EXIT_FATAL
    assert "ОШИБКА" in out


def test_install_dry_run_and_busy_slot(tmp_path, monkeypatch, capsys):
    account = tmp_path / "Roaming" / "Sega" / "P3R" / "Steam" / "11111111111111111"
    account.mkdir(parents=True)
    source = tmp_path / "in.sav"
    source.write_bytes(synth.full_save())
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))

    code, out, err = run(
        ["install", "--in", str(source), "--slot", "2", "--dry-run"], capsys
    )
    assert code == cli.EXIT_OK, err
    assert "не записан" in out
    assert not (account / "SaveData002.sav").exists()

    (account / "SaveData002.sav").write_bytes(b"taken")
    code, _, err = run(["install", "--in", str(source), "--slot", "2"], capsys)
    assert code == cli.EXIT_FATAL
    assert "занят" in err


def test_install_rejects_foreign_file(tmp_path, monkeypatch, capsys):
    account = tmp_path / "Roaming" / "Sega" / "P3R" / "Steam" / "11111111111111111"
    account.mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    bad = tmp_path / "notes.txt"
    bad.write_bytes(b"hello world")
    code, _, err = run(["install", "--in", str(bad), "--slot", "1"], capsys)
    assert code == cli.EXIT_FATAL
    assert "не сохранение" in err


def test_module_entry_point_prints_version():
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-m", "p3r_transfer", "--version"],
        capture_output=True,
        text=True,
        env=dict(os.environ, PYTHONPATH=str(root / "src")),
    )
    assert result.returncode == 0
    assert "p3r-save-transfer" in result.stdout


def test_runpy_main(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["p3r-save-transfer", "--version"])
    with pytest.raises(SystemExit) as info:
        runpy.run_module("p3r_transfer.__main__", run_name="__main__")
    assert info.value.code == 0


def test_convert_ensure_space_message(tmp_path, monkeypatch):
    monkeypatch.setattr(convert.fsutil, "free_space", lambda _p: 5)
    with pytest.raises(TransferError) as info:
        convert.ensure_free_space(tmp_path, 1000)
    assert "Недостаточно места" in str(info.value)


# ---------------------------------------------------- more CLI error branches
def test_install_with_two_accounts_needs_explicit_id(tmp_path, monkeypatch, capsys):
    roaming = tmp_path / "Roaming"
    for steam_id in ("11111111111111111", "22222222222222222"):
        (roaming / "Sega" / "P3R" / "Steam" / steam_id).mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(roaming))
    source = tmp_path / "in.sav"
    source.write_bytes(synth.full_save())
    code, _, err = run(["install", "--in", str(source), "--slot", "1"], capsys)
    assert code == cli.EXIT_USAGE
    assert "Steam-аккаунтов" in err


def test_install_unknown_steam_id(tmp_path, monkeypatch, capsys):
    account = tmp_path / "Roaming" / "Sega" / "P3R" / "Steam" / "11111111111111111"
    account.mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    source = tmp_path / "in.sav"
    source.write_bytes(synth.full_save())
    code, _, err = run(
        ["install", "--in", str(source), "--slot", "1", "--steam-id", "999"], capsys
    )
    assert code == cli.EXIT_USAGE
    assert "не найден" in err


def test_install_slot_out_of_range(tmp_path, monkeypatch, capsys):
    account = tmp_path / "Roaming" / "Sega" / "P3R" / "Steam" / "11111111111111111"
    account.mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    source = tmp_path / "in.sav"
    source.write_bytes(synth.full_save())
    code, _, err = run(["install", "--in", str(source), "--slot", "1000"], capsys)
    assert code == cli.EXIT_USAGE
    assert "Слот" in err


def test_install_force_overwrites_and_backs_up(tmp_path, monkeypatch, capsys):
    account = tmp_path / "Roaming" / "Sega" / "P3R" / "Steam" / "11111111111111111"
    account.mkdir(parents=True)
    (account / "SaveData001.sav").write_bytes(b"old")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    source = tmp_path / "in.sav"
    source.write_bytes(synth.full_save())
    code, out, err = run(
        ["install", "--in", str(source), "--slot", "1", "--force"], capsys
    )
    assert code == cli.EXIT_OK, err
    assert "Бэкап" in out
    assert list(account.glob("_p3r_backup_*"))


def test_keyboard_interrupt_exit_code(monkeypatch, capsys):
    def boom(_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "cmd_scan", boom)
    assert cli.main(["scan", "--wgs", "."]) == 130


def test_run_wrapper_exits(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["p3r-save-transfer", "--version"])
    with pytest.raises(SystemExit) as info:
        cli.run()
    assert info.value.code == 0


def test_scan_with_steam_flag(tmp_path, monkeypatch, capsys):
    local = tmp_path / "LocalAppData"
    roaming = tmp_path / "Roaming"
    wgs = local / "Packages" / "SEGAofAmericaInc.L0cb6b3aea_s751p9cej88mt" / "SystemAppData" / "wgs"
    (wgs / "u" / "g" / "c").mkdir(parents=True)
    (wgs / "u" / "g" / "c" / "C0FFEE01").write_bytes(synth.full_save())
    account = roaming / "Sega" / "P3R" / "Steam" / "11111111111111111"
    account.mkdir(parents=True)
    (account / "SaveData001.sav").write_bytes(synth.sealed_save())
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))
    code, out, _ = run(["scan", "--wgs", str(wgs), "--steam"], capsys)
    assert code == cli.EXIT_OK
    assert "Steam 1" in out


def test_sharing_violation_maps_to_locked(tmp_path, monkeypatch):
    target = tmp_path / "x.sav"
    target.write_bytes(b"GVAS")

    def boom(*_args, **_kwargs):
        error = OSError(32, "sharing violation")
        error.winerror = 32
        raise error

    monkeypatch.setattr(fsutil, "open", boom, raising=False)
    with pytest.raises(LockedFileError):
        fsutil.read_head(target)


def test_scan_steam_include_skipped(tmp_path):
    account = tmp_path / "Steam" / "11111111111111111"
    account.mkdir(parents=True)
    (account / "SaveData001.sav").write_bytes(b"NOPE" + b"\x00" * 60)
    result = discovery.scan_steam(tmp_path / "Steam", include_skipped=True)
    assert len(result.saves) == 1
    assert result.saves[0].kind is discovery.SaveKind.UNKNOWN
    assert result.skipped == []


def test_legacy_write_bytes_reports_locked_file(tmp_path, monkeypatch):
    target = tmp_path / "x.sav"

    def boom(*_args, **_kwargs):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(fsutil, "open", boom, raising=False)
    with pytest.raises(LockedFileError):
        fsutil.write_bytes(target, b"data")


def test_convert_all_json_payload(tmp_path, monkeypatch, capsys):
    wgs = tmp_path / "wgs"
    (wgs / "u" / "g" / "c").mkdir(parents=True)
    (wgs / "u" / "g" / "c" / "C0FFEE01").write_bytes(synth.full_save())
    (wgs / "u" / "g" / "c" / "notes.txt").write_bytes(b"hi")
    code, out, _ = run(
        ["convert-all", "--wgs", str(wgs), "--out-dir", str(tmp_path / "o"), "--json"], capsys
    )
    assert code == cli.EXIT_PARTIAL
    payload = json.loads(out)
    assert payload["converted"] == 1
    assert payload["skipped"][0]["reason"] == "not-a-save"
