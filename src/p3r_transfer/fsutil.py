"""Filesystem helpers that behave on Windows first.

Three things this module exists for:

* long paths - ``%LOCALAPPDATA%\\Packages\\...\\wgs\\<id>\\<guid>\\<guid>\\<file>``
  easily passes 260 characters, so every open goes through ``\\\\?\\``;
* console encoding - ``sys.stdout`` is forced to UTF-8 with ``errors="replace"``
  so a cp866 console or a redirected pipe can never raise ``UnicodeEncodeError``;
* human errors - ``PermissionError`` / sharing violations become a sentence the
  user can act on instead of a traceback.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import logging
import ntpath
import os
import shutil
import sys
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from .errors import LockedFileError, SaveNotFoundError, TransferError

log = logging.getLogger(__name__)

_LOCK_HINT = "Закройте Persona 3 Reload и приложение Xbox, затем повторите."
_SHARING_ERRNOS = {errno.EACCES, errno.EBUSY, getattr(errno, "ETXTBSY", 26)}
_SHARING_WINERRORS = {5, 32, 33}  # access denied, sharing violation, lock violation


def configure_stdio() -> None:
    """Make stdout/stderr UTF-8 and never fatal on unencodable characters.

    On a real Windows console Python talks to the console through
    ``WriteConsoleW``, so the encoding below only affects redirected output
    (files, pipes) - and there it is exactly what we want.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):  # detached or already-closed stream
            log.debug("Не удалось перенастроить %s", name)


def extended_path(path: str | Path, *, windows: bool | None = None) -> str:
    """Return an absolute path, prefixed for the Win32 long-path API on Windows.

    ``\\\\server\\share`` becomes ``\\\\?\\UNC\\server\\share``; already
    prefixed paths are returned untouched.
    """
    if windows is None:
        windows = os.name == "nt"
    pathmod = ntpath if windows else os.path
    text = pathmod.abspath(os.fspath(path))
    if not windows:
        return text
    if text.startswith("\\\\?\\"):
        return text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text


def _locked(path: str | Path) -> LockedFileError:
    return LockedFileError(f"Файл занят или недоступен: {path}", hint=_LOCK_HINT)


def read_bytes(path: str | Path) -> bytes:
    """Read a file, mapping Windows locking problems to a readable error."""
    try:
        with open(extended_path(path), "rb") as handle:
            return handle.read()
    except FileNotFoundError as exc:
        raise SaveNotFoundError(
            f"Файл не найден: {path}",
            hint="Проверьте путь; для облачных сохранений Xbox файл должен быть уже на диске.",
        ) from exc
    except PermissionError as exc:
        raise _locked(path) from exc
    except OSError as exc:
        if exc.errno in _SHARING_ERRNOS or getattr(exc, "winerror", None) in _SHARING_WINERRORS:
            raise _locked(path) from exc
        raise TransferError(f"Не удалось прочитать файл: {path}", hint=str(exc)) from exc


def read_head(path: str | Path, count: int = 16) -> bytes:
    """Read at most *count* bytes; enough to classify a file by its magic."""
    try:
        with open(extended_path(path), "rb") as handle:
            return handle.read(count)
    except FileNotFoundError as exc:
        raise SaveNotFoundError(
            f"Файл не найден: {path}",
            hint="Проверьте путь; для облачных сохранений Xbox файл должен быть уже на диске.",
        ) from exc
    except PermissionError as exc:
        raise _locked(path) from exc
    except OSError as exc:
        if exc.errno in _SHARING_ERRNOS or getattr(exc, "winerror", None) in _SHARING_WINERRORS:
            raise _locked(path) from exc
        raise TransferError(f"Не удалось прочитать файл: {path}", hint=str(exc)) from exc


def write_bytes_atomic(path: str | Path, data: bytes, *, min_size: int | None = None) -> None:
    """Write ``data`` to *path* atomically and verify what landed on disk.

    The bytes go to ``<name>.tmp`` in the same directory, are flushed and
    fsynced, read back for a size/magic check and only then ``os.replace``d over
    the target.  A crash or a full disk leaves the old file untouched instead of
    a half-written save.
    """
    target = Path(path)
    parent = target.parent
    if parent and not parent.exists():
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise TransferError(
                f"Не удалось создать каталог: {parent}",
                hint=f"{exc}. Проверьте права на запись - повышение прав не требуется.",
            ) from exc

    temp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    try:
        with open(extended_path(temp), "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        written = temp.stat().st_size
        if written != len(data):
            raise TransferError(
                f"Записано {written} байт вместо {len(data)}: {temp}",
                hint="Диск переполнен или файл усечён. Целевой файл не изменён.",
            )
        if min_size is not None and written < min_size:
            raise TransferError(f"Результат короче {min_size} байт: {temp}")
        if data[:4] and read_head(temp, 4) != data[:4]:
            raise TransferError(
                f"Проверка после записи не прошла (magic не совпал): {temp}",
                hint="Целевой файл не изменён; попробуйте другой диск.",
            )
        os.replace(extended_path(temp), extended_path(target))
    except TransferError:
        _discard(temp)
        raise
    except PermissionError as exc:
        _discard(temp)
        raise _locked(target) from exc
    except OSError as exc:
        _discard(temp)
        if exc.errno in _SHARING_ERRNOS or getattr(exc, "winerror", None) in _SHARING_WINERRORS:
            raise _locked(target) from exc
        raise TransferError(f"Не удалось записать файл: {target}", hint=str(exc)) from exc

    if target.stat().st_size != len(data):  # pragma: no cover - filesystem lying
        raise TransferError(f"Размер файла на диске не совпал с ожидаемым: {target}")


def _discard(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()


def free_space(path: str | Path) -> int:
    """Free bytes on the volume that would hold *path* (walks up to a parent)."""
    probe = Path(path)
    for candidate in [probe, *probe.parents]:
        try:
            if candidate.exists():
                return shutil.disk_usage(candidate).free
        except OSError:
            continue
    return 0


def write_bytes(path: str | Path, data: bytes) -> None:
    """Write bytes, creating parent directories as needed."""
    target = Path(path)
    parent = target.parent
    if parent and not parent.exists():
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise TransferError(
                f"Не удалось создать каталог: {parent}",
                hint=f"{exc}. Проверьте права на запись - повышение прав не требуется.",
            ) from exc
    try:
        with open(extended_path(target), "wb") as handle:
            handle.write(data)
    except PermissionError as exc:
        raise _locked(target) from exc
    except OSError as exc:
        if exc.errno in _SHARING_ERRNOS or getattr(exc, "winerror", None) in _SHARING_WINERRORS:
            raise _locked(target) from exc
        raise TransferError(f"Не удалось записать файл: {target}", hint=str(exc)) from exc


def sha256_of(path: str | Path) -> str:
    """Hashes a file without loading it whole."""
    digest = hashlib.sha256()
    try:
        with open(extended_path(path), "rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
    except FileNotFoundError as exc:
        raise SaveNotFoundError(f"Файл не найден: {path}") from exc
    except PermissionError as exc:
        raise _locked(path) from exc
    except OSError as exc:
        if exc.errno in _SHARING_ERRNOS or getattr(exc, "winerror", None) in _SHARING_WINERRORS:
            raise _locked(path) from exc
        raise TransferError(f"Не удалось прочитать файл: {path}", hint=str(exc)) from exc
    return digest.hexdigest()


def iter_files(root: str | Path) -> Iterator[Path]:
    """Walk *root* depth-first, tolerating unreadable directories and long paths."""
    stack = [Path(root)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(extended_path(current)) as scan:
                entries = list(scan)
        except OSError as exc:
            log.warning("Каталог пропущен (%s): %s", current, exc)
            continue
        for entry in entries:
            child = current / entry.name
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(child)
                elif entry.is_file(follow_symlinks=False):
                    yield child
            except OSError as exc:  # broken link, vanished file
                log.debug("Запись пропущена (%s): %s", child, exc)


def human_size(size: int) -> str:
    """``1536`` -> ``1.5 КБ``."""
    if size < 1024:
        return f"{size} Б"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} КБ"
    return f"{size / (1024 * 1024):.2f} МБ"


def format_mtime(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
