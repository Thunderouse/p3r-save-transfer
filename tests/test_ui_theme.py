"""Design tokens and the contrast floor the brief asks for."""

from __future__ import annotations

import pytest
from ui import theme


def test_tokens_match_the_brief():
    assert theme.TOKENS == {
        "--p3-bg": "#05070e",
        "--p3-panel": "#0b1226",
        "--p3-blue": "#1f6bff",
        "--p3-cyan": "#37e0ff",
        "--p3-pink": "#ff2f6d",
        "--p3-white": "#f2f5ff",
        "--p3-dim": "#8b93ad",
    }


@pytest.mark.parametrize("surface", [theme.BG, theme.PANEL, theme.PANEL_HI])
def test_secondary_text_is_readable(surface: str):
    assert theme.contrast_ratio(theme.DIM, surface) >= 4.5


@pytest.mark.parametrize("surface", [theme.BG, theme.PANEL, theme.PANEL_HI])
def test_primary_text_is_readable(surface: str):
    assert theme.contrast_ratio(theme.WHITE, surface) >= 7.0


def test_error_colour_is_readable_on_panel():
    assert theme.contrast_ratio(theme.PINK, theme.PANEL) >= 3.0


def test_accent_colours_are_visible():
    assert theme.contrast_ratio(theme.CYAN, theme.PANEL) >= 4.5
    assert theme.contrast_ratio(theme.BLUE, theme.BG) >= 2.5


def test_blend_is_between_the_two_colours():
    mixed = theme.blend(theme.WHITE, theme.BG, 0.5)
    assert theme.rgb(mixed) == pytest.approx((123, 126, 134), abs=2)


def test_mix_endpoints():
    assert theme.mix(theme.BLUE, theme.CYAN, 0.0) == theme.BLUE
    assert theme.mix(theme.BLUE, theme.CYAN, 1.0) == theme.CYAN


def test_metrics_scale_geometry_and_fonts():
    m = theme.Metrics(1.5)
    assert m.px(100) == 150
    assert m.display(18) == -27
    assert m.window == (1500, 1020)
    assert m.minimum == (1290, 900)


def test_ui_scale_env_override(monkeypatch):
    monkeypatch.setenv("P3R_UI_SCALE", "1.25")
    assert theme.ui_scale() == 1.25
    monkeypatch.setenv("P3R_UI_SCALE", "nonsense")
    assert theme.ui_scale() == 1.0
    monkeypatch.setenv("P3R_UI_SCALE", "99")
    assert theme.ui_scale() == 3.0
