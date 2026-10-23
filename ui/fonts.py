"""Font objects, created once per (family, pixel size).

Sizes are negative Tk sizes = device pixels, so the layout does not drift
between Tk builds or DPI settings.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont

from . import theme


def font(root: tk.Misc, family: str, pixels: float) -> tkfont.Font:
    """Font objects live in the Tk interpreter, so the cache does too."""
    size = -int(round(pixels))
    toplevel = root.winfo_toplevel()
    cache = getattr(toplevel, "_p3r_font_cache", None)
    if cache is None:
        cache = {}
        toplevel._p3r_font_cache = cache
    key = (family, size)
    cached = cache.get(key)
    if cached is not None:
        return cached
    created = tkfont.Font(root=root, family=family, size=size)
    cache[key] = created
    return created


def display(root: tk.Misc, pixels: float) -> tkfont.Font:
    return font(root, theme.FONT_DISPLAY, pixels)


def text(root: tk.Misc, pixels: float) -> tkfont.Font:
    return font(root, theme.FONT_TEXT, pixels)


def medium(root: tk.Misc, pixels: float) -> tkfont.Font:
    return font(root, theme.FONT_MEDIUM, pixels)


def semi(root: tk.Misc, pixels: float) -> tkfont.Font:
    return font(root, theme.FONT_SEMI, pixels)


def clear_cache(root: tk.Misc | None = None) -> None:
    """Drop cached fonts (only needed when an interpreter survives a rebuild)."""
    if root is None:
        return
    cache = getattr(root.winfo_toplevel(), "_p3r_font_cache", None)
    if cache is not None:
        cache.clear()
