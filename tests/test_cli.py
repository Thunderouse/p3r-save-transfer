"""CLI level tests: exit codes, Russian output, JSON, encoding safety."""

from __future__ import annotations

import io
import json
import sys

import pytest

import synth
from p3r_transfer import cli, codec, discovery

USER_ID = "25354ABCDEF01234"
CONTAINER = "8F3A1C2E-0B4D-4E6F-9A11-223344556677"
STEAM_ID = "11111111111111111"


def build_env(tmp_path, monkeypatch, *, saves=1, steam_ids=(STEAM_ID,)):
    local = tmp_path / "LocalAppData"
    roaming = tmp_path / "Roaming"
    wgs = local / discovery.WGS_RELATIVE
    user = wgs / USER_ID
    user.mkdir(parents=True)
    for index in range(saves):
        parent = f"0000000{index}-1111-2222-3333-44444444444{index}"
        leaf = user / parent / CONTAINER
        leaf.mkdir(parents=True)
        (leaf / f"C8F1A2B3D4E5F60718293A4B5C6D7E8{index}").write_bytes(synth.full_save())
        (leaf / "container.0000").write_bytes(b"\x00" * 16)
    steam_root = roaming / discovery.STEAM_RELATIVE
    for steam_id in steam_ids:
        (steam_root / steam_id).mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))
    return wgs, steam_root


def run(argv, capsys):
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_scan_human_output(tmp_path, monkeypatch, capsys):
    wgs, _ = build_env(tmp_path, monkeypatch)
    code, out, err = run(["scan", "--wgs", str(wgs)], capsys)
    assert code == 0, err
    assert "Найдено сохранений: 1" in out
    assert "уровень 12" in out
    assert "время 3:00:00" in out
    assert "сложность Normal" in out
    assert CONTAINER in out


def test_scan_json_output(tmp_path, monkeypatch, capsys):
    wgs, _ = build_env(tmp_path, monkeypatch)
    code, out, _ = run(["scan", "--wgs", str(wgs), "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert len(payload) == 1
    record = payload[0]
    assert record["kind"] == "gvas"
    assert record["container_guid"] == CONTAINER
    assert record["user_id"] == USER_ID
    assert record["meta"]["level"] == 12
    assert record["meta"]["play_time"] == "3:00:00"
    assert record["magic"] == "0x53415647"


def test_scan_empty_tree(tmp_path, monkeypatch, capsys):
    wgs, _ = build_env(tmp_path, monkeypatch, saves=1)
    for path in sorted(wgs.rglob("C8F1A2B3D4E5F60718293A4B5C6D7E80")):
        path.unlink()
    code, out, _ = run(["scan", "--wgs", str(wgs)], capsys)
    assert code == 0
    assert "Найдено сохранений: 0" in out


def test_scan_missing_root_exits_nonzero(tmp_path, monkeypatch, capsys):
    build_env(tmp_path, monkeypatch)
    code, _, err = run(["scan", "--wgs", str(tmp_path / "nope")], capsys)
    assert code == 1
    assert "не найден" in err


def test_scan_unknown_magic_is_shown_in_hex(tmp_path, monkeypatch, capsys):
    wgs, _ = build_env(tmp_path, monkeypatch, saves=0)
    leaf = wgs / USER_ID / CONTAINER / "00000009-1111-2222-3333-444444444449"
    leaf.mkdir(parents=True)
    (leaf / "junk.bin").write_bytes(b"JUNK" + b"\x00" * 64)
    code, out, _ = run(["scan", "--wgs", str(wgs)], capsys)
    assert code == 0
    assert "0x4B4E554A" in out
    assert "неизвестный" in out


def test_convert_command(tmp_path, monkeypatch, capsys):
    build_env(tmp_path, monkeypatch)
    source = tmp_path / "mine.sav"
    source.write_bytes(synth.full_save())
    out_dir = tmp_path / "out"
    code, out, err = run(["convert", "--in", str(source), "--out-dir", str(out_dir)], capsys)
    assert code == 0, err
    assert "MS Store -> Steam" in out
    assert "Статус:      ок" in out
    target = out_dir / "SaveData001.sav"
    assert target.read_bytes() == codec.encrypt(source.read_bytes())


def test_convert_busy_slot_message(tmp_path, monkeypatch, capsys):
    build_env(tmp_path, monkeypatch)
    source = tmp_path / "mine.sav"
    source.write_bytes(synth.full_save())
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "SaveData001.sav").write_bytes(b"busy")
    code, _, err = run(
        ["convert", "--in", str(source), "--out-dir", str(out_dir), "--slot", "1"], capsys
    )
    assert code == 1
    assert "уже занят" in err
    assert "--slot 2" in err


def test_convert_reverse_on_plain_is_usage_error(tmp_path, monkeypatch, capsys):
    build_env(tmp_path, monkeypatch)
    source = tmp_path / "mine.sav"
    source.write_bytes(synth.full_save())
    code, _, err = run(
        ["convert", "--in", str(source), "--out-dir", str(tmp_path / "out"), "--reverse"],
        capsys,
    )
    assert code == 2
    assert "--reverse" in err


def test_convert_dry_run_creates_nothing(tmp_path, monkeypatch, capsys):
    build_env(tmp_path, monkeypatch)
    source = tmp_path / "mine.sav"
    source.write_bytes(synth.full_save())
    out_dir = tmp_path / "out"
    code, out, _ = run(
        ["convert", "--in", str(source), "--out-dir", str(out_dir), "--dry-run"], capsys
    )
    assert code == 0
    assert "пробный прогон" in out
    assert not out_dir.exists()


def test_convert_all_reports_table(tmp_path, monkeypatch, capsys):
    wgs, _ = build_env(tmp_path, monkeypatch, saves=2)
    out_dir = tmp_path / "out"
    code, out, err = run(["convert-all", "--wgs", str(wgs), "--out-dir", str(out_dir)], capsys)
    assert code == 0, err
    assert "Успешно: 2 из 2" in out
    assert "SaveData001.sav" in out
    assert (out_dir / "SaveData001.sav").exists()
    assert (out_dir / "SaveData002.sav").exists()
    assert "Номера слотов назначены" in out


def test_convert_all_json(tmp_path, monkeypatch, capsys):
    wgs, _ = build_env(tmp_path, monkeypatch, saves=1)
    code, out, _ = run(
        ["convert-all", "--wgs", str(wgs), "--out-dir", str(tmp_path / "out"), "--json"],
        capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert payload[0]["status"] == "ok"


def test_convert_all_empty_tree(tmp_path, monkeypatch, capsys):
    wgs, _ = build_env(tmp_path, monkeypatch, saves=0)
    code, _, err = run(
        ["convert-all", "--wgs", str(wgs), "--out-dir", str(tmp_path / "out")], capsys
    )
    assert code == 1
    assert "не найдено" in err


def test_verify_exit_codes(tmp_path, monkeypatch, capsys):
    build_env(tmp_path, monkeypatch)
    good = tmp_path / "good.sav"
    good.write_bytes(synth.sealed_save())
    bad = tmp_path / "bad.sav"
    bad.write_bytes(b"JUNK" + b"\x00" * 64)

    code, out, _ = run(["verify", str(good)], capsys)
    assert code == 0
    assert out.startswith("OK")

    code, out, _ = run(["verify", str(good), str(bad)], capsys)
    assert code == 1
    assert "ОШИБКА" in out
    assert "Итог: проблем - 1" in out


def test_verify_missing_file_is_reported(tmp_path, monkeypatch, capsys):
    build_env(tmp_path, monkeypatch)
    code, out, _ = run(["verify", str(tmp_path / "nope.sav")], capsys)
    assert code == 1
    assert "ОШИБКА" in out


def test_install_single_account(tmp_path, monkeypatch, capsys):
    _, steam_root = build_env(tmp_path, monkeypatch)
    source = tmp_path / "mine.sav"
    source.write_bytes(synth.full_save())
    code, out, err = run(["install", "--in", str(source), "--slot", "3"], capsys)
    assert code == 0, err
    target = steam_root / STEAM_ID / "SaveData003.sav"
    assert target.read_bytes() == codec.encrypt(source.read_bytes())
    assert "Готово" in out


def test_install_multiple_accounts_needs_flag(tmp_path, monkeypatch, capsys):
    _, steam_root = build_env(tmp_path, monkeypatch, steam_ids=(STEAM_ID, "11111111111111112"))
    source = tmp_path / "mine.sav"
    source.write_bytes(synth.full_save())
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    code, _, err = run(["install", "--in", str(source), "--slot", "1"], capsys)
    assert code == 2
    assert "Укажите --steam-id" in err
    assert "11111111111111112" in err


def test_install_selects_requested_account(tmp_path, monkeypatch, capsys):
    _, steam_root = build_env(tmp_path, monkeypatch, steam_ids=(STEAM_ID, "11111111111111112"))
    source = tmp_path / "mine.sav"
    source.write_bytes(synth.full_save())
    code, out, err = run(
        ["install", "--in", str(source), "--slot", "1", "--steam-id", "11111111111111112"],
        capsys,
    )
    assert code == 0, err
    assert (steam_root / "11111111111111112" / "SaveData001.sav").exists()
    assert not (steam_root / STEAM_ID / "SaveData001.sav").exists()


def test_install_unknown_account(tmp_path, monkeypatch, capsys):
    build_env(tmp_path, monkeypatch)
    source = tmp_path / "mine.sav"
    source.write_bytes(synth.full_save())
    code, _, err = run(["install", "--in", str(source), "--slot", "1", "--steam-id", "1"], capsys)
    assert code == 2
    assert "не найден" in err


def test_install_force_backs_up_existing(tmp_path, monkeypatch, capsys):
    _, steam_root = build_env(tmp_path, monkeypatch)
    account = steam_root / STEAM_ID
    existing = account / "SaveData001.sav"
    existing.write_bytes(b"old steam save")
    source = tmp_path / "mine.sav"
    source.write_bytes(synth.full_save())
    code, out, err = run(["install", "--in", str(source), "--slot", "1", "--force"], capsys)
    assert code == 0, err
    backups = list(account.glob("_p3r_backup_*"))
    assert backups and (backups[0] / "SaveData001.sav").read_bytes() == b"old steam save"
    assert existing.read_bytes() == codec.encrypt(source.read_bytes())
    assert "Бэкап" in out


def test_install_slot_busy_without_force(tmp_path, monkeypatch, capsys):
    _, steam_root = build_env(tmp_path, monkeypatch)
    (steam_root / STEAM_ID / "SaveData001.sav").write_bytes(b"busy")
    source = tmp_path / "mine.sav"
    source.write_bytes(synth.full_save())
    code, _, err = run(["install", "--in", str(source), "--slot", "1"], capsys)
    assert code == 1
    assert "уже занят" in err


def test_install_copies_already_sealed_file_unchanged(tmp_path, monkeypatch, capsys):
    _, steam_root = build_env(tmp_path, monkeypatch)
    source = tmp_path / "steam.sav"
    payload = synth.sealed_save()
    source.write_bytes(payload)
    code, out, err = run(["install", "--in", str(source), "--slot", "2"], capsys)
    assert code == 0, err
    assert "уже в формате Steam" in out
    assert (steam_root / STEAM_ID / "SaveData002.sav").read_bytes() == payload


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(["--version"])
    assert info.value.code == 0
    assert "p3r-save-transfer" in capsys.readouterr().out


def test_missing_required_argument_exits_2(capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(["convert", "--in", "x"])
    assert info.value.code == 2


def test_russian_output_survives_cp866_stream(tmp_path, monkeypatch):
    """The CLI must switch a redirected, non-UTF-8 stream to UTF-8."""
    wgs, _ = build_env(tmp_path, monkeypatch)
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp866", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)
    try:
        assert cli.main(["scan", "--wgs", str(wgs)]) == 0
        stream.flush()
        text = raw.getvalue().decode("utf-8")
    finally:
        monkeypatch.undo()
    assert "Найдено сохранений: 1" in text


def test_russian_output_survives_utf8_stream(tmp_path, monkeypatch):
    wgs, _ = build_env(tmp_path, monkeypatch)
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="utf-8", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)
    try:
        assert cli.main(["scan", "--wgs", str(wgs)]) == 0
        stream.flush()
        text = raw.getvalue().decode("utf-8")
    finally:
        monkeypatch.undo()
    assert "сложность Normal" in text


def test_verbose_sets_debug_logging(tmp_path, monkeypatch, capsys):
    import logging

    wgs, _ = build_env(tmp_path, monkeypatch)
    code, _, _ = run(["-v", "scan", "--wgs", str(wgs)], capsys)
    assert code == 0
    assert logging.getLogger().level == logging.DEBUG
