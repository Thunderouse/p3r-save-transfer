"""The only surface the window talks to.

Every method returns a JSON-serialisable dict and never lets an exception
escape: failures come back as ``{"ok": False, "error": "<human text>"}``.
Nothing here re-implements the engine - scan/convert/verify are called from
:mod:`p3r_transfer` directly, and no subprocess is ever spawned.

The window runs these calls on a worker thread; ``convert`` accepts optional
callbacks so it can report progress and be cancelled between files.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable
from pathlib import Path

from p3r_transfer import __version__, codec, convert, discovery, fsutil
from p3r_transfer.codec import SaveKind
from p3r_transfer.errors import TransferError

log = logging.getLogger("p3r_transfer.ui.bridge")

ProgressCallback = Callable[[dict], None]
CancelCallback = Callable[[], bool]


def _guard(func):
    """Turn every failure into a dict the UI can render."""

    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except TransferError as exc:
            self._log(f"! {exc.message}")
            return {
                "ok": False,
                "error": exc.message,
                "hint": exc.hint,
                "code": type(exc).__name__,
            }
        except Exception as exc:  # noqa: BLE001 - the UI must never see a traceback
            log.exception("Ошибка моста: %s", func.__name__)
            self._log(f"! {exc}")
            return {"ok": False, "error": str(exc) or type(exc).__name__, "code": "Unhandled"}

    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper


def _kind_of(record_kind: SaveKind) -> str:
    return {SaveKind.GVAS: "gvas", SaveKind.OBFUSCATED: "steam"}.get(record_kind, "unknown")


class Bridge:
    """Stateless glue between the widgets and the conversion engine."""

    def __init__(
        self,
        on_log: Callable[[str], None] | None = None,
        opener: Callable[[str], None] | None = None,
    ) -> None:
        self._on_log = on_log
        self._opener = opener

    # --- helpers -----------------------------------------------------------

    def _log(self, message: str) -> None:
        log.info(message)
        if self._on_log is not None:
            try:
                self._on_log(message)
            except Exception:  # noqa: BLE001 - logging must never break a command
                log.exception("Ошибка в обработчике лога")

    def _record_payload(self, record: discovery.SaveRecord, slot: int | None) -> dict:
        meta = record.meta
        return {
            "path": str(record.path),
            "name": record.path.name,
            "size": record.size,
            "kind": _kind_of(record.kind),
            "magic": None if record.magic is None else f"0x{record.magic:08X}",
            "slot": slot,
            "mtime": record.mtime,
            "level": None if meta is None else meta.level,
            "play_time": None if meta is None else meta.play_time_seconds,
            "difficulty": None if meta is None else meta.difficulty,
            "container": record.container_guid,
            "user": record.user_id,
            "note": record.note,
        }

    # --- commands ----------------------------------------------------------

    @_guard
    def version(self) -> dict:
        return {"ok": True, "version": __version__, "engine": "p3r_transfer"}

    @_guard
    def default_source(self) -> dict:
        """Where “Find automatically” looks, if the game is installed."""
        try:
            root = discovery.default_wgs_root()
        except TransferError as exc:
            return {"ok": True, "path": None, "exists": False, "error": exc.message}
        return {"ok": True, "path": str(root), "exists": root.is_dir()}

    @_guard
    def scan(self, path: str | None = None) -> dict:
        """Find saves in a folder, a wgs tree or a single file."""
        target = Path(path) if path else discovery.default_wgs_root()

        if target.is_file():
            records = [self._single_file(target)]
            source_kind = "file"
        elif target.is_dir():
            records, source_kind = self._scan_dir(target)
        else:
            raise TransferError(
                f"Путь не найден: {target}",
                hint="Перетащи папку wgs или файл .sav, либо выбери папку кнопкой.",
            )

        ordered = self._order(records)
        slots = self._propose_slots(ordered)
        payload = [
            self._record_payload(record, slot)
            for record, slot in zip(ordered, slots, strict=False)
        ]
        self._log(f"scan: {target} -> {len(payload)}")
        return {
            "ok": True,
            "source": str(target),
            "source_kind": source_kind,
            "saves": payload,
            "count": len(payload),
        }

    def _single_file(self, target: Path) -> discovery.SaveRecord:
        data = fsutil.read_bytes(target)
        kind = codec.detect_kind(data)
        if kind is SaveKind.UNKNOWN:
            raise TransferError(
                f"Неизвестный magic {codec.format_magic(data)} у файла {target.name}",
                hint="Это не сохранение Persona 3 Reload (ни GVAS, ни обёртка Steam).",
            )
        plain = codec.decrypt(data) if kind is SaveKind.OBFUSCATED else data
        from p3r_transfer.meta import read_metadata

        return discovery.SaveRecord(
            path=target,
            size=len(data),
            kind=kind,
            mtime=target.stat().st_mtime,
            slot=discovery.slot_from_filename(target.name),
            meta=read_metadata(plain),
            magic=codec.magic_of(data),
        )

    def _scan_dir(self, target: Path) -> tuple[list[discovery.SaveRecord], str]:
        """Steam root (folders named after SteamID64) or a wgs-style tree."""
        steam_ids = [p for p in target.iterdir() if p.is_dir() and p.name.isdigit()]
        looks_like_steam = any(
            child.name.lower().startswith("savedata")
            for folder in steam_ids
            for child in folder.iterdir()
        )
        if steam_ids and looks_like_steam:
            return list(discovery.iter_steam_saves(target)), "steam"
        return list(discovery.iter_wgs_saves(target)), "wgs"

    @staticmethod
    def _order(records: Iterable[discovery.SaveRecord]) -> list[discovery.SaveRecord]:
        from p3r_transfer.discovery import sort_for_slots

        return sort_for_slots(records)

    @staticmethod
    def _propose_slots(records: list[discovery.SaveRecord]) -> list[int | None]:
        """Keep a slot from the file name when it has one, otherwise number 1..N."""
        used: set[int] = set()
        proposed: list[int | None] = []
        for record in records:
            if record.slot is not None and record.slot not in used:
                used.add(record.slot)
                proposed.append(record.slot)
            else:
                proposed.append(None)
        next_slot = 1
        for index, slot in enumerate(proposed):
            if slot is not None:
                continue
            while next_slot in used:
                next_slot += 1
            used.add(next_slot)
            proposed[index] = next_slot
        return proposed

    @_guard
    def detect_dest(self, preferred_account: str | None = None) -> dict:
        """Locate ``%APPDATA%\\Sega\\P3R\\Steam`` and list SteamID folders."""
        root = discovery.default_steam_root()
        if not root.is_dir():
            return {
                "ok": True,
                "root": str(root),
                "accounts": [],
                "path": "",
                "error": None,
                "exists": False,
            }
        accounts = []
        for folder in discovery.find_steam_ids(root):
            saves = sum(1 for _ in folder.glob("SaveData*.sav"))
            accounts.append({"id": folder.name, "path": str(folder), "saves": saves})
        chosen = ""
        if preferred_account:
            match = [a for a in accounts if a["id"] == preferred_account]
            if match:
                chosen = match[0]["path"]
        if not chosen and accounts:
            chosen = accounts[0]["path"]
        return {
            "ok": True,
            "root": str(root),
            "accounts": accounts,
            "path": chosen,
            "exists": True,
        }

    @_guard
    def plan_convert(self, items: list[dict], dst: str, opts: dict | None = None) -> dict:
        """What would be written, and which targets already exist."""
        opts = opts or {}
        destination = self._require_dest(dst)
        keep_force = bool(opts.get("force"))
        taken = set(discovery.existing_slots(destination))
        plan = []
        conflicts = []
        for item in items:
            slot = item.get("slot")
            if slot is None:
                slot = self._next_free(taken)
            if slot in taken and not keep_force:
                conflicts.append(convert.slot_filename(slot))
            taken.add(slot)
            target = destination / convert.slot_filename(slot)
            plan.append(
                {
                    "path": item["path"],
                    "name": Path(item["path"]).name,
                    "slot": slot,
                    "target": str(target),
                    "exists": target.exists(),
                }
            )
        return {"ok": True, "plan": plan, "conflicts": conflicts, "dst": str(destination)}

    @_guard
    def convert(
        self,
        items: list[dict],
        dst: str,
        opts: dict | None = None,
        on_progress: ProgressCallback | None = None,
        should_cancel: CancelCallback | None = None,
    ) -> dict:
        """Convert the selected saves into the destination folder."""
        opts = opts or {}
        dry_run = bool(opts.get("dry_run"))
        backup = bool(opts.get("backup", True))
        force = bool(opts.get("force"))
        destination = self._require_dest(dst, create=not dry_run)
        if not items:
            raise TransferError("Не выбрано ни одного сохранения")

        results: list[dict] = []
        converted = skipped = failed = 0
        cancelled = False
        total = len(items)

        if on_progress:
            on_progress({"event": "start", "total": total, "dst": str(destination)})

        backup_dir = None
        if backup and not dry_run:
            backup_dir = convert.create_backup(destination)
            if backup_dir:
                self._log(f"backup: {backup_dir}")

        for index, item in enumerate(items, start=1):
            if should_cancel and should_cancel():
                cancelled = True
                for rest in items[index - 1 :]:
                    results.append(
                        {
                            "path": rest["path"],
                            "slot": rest.get("slot"),
                            "status": "cancelled",
                            "target": None,
                            "error": None,
                        }
                    )
                break
            if on_progress:
                on_progress(
                    {
                        "event": "item",
                        "index": index,
                        "total": total,
                        "path": item["path"],
                        "slot": item.get("slot"),
                        "status": "running",
                    }
                )
            try:
                outcome = convert.convert_file(
                    item["path"],
                    destination,
                    slot=item.get("slot"),
                    force=force,
                    dry_run=dry_run,
                    backup=False,
                )
            except TransferError as exc:
                failed += 1
                self._log(f"! {Path(item['path']).name}: {exc.message}")
                results.append(
                    {
                        "path": item["path"],
                        "slot": item.get("slot"),
                        "status": "error",
                        "target": None,
                        "error": exc.message,
                        "hint": exc.hint,
                    }
                )
                if on_progress:
                    on_progress(
                        {
                            "event": "item",
                            "index": index,
                            "total": total,
                            "path": item["path"],
                            "slot": item.get("slot"),
                            "status": "error",
                            "error": exc.message,
                        }
                    )
                continue
            converted += 1
            self._log(f"ok: {Path(item['path']).name} -> {outcome.target}")
            results.append(
                {
                    "path": str(outcome.source),
                    "slot": outcome.slot,
                    "status": "ok",
                    "target": str(outcome.target),
                    "error": None,
                }
            )
            if on_progress:
                on_progress(
                    {
                        "event": "item",
                        "index": index,
                        "total": total,
                        "path": item["path"],
                        "slot": outcome.slot,
                        "status": "ok",
                        "target": str(outcome.target),
                    }
                )

        summary = {
            "ok": True,
            "converted": converted,
            "skipped": skipped,
            "failed": failed,
            "cancelled": cancelled,
            "dry_run": dry_run,
            "dst": str(destination),
            "backup": str(backup_dir) if backup_dir else None,
            "items": results,
        }
        if on_progress:
            on_progress({"event": "done", **summary})
        self._log(
            f"convert: ok={converted} failed={failed} cancelled={cancelled} dry_run={dry_run}"
        )
        return summary

    @_guard
    def open_folder(self, path: str) -> dict:
        target = Path(path)
        if not target.exists():
            raise TransferError(f"Папки нет: {target}")
        if self._opener is not None:
            self._opener(str(target))
            return {"ok": True}
        if os.name == "nt":
            os.startfile(str(target))  # noqa: S606 - Windows shell integration
            return {"ok": True}
        raise TransferError("Открытие папки поддерживается только в Windows", hint=str(target))

    # --- internals ---------------------------------------------------------

    @staticmethod
    def _next_free(taken: set[int], start: int = 1) -> int:
        slot = start
        while slot in taken:
            slot += 1
        return slot

    @staticmethod
    def _require_dest(dst: str, *, create: bool = False) -> Path:
        if not dst or not str(dst).strip():
            raise TransferError(
                "Не указана папка назначения",
                hint="Определи папку Steam автоматически или выбери её вручную.",
            )
        path = Path(str(dst).strip().strip('"'))
        if path.exists() and not path.is_dir():
            raise TransferError(f"Это файл, а не папка: {path}")
        if create and not path.exists():
            path.mkdir(parents=True, exist_ok=True)
        return path
