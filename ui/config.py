"""Portable settings: ``config.json`` next to the exe, with a documented fallback.

If the app folder is read-only (a CD, a locked USB stick, Program Files), the
settings move to ``%LOCALAPPDATA%\\P3R-Save-Transfer\\config.json`` and the UI
is told about it - never a silent failure.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

APP_FOLDER_NAME = "P3R-Save-Transfer"
FILE_NAME = "config.json"

DEFAULTS: dict[str, object] = {
    "lang": "ru",
    "source": "",
    "destination": "",
    "steam_id": "",
    "backup": True,
    "dry_run": False,
}


def _fallback_dir() -> Path | None:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    if not base:
        base = str(Path.home() / ".local" / "share")
    return Path(base) / APP_FOLDER_NAME


class Config:
    """Load/save a small dict, degrading to the fallback path when read-only."""

    def __init__(
        self,
        app_dir: Path,
        on_notice: Callable[[str], None] | None = None,
    ) -> None:
        self.app_dir = Path(app_dir)
        self._on_notice = on_notice
        self.values: dict[str, object] = dict(DEFAULTS)
        self.path: Path | None = None
        self.used_fallback = False
        self.load()

    # --- locations ---------------------------------------------------------

    def candidates(self) -> list[Path]:
        paths = [self.app_dir / FILE_NAME]
        fallback = _fallback_dir()
        if fallback is not None:
            paths.append(fallback / FILE_NAME)
        return paths

    # --- io ----------------------------------------------------------------

    def load(self) -> None:
        for path in self.candidates():
            if not path.is_file():
                continue
            try:
                with open(path, encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, ValueError) as exc:
                log.warning("Настройки не прочитаны (%s): %s", path, exc)
                continue
            if not isinstance(data, dict):
                continue
            self._merge(data)
            self.path = path
            self.used_fallback = path != self.candidates()[0]
            if self.used_fallback:
                self._notice(path)
            return

    def _merge(self, data: dict) -> None:
        if isinstance(data.get("lang"), str) and data["lang"] in {"ru", "en"}:
            self.values["lang"] = data["lang"]
        for key in ("source", "destination", "steam_id"):
            if isinstance(data.get(key), str):
                self.values[key] = data[key]
        for key in ("backup", "dry_run"):
            if isinstance(data.get(key), bool):
                self.values[key] = data[key]

    def save(self) -> None:
        payload = json.dumps(self.values, ensure_ascii=False, indent=2).encode("utf-8")
        for path in self.candidates():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                self._write_atomic(path, payload)
            except OSError as exc:
                log.warning("Не удалось записать настройки в %s: %s", path, exc)
                continue
            if path != self.candidates()[0]:
                self._notice(path)
            self.path = path
            self.used_fallback = path != self.candidates()[0]
            return
        log.error("Настройки не сохранены: нет доступного пути")

    @staticmethod
    def _write_atomic(path: Path, payload: bytes) -> None:
        fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".config-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
            os.replace(temporary, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise

    def _notice(self, path: Path) -> None:
        log.warning("Настройки сохраняются в запасной путь: %s", path)
        if self._on_notice is not None:
            try:
                self._on_notice(str(path))
            except Exception:  # noqa: BLE001
                log.exception("Ошибка обработчика настроек")

    # --- convenience -------------------------------------------------------

    def get(self, key: str, default: object = None) -> object:
        return self.values.get(key, DEFAULTS.get(key, default))

    def update(self, **values: object) -> None:
        self.values.update(values)
        self.save()
