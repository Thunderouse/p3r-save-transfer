"""Console entry point: ``p3r-save-transfer``.

All user-facing text is Russian, all printing lives here.  Every command
returns an exit code (0 ok, 1 problem, 2 usage).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from . import __version__, codec, convert, discovery, fsutil
from .codec import SaveKind
from .errors import SlotBusyError, SteamDirNotFoundError, TransferError, UsageError
from .meta import SaveMetadata

log = logging.getLogger("p3r_transfer")

STATUS_LABELS = {"ok": "ок", "dry-run": "пробный прогон"}

#: Exit codes are public API (README, --help, tests).
EXIT_OK = 0
EXIT_FATAL = 1
EXIT_USAGE = 2
EXIT_PARTIAL = 3

REASON_LABELS = {
    discovery.SKIP_NOT_A_SAVE: "не сохранение",
    discovery.SKIP_EMPTY: "пустой файл",
    discovery.SKIP_OS_JUNK: "служебный файл ОС",
    discovery.SKIP_TEMP_FILE: "временный файл",
    discovery.SKIP_UNREADABLE: "не читается",
    discovery.SKIP_TOO_LARGE: "слишком большой",
    discovery.SKIP_NO_ACCESS: "нет доступа",
}


# ------------------------------------------------------------------ formatting


def _fmt_playtime(seconds: int | None) -> str:
    if seconds is None:
        return "n/a"
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def _meta_summary(meta: SaveMetadata | None) -> str:
    if meta is None or not meta.parsed:
        return "n/a"
    parts = [
        f"уровень {meta.level}" if meta.level is not None else "уровень n/a",
        f"время {_fmt_playtime(meta.play_time_seconds)}",
        f"сложность {meta.difficulty or 'n/a'}",
    ]
    if meta.format_version is not None:
        parts.append(f"формат v{meta.format_version}")
    return ", ".join(parts)


def _size_pair(size: int) -> str:
    human = fsutil.human_size(size)
    return human if human == f"{size} Б" else f"{human} ({size} Б)"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    lines = [
        "  ".join(header.ljust(widths[i]) for i, header in enumerate(headers)).rstrip(),
        "  ".join("-" * width for width in widths),
    ]
    for row in rows:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    return "\n".join(lines)


def _relative(path: Path, root: Path | None) -> str:
    """Path relative to the scanned root - safe to paste into an issue."""
    if root is not None:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            pass
    return path.name


def _record_json(record: discovery.SaveRecord, root: Path | None = None) -> dict:
    meta = record.meta
    return {
        "path": _relative(record.path, root),
        "category": record.category,
        "size": record.size,
        "kind": record.kind.value,
        "container_guid": record.container_guid,
        "user_id": record.user_id,
        "slot": record.slot,
        "magic": None if record.magic is None else f"0x{record.magic:08X}",
        "mtime": datetime.fromtimestamp(record.mtime).isoformat(timespec="seconds")
        if record.mtime
        else None,
        "note": record.note,
        "meta": None
        if meta is None
        else {
            "parsed": meta.parsed,
            "level": meta.level,
            "play_time_seconds": meta.play_time_seconds,
            "play_time": _fmt_playtime(meta.play_time_seconds),
            "difficulty": meta.difficulty,
            "format_version": meta.format_version,
        },
    }


def _print_record(index: int, total: int, record: discovery.SaveRecord) -> None:
    print(f"[{index}/{total}] {record.path}")
    magic = f", magic 0x{record.magic:08X}" if record.magic is not None else ""
    print(f"    размер: {_size_pair(record.size)} | тип: {record.kind_label}{magic}")
    guid = record.container_guid or "n/a"
    user = record.user_id or "n/a"
    when = fsutil.format_mtime(record.mtime) if record.mtime else "n/a"
    print(f"    контейнер: {guid} | пользователь: {user} | mtime: {when}")
    print(f"    метаданные: {_meta_summary(record.meta)}")
    if record.note:
        print(f"    примечание: {record.note}")


def _print_result(result: convert.ConversionResult) -> None:
    print(f"Источник:    {result.source}")
    print(f"Тип входа:   {discovery.KIND_LABELS[result.source_kind]}")
    print(f"Направление: {result.direction}")
    print(f"Цель:        {result.target}")
    print(f"Слот:        {result.slot}")
    print(f"Размер:      {_size_pair(result.size)}, без изменений")
    print(f"Статус:      {STATUS_LABELS.get(result.status, result.status)}")
    if result.backup:
        print(f"Бэкап:       {result.backup}")
    if result.status == "dry-run":
        print("Ни один файл не записан (--dry-run).")


# -------------------------------------------------------------------- commands


def _file_label(path: Path, root: Path | None) -> str:
    """Just the file name - the GUID folders are noise in a summary table."""
    return path.name


def _skipped_rows(items: list[discovery.SkippedFile], root: Path | None) -> list[list[str]]:
    rows: list[list[str]] = []
    for item in items:
        rows.append(
            [
                _file_label(item.path, root),
                item.reason,
                fsutil.human_size(item.size) if item.size else "-",
                item.detail or REASON_LABELS.get(item.reason, ""),
            ]
        )
    return rows


def _print_skipped(items: list[discovery.SkippedFile], root: Path | None) -> None:
    if not items:
        return
    print()
    print(f"Пропущено ({len(items)}):")
    print(_table(["Файл", "Причина", "Размер", "Пояснение"], _skipped_rows(items, root)))


def _skipped_json(items: list[discovery.SkippedFile], root: Path | None) -> list[dict]:
    return [
        {
            "path": _relative(item.path, root),
            "category": discovery.CATEGORY_SKIPPED,
            "reason": item.reason,
            "size": item.size,
            "detail": item.detail,
            "magic": None if item.magic is None else f"0x{item.magic:08X}",
        }
        for item in items
    ]


def _counts_json(result: discovery.ScanResult) -> dict:
    gvas = sum(1 for record in result.saves if record.kind is SaveKind.GVAS)
    return {
        "saves": len(result.saves),
        "save_gvas": gvas,
        "save_steam": len(result.saves) - gvas,
        "skipped": len(result.skipped),
        "metadata": result.metadata_files,
        "by_reason": result.reason_counts(),
    }


def explain_file(path: str | Path) -> int:
    """Print why a file is or is not treated as a save."""
    target = Path(path)
    if not target.exists():
        raise TransferError(f"Файл не найден: {target}")
    if target.is_dir():
        raise TransferError(f"Это каталог, а не файл: {target}")
    verdict = discovery.inspect_file(target)
    print(f"Файл:    {target}")
    try:
        size = target.stat().st_size
    except OSError:
        size = 0
    print(f"Размер:  {_size_pair(size)}")
    head = b""
    try:
        head = fsutil.read_head(target, 16)
    except TransferError as exc:
        print(f"Заголовок: не читается ({exc.message})")
    if head:
        print(f"Magic:   {codec.format_magic(head)}")
        print(f"Первые 16 байт: {head.hex(' ')}")
    if verdict is None:
        print("Категория: metadata (служебный файл Xbox, не сохранение)")
        return EXIT_OK
    if isinstance(verdict, discovery.SaveRecord):
        print(f"Категория: {verdict.category} ({verdict.kind_label})")
        print(f"Метаданные: {_meta_summary(verdict.meta)}")
        print("Решение: конвертировать")
        return EXIT_OK
    print(f"Категория: {discovery.CATEGORY_SKIPPED} ({verdict.reason})")
    print(f"Пояснение: {verdict.detail or REASON_LABELS.get(verdict.reason, '')}")
    print("Решение: пропустить")
    return EXIT_OK


def cmd_explain(args: argparse.Namespace) -> int:
    return explain_file(args.file)


def cmd_scan(args: argparse.Namespace) -> int:
    if getattr(args, "explain", None):
        return explain_file(args.explain)

    root = discovery.resolve_wgs_root(args.wgs)
    result = discovery.scan_tree(root, include_skipped=args.include_skipped)
    if args.steam:
        try:
            steam_root = discovery.resolve_steam_root(args.steam_root)
        except SteamDirNotFoundError as exc:
            print(f"Внимание: {exc}", file=sys.stderr)
        else:
            result.extend(
                discovery.scan_steam(steam_root, include_skipped=args.include_skipped)
            )
    result.saves.sort(key=lambda record: (record.mtime, str(record.path)))

    if args.json:
        print(
            json.dumps(
                {
                    "version": __version__,
                    "root": str(root),
                    "counts": _counts_json(result),
                    "saves": [_record_json(record, root) for record in result.saves],
                    "skipped": _skipped_json(result.skipped, root),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        counts = _counts_json(result)
        print(f"Каталог wgs: {root}")
        print(
            f"Сохранений: {counts['saves']} "
            f"(GVAS {counts['save_gvas']}, Steam {counts['save_steam']}), "
            f"пропущено: {counts['skipped']}, служебных файлов: {counts['metadata']}"
        )
        if result.saves:
            print()
            for index, record in enumerate(result.saves, start=1):
                _print_record(index, len(result.saves), record)
                if index != len(result.saves):
                    print()
        elif not result.skipped:
            print(
                "Пусто. Запустите игру из Microsoft Store хотя бы раз, чтобы сохранения "
                "появились на диске (облачные сохранения сначала нужно скачать)."
            )
        _print_skipped(result.skipped, root)

    if args.strict and result.skipped:
        print(
            f"Строгий режим: пропущено {len(result.skipped)} файл(ов), это ошибка.",
            file=sys.stderr,
        )
        return EXIT_FATAL
    if not result.saves:
        print(
            f"Сохранений не найдено, пропущено {len(result.skipped)} файл(ов) "
            f"({result.reason_summary() or 'файлов нет'}).",
            file=sys.stderr,
        )
        return EXIT_PARTIAL
    return EXIT_OK


def cmd_convert(args: argparse.Namespace) -> int:
    result = convert.convert_file(
        args.source,
        args.out_dir,
        slot=args.slot,
        force=args.force,
        reverse=args.reverse,
        dry_run=args.dry_run,
        backup=not args.no_backup,
    )
    if args.json:
        print(
            json.dumps(
                {
                    "source": str(result.source),
                    "target": str(result.target),
                    "source_kind": result.source_kind.value,
                    "direction": result.direction,
                    "slot": result.slot,
                    "size": result.size,
                    "status": result.status,
                    "backup": str(result.backup) if result.backup else None,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    _print_result(result)
    return 0


def cmd_convert_all(args: argparse.Namespace) -> int:
    root = discovery.resolve_wgs_root(args.wgs)
    result = discovery.scan_tree(root, include_skipped=args.include_skipped)

    if args.strict and result.skipped:
        print(f"Источник: {root}")
        _print_skipped(result.skipped, root)
        print(
            f"Строгий режим: пропущено {len(result.skipped)} файл(ов), ничего не записано.",
            file=sys.stderr,
        )
        return EXIT_FATAL

    if not result.saves:
        print(
            f"Сохранений не найдено в {root}, пропущено {len(result.skipped)} файл(ов) "
            f"({result.reason_summary() or 'нет файлов'}).",
            file=sys.stderr,
        )
        _print_skipped(result.skipped, root)
        return EXIT_PARTIAL

    out_dir = Path(args.out_dir)
    if not args.dry_run:
        convert.ensure_free_space(out_dir, sum(record.size for record in result.saves))

    plan = convert.plan_slots(result.saves, out_dir, force=args.force)

    backup_dir = None
    if not args.dry_run and not args.no_backup:
        backup_dir = convert.create_backup(out_dir)

    rows: list[list[str]] = []
    failures = 0
    payload: list[dict] = []
    for record, slot in plan:
        try:
            result_one = convert.convert_file(
                record.path,
                out_dir,
                slot=slot,
                force=args.force,
                dry_run=args.dry_run,
                backup=False,
            )
        except TransferError as exc:
            failures += 1
            rows.append(
                [
                    record.path.name,
                    str(slot),
                    "-",
                    fsutil.human_size(record.size),
                    f"ошибка: {exc.message}",
                ]
            )
            payload.append(
                {
                    "source": str(record.path),
                    "slot": slot,
                    "status": "error",
                    "error": str(exc),
                }
            )
            continue
        rows.append(
            [
                record.path.name,
                str(result_one.slot),
                result_one.target.name if result_one.target else "-",
                fsutil.human_size(result_one.size),
                STATUS_LABELS.get(result_one.status, result_one.status),
            ]
        )
        payload.append(
            {
                "source": str(result_one.source),
                "target": str(result_one.target) if result_one.target else None,
                "slot": result_one.slot,
                "size": result_one.size,
                "status": result_one.status,
            }
        )

    converted = len(plan) - failures
    skipped_count = len(result.skipped)
    if args.json:
        print(
            json.dumps(
                {
                    "version": __version__,
                    "source": str(root),
                    "destination": str(out_dir),
                    "converted": converted,
                    "failed": failures,
                    "skipped": _skipped_json(result.skipped, root),
                    "files": payload,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"Источник: {root}")
        print(f"Цель:     {out_dir}")
        print()
        print(_table(["Файл", "Слот", "Результат", "Размер", "Статус"], rows))
        print()
        print(f"Сконвертировано {converted}, пропущено {skipped_count}.")
        if failures:
            print(f"Не удалось обработать: {failures}.")
        _print_skipped(result.skipped, root)
        if backup_dir:
            print(f"Бэкап: {backup_dir}")
        if args.dry_run:
            print("Пробный прогон: файлы не записаны.")
        print(
            "Номера слотов назначены по возрастанию игрового времени: "
            "слот 1 - самое старое сохранение."
        )
    return EXIT_PARTIAL if (failures or skipped_count) else EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    failures = 0
    for name in args.files:
        try:
            report = convert.verify_file(name)
        except TransferError as exc:
            failures += 1
            print(f"ОШИБКА  {name}: {exc}")
            continue
        if report.ok:
            print(
                f"OK      {name}: {discovery.KIND_LABELS[report.kind]}, "
                f"{_size_pair(report.size)}, round-trip совпал; {_meta_summary(report.meta)}"
            )
        else:
            failures += 1
            print(f"ОШИБКА  {name}: " + "; ".join(report.problems))
        for warning in report.warnings:
            print(f"        предупреждение: {warning}")
    print()
    print("Итог: всё в порядке" if not failures else f"Итог: проблем - {failures}")
    return 1 if failures else 0


def _pick_steam_id(ids: list[Path]) -> Path:
    if not getattr(sys.stdin, "isatty", lambda: False)():
        listing = "\n".join(f"  {i}. {p.name}  ({p})" for i, p in enumerate(ids, start=1))
        raise UsageError(
            "Найдено несколько Steam-аккаунтов, а консоль не интерактивна",
            hint=f"Укажите --steam-id. Доступны:\n{listing}",
        )
    print("Найдено несколько Steam-аккаунтов:")
    for index, path in enumerate(ids, start=1):
        print(f"  {index}. {path.name}  ({path})")
    while True:
        try:
            raw = input(f"Выберите номер 1..{len(ids)}: ").strip()
        except EOFError as exc:
            raise UsageError("Ввод прерван", hint="Укажите --steam-id вручную.") from exc
        if raw.isdigit() and 1 <= int(raw) <= len(ids):
            return ids[int(raw) - 1]
        print("Нужен номер из списка.")


def cmd_install(args: argparse.Namespace) -> int:
    steam_root = discovery.resolve_steam_root(args.steam_root)
    ids = discovery.find_steam_ids(steam_root)
    if not ids:
        raise SteamDirNotFoundError(
            f"В {steam_root} нет каталогов SteamID",
            hint="Запустите Steam-версию P3R один раз, чтобы игра создала профиль.",
        )
    if args.steam_id:
        matches = [path for path in ids if path.name == args.steam_id]
        if not matches:
            raise UsageError(
                f"SteamID {args.steam_id} не найден в {steam_root}",
                hint="Доступны: " + ", ".join(path.name for path in ids),
            )
        account = matches[0]
    elif len(ids) == 1:
        account = ids[0]
    else:
        account = _pick_steam_id(ids)

    if not 1 <= args.slot <= convert.MAX_SLOT:
        raise UsageError(f"Слот должен быть от 1 до {convert.MAX_SLOT}")
    target = account / convert.slot_filename(args.slot)

    data = fsutil.read_bytes(args.source)
    kind = codec.detect_kind(data)
    if kind is SaveKind.GVAS:
        payload = codec.encrypt(data)
        note = "вход был GVAS, упакован в формат Steam"
    elif kind is SaveKind.OBFUSCATED:
        payload = data
        note = "вход уже в формате Steam, скопирован как есть"
    else:
        raise TransferError(
            f"Это не сохранение P3R: первые байты {codec.format_magic(data)}",
            hint="Ожидался GVAS (0x53415647) или обфускация Steam (0x0B650015). "
            f"Начало файла: {data[:16].hex(' ')}",
        )
    if len(data) < codec.MIN_SAVE_SIZE:
        raise TransferError(f"Файл слишком мал: {len(data)} байт")

    conflict = target.exists()
    if conflict and not args.force:
        free = convert.choose_slot(account, None)
        raise SlotBusyError(
            f"Слот {args.slot} уже занят: {target}",
            hint=f"Свободен слот {free}: --slot {free}. Либо --force, чтобы перезаписать.",
        )

    print(f"Аккаунт: {account}")
    print(f"Цель:    {target}")
    print(f"Размер:  {_size_pair(len(payload))}")
    print(f"Действие: {note}")
    if args.dry_run:
        print("Пробный прогон: файл не записан.")
        return 0

    backup_dir = None
    if conflict and not args.no_backup:
        backup_dir = convert.create_backup(account, files=[target])
        print(f"Бэкап:   {backup_dir}")
    fsutil.write_bytes_atomic(target, payload, min_size=codec.MIN_SAVE_SIZE)
    if fsutil.sha256_of(target) != hashlib.sha256(payload).hexdigest():
        raise TransferError(f"Запись не совпала с ожидаемыми байтами: {target}")
    print("Готово. Запустите игру и проверьте слот в списке загрузки.")
    return 0


# ---------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="p3r-save-transfer",
        description=(
            "Перенос сохранений Persona 3 Reload между Microsoft Store "
            "(Xbox/Game Pass) и Steam."
        ),
        epilog=(
            "Примеры:\n"
            "  p3r-save-transfer scan\n"
            "  p3r-save-transfer convert --in \"C:\\wgs\\save\" "
            "--out-dir \"%APPDATA%\\Sega\\P3R\\Steam\\<SteamID64>\"\n"
            "  p3r-save-transfer convert-all --wgs \"%LOCALAPPDATA%\\Packages\\...\\wgs\" "
            "--out-dir .\\out --dry-run\n"
            "  p3r-save-transfer verify SaveData001.sav\n"
            "  p3r-save-transfer install --in SaveData001.sav --slot 1\n"
            "  p3r-save-transfer explain notes.txt\n"
            "\n"
            "Коды возврата:\n"
            "  0 - всё обработано, пропусков нет\n"
            "  1 - фатальная ошибка (нет каталога, нет прав, указанный файл не сохранение)\n"
            "  2 - неверные аргументы\n"
            "  3 - частичный успех: что-то сконвертировано, что-то пропущено\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"p3r-save-transfer {__version__}")
    parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="подробный лог (можно -vv)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="найти сохранения в дереве wgs и показать метаданные")
    scan.add_argument("--wgs", help="каталог wgs (по умолчанию из %%LOCALAPPDATA%%)")
    scan.add_argument("--steam", action="store_true", help="показать также сохранения Steam")
    scan.add_argument("--steam-root", help="каталог Steam (по умолчанию из %%APPDATA%%)")
    scan.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    scan.add_argument(
        "--strict", action="store_true", help="любой пропущенный файл считать ошибкой"
    )
    scan.add_argument(
        "--include-skipped",
        action="store_true",
        help="показывать файлы с незнакомым magic как кандидатов",
    )
    scan.add_argument("--explain", metavar="FILE", help="объяснить решение по одному файлу")
    scan.set_defaults(func=cmd_scan)

    one = sub.add_parser("convert", help="конвертировать один файл")
    one.add_argument("--in", dest="source", required=True, help="входной файл сохранения")
    one.add_argument("--out-dir", required=True, help="каталог, куда писать SaveDataNNN.sav")
    one.add_argument("--slot", type=int, help="номер слота (по умолчанию - первый свободный)")
    one.add_argument("--force", action="store_true", help="перезаписать занятый слот")
    one.add_argument(
        "--reverse", action="store_true", help="Steam -> MS Store (обратное направление)"
    )
    one.add_argument(
        "--dry-run", action="store_true", help="только показать план, ничего не писать"
    )
    one.add_argument(
        "--no-backup", action="store_true", help="не создавать бэкап целевого каталога"
    )
    one.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    one.set_defaults(func=cmd_convert)

    every = sub.add_parser("convert-all", help="конвертировать все сохранения из wgs")
    every.add_argument("--wgs", required=True, help="каталог wgs")
    every.add_argument("--out-dir", required=True, help="каталог назначения")
    every.add_argument("--force", action="store_true", help="перезаписывать занятые слоты")
    every.add_argument("--dry-run", action="store_true", help="только показать план")
    every.add_argument("--no-backup", action="store_true", help="не создавать бэкап")
    every.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    every.add_argument(
        "--strict", action="store_true", help="любой пропущенный файл считать ошибкой"
    )
    every.add_argument(
        "--include-skipped",
        action="store_true",
        help="пытаться конвертировать даже файлы с незнакомым magic",
    )
    every.set_defaults(func=cmd_convert_all)

    explain = sub.add_parser("explain", help="показать, почему файл считается сохранением")
    explain.add_argument("file", help="файл для проверки")
    explain.set_defaults(func=cmd_explain)

    check = sub.add_parser("verify", help="проверить magic, размер и round-trip")
    check.add_argument("files", nargs="+", help="файлы для проверки")
    check.set_defaults(func=cmd_verify)

    install = sub.add_parser("install", help="скопировать сохранение прямо в каталог Steam")
    install.add_argument("--in", dest="source", required=True, help="входной файл")
    install.add_argument("--slot", type=int, required=True, help="номер слота в Steam-версии")
    install.add_argument("--steam-root", help="каталог Steam (по умолчанию из %%APPDATA%%)")
    install.add_argument("--steam-id", help="SteamID64, если аккаунтов несколько")
    install.add_argument("--force", action="store_true", help="перезаписать занятый слот")
    install.add_argument("--dry-run", action="store_true", help="только показать план")
    install.add_argument("--no-backup", action="store_true", help="не создавать бэкап")
    install.set_defaults(func=cmd_install)
    return parser


def main(argv: list[str] | None = None) -> int:
    fsutil.configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )
    try:
        return args.func(args) or 0
    except TransferError as exc:
        print(f"Ошибка: {exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"Подсказка: {exc.hint}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("\nПрервано.", file=sys.stderr)
        return 130


def run() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    run()
