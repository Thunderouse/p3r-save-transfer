"""JSON-backed interface strings, with locale-aware numbers and dates.

No user-visible text belongs in Python or in widget code: everything is looked
up by key here, so switching language is a reload rather than a restart.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .resources import asset

log = logging.getLogger(__name__)

DEFAULT_LANG = "ru"
LANGUAGES = ("ru", "en")


def _load(lang: str) -> dict[str, str]:
    path: Path = asset("i18n", f"{lang}.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:  # missing translation must not kill the app
        log.error("Не удалось прочитать %s: %s", path, exc)
        return {}
    return {key: value for key, value in data.items() if isinstance(value, str)}


class I18n:
    """Tiny translator with a safe fallback: show the key, never crash."""

    def __init__(self, lang: str = DEFAULT_LANG) -> None:
        self.lang = lang if lang in LANGUAGES else DEFAULT_LANG
        self._strings: dict[str, str] = _load(self.lang)
        self._fallback: dict[str, str] = (
            _load(DEFAULT_LANG) if self.lang != DEFAULT_LANG else self._strings
        )

    def set_lang(self, lang: str) -> None:
        if lang == self.lang or lang not in LANGUAGES:
            return
        self.lang = lang
        self._strings = _load(lang)
        self._fallback = _load(DEFAULT_LANG)

    def t(self, key: str, **fmt: object) -> str:
        text = self._strings.get(key) or self._fallback.get(key) or key
        if not fmt:
            return text
        try:
            return text.format(**fmt)
        except (KeyError, IndexError, ValueError):
            log.warning("Плохой шаблон перевода: %s", key)
            return text

    # --- locale formatting -------------------------------------------------

    def number(self, value: int) -> str:
        """1234567 -> ``1 234 567`` (ru) / ``1,234,567`` (en)."""
        grouped = f"{value:,}".replace(",", "\u00a0" if self.lang == "ru" else ",")
        return grouped if self.lang == "ru" else grouped

    def size(self, num_bytes: int) -> str:
        if num_bytes < 1024:
            return f"{self.number(num_bytes)} {self.t('unit.byte')}"
        if num_bytes < 1024 * 1024:
            value = num_bytes / 1024
            return self._decimal(value, 1) + " " + self.t("unit.kb")
        value = num_bytes / (1024 * 1024)
        return self._decimal(value, 2) + " " + self.t("unit.mb")

    def _decimal(self, value: float, digits: int) -> str:
        text = f"{value:.{digits}f}"
        return text.replace(".", ",") if self.lang == "ru" else text

    def date(self, timestamp: float | None) -> str:
        if not timestamp:
            return "—"
        from datetime import datetime

        moment = datetime.fromtimestamp(timestamp)
        return moment.strftime(self.t("format.date"))

    def playtime(self, seconds: int | None) -> str:
        if seconds is None:
            return "—"
        hours, rest = divmod(int(seconds), 3600)
        minutes = rest // 60
        return f"{self.number(hours)}:{minutes:02d}"

    def level(self, value: int | None) -> str:
        return "—" if value is None else str(value)
