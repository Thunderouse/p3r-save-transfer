"""Reliability guards: atomic writes, free space, backups, codec edge cases."""

from __future__ import annotations

import hashlib
import json
import os
import random

import pytest

import synth
from p3r_transfer import codec, convert, fsutil
from p3r_transfer.errors import TransferError


def payload(size: int = 512, seed: int = 0) -> bytes:
    return synth.full_save() + bytes(random.Random(seed).randrange(256) for _ in range(size))


# ---------------------------------------------------------------- atomic write
def test_atomic_write_replaces_target_and_leaves_no_temp(tmp_path):
    target = tmp_path / "SaveData001.sav"
    target.write_bytes(b"old")
    fsutil.write_bytes_atomic(target, b"GVAS" + b"\x00" * 60)
    assert target.read_bytes() == b"GVAS" + b"\x00" * 60
    assert [p.name for p in tmp_path.iterdir()] == ["SaveData001.sav"]


def test_atomic_write_keeps_old_file_when_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / "SaveData001.sav"
    target.write_bytes(b"original")

    def boom(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(fsutil.os, "replace", boom)
    with pytest.raises(TransferError):
        fsutil.write_bytes_atomic(target, b"GVAS" + b"\x00" * 60)
    assert target.read_bytes() == b"original"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["SaveData001.sav"]


def test_atomic_write_rejects_truncated_payload(tmp_path, monkeypatch):
    target = tmp_path / "x.sav"
    real_open = open

    class Short:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def write(self, data):
            return self.handle.write(data[:4])

        def __exit__(self, *exc):
            return self.handle.__exit__(*exc)

        def __getattr__(self, name):
            return getattr(self.handle, name)

    def fake_open(path, mode="rb", *args, **kwargs):
        handle = real_open(path, mode, *args, **kwargs)
        return Short(handle) if "w" in mode else handle

    monkeypatch.setattr(fsutil, "open", fake_open, raising=False)
    with pytest.raises(TransferError):
        fsutil.write_bytes_atomic(target, b"GVAS" + b"\x00" * 100)
    assert not target.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_convert_file_is_atomic(monkeypatch, tmp_path):
    source = tmp_path / "in.sav"
    source.write_bytes(payload())
    out = tmp_path / "out"

    def boom(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(fsutil.os, "replace", boom)
    with pytest.raises(TransferError):
        convert.convert_file(source, out, slot=1, backup=False)
    assert not (out / "SaveData001.sav").exists()
    assert not list(out.glob("*.tmp")) if out.exists() else True


# ---------------------------------------------------------------- free space
def test_ensure_free_space_refuses_when_volume_is_full(tmp_path, monkeypatch):
    monkeypatch.setattr(convert.fsutil, "free_space", lambda _path: 1024)
    with pytest.raises(TransferError) as info:
        convert.ensure_free_space(tmp_path, 10 * 1024 * 1024)
    assert "места" in str(info.value)


def test_ensure_free_space_allows_when_enough(tmp_path, monkeypatch):
    monkeypatch.setattr(convert.fsutil, "free_space", lambda _path: 10**12)
    convert.ensure_free_space(tmp_path, 10 * 1024 * 1024)  # must not raise


def test_convert_all_reports_full_disk(tmp_path, monkeypatch, capsys):
    from p3r_transfer import cli, discovery

    wgs = tmp_path / "wgs" / "u" / "g" / "c"
    wgs.mkdir(parents=True)
    (wgs / "C0FFEE01").write_bytes(synth.full_save())
    monkeypatch.setattr(convert.fsutil, "free_space", lambda _path: 10)
    code = cli.main(["convert-all", "--wgs", str(tmp_path / "wgs"), "--out-dir", str(tmp_path / "out")])
    err = capsys.readouterr().err
    assert code == cli.EXIT_FATAL
    assert "Недостаточно места" in err
    assert not (tmp_path / "out").exists()
    assert discovery is not None


# -------------------------------------------------------------------- backup
def test_backup_manifest_and_restore(tmp_path):
    account = tmp_path / "steam"
    account.mkdir()
    original = payload(seed=7)
    (account / "SaveData001.sav").write_bytes(original)

    backup_dir = convert.create_backup(account, stamp="20260101-000000")
    assert backup_dir is not None

    manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
    entry = next(item for item in manifest["files"] if item["name"] == "SaveData001.sav")
    assert entry["size"] == len(original)
    assert entry["sha256"] == hashlib.sha256(original).hexdigest()

    restored = (backup_dir / "SaveData001.sav").read_bytes()
    assert restored == original
    assert hashlib.sha256(restored).hexdigest() == entry["sha256"]


def test_backup_skips_when_there_is_nothing_to_copy(tmp_path):
    assert convert.create_backup(tmp_path) is None


# --------------------------------------------------------------------- codec
@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x00",
        b"GVAS",
        b"GVAS" + b"\x00" * 27,
        b"GVAS" + bytes(range(64)),
        b"X" * 31,
        b"X" * 62,
        bytes(random.Random(3).randrange(256) for _ in range(1000)),
    ],
)
def test_codec_round_trip_edges(data):
    sealed = codec.encrypt(data)
    assert len(sealed) == len(data)
    assert codec.decrypt(sealed) == data
    plain = codec.decrypt(data)
    assert codec.encrypt(plain) == data


def test_codec_is_involutive_on_bit_swap():
    for value in range(256):
        swapped = codec.swap_bits(value)
        assert codec.swap_bits(swapped) == value


def test_detect_kind_rejects_short_and_foreign_buffers():
    assert codec.detect_kind(b"") is codec.SaveKind.UNKNOWN
    assert codec.detect_kind(b"GVA") is codec.SaveKind.UNKNOWN
    assert codec.detect_kind(b"GVAS") is codec.SaveKind.GVAS
    assert codec.detect_kind(codec.encrypt(b"GVAS")) is codec.SaveKind.OBFUSCATED
    assert codec.detect_kind(os.urandom(64)) is codec.SaveKind.UNKNOWN


def test_round_trip_property_1000_buffers():
    rng = random.Random(20261028)
    for _ in range(1000):
        size = rng.randrange(0, 5000)
        data = bytes(rng.randrange(256) for _ in range(size))
        sealed = codec.encrypt(data)
        assert len(sealed) == size
        assert codec.decrypt(sealed) == data


def test_verify_accepts_short_but_consistent_buffers(tmp_path):
    """The transform is byte-wise, so one byte less still round-trips.

    Truncation is caught by the game, not by the wrapper: ``verify`` checks the
    wrapper, and a shorter buffer is a perfectly valid wrapper for a shorter
    body.  Documented in README ("известные ограничения").
    """
    source = tmp_path / "a.sav"
    source.write_bytes(synth.full_save())
    assert convert.verify_file(source).ok

    shorter = tmp_path / "b.sav"
    shorter.write_bytes(codec.encrypt(synth.full_save())[:-1])
    assert convert.verify_file(shorter).ok


def test_verify_detects_non_inverse_transform(tmp_path, monkeypatch):
    source = tmp_path / "a.sav"
    source.write_bytes(synth.full_save())
    monkeypatch.setattr(convert.codec, "encrypt", lambda data: data)
    report = convert.verify_file(source)
    assert not report.ok
    assert any("round-trip" in problem for problem in report.problems)
