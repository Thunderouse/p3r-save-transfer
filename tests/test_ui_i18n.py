"""Translations: same keys everywhere, nothing hard-coded in the widgets."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ui.i18n import DEFAULT_LANG, LANGUAGES, I18n
from ui.resources import asset

UI_DIR = Path(__file__).resolve().parent.parent / "ui"


def load(lang: str) -> dict:
    return json.loads(
        (UI_DIR.parent / "assets" / "i18n" / f"{lang}.json").read_text(encoding="utf-8")
    )


def test_keys_match_between_languages():
    ru, en = load("ru"), load("en")
    assert set(ru) == set(en), set(ru) ^ set(en)


def test_every_literal_key_is_translated():
    used: set[str] = set()
    pattern = re.compile(r"\bt\(\s*[\"']([a-z0-9_.]+)[\"']")
    for path in UI_DIR.glob("*.py"):
        used.update(pattern.findall(path.read_text(encoding="utf-8")))
    assert used, "no literal keys found - the scan regex broke"
    missing = {key for key in used if key not in load(DEFAULT_LANG)}
    assert not missing, missing


def test_dynamic_key_families_exist():
    ru = load("ru")
    for key in (
        "table.slot",
        "table.date",
        "table.level",
        "table.time",
        "table.size",
        "table.type",
    ):
        assert key in ru
    for status in ("pending", "running", "ok", "error", "cancelled"):
        assert f"status.{status}" in ru
    for step in ("step1", "step2", "step3", "files", "warn"):
        assert f"help.{step}.title" in ru and f"help.{step}.body" in ru


def test_missing_key_falls_back_to_itself():
    assert I18n("ru").t("nope.nothing") == "nope.nothing"


def test_language_switch():
    tr = I18n("ru")
    title = tr.t("app.title")
    tr.set_lang("en")
    assert tr.lang == "en"
    assert tr.t("action.convert") == "CONVERT"
    tr.set_lang("de")  # ignored
    assert tr.lang == "en"
    assert title == "P3R SAVE TRANSFER"


def test_formatting_is_locale_aware():
    ru, en = I18n("ru"), I18n("en")
    assert ru.number(1234567) == "1\u00a0234\u00a0567"
    assert en.number(1234567) == "1,234,567"
    assert ru.size(1_400_000) == "1,34 МБ"
    assert en.size(1_400_000) == "1.34 MB"
    assert ru.size(900) == "900 Б"
    assert en.size(1536) == "1.5 KB"
    assert ru.playtime(3725) == "1:02"
    assert ru.playtime(None) == "—"
    assert ru.level(None) == "—"
    assert ru.date(None) == "—"
    assert ru.date(0) == "—"
    assert ru.date(1_800_000_000).count(".") == 2
    assert ru.date(1_800_000_000) != en.date(1_800_000_000)


def test_all_languages_load():
    for lang in LANGUAGES:
        assert I18n(lang).t("action.convert")


def test_assets_are_next_to_the_code():
    assert asset("i18n", "ru.json").is_file()
    assert asset("fonts", "Oswald-SemiBold.ttf").is_file()


def test_templates_do_not_crash():
    tr = I18n("ru")
    assert "{count}" in tr.t("source.found", count=5) or "5" in tr.t("source.found", count=5)
    assert tr.t("table.selected", count=1, total=2)
    # a missing placeholder must not raise
    assert tr.t("table.selected", count=1)
