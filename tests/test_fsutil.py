"""Windows-facing helpers: long-path prefixes, encoding, locking errors."""

from __future__ import annotations

import builtins
import io
import os
import sys

import pytest

from p3r_transfer import fsutil
from p3r_transfer.errors import LockedFileError, SaveNotFoundError, TransferError


def test_extended_path_adds_prefix_on_windows():
    assert (
        fsutil.extended_path(r"C:\Данные\Иван\AppData\Local\x.sav", windows=True)
        == r"\\?\C:\Данные\Иван\AppData\Local\x.sav"
    )


def test_extended_path_handles_unc_and_existing_prefix():
    assert (
        fsutil.extended_path(r"\\server\share\save.sav", windows=True)
        == r"\\?\UNC\server\share\save.sav"
    )
    already = r"\\?\C:\very\long"
    assert fsutil.extended_path(already, windows=True) == already


def test_extended_path_without_windows_is_plain_abspath(tmp_path):
    relative = tmp_path / "a" / "b"
    assert fsutil.extended_path(relative, windows=False) == os.path.abspath(relative)
    assert not fsutil.extended_path(relative, windows=False).startswith("\\\\?\\")


def test_read_write_round_trip_with_nested_dir(tmp_path):
    target = tmp_path / "новый каталог" / "SaveData001.sav"
    fsutil.write_bytes(target, b"GVAS" + b"\x01" * 40)
    assert fsutil.read_bytes(target) == b"GVAS" + b"\x01" * 40
    assert target.stat().st_size == 44


def test_read_missing_file(tmp_path):
    with pytest.raises(SaveNotFoundError):
        fsutil.read_bytes(tmp_path / "nope.sav")


def test_read_permission_error_becomes_hint(tmp_path, monkeypatch):
    target = tmp_path / "locked.sav"
    target.write_bytes(b"x")

    def boom(*args, **kwargs):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(builtins, "open", boom)
    with pytest.raises(LockedFileError) as info:
        fsutil.read_bytes(target)
    assert "закройте" in str(info.value).lower()


def test_write_oserror_becomes_transfer_error(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError(28, "no space")

    monkeypatch.setattr(builtins, "open", boom)
    with pytest.raises(TransferError) as info:
        fsutil.write_bytes(tmp_path / "x.sav", b"data")
    assert "записать" in str(info.value)


def test_sha256_matches_hashlib(tmp_path):
    import hashlib

    target = tmp_path / "x.bin"
    payload = bytes(range(256)) * 300
    target.write_bytes(payload)
    assert fsutil.sha256_of(target) == hashlib.sha256(payload).hexdigest()


def test_sha256_missing_file(tmp_path):
    with pytest.raises(SaveNotFoundError):
        fsutil.sha256_of(tmp_path / "missing.bin")


def test_iter_files_walks_nested_dirs(tmp_path):
    (tmp_path / "one").mkdir()
    (tmp_path / "one" / "two").mkdir()
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "one" / "b.txt").write_text("b")
    (tmp_path / "one" / "two" / "c.txt").write_text("c")
    found = {path.name for path in fsutil.iter_files(tmp_path)}
    assert found == {"a.txt", "b.txt", "c.txt"}


def test_iter_files_survives_unreadable_directory(tmp_path, monkeypatch):
    (tmp_path / "ok").mkdir()
    (tmp_path / "ok" / "file.sav").write_bytes(b"x")
    (tmp_path / "bad").mkdir()
    real_scandir = os.scandir

    def fake_scandir(path):
        if str(path).endswith("bad") or str(path).endswith(r"bad"):
            raise PermissionError(13, "denied")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", fake_scandir)
    names = {path.name for path in fsutil.iter_files(tmp_path)}
    assert names == {"file.sav"}


def test_human_size_units():
    assert fsutil.human_size(10) == "10 Б"
    assert fsutil.human_size(2048) == "2.0 КБ"
    assert fsutil.human_size(5 * 1024 * 1024) == "5.00 МБ"


def test_format_mtime():
    assert fsutil.format_mtime(0)  # does not raise, locale-independent shape
    assert len(fsutil.format_mtime(0)) == 19


def test_configure_stdio_skips_streams_without_reconfigure(monkeypatch):
    monkeypatch.setattr(sys, "stdout", object())
    monkeypatch.setattr(sys, "stderr", object())
    fsutil.configure_stdio()  # must not raise


def test_configure_stdio_switches_redirected_cp866_stream_to_utf8(monkeypatch):
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp866", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)
    fsutil.configure_stdio()
    stream.write("Привет, сохранение")
    stream.flush()
    assert "Привет".encode() in raw.getvalue()


def test_cp866_stream_without_errors_handler_would_fail():
    """Documents why configure_stdio sets errors="replace" at all."""
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp866", errors="strict")
    with pytest.raises(UnicodeEncodeError):
        stream.write("→ 日本語")
    stream.detach()


def test_configure_stdio_ignores_value_error(monkeypatch):
    class Broken:
        def reconfigure(self, **kwargs):
            raise ValueError("detached")

        def write(self, text):  # logging may land here
            return len(text)

    monkeypatch.setattr(sys, "stdout", Broken())
    monkeypatch.setattr(sys, "stderr", Broken())
    fsutil.configure_stdio()
