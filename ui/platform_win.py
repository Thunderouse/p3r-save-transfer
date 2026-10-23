"""Windows-only glue, kept in one place and guarded so nothing here can crash the app.

* DPI awareness before the first window exists;
* private font loading (the bundled TTFs are not installed system-wide);
* “animations allowed?” from the Windows accessibility setting;
* drag & drop of files/folders through ``WM_DROPFILES``.

Every function degrades to a safe default off Windows, which is also what the
Linux CI uses.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import os
import sys
from collections.abc import Callable, Iterable
from ctypes import wintypes
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

IS_WINDOWS = os.name == "nt"

# ctypes.windll/WINFUNCTYPE only exist on Windows; keep the accessor typed as Any
# so static checkers do not reject the module when analysed on another platform.
_windll: Any = getattr(ctypes, "windll", None)
_WINFUNCTYPE: Any = getattr(ctypes, "WINFUNCTYPE", None)

# --- DPI ---------------------------------------------------------------------


def enable_dpi_awareness() -> float:
    """Become system-DPI aware and return the scale factor (1.0 = 96 dpi)."""
    if not IS_WINDOWS:
        return 1.0
    try:
        # PROCESS_SYSTEM_DPI_AWARE = 1
        _windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001 - Windows 7 / older shcore
        try:
            _windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            log.debug("DPI awareness недоступна", exc_info=True)
    try:
        dpi = int(_windll.user32.GetDpiForSystem())
    except Exception:  # noqa: BLE001
        try:
            hdc = _windll.user32.GetDC(0)
            dpi = int(_windll.gdi32.GetDeviceCaps(hdc, 88))  # LOGPIXELSX
            _windll.user32.ReleaseDC(0, hdc)
        except Exception:  # noqa: BLE001
            return 1.0
    return max(1.0, min(3.0, dpi / 96.0))


# --- fonts -------------------------------------------------------------------

FR_PRIVATE = 0x10
WM_FONTCHANGE = 0x001D
HWND_BROADCAST = 0xFFFF


def load_private_fonts(paths: Iterable[Path]) -> list[str]:
    """Register TTFs for this process only; returns the ones that loaded."""
    loaded: list[str] = []
    for path in paths:
        if not Path(path).is_file():
            continue
        if not IS_WINDOWS:
            loaded.append(str(path))  # on Linux fontconfig already has them
            continue
        try:
            added = _windll.gdi32.AddFontResourceExW(str(path), FR_PRIVATE, 0)
        except Exception:  # noqa: BLE001
            log.debug("Шрифт не загружен: %s", path, exc_info=True)
            continue
        if added:
            loaded.append(str(path))
        else:
            log.warning("AddFontResourceExW отверг шрифт: %s", path)
    if IS_WINDOWS and loaded:
        with contextlib.suppress(Exception):  # broadcast is best effort
            _windll.user32.SendMessageW(HWND_BROADCAST, WM_FONTCHANGE, 0, 0)
    return loaded


# --- accessibility -----------------------------------------------------------

SPI_GETCLIENTAREAANIMATION = 0x1042


def animations_allowed() -> bool:
    """Respect “Show animations in Windows” (and ``P3R_NO_ANIMATION``)."""
    if os.environ.get("P3R_NO_ANIMATION"):
        return False
    if not IS_WINDOWS:
        return True
    try:
        enabled = wintypes.BOOL(True)
        ok = _windll.user32.SystemParametersInfoW(
            SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0
        )
        return bool(enabled.value) if ok else True
    except Exception:  # noqa: BLE001
        return True


# --- drag & drop -------------------------------------------------------------

WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4
GA_ROOT = 2

_WNDPROC = (
    _WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    if _WINFUNCTYPE is not None
    else None
)


def split_drop_paths(raw: str) -> list[str]:
    """``WM_DROPFILES`` hands back NUL-separated paths in one buffer."""
    return [chunk for chunk in raw.split("\x00") if chunk]


class DropTarget:
    """Accept files/folders dropped on the toplevel window.

    Implemented with ``DragAcceptFiles`` + a window-proc hook.  If anything
    about the hook fails the method returns ``False`` and the UI keeps working
    with the folder picker and the hotkey.
    """

    def __init__(self) -> None:
        self._hwnd: int | None = None
        self._old_proc: int | None = None
        self._callback = None
        self._on_drop: Callable[[list[str]], None] | None = None

    def attach(self, tk_window, on_drop: Callable[[list[str]], None]) -> bool:
        if not IS_WINDOWS or _WNDPROC is None:
            return False
        try:
            user32 = _windll.user32
            shell32 = _windll.shell32
            hwnd = user32.GetAncestor(tk_window.winfo_id(), GA_ROOT) or tk_window.winfo_id()
            shell32.DragAcceptFiles(wintypes.HWND(hwnd), True)

            self._on_drop = on_drop
            self._hwnd = hwnd
            self._callback = _WNDPROC(self._handler)

            setter = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
            setter.restype = ctypes.c_ssize_t
            setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            self._old_proc = setter(
                hwnd, GWLP_WNDPROC, ctypes.cast(self._callback, ctypes.c_void_p).value
            )
            if not self._old_proc:
                log.warning("Не удалось подменить оконную процедуру для drag&drop")
                return False
            log.info("drag&drop включён (hwnd=%s)", hwnd)
            return True
        except Exception:  # noqa: BLE001
            log.warning("drag&drop недоступен", exc_info=True)
            self._hwnd = None
            return False

    def _handler(self, hwnd, msg, wparam, lparam):  # noqa: ANN001 - Win32 signature
        if msg == WM_DROPFILES:
            try:
                paths = self._read_paths(wparam)
                if paths and self._on_drop is not None:
                    self._on_drop(paths)
            except Exception:  # noqa: BLE001 - never break the message loop
                log.exception("Ошибка обработки перетаскивания")
            finally:
                _windll.shell32.DragFinish(wintypes.HANDLE(wparam))
            return 0
        proc = _windll.user32.CallWindowProcW
        proc.restype = ctypes.c_ssize_t
        proc.argtypes = [
            ctypes.c_void_p,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        return proc(self._old_proc, hwnd, msg, wparam, lparam)

    @staticmethod
    def _read_paths(hdrop) -> list[str]:
        shell32 = _windll.shell32
        count = shell32.DragQueryFileW(wintypes.HANDLE(hdrop), 0xFFFFFFFF, None, 0)
        paths: list[str] = []
        for index in range(count):
            length = shell32.DragQueryFileW(wintypes.HANDLE(hdrop), index, None, 0)
            buffer = ctypes.create_unicode_buffer(length + 1)
            shell32.DragQueryFileW(wintypes.HANDLE(hdrop), index, buffer, length + 1)
            if buffer.value:
                paths.append(buffer.value)
        return paths

    def detach(self) -> None:
        if not IS_WINDOWS or self._hwnd is None or self._old_proc is None:
            return
        try:
            user32 = _windll.user32
            setter = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
            setter.restype = ctypes.c_ssize_t
            setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            setter(self._hwnd, GWLP_WNDPROC, self._old_proc)
            _windll.shell32.DragAcceptFiles(wintypes.HWND(self._hwnd), False)
        except Exception:  # noqa: BLE001
            log.debug("Не удалось снять hook drag&drop", exc_info=True)
        self._hwnd = None
        self._old_proc = None
        self._callback = None


def set_dark_titlebar(tk_window) -> None:
    """Ask DWM for a dark title bar (Windows 10 1809+ / Windows 11)."""
    if not IS_WINDOWS:
        return
    try:
        hwnd = _windll.user32.GetAncestor(tk_window.winfo_id(), GA_ROOT)
        value = ctypes.c_int(1)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE, pre-20H1
            result = _windll.dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd),
                ctypes.c_int(attribute),
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
            if result == 0:
                break
    except Exception:  # noqa: BLE001
        log.debug("Тёмный заголовок окна недоступен", exc_info=True)


def is_windows() -> bool:
    return sys.platform.startswith("win")
