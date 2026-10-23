"""Where the app finds its files, frozen or from the source tree."""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """Folder next to the exe (portable mode) or the repository root."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def asset_dir() -> Path:
    """Bundled read-only assets (fonts, i18n, icon)."""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled) / "assets"
    return app_dir() / "assets"


def asset(*parts: str) -> Path:
    return asset_dir().joinpath(*parts)
