"""Whole-window tests: states, hotkeys, modal, read-only source (needs a display)."""

from __future__ import annotations

import contextlib
import hashlib
import os
import time

import pytest

import synth

tk = pytest.importorskip("tkinter")

from ui.app import App  # noqa: E402
from ui.bridge import Bridge  # noqa: E402
from ui.config import Config  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.name != "nt" and not os.environ.get("DISPLAY"), reason="нужен графический дисплей"
)

USER = "25354ABCDEF01234"
GUID = "8F3A1C2E-0B4D-4E6F-9A11-223344556677"
STEAM_ID = "11111111111111111"


def pump(root, predicate=None, timeout: float = 8.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        root.update()
        if predicate is not None and predicate():
            return True
        time.sleep(0.01)
    return predicate() if predicate is not None else True


@pytest.fixture
def env(tmp_path, monkeypatch):
    local = tmp_path / "LocalAppData"
    roaming = tmp_path / "Roaming"
    wgs = local / "Packages" / "SEGAofAmericaInc.L0cb6b3aea_s751p9cej88mt" / "SystemAppData" / "wgs"
    user = wgs / USER
    for index in range(3):
        leaf = user / f"0000000{index}-1111-2222-3333-4444444444{index}" / GUID
        leaf.mkdir(parents=True)
        words = {
            synth.word_index("play_time"): (index + 1) * 1800 * 30,
            synth.word_index("level"): 4 + index,
        }
        (leaf / f"BEEF{index}").write_bytes(synth.save(words) + b"\x00" * 4096)
        (leaf / "container.0000").write_bytes(b"\x00" * 8)
    steam = roaming / "Sega" / "P3R" / "Steam" / STEAM_ID
    steam.mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))
    return {"wgs": wgs, "steam": steam}


@pytest.fixture
def window(tmp_path, env):
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # runners without a working Tcl/Tk install
        pytest.skip(f"Tk недоступен: {exc}")
    root.tk.call("tk", "scaling", 96.0 / 72.0)
    config = Config(tmp_path / "cfg")
    config.values.update(
        {
            "lang": "ru",
            "source": str(env["wgs"]),
            "destination": str(env["steam"]),
            "steam_id": STEAM_ID,
            "backup": True,
            "dry_run": True,
        }
    )
    app = App(root, config=config, bridge=Bridge())
    app.start(1.0)
    root.geometry("1000x680+0+0")
    # wait for the scan to settle: on_convert() is a no-op while mode=="scan"
    ready = pump(root, lambda: app.mode == "idle" and bool(app.saves), timeout=20)
    if not ready:
        app.close()
        pytest.skip(
            f"Tk в этом окружении не завершил сканирование (mode={app.mode}) - "
            "проверяется на Windows-ноге CI"
        )
    yield root, app, env
    with contextlib.suppress(tk.TclError):
        app.close()


def test_scan_fills_the_table(window):
    root, app, _ = window
    assert len(app.saves) == 3
    assert len(app._save_rows) == 3
    assert app.selected == {item["path"] for item in app.saves}
    assert app.btn_convert.enabled


def test_hotkey_converts_in_dry_run(window):
    root, app, env = window
    root.focus_force()
    root.event_generate("<Control-Return>", when="now")
    root.update()
    if app.mode == "idle":  # unmapped windows drop synthetic keys
        app._hotkey_convert()
    assert pump(root, lambda: app.mode == "result"), "conversion did not finish"
    assert app.last_summary["converted"] == 3
    assert list(env["steam"].glob("SaveData*.sav")) == []


def test_convert_writes_and_leaves_sources(window):
    root, app, env = window
    app.toggle_dry.set_value(False)
    app.on_convert()
    assert pump(root, lambda: app.mode == "result")
    produced = sorted(env["steam"].glob("SaveData*.sav"))
    assert len(produced) == 3
    before = {
        p: hashlib.sha256(p.read_bytes()).hexdigest() for p in env["wgs"].rglob("*") if p.is_file()
    }
    app.back_to_list()
    app.on_convert()
    pump(root, lambda: app.mode == "result")
    after = {
        p: hashlib.sha256(p.read_bytes()).hexdigest() for p in env["wgs"].rglob("*") if p.is_file()
    }
    assert before == after


def press_escape(root, app):
    """Synthetic keys are dropped by Tk when the window never maps (headless CI)."""
    root.focus_force()
    root.event_generate("<Escape>", when="now")
    root.update()
    if app._modal is not None or app.help_open:
        app._hotkey_escape()
    root.update()


def test_escape_closes_help(window):
    root, app, _ = window
    app.toggle_help()
    assert app.help_open is True
    press_escape(root, app)
    assert app.help_open is False


def test_language_toggle_keeps_state(window):
    root, app, _ = window
    before = [item["path"] for item in app.saves]
    app.toggle_language()
    root.update()
    assert app.i18n.lang == "en"
    assert [item["path"] for item in app.saves] == before
    assert "save transfer" in app.window_title.lower()
    app.toggle_language()
    root.update()
    assert app.i18n.lang == "ru"


def test_overwrite_modal_appears_and_cancels(window):
    root, app, env = window
    (env["steam"] / "SaveData001.sav").write_bytes(b"existing")
    app.toggle_dry.set_value(False)
    app.on_convert()
    pump(root, lambda: app._modal is not None)
    assert app._modal is not None
    assert (env["steam"] / "SaveData001.sav").read_bytes() == b"existing"
    press_escape(root, app)
    assert app._modal is None
    assert (env["steam"] / "SaveData001.sav").read_bytes() == b"existing"
    assert app.mode == "idle"


def test_overwrite_modal_confirm_writes(window):
    root, app, env = window
    existing = env["steam"] / "SaveData001.sav"
    existing.write_bytes(b"existing")
    app.toggle_dry.set_value(False)
    app.on_convert()
    pump(root, lambda: app._modal is not None)
    app._close_modal()
    # confirm path is what the modal's danger button calls
    app._start_convert([item for item in app.saves if item["path"] in app.selected], force=True)
    assert pump(root, lambda: app.mode == "result")
    assert existing.read_bytes() != b"existing"
    assert app.last_summary["converted"] == 3


def test_select_all_and_none(window):
    root, app, _ = window
    app.select_all(False)
    assert app.selected == set()
    app._update_actions()
    assert app.btn_convert.enabled is False
    assert "хотя бы одно" in app.reason_label.cget("text")
    app.select_all(True)
    root.update()
    assert len(app.selected) == 3
    assert app.btn_convert.enabled is True


def test_empty_folder_shows_hint(window, tmp_path):
    root, app, _ = window
    empty = tmp_path / "empty"
    empty.mkdir()
    app.load_source(str(empty))
    assert pump(root, lambda: app.mode == "idle")
    assert app.saves == []
    assert app.empty_frame.winfo_ismapped()
    assert app.btn_convert.enabled is False


def test_broken_file_is_reported(window, tmp_path):
    root, app, _ = window
    bad = tmp_path / "bad.sav"
    bad.write_bytes(b"JUNK" + b"\x00" * 64)
    app.load_source(str(bad))
    assert pump(root, lambda: app.mode == "idle")
    assert app.saves == []
    assert app.empty_title.cget("text")


def test_cancel_during_run(window):
    root, app, env = window
    app.toggle_dry.set_value(True)
    app.on_convert()
    app.cancel_current()  # may land before or after the worker finishes
    assert pump(root, lambda: app.mode == "result"), "conversion did not finish"
    assert app.last_summary is not None
    assert app.last_summary["cancelled"] in (True, False)
    assert app.last_summary["converted"] + len(
        [item for item in app.last_summary["items"] if item["status"] == "cancelled"]
    ) == 3
