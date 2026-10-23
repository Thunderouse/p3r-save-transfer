"""Locate P3R saves in the Microsoft Store (wgs) and Steam trees.

Microsoft Store layout (one file per save, folder names are GUIDs)::

    %LOCALAPPDATA%\\Packages\\SEGAofAmericaInc.L0cb6b3aea_s751p9cej88mt\\
        SystemAppData\\wgs\\<XboxUserID>\\<GUID>\\<GUID>\\<garbage name>
        (plus a ``container.*`` service file next to it)

Steam layout::

    %APPDATA%\\Sega\\P3R\\Steam\\<SteamID64>\\SaveDataNNN.sav

Everything is read through :mod:`p3r_transfer.fsutil`, so 260+ character
paths work.  Discovery only ever reads.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from . import codec, fsutil
from .codec import SaveKind
from .errors import SteamDirNotFoundError, TransferError
from .meta import SaveMetadata, read_metadata

log = logging.getLogger(__name__)

PACKAGE_DIR = "SEGAofAmericaInc.L0cb6b3aea_s751p9cej88mt"
WGS_RELATIVE = Path("Packages") / PACKAGE_DIR / "SystemAppData" / "wgs"
STEAM_RELATIVE = Path("Sega") / "P3R" / "Steam"

_GUID_RE = re.compile(r"^[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}$")
_SLOT_RE = re.compile(r"^SaveData(\d{1,4})\.sav$", re.IGNORECASE)
_SERVICE_PREFIXES = ("container.",)

KIND_LABELS = {
    SaveKind.GVAS: "GVAS (MS Store)",
    SaveKind.OBFUSCATED: "обфусцированный (Steam)",
    SaveKind.UNKNOWN: "неизвестный",
}


@dataclass(frozen=True)
class SaveRecord:
    """One save file found on disk."""

    path: Path
    size: int
    kind: SaveKind
    mtime: float
    container_guid: str | None = None
    user_id: str | None = None
    slot: int | None = None
    meta: SaveMetadata | None = None
    note: str | None = None
    magic: int | None = None

    @property
    def kind_label(self) -> str:
        return KIND_LABELS[self.kind]


def env_dir(variable: str) -> Path | None:
    """Read a Windows environment directory, honouring redirection to any drive."""
    value = os.environ.get(variable)
    return Path(value).expanduser() if value else None


def default_wgs_root() -> Path:
    local = env_dir("LOCALAPPDATA")
    if local is None:
        raise TransferError(
            "Переменная LOCALAPPDATA не задана",
            hint="Запустите инструмент в Windows или укажите каталог через --wgs.",
        )
    return local / WGS_RELATIVE


def default_steam_root() -> Path:
    appdata = env_dir("APPDATA")
    if appdata is None:
        raise TransferError(
            "Переменная APPDATA не задана",
            hint="Запустите инструмент в Windows; путь Steam можно передать через --steam-root.",
        )
    return appdata / STEAM_RELATIVE


def resolve_wgs_root(explicit: str | Path | None = None) -> Path:
    root = Path(explicit) if explicit else default_wgs_root()
    if not root.exists():
        raise TransferError(
            f"Каталог wgs не найден: {root}",
            hint="Игра из Microsoft Store не устанавливалась на этом ПК, "
            "либо LOCALAPPDATA перенаправлен на другой диск.",
        )
    if not root.is_dir():
        raise TransferError(f"Ожидался каталог, а не файл: {root}")
    return root


def resolve_steam_root(explicit: str | Path | None = None) -> Path:
    root = Path(explicit) if explicit else default_steam_root()
    if not root.is_dir():
        raise SteamDirNotFoundError(
            f"Каталог сохранений Steam не найден: {root}",
            hint="Запустите Steam-версию P3R хотя бы один раз, чтобы игра создала каталог.",
        )
    return root


def slot_from_filename(name: str) -> int | None:
    match = _SLOT_RE.match(name)
    if match is None:
        return None
    slot = int(match.group(1))
    return slot if 1 <= slot <= 999 else None


def existing_slots(directory: Path) -> dict[int, Path]:
    """Map slot number -> file for the saves already in *directory*."""
    taken: dict[int, Path] = {}
    directory = Path(directory)
    if not directory.is_dir():
        return taken
    try:
        with os.scandir(fsutil.extended_path(directory)) as scan:
            names = [entry.name for entry in scan if entry.is_file()]
    except OSError as exc:
        log.warning("Не удалось прочитать каталог %s: %s", directory, exc)
        return taken
    for name in names:
        slot = slot_from_filename(name)
        if slot is not None:
            taken[slot] = directory / name
    return taken


def find_steam_ids(steam_root: Path) -> list[Path]:
    """Sub-folders of ``Steam`` that look like SteamID64 accounts."""
    try:
        entries = [p for p in steam_root.iterdir() if p.is_dir() and p.name.isdigit()]
    except OSError as exc:
        raise TransferError(f"Не удалось прочитать {steam_root}", hint=str(exc)) from exc
    return sorted(entries, key=lambda p: p.name)


def _make_record(path: Path, *, root: Path | None = None) -> SaveRecord:
    try:
        stat = path.stat()
        data = fsutil.read_bytes(path)
    except TransferError as exc:
        log.warning("Файл пропущен: %s", exc)
        size = 0
        with contextlib.suppress(OSError):
            size = path.stat().st_size
        return SaveRecord(
            path=path, size=size, kind=SaveKind.UNKNOWN, mtime=0.0, note=str(exc)
        )

    kind = codec.detect_kind(data)
    meta = None
    if kind is SaveKind.GVAS:
        meta = read_metadata(data)
    elif kind is SaveKind.OBFUSCATED:
        meta = read_metadata(codec.decrypt(data))

    container_guid = None
    user_id = None
    if root is not None:
        try:
            parts = path.relative_to(root).parts
        except ValueError:  # --wgs pointed at a directory outside the file's tree
            parts = path.parts[-4:]
        if len(parts) >= 2 and _GUID_RE.match(parts[-2]):
            container_guid = parts[-2]
        if len(parts) >= 3:
            user_id = parts[0]

    return SaveRecord(
        path=path,
        size=len(data),
        kind=kind,
        mtime=stat.st_mtime,
        container_guid=container_guid,
        user_id=user_id,
        slot=slot_from_filename(path.name),
        meta=meta,
        magic=codec.magic_of(data),
    )


def iter_wgs_saves(root: Path) -> Iterable[SaveRecord]:
    """Yield every save found under the ``wgs`` tree (read-only)."""
    for path in fsutil.iter_files(root):
        if path.name.lower().startswith(_SERVICE_PREFIXES):
            continue
        if path.name.startswith("."):
            continue
        try:
            if path.stat().st_size == 0:
                continue
        except OSError:
            continue
        if path.suffix.lower() in {".tmp", ".log"}:
            continue
        yield _make_record(path, root=root)


def iter_steam_saves(steam_root: Path) -> Iterable[SaveRecord]:
    for steam_id in find_steam_ids(steam_root):
        for path in sorted(steam_id.glob("SaveData*.sav")):
            if not path.is_file():
                continue
            yield _make_record(path)


def sort_for_slots(records: Iterable[SaveRecord]) -> list[SaveRecord]:
    """Oldest saves first, using in-save play time when it is readable.

    Xbox container names are random, so the original slot order is simply not
    on disk.  Play time is the closest thing to it: the slot the player filled
    first has the least play time.  Files without metadata fall back to mtime
    and are placed after the measured ones.
    """
    def key(record: SaveRecord):
        play_time = record.meta.play_time_seconds if record.meta else None
        if play_time is None:
            return (1, record.mtime)
        return (0, float(play_time))

    return sorted(records, key=key)
