"""Conversion core: slots, backups, dry-run, verification, byte-exact reverse."""

from __future__ import annotations

import hashlib
import json

import pytest

import synth
from p3r_transfer import codec, convert, discovery, fsutil
from p3r_transfer.codec import SaveKind
from p3r_transfer.errors import SlotBusyError, TransferError, UnknownMagicError, UsageError
from p3r_transfer.meta import SaveMetadata


@pytest.fixture
def plain(tmp_path):
    path = tmp_path / "in" / "plain.save"
    fsutil.write_bytes(path, synth.full_save())
    return path


def test_slot_filename_pads_to_three_digits():
    assert convert.slot_filename(1) == "SaveData001.sav"
    assert convert.slot_filename(42) == "SaveData042.sav"
    assert convert.slot_filename(999) == "SaveData999.sav"


def test_choose_slot_defaults_to_first_free(tmp_path):
    assert convert.choose_slot(tmp_path) == 1
    (tmp_path / "SaveData001.sav").write_bytes(b"x")
    (tmp_path / "SaveData002.sav").write_bytes(b"x")
    assert convert.choose_slot(tmp_path) == 3


def test_choose_slot_busy_suggests_next(tmp_path):
    (tmp_path / "SaveData001.sav").write_bytes(b"x")
    with pytest.raises(SlotBusyError) as info:
        convert.choose_slot(tmp_path, 1)
    assert "Свободен слот 2" in info.value.hint
    assert convert.choose_slot(tmp_path, 1, force=True) == 1


def test_choose_slot_range(tmp_path):
    with pytest.raises(UsageError):
        convert.choose_slot(tmp_path, 0)
    with pytest.raises(UsageError):
        convert.choose_slot(tmp_path, 1000)


def test_choose_slot_when_everything_is_taken(tmp_path, monkeypatch):
    monkeypatch.setattr(convert, "MAX_SLOT", 2)
    (tmp_path / "SaveData001.sav").write_bytes(b"x")
    (tmp_path / "SaveData002.sav").write_bytes(b"x")
    with pytest.raises(TransferError):
        convert.choose_slot(tmp_path)


def test_create_backup_copies_and_hashes(tmp_path):
    (tmp_path / "SaveData001.sav").write_bytes(b"old-1")
    (tmp_path / "SaveData002.sav").write_bytes(b"old-2")
    (tmp_path / "notes.txt").write_text("not a save")
    backup = convert.create_backup(tmp_path, stamp="20260101-120000")
    assert backup is not None and backup.name == "_p3r_backup_20260101-120000"
    assert (backup / "SaveData001.sav").read_bytes() == b"old-1"
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    assert {entry["name"] for entry in manifest["files"]} == {
        "SaveData001.sav",
        "SaveData002.sav",
    }
    hashes = {entry["name"]: entry["sha256"] for entry in manifest["files"]}
    assert hashes["SaveData001.sav"] == hashlib.sha256(b"old-1").hexdigest()


def test_create_backup_nothing_to_copy(tmp_path):
    assert convert.create_backup(tmp_path) is None
    assert convert.create_backup(tmp_path / "missing") is None
    (tmp_path / "SaveData007.sav").write_bytes(b"x")
    assert convert.create_backup(tmp_path, files=[]) is None


def test_create_backup_does_not_collide(tmp_path):
    (tmp_path / "SaveData001.sav").write_bytes(b"x")
    first = convert.create_backup(tmp_path, stamp="same")
    second = convert.create_backup(tmp_path, stamp="same")
    assert first is not None and second is not None
    assert first != second and second.name.endswith("-1")


def test_convert_bytes_seals_gvas():
    plain = synth.full_save()
    sealed, kind, direction = convert.convert_bytes(plain)
    assert kind is SaveKind.GVAS
    assert direction == convert.DIRECTION_TO_STEAM
    assert codec.detect_kind(sealed) is SaveKind.OBFUSCATED
    assert codec.decrypt(sealed) == plain
    assert len(sealed) == len(plain)


def test_convert_bytes_opens_sealed_by_default():
    sealed = synth.sealed_save()
    plain, kind, direction = convert.convert_bytes(sealed)
    assert kind is SaveKind.OBFUSCATED
    assert direction == convert.DIRECTION_TO_MS
    assert plain == synth.save()


def test_convert_bytes_reverse_direction():
    sealed = synth.sealed_save()
    plain, _, direction = convert.convert_bytes(sealed, reverse=True)
    assert direction == convert.DIRECTION_TO_MS
    assert codec.detect_kind(plain) is SaveKind.GVAS
    with pytest.raises(UsageError):
        convert.convert_bytes(synth.full_save(), reverse=True)


def test_convert_bytes_unknown_magic_reports_it():
    with pytest.raises(UnknownMagicError) as info:
        convert.convert_bytes(b"XXXX" + b"\x00" * 100)
    assert "0x58585858" in info.value.message


def test_convert_bytes_rejects_tiny_file():
    with pytest.raises(TransferError) as info:
        convert.convert_bytes(b"GVAS" + b"\x00" * 3)
    assert "слишком мал" in info.value.message


def test_convert_file_writes_slot_and_leaves_source_alone(tmp_path, plain):
    source_hash = fsutil.sha256_of(plain)
    out = tmp_path / "out"
    result = convert.convert_file(plain, out)
    assert result.status == "ok"
    assert result.target == out / "SaveData001.sav"
    assert result.target.read_bytes() == codec.encrypt(plain.read_bytes())
    assert result.size == result.source_size
    assert result.backup is None  # nothing to back up in an empty directory
    assert fsutil.sha256_of(plain) == source_hash
    assert not any(p.name.startswith("_p3r_backup") for p in out.iterdir())


def test_convert_file_refuses_busy_slot(tmp_path, plain):
    out = tmp_path / "out"
    out.mkdir()
    existing = out / "SaveData001.sav"
    existing.write_bytes(b"keep me")
    with pytest.raises(SlotBusyError):
        convert.convert_file(plain, out, slot=1)
    assert existing.read_bytes() == b"keep me"
    # without an explicit slot the tool is allowed to take the next free one
    result = convert.convert_file(plain, out)
    assert result.target == out / "SaveData002.sav"
    assert existing.read_bytes() == b"keep me"


def test_convert_file_force_overwrites_with_backup(tmp_path, plain):
    out = tmp_path / "out"
    out.mkdir()
    existing = out / "SaveData001.sav"
    existing.write_bytes(b"keep me")
    result = convert.convert_file(plain, out, slot=1, force=True)
    assert existing.read_bytes() == codec.encrypt(plain.read_bytes())
    assert result.backup is not None
    assert (result.backup / "SaveData001.sav").read_bytes() == b"keep me"


def test_convert_file_dry_run_writes_nothing(tmp_path, plain):
    out = tmp_path / "not-created"
    result = convert.convert_file(plain, out, slot=4, dry_run=True)
    assert result.status == "dry-run"
    assert result.target == out / "SaveData004.sav"
    assert not out.exists()


def test_convert_file_no_backup_flag(tmp_path, plain):
    out = tmp_path / "out"
    out.mkdir()
    (out / "SaveData001.sav").write_bytes(b"old")
    result = convert.convert_file(plain, out, force=True, backup=False)
    assert result.backup is None
    assert not any(p.name.startswith("_p3r_backup") for p in out.iterdir())


def test_convert_file_missing_source(tmp_path):
    with pytest.raises(TransferError):
        convert.convert_file(tmp_path / "nope.sav", tmp_path / "out")


def test_reverse_round_trip_is_byte_identical(tmp_path, plain):
    """Acceptance criterion: convert, then --reverse, must equal the original."""
    original = plain.read_bytes()
    first = convert.convert_file(plain, tmp_path / "out")
    back = convert.convert_file(first.target, tmp_path / "back", slot=1, reverse=True)
    assert back.target.read_bytes() == original
    assert fsutil.sha256_of(back.target) == hashlib.sha256(original).hexdigest()


def test_plan_slots_keeps_known_slots_and_fills_gaps(tmp_path):
    (tmp_path / "SaveData002.sav").write_bytes(b"x")
    records = [
        discovery.SaveRecord(
            path=tmp_path / "a.bin",
            size=1,
            kind=SaveKind.GVAS,
            mtime=100.0,
            meta=SaveMetadata(play_time_seconds=10, parsed=True),
        ),
        discovery.SaveRecord(
            path=tmp_path / "b.bin",
            size=1,
            kind=SaveKind.OBFUSCATED,
            mtime=50.0,
            slot=5,
            meta=SaveMetadata(play_time_seconds=20, parsed=True),
        ),
        discovery.SaveRecord(
            path=tmp_path / "c.bin",
            size=1,
            kind=SaveKind.GVAS,
            mtime=10.0,
            meta=SaveMetadata(play_time_seconds=30, parsed=True),
        ),
    ]
    plan = convert.plan_slots(records, tmp_path)
    # slot 2 is occupied by an existing save, so the third record lands in 3
    assert [slot for _, slot in plan] == [1, 5, 3]
    assert [record.path.name for record, _ in plan] == ["a.bin", "b.bin", "c.bin"]


def test_realistic_size_file_round_trip(tmp_path):
    """Real saves are ~1.4 MB; make sure nothing assumes a small buffer."""
    import random

    rng = random.Random(2026)
    payload = synth.full_save() + bytes(rng.randrange(256) for _ in range(1_400_000))
    source = tmp_path / "big.bin"
    source.write_bytes(payload)

    result = convert.convert_file(source, tmp_path / "out")
    assert result.target.stat().st_size == len(payload)
    assert codec.decrypt(result.target.read_bytes()) == payload
    assert convert.verify_file(result.target).ok


def test_verify_accepts_both_formats(tmp_path, plain):
    sealed = tmp_path / "sealed.sav"
    sealed.write_bytes(codec.encrypt(plain.read_bytes()))
    for path in (plain, sealed):
        report = convert.verify_file(path)
        assert report.ok, report.problems
        assert report.meta is not None and report.meta.level == 12
    assert convert.verify_file(sealed).kind is SaveKind.OBFUSCATED


def test_verify_rejects_unknown_magic_and_short_files(tmp_path):
    bad = tmp_path / "bad.sav"
    bad.write_bytes(b"JUNK" + b"\x00" * 64)
    report = convert.verify_file(bad)
    assert not report.ok
    assert "0x4B4E554A" in " ".join(report.problems)

    tiny = tmp_path / "tiny.sav"
    tiny.write_bytes(b"GVAS")
    report = convert.verify_file(tiny)
    assert not report.ok
    assert any("меньше минимума" in problem for problem in report.problems)


def test_verify_missing_file(tmp_path):
    with pytest.raises(TransferError):
        convert.verify_file(tmp_path / "nope.sav")


def test_verify_flags_broken_gvas_header(tmp_path):
    import struct

    path = tmp_path / "broken.sav"
    # magic and size are fine, but the engine branch string claims 2 GB
    broken = b"GVAS" + struct.pack("<iiHHHi", 2, 522, 4, 27, 2, 0)
    broken += struct.pack("<i", 0x7FFFFF00) + b"\x00" * 40
    path.write_bytes(broken)
    report = convert.verify_file(path)
    assert report.ok  # magic/size/round-trip still fine
    assert report.warnings and "заголовок" in report.warnings[0].lower()
