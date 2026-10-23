"""Widget drawing helpers and keyboard behaviour (needs a display)."""

from __future__ import annotations

import os

import pytest

tk = pytest.importorskip("tkinter")

from ui import fonts, theme, widgets  # noqa: E402
from ui.theme import Metrics  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.name != "nt" and not os.environ.get("DISPLAY"), reason="нужен графический дисплей"
)


@pytest.fixture
def root():
    try:
        window = tk.Tk()
    except tk.TclError as exc:  # runners without a working Tcl/Tk install
        pytest.skip(f"Tk недоступен: {exc}")
    window.geometry("800x600")
    yield window
    window.destroy()


def test_panel_points_are_closed_and_chamfered():
    points = widgets.panel_points(0, 0, 100, 40, 10)
    assert len(points) == 12
    assert (90, 0) in list(zip(points[0::2], points[1::2], strict=False))
    assert (100, 10) in list(zip(points[0::2], points[1::2], strict=False))
    assert (10, 40) in list(zip(points[0::2], points[1::2], strict=False))


def test_text_measurement_and_elide(root):
    fnt = fonts.text(root, 13)
    assert widgets.text_width(fnt, "Привет", 1.0) > 0
    assert widgets.text_width(fnt, "", 1.0) == 0
    short = widgets.elide(fnt, "X" * 200, 40)
    assert short.endswith("…") and fnt.measure(short) <= 60


def test_button_keyboard_activation(root):
    calls = []
    button = widgets.ShardButton(root, "OK", lambda: calls.append(1), m=Metrics(1.0))
    button.pack()
    root.update()
    button.focus_force()
    root.update()
    button.event_generate("<Return>", when="now")
    root.update()
    assert calls == [1]

    button.set_enabled(False)
    button.event_generate("<Return>", when="now")
    root.update()
    assert calls == [1]

    button.set_enabled(True)
    button.event_generate("<space>", when="now")
    root.update()
    assert calls == [1, 1]


def test_button_width_follows_text(root):
    button = widgets.ShardButton(root, "A", None, m=Metrics(1.0), min_width=100)
    assert button._width() == 100
    button.set_text("A QUITE LONG BUTTON LABEL")
    assert button._width() > 100


def test_toggle_mouse_and_keyboard(root):
    values = []
    toggle = widgets.Toggle(root, "Бэкап", True, values.append, m=Metrics(1.0))
    toggle.pack()
    root.update()
    assert toggle.value is True
    toggle.event_generate("<Button-1>", x=5, y=5, when="now")
    root.update()
    assert toggle.value is False and values == [False]
    toggle.focus_force()
    toggle.event_generate("<space>", when="now")
    root.update()
    assert toggle.value is True and values == [False, True]


def test_checkbox_toggle(root):
    seen = []
    check = widgets.Check(root, True, seen.append, m=Metrics(1.0))
    check.pack()
    root.update()
    check.event_generate("<Button-1>", x=5, y=5, when="now")
    root.update()
    assert check.value is False and seen == [False]


def test_badge_resizes_with_text(root):
    badge = widgets.Badge(root, "GVAS", m=Metrics(1.0))
    first = badge.winfo_reqwidth()
    badge.set("STEAM!", theme.BLUE)
    assert badge.winfo_reqwidth() != first


def test_progress_bar_clamps(root):
    bar = widgets.ProgressBar(root, m=Metrics(1.0))
    bar.pack(fill="x")
    bar.set(-1)
    bar.set(2)
    root.update()
    assert bar._value == 1.0


def test_scroll_area_scrolls(root):
    area = widgets.ScrollArea(root, m=Metrics(1.0))
    area.pack(fill="both", expand=True)
    for index in range(40):
        tk.Label(area.inner, text=f"row {index}").pack()
    root.update()
    area.refresh()
    area._scroll(3)
    root.update()
    assert area.canvas.yview()[0] > 0
