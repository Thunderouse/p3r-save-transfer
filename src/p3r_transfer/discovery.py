"""Locate P3R saves in the Microsoft Store (wgs) and Steam trees.

Microsoft Store layout (one file per save, folder names are GUIDs)::

    %LOCALAPPDATA%\\Packages\\SEGAofAmericaInc.L0cb6b3aea_s751p9cej88mt\\
        SystemAppData\\wgs\\<XboxUserID>\\<GUID>\\<GUID>\\<garbage name>
        (plus a ``container.*`` service file next to it)

Steam layout::

    %APPDATA%\\Sega\\P3R\\Steam\\<SteamID64>\\SaveDataNNN.sav

Everything is read through :mod:`p3r_transfer.fsutil`, so 260+ character
paths work.  Discovery only ever reads.

A save folder is a user folder: it may contain notes, ``desktop.ini``,
``Thumbs.db``, half-downloaded files, backups or a truncated save.  Those are
classified and reported, never treated as saves and never allowed to abort the
scan (see :func:`scan_tree`).
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from . import codec, fsutil
from .codec import SaveKind
from .errors import SteamDirNotFoundError, TransferError
from .meta import SaveMetadata, read_metadata

log = logging.getLogger(__name__)

PACKAGE_DIR = "SEGAofAmericaInc.L0cb6b3aea_s751p9cej88mt"
WGS_RELATIVE = Path("Packages") / PACKAGE_DIR / "SystemAppData" / "wgs"
STEAM_RELATIVE = Path("Sega") / "P3R" / "Steam"

#: Anything above this is not a Persona 3 Reload save (real ones are ~1-2 MB).
#: It is refused *before* reading so a stray video or image cannot exhaust memory.
MAX_SAVE_BYTES: int = 32 * 1024 * 1024

MIN_SAVE_BYTES: int = codec.MIN_SAVE_SIZE

_GUID_RE = re.compile(r"^[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}$")
_SLOT_RE = re.compile(r"^SaveData(\d{1,4})\.sav$", re.IGNORECASE)
_SERVICE_PREFIXES = ("container.",)

KIND_LABELS = {
    SaveKind.GVAS: "GVAS (MS Store)",
    SaveKind.OBFUSCATED: "обфусцированный (Steam)",
    SaveKind.UNKNOWN: "неизвестный",
}

#: Category names used in reports and in ``--json``.
CATEGORY_GVAS = "save-gvas"
CATEGORY_STEAM = "save-steam"
CATEGORY_METADATA = "metadata"
CATEGORY_SKIPPED = "skipped"

#: Stable skip codes.  They are part of the public contract (README, JSON).
SKIP_NOT_A_SAVE = "not-a-save"
SKIP_EMPTY = "empty"
SKIP_OS_JUNK = "os-junk"
SKIP_TEMP_FILE = "temp-file"
SKIP_UNREADABLE = "unreadable"
SKIP_TOO_LARGE = "too-large"
SKIP_NO_ACCESS = "no-access"

SKIP_REASONS = (
    SKIP_NOT_A_SAVE,
    SKIP_EMPTY,
    SKIP_OS_JUNK,
    SKIP_TEMP_FILE,
    SKIP_UNREADABLE,
    SKIP_TOO_LARGE,
    SKIP_NO_ACCESS,
)

OS_JUNK_NAMES = frozenset(
    {"desktop.ini", "thumbs.db", "ehthumbs.db", ".ds_store", "icon\r", ".localized"}
)
OS_JUNK_PREFIXES = ("~$",)
TEMP_SUFFIXES = frozenset({".tmp", ".bak", ".part", ".partial", ".crdownload", ".download"})


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

    @property
    def category(self) -> str:
        return CATEGORY_GVAS if self.kind is SaveKind.GVAS else CATEGORY_STEAM


@dataclass(frozen=True)
class SkippedFile:
    """A file that was looked at and deliberately not treated as a save."""

    path: Path
    reason: str
    detail: str | None = None
    size: int = 0
    magic: int | None = None

    @property
    def category(self) -> str:
        return CATEGORY_SKIPPED


@dataclass
class ScanResult:
    """Everything a directory scan found, including what it refused."""

    root: Path | None = None
    saves: list[SaveRecord] = field(default_factory=list)
    skipped: list[SkippedFile] = field(default_factory=list)
    metadata: list[Path] = field(default_factory=list)
    unreadable_dirs: list[Path] = field(default_factory=list)

    def extend(self, other: ScanResult) -> ScanResult:
        self.saves.extend(other.saves)
        self.skipped.extend(other.skipped)
        self.metadata.extend(other.metadata)
        self.unreadable_dirs.extend(other.unreadable_dirs)
        return self

    @property
    def metadata_files(self) -> int:
        return len(self.metadata)

    @property
    def total_seen(self) -> int:
        return len(self.saves) + len(self.skipped) + len(self.metadata)

    def reason_counts(self) -> dict[str, int]:
        return dict(Counter(item.reason for item in self.skipped))

    def reason_summary(self) -> str:
        counts = self.reason_counts()
        return ", ".join(f"{reason}: {count}" for reason, count in sorted(counts.items()))


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


# ---------------------------------------------------------------- classifying


def _reason_detail(reason: str, **parts: object) -> str:
    """Human-readable Russian one-liner for a skip code."""
    if reason == SKIP_OS_JUNK:
        return "служебный файл Windows/Finder"
    if reason == SKIP_TEMP_FILE:
        return "временный файл или недокачанный фрагмент"
    if reason == SKIP_EMPTY:
        return "файл нулевого размера"
    if reason == SKIP_TOO_LARGE:
        return (
            f"{parts.get('size_text', '?')} при пределе {parts.get('limit_text', '?')}: "
            "это не сохранение P3R"
        )
    if reason == SKIP_UNREADABLE:
        return str(parts.get("error") or "не удалось прочитать файл")
    if reason == SKIP_NO_ACCESS:
        return str(parts.get("error") or "каталог недоступен")
    if reason == SKIP_NOT_A_SAVE:
        return (
            f"незнакомый magic {parts.get('magic', '?')}, "
            f"начало файла {parts.get('head', '?')}"
        )
    return ""


def _junk_reason(path: Path) -> str | None:
    """Cheap name-only checks; nothing is opened here."""
    name = path.name.lower()
    if name.startswith(".") or name in OS_JUNK_NAMES or name.startswith(OS_JUNK_PREFIXES):
        return SKIP_OS_JUNK
    if path.suffix.lower() in TEMP_SUFFIXES:
        return SKIP_TEMP_FILE
    return None


def is_service_name(name: str) -> bool:
    """``container.0000`` and friends are Xbox bookkeeping, not saves."""
    lowered = name.lower()
    return lowered == "container" or lowered.startswith(_SERVICE_PREFIXES)


def _record_from_bytes(
    path: Path, data: bytes, *, stat_size: int, mtime: float, root: Path | None
) -> SaveRecord:
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
        size=stat_size,
        kind=kind,
        mtime=mtime,
        container_guid=container_guid,
        user_id=user_id,
        slot=slot_from_filename(path.name),
        meta=meta,
        magic=codec.magic_of(data),
    )


def inspect_file(
    path: Path, *, root: Path | None = None
) -> SaveRecord | SkippedFile | None:
    """Classify one file.

    Returns a :class:`SaveRecord` for a save, a :class:`SkippedFile` for
    anything else, and ``None`` for Xbox bookkeeping (``container.*``), which is
    counted separately instead of being reported as a problem.
    """
    if is_service_name(path.name):
        return None  # container.* is Xbox bookkeeping, not a problem file

    junk = _junk_reason(path)
    if junk is not None:
        return SkippedFile(path=path, reason=junk, detail=_reason_detail(junk))

    try:
        stat = path.stat()
    except FileNotFoundError:
        return SkippedFile(
            path=path, reason=SKIP_UNREADABLE, detail="файл исчез во время сканирования"
        )
    except OSError as exc:
        return SkippedFile(
            path=path, reason=SKIP_UNREADABLE, detail=_reason_detail(SKIP_UNREADABLE, error=exc)
        )

    if stat.st_size == 0:
        return SkippedFile(path=path, reason=SKIP_EMPTY, detail=_reason_detail(SKIP_EMPTY))

    if stat.st_size > MAX_SAVE_BYTES:
        return SkippedFile(
            path=path,
            reason=SKIP_TOO_LARGE,
            size=stat.st_size,
            detail=_reason_detail(
                SKIP_TOO_LARGE,
                size_text=fsutil.human_size(stat.st_size),
                limit_text=fsutil.human_size(MAX_SAVE_BYTES),
            ),
        )

    try:
        head = fsutil.read_head(path, 16)
    except TransferError as exc:
        return SkippedFile(
            path=path,
            reason=SKIP_UNREADABLE,
            size=stat.st_size,
            detail=_reason_detail(SKIP_UNREADABLE, error=exc.message),
        )

    kind = codec.detect_kind(head)
    if kind is SaveKind.UNKNOWN:
        magic = codec.magic_of(head)
        return SkippedFile(
            path=path,
            reason=SKIP_NOT_A_SAVE,
            size=stat.st_size,
            magic=magic,
            detail=_reason_detail(
                SKIP_NOT_A_SAVE,
                magic=codec.format_magic(head),
                head=head[:16].hex(" "),
            ),
        )

    if stat.st_size < MIN_SAVE_BYTES:
        return SkippedFile(
            path=path,
            reason=SKIP_NOT_A_SAVE,
            size=stat.st_size,
            magic=codec.magic_of(head),
            detail=f"файл короче минимальных {MIN_SAVE_BYTES} байт",
        )

    try:
        data = fsutil.read_bytes(path)
    except TransferError as exc:
        return SkippedFile(
            path=path,
            reason=SKIP_UNREADABLE,
            size=stat.st_size,
            detail=_reason_detail(SKIP_UNREADABLE, error=exc.message),
        )
    except MemoryError:  # a file that grew after the size check
        return SkippedFile(
            path=path,
            reason=SKIP_TOO_LARGE,
            size=stat.st_size,
            detail="файл не помещается в память",
        )

    record = _record_from_bytes(
        path, data, stat_size=stat.st_size, mtime=stat.st_mtime, root=root
    )
    return record


def scan_tree(root: Path, *, include_skipped: bool = False) -> ScanResult:
    """Walk a wgs tree and classify every file.

    Never raises because of one file: unreadable files, junk and unknown
    contents end up in :attr:`ScanResult.skipped`.  ``include_skipped`` turns
    files with an unknown magic into attempts (``not-a-save`` entries get a
    :class:`SaveRecord` with ``kind=UNKNOWN``) - the caller still must not
    crash on them.
    """
    result = ScanResult(root=Path(root))
    for path in fsutil.iter_files(root):
        verdict = inspect_file(path, root=Path(root))
        if verdict is None:
            result.metadata.append(path)
        elif isinstance(verdict, SaveRecord):
            result.saves.append(verdict)
        elif include_skipped and verdict.reason == SKIP_NOT_A_SAVE:
            result.saves.append(
                SaveRecord(
                    path=verdict.path,
                    size=verdict.size,
                    kind=SaveKind.UNKNOWN,
                    mtime=_safe_mtime(verdict.path),
                    magic=verdict.magic,
                    note=verdict.detail,
                )
            )
        else:
            result.skipped.append(verdict)
    return result


def _safe_mtime(path: Path) -> float:
    with contextlib.suppress(OSError):
        return path.stat().st_mtime
    return 0.0


def scan_steam(steam_root: Path, *, include_skipped: bool = False) -> ScanResult:
    """Classify the files of every Steam account folder."""
    result = ScanResult(root=Path(steam_root))
    for steam_id in find_steam_ids(steam_root):
        for path in sorted(steam_id.iterdir()):
            if not path.is_file():
                continue
            verdict = inspect_file(path)
            if verdict is None:
                result.metadata.append(path)
            elif isinstance(verdict, SaveRecord):
                result.saves.append(verdict)
            elif include_skipped and verdict.reason == SKIP_NOT_A_SAVE:
                result.saves.append(
                    SaveRecord(
                        path=verdict.path,
                        size=verdict.size,
                        kind=SaveKind.UNKNOWN,
                        mtime=_safe_mtime(verdict.path),
                        magic=verdict.magic,
                        note=verdict.detail,
                    )
                )
            else:
                result.skipped.append(verdict)
    return result


def iter_wgs_saves(root: Path) -> Iterator[SaveRecord]:
    """Yield the real saves under *root* (junk is filtered out, never raised).

    Kept for callers that only need the saves; :func:`scan_tree` also returns
    what was skipped.
    """
    yield from scan_tree(root).saves


def iter_steam_saves(steam_root: Path) -> Iterator[SaveRecord]:
    yield from scan_steam(steam_root).saves


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
