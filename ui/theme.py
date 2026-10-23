"""Design tokens and small drawing helpers.

Every colour lives here; widgets and the app only reference these names.
Tk 8.6 has no alpha in canvas colours, so translucency is pre-blended against
the surface it sits on instead of being passed as ``#RRGGBBAA``.
"""

from __future__ import annotations

import os

# --- tokens -----------------------------------------------------------------

BG = "#05070e"
BG_DEEP = "#03050a"
PANEL = "#0b1226"
PANEL_HI = "#101a33"
BLUE = "#1f6bff"
CYAN = "#37e0ff"
PINK = "#ff2f6d"
WHITE = "#f2f5ff"
DIM = "#8b93ad"

TOKENS = {
    "--p3-bg": BG,
    "--p3-panel": PANEL,
    "--p3-blue": BLUE,
    "--p3-cyan": CYAN,
    "--p3-pink": PINK,
    "--p3-white": WHITE,
    "--p3-dim": DIM,
}

# Fonts are renamed static instances of Oswald (SIL OFL) and Inter (SIL OFL),
# both with full Cyrillic coverage. Sizes are device pixels, so they behave the
# same on every DPI once multiplied by the scale factor.
FONT_DISPLAY = "P3R Display"
FONT_TEXT = "P3R Text"
FONT_MEDIUM = "P3R Text Medium"
FONT_SEMI = "P3R Text Semi"

# --- geometry ---------------------------------------------------------------

_WIN_W, _WIN_H = 1000, 680
_MIN_W, _MIN_H = 860, 600


def ui_scale() -> float:
    """Device pixel ratio. ``P3R_UI_SCALE`` exists for reproducible screenshots."""
    override = os.environ.get("P3R_UI_SCALE")
    if override:
        try:
            return max(0.75, min(3.0, float(override)))
        except ValueError:
            pass
    return 1.0


class Metrics:
    """Pixel metrics for one scale factor."""

    def __init__(self, scale: float) -> None:
        self.scale = scale

    def px(self, value: float) -> int:
        return int(round(value * self.scale))

    @property
    def window(self) -> tuple[int, int]:
        return self.px(_WIN_W), self.px(_WIN_H)

    @property
    def minimum(self) -> tuple[int, int]:
        return self.px(_MIN_W), self.px(_MIN_H)

    # font sizes, in device pixels
    def display(self, size: float) -> int:
        return -self.px(size)

    def text(self, size: float) -> int:
        return -self.px(size)


# --- colour helpers ---------------------------------------------------------


def rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def hexof(value: tuple[float, float, float]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*tuple(max(0, min(255, int(round(c)))) for c in value))


def blend(fg: str, bg: str, alpha: float) -> str:
    """``alpha`` of *fg* over *bg*; the result is opaque."""
    fr, fg_, fb = rgb(fg)
    br, bg_, bb = rgb(bg)
    return hexof(
        (
            fr * alpha + br * (1 - alpha),
            fg_ * alpha + bg_ * (1 - alpha),
            fb * alpha + bb * (1 - alpha),
        )
    )


def mix(a: str, b: str, t: float) -> str:
    return blend(b, a, t)


def lift(color: str, amount: float = 0.08) -> str:
    return blend(WHITE, color, amount)


def shade(color: str, amount: float = 0.12) -> str:
    return blend("#000000", color, amount)


def contrast_ratio(fg: str, bg: str) -> float:
    def luminance(color: str) -> float:
        channels = []
        for value in rgb(color):
            srgb = value / 255
            channels.append(srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4)
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    a, b = luminance(fg), luminance(bg)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)
