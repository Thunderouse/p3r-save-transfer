"""Conversion, slot selection, backups and verification.

This is the library half of the tool: no printing, no argparse, no exit codes.
The CLI only formats what these functions return.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import codec, fsutil
from .codec import MIN_SAVE_SIZE, SaveKind
from .discovery import SaveRecord, existing_slots, slot_from_filename
from .errors import (
    SlotBusyError,
    TransferError,
    UnknownMagicError,
    UsageError,
)
from .meta import SaveMetadata, header_version, read_metadata

log = logging.getLogger(__name__)

BACKUP_PREFIX = "_p3r_backup_"
MAX_SLOT = 999

DIRECTION_TO_STEAM = "MS Store -> Steam"
DIRECTION_TO_MS = "Steam -> MS Store"


def slot_filename(slot: int) -> str:
    """``3`` -> ``SaveData003.sav`` (the game pads to three digits)."""
    return f"SaveData{slot:03d}.sav"


def _next_free(taken: dict[int, Path], start: int) -> int | None:
    for slot in range(max(1, start), MAX_SLOT + 1):
        if slot not in taken:
            return slot
    return None


def choose_slot(
    out_dir: Path, requested: int | None = None, *, force: bool = False
) -> int:
    """Pick a slot, refusing to overwrite unless *force* is set."""
    taken = existing_slots(out_dir)
    if requested is not None:
        if not 1 <= requested <= MAX_SLOT:
            raise UsageError(f"Слот должен быть от 1 до {MAX_SLOT}, получено {requested}")
        if requested in taken and not force:
            free = _next_free(taken, requested + 1) or _next_free(taken, 1)
            hint = f"Свободен слот {free}: --slot {free}" if free else "Свободных слотов нет"
            raise SlotBusyError(
                f"Слот {requested} уже занят: {taken[requested].name}",
                hint=f"{hint}. Либо добавьте --force, чтобы перезаписать.",
            )
        return requested
    free = _next_free(taken, 1)
    if free is None:
        raise TransferError("В каталоге заняты все слоты 1..999")
    return free


def create_backup(
    directory: Path,
    *,
    files: Sequence[Path] | None = None,
    stamp: str | None = None,
) -> Path | None:
    """Copy ``SaveData*.sav`` (or an explicit list) into ``_p3r_backup_<ts>``.

    Returns the backup directory, or ``None`` when there was nothing to copy.
    Copies are written through the long-path helpers and hash-checked.
    """
    directory = Path(directory)
    if files is None:
        candidates = [p for p in fsutil.iter_files(directory) if p.parent == directory]
        targets = sorted(p for p in candidates if slot_from_filename(p.name) is not None)
    else:
        targets = [Path(p) for p in files]
    targets = [p for p in targets if p.is_file()]
    if not targets:
        return None

    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = directory / f"{BACKUP_PREFIX}{stamp}"
    counter = 1
    while backup_dir.exists():
        backup_dir = directory / f"{BACKUP_PREFIX}{stamp}-{counter}"
        counter += 1
    backup_dir.mkdir(parents=True)

    entries = []
    for source in targets:
        data = fsutil.read_bytes(source)
        digest = hashlib.sha256(data).hexdigest()
        destination = backup_dir / source.name
        fsutil.write_bytes(destination, data)
        if fsutil.sha256_of(destination) != digest:
            raise TransferError(f"Бэкап не совпал с исходником: {destination}")
        entries.append({"name": source.name, "size": len(data), "sha256": digest})

    manifest = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "source_dir": str(directory),
        "files": entries,
    }
    fsutil.write_bytes(
        backup_dir / "manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
    )
    log.info("Бэкап: %s (%d файлов)", backup_dir, len(entries))
    return backup_dir


@dataclass(frozen=True)
class ConversionResult:
    source: Path
    target: Path | None
    source_kind: SaveKind
    direction: str
    source_size: int
    size: int
    slot: int | None
    status: str  # "ok" | "dry-run"
    backup: Path | None = None


def convert_bytes(data: bytes, *, reverse: bool = False) -> tuple[bytes, SaveKind, str]:
    """Transform a whole buffer and prove the inverse brings it back."""
    kind = codec.detect_kind(data)
    if kind is SaveKind.UNKNOWN:
        raise UnknownMagicError(
            f"Это не сохранение P3R: первые байты {codec.format_magic(data)}",
            hint="Ожидался GVAS (0x53415647) или обфускация Steam (0x0B650015). "
            f"Начало файла: {data[:16].hex(' ')}",
        )
    if len(data) < MIN_SAVE_SIZE:
        raise TransferError(
            f"Файл слишком мал: {len(data)} байт (минимум {MIN_SAVE_SIZE})",
            hint="Файл повреждён или это не сохранение.",
        )

    if reverse:
        if kind is not SaveKind.OBFUSCATED:
            raise UsageError(
                "--reverse ожидает обфусцированный файл Steam",
                hint="Вход уже в формате GVAS (MS Store), обратное направление не нужно.",
            )
        payload, label, expected = codec.decrypt(data), DIRECTION_TO_MS, SaveKind.GVAS
    elif kind is SaveKind.GVAS:
        payload, label, expected = codec.encrypt(data), DIRECTION_TO_STEAM, SaveKind.OBFUSCATED
    else:
        payload, label, expected = codec.decrypt(data), DIRECTION_TO_MS, SaveKind.GVAS

    if codec.detect_kind(payload) is not expected:
        raise TransferError("Результат конвертации имеет неожидаемый magic - прервано")
    restored = codec.decrypt(payload) if expected is SaveKind.OBFUSCATED else codec.encrypt(payload)
    if restored != data:
        raise TransferError("Обратное преобразование не совпало с исходником - прервано")
    return payload, kind, label


def convert_file(
    source: str | Path,
    out_dir: str | Path,
    *,
    slot: int | None = None,
    force: bool = False,
    reverse: bool = False,
    dry_run: bool = False,
    backup: bool = True,
) -> ConversionResult:
    """Convert one save.  The source file is only ever read."""
    source = Path(source)
    out_dir = Path(out_dir)
    data = fsutil.read_bytes(source)
    payload, source_kind, direction = convert_bytes(data, reverse=reverse)

    chosen = choose_slot(out_dir, slot, force=force)
    target = out_dir / slot_filename(chosen)
    result = ConversionResult(
        source=source,
        target=target,
        source_kind=source_kind,
        direction=direction,
        source_size=len(data),
        size=len(payload),
        slot=chosen,
        status="dry-run",
    )
    if dry_run:
        return result

    backup_dir = create_backup(out_dir) if backup else None
    fsutil.write_bytes_atomic(target, payload, min_size=codec.MIN_SAVE_SIZE)
    if fsutil.sha256_of(target) != hashlib.sha256(payload).hexdigest():
        raise TransferError(f"Запись не совпала с ожидаемыми байтами: {target}")
    log.info("Записано: %s (%d байт)", target, len(payload))
    return ConversionResult(
        source=result.source,
        target=target,
        source_kind=source_kind,
        direction=direction,
        source_size=result.source_size,
        size=result.size,
        slot=chosen,
        status="ok",
        backup=backup_dir,
    )


def plan_slots(
    records: Iterable[SaveRecord], out_dir: Path, *, force: bool = False
) -> list[tuple[SaveRecord, int]]:
    """Assign a slot per record, keeping known slots when they are free."""
    from .discovery import sort_for_slots

    taken = existing_slots(out_dir)
    plan: list[tuple[SaveRecord, int]] = []
    for record in sort_for_slots(records):
        candidate = record.slot
        if candidate is not None and (candidate not in taken or force):
            slot = candidate
        else:
            slot = _next_free(taken, 1)
            if slot is None:
                raise TransferError("В каталоге заняты все слоты 1..999")
        taken[slot] = record.path
        plan.append((record, slot))
    return plan


@dataclass(frozen=True)
class VerifyReport:
    path: Path
    kind: SaveKind
    size: int
    problems: list[str]
    warnings: list[str]
    meta: SaveMetadata | None = None

    @property
    def ok(self) -> bool:
        return not self.problems


def verify_file(path: str | Path) -> VerifyReport:
    """Check magic, size and round-trip.  Any problem is fatal for the caller."""
    path = Path(path)
    data = fsutil.read_bytes(path)
    kind = codec.detect_kind(data)
    problems: list[str] = []
    warnings: list[str] = []

    if len(data) < MIN_SAVE_SIZE:
        problems.append(f"размер {len(data)} байт меньше минимума {MIN_SAVE_SIZE}")
    if kind is SaveKind.UNKNOWN:
        problems.append(f"неизвестный magic {codec.format_magic(data)}")

    plain: bytes | None = None
    if kind is SaveKind.OBFUSCATED:
        plain = codec.decrypt(data)
        if codec.encrypt(plain) != data:
            problems.append("round-trip encrypt(decrypt(x)) не совпал с файлом")
    elif kind is SaveKind.GVAS:
        plain = data
        if codec.decrypt(codec.encrypt(data)) != data:
            problems.append("round-trip decrypt(encrypt(x)) не совпал с файлом")

    meta = None
    if plain is not None:
        version = header_version(plain)
        if version is None:
            warnings.append("заголовок GVAS не разобран (конвертации это не мешает)")
        meta = read_metadata(plain)
        if meta.format_version is None and version is not None:
            meta = SaveMetadata(format_version=version)

    return VerifyReport(
        path=path, kind=kind, size=len(data), problems=problems, warnings=warnings, meta=meta
    )


def ensure_free_space(out_dir: str | Path, total_bytes: int, *, margin: float = 0.10) -> None:
    """Refuse to start a batch that cannot fit, instead of dying mid-way."""
    needed = int(total_bytes * (1.0 + margin))
    free = fsutil.free_space(out_dir)
    if free and free < needed:
        raise TransferError(
            f"Недостаточно места в {Path(out_dir)}: нужно {fsutil.human_size(needed)}, "
            f"свободно {fsutil.human_size(free)}"
        )
