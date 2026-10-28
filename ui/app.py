"""The single window: source, list, destination, run view, help, log.

Layout is placed explicitly (not grid) so the shard geometry stays exact at any
window size; a <Configure> handler re-places the blocks.  All long operations
run on a worker thread and talk to the window through a queue, so the UI never
freezes and every state is reachable with the keyboard alone.
"""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import random
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from p3r_transfer import __version__

from . import fonts, platform_win, resources, theme, widgets
from . import i18n as i18n_mod
from .bridge import Bridge
from .config import Config
from .theme import Metrics

log = logging.getLogger("p3r_transfer.ui")

DASH = "—"


class _UiLogHandler(logging.Handler):
    """Feed the in-window log panel; windowed builds have no stderr."""

    def __init__(self, sink: queue.Queue) -> None:
        super().__init__()
        self._sink = sink
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            if record.levelno >= logging.WARNING:
                message = f"! {message}"
            self._sink.put(("log", message))
        except Exception:  # noqa: BLE001 - logging must never break the UI
            pass


def _read_theme_color(widget: tk.Misc, option: str, fallback: str) -> str:
    try:
        return str(widget.cget(option))
    except tk.TclError:
        return fallback


class DropZone(tk.Canvas):
    """Large dashed target: click, Enter/Space or a real drop all work."""

    def __init__(self, master: tk.Misc, app: App, *, m: Metrics) -> None:
        self.m = m
        self.app = app
        super().__init__(
            master, bg=theme.PANEL, highlightthickness=0, bd=0, takefocus=1, cursor="hand2"
        )
        self._title_font = fonts.display(self, m.px(15))
        self._hint_font = fonts.font(self, theme.FONT_TEXT, m.px(11))
        self._title = ""
        self._hint = ""
        self._sub = ""
        self._focus = False
        self._hover = False
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Button-1>", lambda _e: self.focus_set() or self.app.choose_folder())
        self.bind("<Return>", lambda _e: self.app.choose_folder())
        self.bind("<space>", lambda _e: self.app.choose_folder())
        self.bind("<FocusIn>", self._focus_in)
        self.bind("<FocusOut>", self._focus_out)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.active = False

    def set_texts(self, title: str, hint: str, sub: str) -> None:
        self._title, self._hint, self._sub = title, hint, sub
        self._draw()

    def _focus_in(self, _e=None) -> None:
        self._focus = True
        self._draw()

    def _focus_out(self, _e=None) -> None:
        self._focus = False
        self._draw()

    def _enter(self, _e=None) -> None:
        self._hover = True
        self._draw()

    def _leave(self, _e=None) -> None:
        self._hover = False
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 2 or height <= 2:
            return
        active = self._hover or self._focus or self.active
        border = theme.CYAN if active else theme.blend(theme.WHITE, theme.PANEL, 0.18)
        points = widgets.panel_points(1, 1, width - 1, height - 1, self.m.px(14))
        self.create_polygon(
            points, fill=theme.PANEL_HI if active else theme.PANEL, outline=border, width=1
        )
        if self._focus:
            self.create_polygon(
                widgets.panel_points(4, 4, width - 4, height - 4, self.m.px(12)),
                fill="",
                outline=theme.CYAN,
                width=1,
            )
        cx = width / 2
        roomy = height >= self.m.px(96)
        title_y = height / 2 - self.m.px(14 if roomy else 8)
        widgets.tracked_text(
            self,
            cx,
            title_y,
            self._title.upper(),
            self._title_font,
            theme.WHITE,
            self.m.px(1.2),
            "center",
        )
        if roomy:
            self.create_text(
                cx, title_y + self.m.px(19), text=self._hint, font=self._hint_font, fill=theme.DIM
            )
            sub_y, sub_anchor = height - self.m.px(9), "s"
        else:
            sub_y, sub_anchor = title_y + self.m.px(19), "n"
        if self._sub:
            self.create_text(
                cx,
                sub_y,
                text=widgets.elide(self._hint_font, self._sub, width - self.m.px(30)),
                font=self._hint_font,
                fill=theme.CYAN if self.active else theme.DIM,
                anchor=sub_anchor,
            )


class SaveRow(tk.Frame):
    """One selectable line of the table."""

    def __init__(self, master: tk.Misc, app: App, item: dict, *, m: Metrics) -> None:
        super().__init__(master, bg=theme.PANEL, height=m.px(34))
        self.app = app
        self.item = item
        self.m = m
        self._bg = theme.PANEL
        self.grid_propagate(False)
        self.pack_propagate(False)

        self.check = widgets.Check(
            self, app.is_selected(item["path"]), lambda _v, p=item["path"]: app.toggle_item(p), m=m
        )
        self.check.place(x=m.px(6), y=m.px(8))

        font_slot = fonts.display(self, m.px(16))
        font_cell = fonts.font(self, theme.FONT_TEXT, m.px(13))
        font_small = fonts.font(self, theme.FONT_TEXT, m.px(12))

        self.slot = tk.Label(
            self,
            text=f"{item['slot']:02d}" if item.get("slot") else DASH,
            font=font_slot,
            fg=theme.WHITE,
            bg=theme.PANEL,
            bd=0,
        )
        self.slot.place(x=m.px(34), y=m.px(6))

        self.date = tk.Label(
            self,
            text=app.fmt_date(item.get("mtime")),
            font=font_small,
            fg=theme.DIM,
            bg=theme.PANEL,
            bd=0,
            anchor="w",
        )
        self.date.place(x=m.px(86), y=m.px(9), width=m.px(150))

        self.level = tk.Label(
            self,
            text=app.fmt_level(item.get("level")),
            font=font_cell,
            fg=theme.WHITE,
            bg=theme.PANEL,
            bd=0,
            anchor="w",
        )
        self.level.place(x=m.px(246), y=m.px(8), width=m.px(46))

        self.time = tk.Label(
            self,
            text=app.fmt_playtime(item.get("play_time")),
            font=font_cell,
            fg=theme.WHITE,
            bg=theme.PANEL,
            bd=0,
            anchor="w",
        )
        self.time.place(x=m.px(300), y=m.px(8), width=m.px(90))

        self.size = tk.Label(
            self,
            text=app.fmt_size(item.get("size") or 0),
            font=font_cell,
            fg=theme.DIM,
            bg=theme.PANEL,
            bd=0,
            anchor="w",
        )
        self.size.place(x=m.px(398), y=m.px(8), width=m.px(96))

        kind = item.get("kind")
        label, color = app.badge_for(kind)
        self.badge = widgets.Badge(self, label, m=m, color=color)

        for child in (self, self.slot, self.date, self.level, self.time, self.size):
            child.bind("<Button-1>", self._click)
            child.bind("<Enter>", self._enter)
            child.bind("<Leave>", self._leave)

    def _click(self, _event=None):
        self.app.toggle_item(self.item["path"])
        return "break"

    def _enter(self, _event=None) -> None:
        self.set_bg(theme.PANEL_HI)

    def _leave(self, _event=None) -> None:
        self.set_bg(theme.PANEL)

    def set_bg(self, color: str) -> None:
        self._bg = color
        self.configure(bg=color)
        for child in (self.slot, self.date, self.level, self.time, self.size):
            child.configure(bg=color)
        self.check.set_bg(color)
        self.badge.set_bg(color)

    def place_badge(self, width: int) -> None:
        self.badge.place(x=width - self.badge.winfo_reqwidth() - self.m.px(12), y=self.m.px(7))

    def sync_check(self) -> None:
        self.check.set_value(self.app.is_selected(self.item["path"]))


class RunRow(tk.Frame):
    """One line of the “file → slot → status” view."""

    def __init__(self, master: tk.Misc, app: App, item: dict, *, m: Metrics) -> None:
        super().__init__(master, bg=theme.PANEL, height=m.px(34))
        self.m = m
        self.item = item
        self.pack_propagate(False)
        self._font = fonts.font(self, theme.FONT_TEXT, m.px(12))
        self._font_semi = fonts.font(self, theme.FONT_SEMI, m.px(11))
        self.name = tk.Label(
            self,
            text=widgets.elide(self._font, Path(item["path"]).name, m.px(300)),
            font=self._font,
            fg=theme.WHITE,
            bg=theme.PANEL,
            bd=0,
            anchor="w",
        )
        self.name.place(x=m.px(6), y=m.px(8), width=m.px(310))
        self.target = tk.Label(
            self, text="", font=self._font, fg=theme.DIM, bg=theme.PANEL, bd=0, anchor="w"
        )
        self.target.place(x=m.px(320), y=m.px(8), width=m.px(220))
        self.status = tk.Label(
            self, text="", font=self._font_semi, fg=theme.DIM, bg=theme.PANEL, bd=0, anchor="e"
        )
        self.status.place(relx=1.0, x=-m.px(10), y=m.px(9), width=m.px(160), anchor="ne")

    def update_state(
        self, status: str, target: str | None, error: str | None, texts: dict, m: Metrics
    ) -> None:
        color = {
            "ok": theme.CYAN,
            "error": theme.PINK,
            "running": theme.WHITE,
            "cancelled": theme.DIM,
            "pending": theme.DIM,
        }.get(status, theme.DIM)
        label = texts.get(status, status)
        self.status.configure(text=label.upper() if status in {"ok", "error"} else label, fg=color)
        if target:
            self.target.place_configure(width=self.m.px(220))
            self.target.configure(
                text=widgets.elide(self._font, Path(target).name, self.m.px(210)), fg=theme.DIM
            )
        if error:
            self.status.configure(text=texts["error"].upper(), fg=theme.PINK)
            self.name.configure(fg=theme.PINK)
            self.target.place_configure(width=self.m.px(320))
            reason = error.split(": ", 1)[0] if ": " in error else error
            self.target.configure(
                text=widgets.elide(self._font, reason, self.m.px(316)),
                fg=theme.blend(theme.PINK, theme.PANEL, 0.8),
            )


class App:
    def __init__(
        self, root: tk.Tk, *, config: Config | None = None, bridge: Bridge | None = None
    ) -> None:
        self.root = root
        self.scale = 1.0
        self.m = Metrics(1.0)
        self.cfg = config
        self.i18n = i18n_mod.I18n(str(self.cfg.get("lang")) if self.cfg else "ru")
        # bridge messages reach the panel through the logging handler below,
        # so nothing is logged twice
        self.bridge = bridge or Bridge()

        self.saves: list[dict] = []
        self.selected: set[str] = set()
        self.source_path: str = str(self.cfg.get("source") or "") if self.cfg else ""
        self.dest_path: str = str(self.cfg.get("destination") or "") if self.cfg else ""
        self.accounts: list[dict] = []
        self.steam_id = str(self.cfg.get("steam_id") or "") if self.cfg else ""
        self.mode = "idle"  # idle | scan | run | result
        self.run_items: list[dict] = []
        self.run_rows: dict[str, RunRow] = {}
        self.last_summary: dict | None = None
        self.skipped: list[dict] = []
        self.metadata_count = 0
        self._cancel = threading.Event()
        self._queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._logs: list[str] = []
        self.log_open = False
        self.help_open = False
        self._modal: tk.Toplevel | None = None
        self._dim: tk.Canvas | None = None
        self._tokens: list[tk.Widget] = []
        self._drop = platform_win.DropTarget()
        self._busy_hint = ""
        self._closing = False
        self._poll_job = None

    # ------------------------------------------------------------------ setup

    def start(self, scale: float) -> None:
        self.scale = scale
        self.m = Metrics(scale)
        self.root.title(self.window_title)
        width, height = self.m.window
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(*self.m.minimum)
        self.root.configure(bg=theme.BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._setup_ttk()
        self._build()
        self.root.update_idletasks()
        platform_win.set_dark_titlebar(self.root)
        self._apply_icon()
        self.root.after(150, self._attach_drop)
        self._poll_job = self.root.after(60, self._poll_queue)
        self._log_handler = _UiLogHandler(self._queue)
        logging.getLogger().addHandler(self._log_handler)
        logging.getLogger().setLevel(logging.INFO)
        self._bind_hotkeys()
        self.root.after_idle(self._startup_probe)
        # First scan runs now (the scan itself happens on a worker thread, so the
        # window is already painted).  A delayed timer here used to race with an
        # early click and wipe the freshly found list.
        if not self.source_path:
            self.auto_find()
        else:
            self.load_source(self.source_path)

    def _bind_hotkeys(self) -> None:
        self.root.bind_all("<Control-o>", self._hotkey_open)
        self.root.bind_all("<Control-O>", self._hotkey_open)
        self.root.bind_all("<F5>", self._hotkey_rescan)
        self.root.bind_all("<Control-Return>", self._hotkey_convert)
        self.root.bind_all("<Control-KP_Enter>", self._hotkey_convert)
        self.root.bind_all("<Escape>", self._hotkey_escape)

    def _hotkey_open(self, _event=None):
        self.choose_folder()
        return "break"

    def _hotkey_rescan(self, _event=None):
        if self.mode in {"scan", "run"}:
            return "break"
        if self.source_path:
            self.load_source(self.source_path)
        else:
            self.auto_find()
        return "break"

    def _hotkey_convert(self, _event=None):
        self.primary_action()
        return "break"

    def _hotkey_escape(self, _event=None):
        if self._modal is not None:
            self._close_modal()
        elif self.help_open:
            self.toggle_help()
        elif self.mode in {"scan", "run"}:
            self.cancel_current()
        return "break"

    def _startup_probe(self) -> None:
        """Write “milliseconds until the window is on screen” for benchmarks."""
        target = os.environ.get("P3R_STARTUP_FILE")
        if not target:
            return
        if not self.root.winfo_ismapped():
            self.root.after(5, self._startup_probe)
            return
        self.root.update_idletasks()
        elapsed = (time.perf_counter() - _PROCESS_START) * 1000
        with contextlib.suppress(OSError):
            Path(target).write_text(f"{elapsed:.0f}\n", encoding="utf-8")

    def _apply_icon(self) -> None:
        icon = resources.asset("icon.ico")
        if icon.is_file():
            try:
                self.root.iconbitmap(default=str(icon))
            except tk.TclError:
                log.debug("iconbitmap недоступен", exc_info=True)

    def _attach_drop(self) -> None:
        ok = self._drop.attach(self.root, self._on_drop)
        if not ok:
            log.debug("drag&drop не подключён")

    def _setup_ttk(self) -> None:
        style = ttk.Style(self.root)
        with contextlib.suppress(tk.TclError):
            style.theme_use("clam")
        border = theme.blend(theme.WHITE, theme.PANEL_HI, 0.18)
        style.configure(
            "P3.TCombobox",
            fieldbackground=theme.PANEL_HI,
            background=theme.PANEL_HI,
            foreground=theme.WHITE,
            arrowcolor=theme.CYAN,
            bordercolor=border,
            lightcolor=theme.PANEL_HI,
            darkcolor=theme.PANEL_HI,
            padding=self.m.px(4),
            font=fonts.font(self.root, theme.FONT_TEXT, self.m.px(12)),
        )
        style.map(
            "P3.TCombobox",
            fieldbackground=[("readonly", theme.PANEL_HI)],
            foreground=[("readonly", theme.WHITE)],
            selectbackground=[("readonly", theme.PANEL_HI)],
            selectforeground=[("readonly", theme.WHITE)],
        )
        for option, value in (
            ("*TCombobox*Listbox.background", theme.PANEL_HI),
            ("*TCombobox*Listbox.foreground", theme.WHITE),
            ("*TCombobox*Listbox.selectBackground", theme.BLUE),
            ("*TCombobox*Listbox.selectForeground", theme.WHITE),
        ):
            self.root.option_add(option, value)

    # ------------------------------------------------------------------ i18n

    def t(self, key: str, **fmt: object) -> str:
        return self.i18n.t(key, **fmt)

    @property
    def window_title(self) -> str:
        """Logical title; ``wm title`` reads back empty until the window maps."""
        return self.t("window.title")

    def fmt_date(self, value: float | None) -> str:
        return self.i18n.date(value)

    def fmt_level(self, value: int | None) -> str:
        return self.i18n.level(value)

    def fmt_playtime(self, value: int | None) -> str:
        return self.i18n.playtime(value)

    def fmt_size(self, value: int) -> str:
        return self.i18n.size(value)

    def badge_for(self, kind: str | None) -> tuple[str, str]:
        if kind == "steam":
            return "STEAM", theme.BLUE
        if kind == "gvas":
            return "GVAS", theme.CYAN
        return "?", theme.PINK

    # ------------------------------------------------------------------ build

    def _build(self) -> None:
        if getattr(self, "container", None) is not None:
            self.container.destroy()
        self._tokens = []
        self.container = tk.Frame(self.root, bg=theme.BG)
        self.container.place(x=0, y=0, relwidth=1, relheight=1)
        self.bg = tk.Canvas(self.container, bg=theme.BG, highlightthickness=0, bd=0)
        self.bg.place(x=0, y=0, relwidth=1, relheight=1)
        self.bg.bind("<Configure>", lambda _e: self._draw_background())

        self._build_header()
        self._build_source()
        self._build_list()
        self._build_footer()
        self._build_log()
        self._build_help()
        self.container.bind("<Configure>", lambda _e: self._relayout())
        self._relayout()
        self._render_saves()
        self._update_actions()
        self._update_log_view()

    # --- header ------------------------------------------------------------

    def _build_header(self) -> None:
        self.header = tk.Frame(self.container, bg=theme.BG)
        self.header_canvas = tk.Canvas(self.header, bg=theme.BG, highlightthickness=0, bd=0)
        self.header_canvas.pack(side="left", fill="both", expand=True)
        self.header_canvas.bind("<Configure>", lambda _e: self._draw_header())

        right = tk.Frame(self.header, bg=theme.BG)
        right.pack(side="right", padx=(0, self.m.px(26)))
        self.btn_lang = widgets.IconButton(
            right,
            self.t("header.lang"),
            self.toggle_language,
            m=self.m,
            kind="quiet",
            size=28,
        )
        self.btn_lang.pack(side="left", padx=(0, self.m.px(8)))
        self.btn_help = widgets.IconButton(
            right, "?", self.toggle_help, m=self.m, kind="quiet", size=28
        )
        self.btn_help.pack(side="left", padx=(0, self.m.px(12)))
        self.version_label = tk.Label(
            right,
            text=f"v{__version__}",
            font=fonts.font(self.root, theme.FONT_TEXT, self.m.px(11)),
            fg=theme.DIM,
            bg=theme.BG,
        )
        self.version_label.pack(side="left")

    def _draw_header(self) -> None:
        canvas = self.header_canvas
        canvas.delete("all")
        width = canvas.winfo_width()
        height = self.m.px(70)
        if width <= 1:
            return
        title_font = fonts.display(self.root, self.m.px(25))
        sub_font = fonts.font(self.root, theme.FONT_TEXT, self.m.px(10))
        title = self.t("app.title")
        track = self.m.px(1.6)
        title_w = widgets.text_width(title_font, title, track)
        pad = self.m.px(26)
        top, bottom = self.m.px(11), self.m.px(47)
        x0 = pad - self.m.px(10)
        x1 = x0 + title_w + self.m.px(34)
        cut = self.m.px(12)
        canvas.create_polygon(
            x0,
            top,
            x1 - cut,
            top,
            x1,
            top + cut,
            x1,
            bottom,
            x0 + self.m.px(7),
            bottom,
            x0,
            bottom - self.m.px(7),
            fill=theme.PANEL,
            outline=theme.blend(theme.WHITE, theme.PANEL, 0.14),
        )
        # electric sliver on the cut corner
        canvas.create_polygon(
            x1 - cut,
            top,
            x1,
            top + cut,
            x1,
            top + cut + self.m.px(3),
            x1 - cut - self.m.px(3),
            top,
            fill=theme.blend(theme.CYAN, theme.PANEL, 0.7),
            outline="",
        )
        widgets.tracked_text(
            canvas, pad, (top + bottom) / 2 + 1, title, title_font, theme.WHITE, track, "w"
        )
        widgets.tracked_text(
            canvas,
            pad,
            bottom + self.m.px(13),
            self.t("app.subtitle"),
            sub_font,
            theme.CYAN,
            self.m.px(1.2),
            "w",
        )
        canvas.create_line(0, height, width, height, fill=theme.blend(theme.CYAN, theme.BG, 0.22))

    # --- source ------------------------------------------------------------

    def _build_source(self) -> None:
        self.source_panel = widgets.ShardPanel(
            self.container, m=self.m, title=self.t("source.title")
        )
        body = self.source_panel.body
        self.drop = DropZone(body, self, m=self.m)
        body.bind("<Configure>", self._layout_source_body)
        self.btn_auto = widgets.ShardButton(
            body,
            self.t("source.auto"),
            self.auto_find,
            kind="ghost",
            m=self.m,
            font_px=12,
            height=34,
            padx=14,
            min_width=206,
        )
        self.btn_choose = widgets.ShardButton(
            body,
            self.t("source.choose"),
            self.choose_folder,
            kind="ghost",
            m=self.m,
            font_px=12,
            height=34,
            padx=14,
            min_width=206,
        )
        self.btn_auto.place(relx=1.0, x=-self.m.px(206), y=0)
        self.btn_choose.place(relx=1.0, x=-self.m.px(206), y=self.m.px(44))
        self._update_drop_text()

    def _layout_source_body(self, event=None) -> None:
        if not hasattr(self, "drop"):
            return
        width = event.width if event is not None else self.drop.master.winfo_width()
        height = event.height if event is not None else self.drop.master.winfo_height()
        self.drop.place(x=0, y=0, width=max(self.m.px(120), width - self.m.px(224)), height=height)

    def _update_drop_text(self) -> None:
        if not hasattr(self, "drop"):
            return
        if self.saves:
            sub = f"{self.t('source.found', count=len(self.saves))}  ·  {self.source_path}"
        else:
            sub = self.source_path or self.t("source.none")
        self.drop.set_texts(self.t("source.drop_title"), self.t("source.drop_hint"), sub)
        self.drop.active = bool(self.saves)

    # --- list --------------------------------------------------------------

    def _build_list(self) -> None:
        self.list_panel = widgets.ShardPanel(self.container, m=self.m, title=self.t("table.title"))
        body = self.list_panel.body
        self.table_header = tk.Frame(body, bg=theme.PANEL)
        self.table_header.place(x=0, y=0, relwidth=1.0, height=self.m.px(24))
        self.column_labels = {}
        for key, x, width, anchor in (
            ("slot", 34, 46, "w"),
            ("date", 86, 150, "w"),
            ("level", 246, 46, "w"),
            ("time", 300, 90, "w"),
            ("size", 398, 96, "w"),
        ):
            label = tk.Label(
                self.table_header,
                text=self.t(f"table.{key}"),
                font=fonts.font(self.root, theme.FONT_SEMI, self.m.px(10)),
                fg=theme.DIM,
                bg=theme.PANEL,
                anchor=anchor,
            )
            label.place(x=self.m.px(x), y=0, width=self.m.px(width))
            self.column_labels[key] = label
        self.type_label = tk.Label(
            self.table_header,
            text=self.t("table.type"),
            font=fonts.font(self.root, theme.FONT_SEMI, self.m.px(10)),
            fg=theme.DIM,
            bg=theme.PANEL,
            anchor="e",
        )
        self.type_label.place(relx=1.0, x=-self.m.px(24), y=0, width=self.m.px(70), anchor="ne")

        self.scroll = widgets.ScrollArea(body, m=self.m)
        body.bind("<Configure>", self._layout_list_body)
        self.empty_frame = tk.Frame(body, bg=theme.PANEL)
        self.empty_title = tk.Label(
            self.empty_frame,
            text=self.t("table.empty_title"),
            font=fonts.display(self.root, self.m.px(16)),
            fg=theme.WHITE,
            bg=theme.PANEL,
        )
        self.empty_title.pack(anchor="w")
        self.empty_body = tk.Label(
            self.empty_frame,
            text=self.t("table.empty_body"),
            font=fonts.font(self.root, theme.FONT_TEXT, self.m.px(12)),
            fg=theme.DIM,
            bg=theme.PANEL,
            justify="left",
            wraplength=self.m.px(620),
        )
        self.empty_body.pack(anchor="w", pady=(self.m.px(8), 0))
        self.empty_log_btn = widgets.ShardButton(
            self.empty_frame,
            self.t("action.show_log"),
            self.toggle_log,
            kind="ghost",
            m=self.m,
            height=30,
            padx=14,
        )
        self.hint_label = tk.Label(
            body,
            text=self.t("table.hint"),
            font=fonts.font(self.root, theme.FONT_TEXT, self.m.px(10)),
            fg=theme.DIM,
            bg=theme.PANEL,
            anchor="w",
        )
        self.btn_all = widgets.ShardButton(
            body,
            self.t("table.all"),
            lambda: self.select_all(True),
            kind="ghost",
            m=self.m,
            font_px=10,
            height=20,
            padx=9,
            tracking=0.6,
        )
        self.btn_none = widgets.ShardButton(
            body,
            self.t("table.none"),
            lambda: self.select_all(False),
            kind="ghost",
            m=self.m,
            font_px=10,
            height=20,
            padx=9,
            tracking=0.6,
        )

    def _layout_list_body(self, event=None) -> None:
        if not hasattr(self, "scroll"):
            return
        width = event.width if event is not None else self.list_panel.body.winfo_width()
        height = event.height if event is not None else self.list_panel.body.winfo_height()
        if self.mode == "run":
            self.table_header.place_forget()
            self.hint_label.place_forget()
            self.btn_all.place_forget()
            self.btn_none.place_forget()
            self.scroll.place(
                x=0, y=self.m.px(26), width=width, height=max(self.m.px(40), height - self.m.px(40))
            )
            if getattr(self, "run_counter", None) is not None:
                self.run_counter.place(x=0, y=0, width=width, height=self.m.px(20))
            if getattr(self, "run_progress", None) is not None:
                self.run_progress.place(
                    x=0, rely=1.0, y=-self.m.px(8), width=width, height=self.m.px(6)
                )
            return
        self.table_header.place(x=0, y=0, width=width, height=self.m.px(24))
        self.scroll.place(
            x=0, y=self.m.px(28), width=width, height=max(self.m.px(40), height - self.m.px(50))
        )
        if self.saves:
            self.hint_label.place(
                x=0,
                rely=1.0,
                y=-self.m.px(18),
                width=max(self.m.px(100), width - self.m.px(130)),
                height=self.m.px(14),
            )
            self.btn_none.place(
                relx=1.0, x=-self.btn_none.winfo_reqwidth(), rely=1.0, y=-self.m.px(22)
            )
            self.btn_all.place(
                relx=1.0,
                x=-self.btn_none.winfo_reqwidth() - self.m.px(8) - self.btn_all.winfo_reqwidth(),
                rely=1.0,
                y=-self.m.px(22),
            )
        else:
            self.hint_label.place_forget()
            self.btn_all.place_forget()
            self.btn_none.place_forget()

    # --- footer ------------------------------------------------------------

    def _build_footer(self) -> None:
        self.footer = tk.Frame(self.container, bg=theme.PANEL)
        self.footer_top = tk.Frame(
            self.footer, bg=theme.blend(theme.CYAN, theme.PANEL, 0.25), height=1
        )
        self.footer_top.pack(fill="x")
        inner = tk.Frame(self.footer, bg=theme.PANEL)
        inner.pack(
            fill="both", expand=True, padx=self.m.px(26), pady=(self.m.px(12), self.m.px(10))
        )

        row1 = tk.Frame(inner, bg=theme.PANEL)
        row1.pack(fill="x")
        self.dest_title = tk.Label(
            row1,
            text=self.t("dest.title"),
            font=fonts.font(self.root, theme.FONT_SEMI, self.m.px(10)),
            fg=theme.DIM,
            bg=theme.PANEL,
        )
        self.dest_title.pack(side="left", padx=(0, self.m.px(12)))
        self.dest_entry = tk.Entry(
            row1,
            font=fonts.font(self.root, theme.FONT_TEXT, self.m.px(12)),
            bg=theme.PANEL_HI,
            fg=theme.WHITE,
            insertbackground=theme.CYAN,
            relief="flat",
            highlightthickness=1,
            highlightbackground=theme.blend(theme.WHITE, theme.PANEL_HI, 0.16),
            highlightcolor=theme.CYAN,
            disabledbackground=theme.shade(theme.PANEL_HI, 0.3),
            disabledforeground=theme.DIM,
        )
        self._placeholder_active = False
        self.dest_entry.pack(side="left", fill="x", expand=True, ipady=self.m.px(4))
        self.dest_entry.bind("<Return>", lambda _e: self._on_dest_edited())
        self.dest_entry.bind("<FocusIn>", self._on_dest_focus_in)
        self.dest_entry.bind("<FocusOut>", self._on_dest_focus_out)
        self._set_dest_text(self.dest_path)
        self.btn_browse = widgets.IconButton(row1, "…", self.choose_destination, m=self.m, size=28)
        self.btn_browse.pack(side="left", padx=(self.m.px(8), 0))
        self.account_label = tk.Label(
            row1,
            text=self.t("dest.account"),
            font=fonts.font(self.root, theme.FONT_TEXT, self.m.px(11)),
            fg=theme.DIM,
            bg=theme.PANEL,
        )
        self.account_box = ttk.Combobox(
            row1, values=[], state="readonly", style="P3.TCombobox", width=20
        )
        self.account_box.bind("<<ComboboxSelected>>", self._on_account_selected)

        row2 = tk.Frame(inner, bg=theme.PANEL)
        row2.pack(fill="x", pady=(self.m.px(10), 0))
        self.toggle_backup = widgets.Toggle(
            row2,
            self.t("opt.backup"),
            bool(self.cfg.get("backup", True) if self.cfg else True),
            self._on_backup,
            m=self.m,
        )
        self.toggle_backup.pack(side="left")
        self.toggle_dry = widgets.Toggle(
            row2,
            self.t("opt.dry"),
            bool(self.cfg.get("dry_run", False) if self.cfg else False),
            self._on_dry,
            m=self.m,
        )
        self.toggle_dry.pack(side="left", padx=(self.m.px(22), 0))
        self.reason_label = tk.Label(
            row2,
            text="",
            font=fonts.font(self.root, theme.FONT_TEXT, self.m.px(11)),
            fg=theme.DIM,
            bg=theme.PANEL,
            anchor="e",
        )
        self.reason_label.pack(side="right")

        row3 = tk.Frame(inner, bg=theme.PANEL)
        row3.pack(fill="x", pady=(self.m.px(10), 0))
        self.btn_convert = widgets.ShardButton(
            row3,
            self.t("action.convert"),
            self.primary_action,
            kind="primary",
            m=self.m,
            font_px=17,
            height=44,
            padx=30,
            min_width=300,
            tracking=2.0,
        )
        self.btn_convert.pack(side="left")
        self.status_label = tk.Label(
            row3,
            text="",
            font=fonts.font(self.root, theme.FONT_TEXT, self.m.px(12)),
            fg=theme.DIM,
            bg=theme.PANEL,
            anchor="w",
            justify="left",
        )
        self.btn_open = widgets.ShardButton(
            row3,
            self.t("run.open"),
            self.open_result_folder,
            kind="ghost",
            m=self.m,
            font_px=11,
            height=38,
            padx=18,
            min_width=180,
        )
        self.status_label.pack(side="left", padx=(self.m.px(18), 0), fill="x", expand=True)

    # --- log ---------------------------------------------------------------

    def _build_log(self) -> None:
        self.log_panel = tk.Frame(self.container, bg=theme.BG)
        self.log_bar = tk.Frame(self.log_panel, bg=theme.BG, height=self.m.px(28))
        self.log_bar.pack(fill="x", side="top")
        self.log_toggle = widgets.ShardButton(
            self.log_bar,
            self.t("log.title"),
            self.toggle_log,
            kind="quiet",
            m=self.m,
            font_px=10,
            height=22,
            padx=10,
            min_width=0,
            tracking=1.4,
        )
        self.log_toggle.pack(side="left", padx=(self.m.px(26), self.m.px(10)))
        self.log_count = tk.Label(
            self.log_bar,
            text="0",
            font=fonts.font(self.root, theme.FONT_TEXT, self.m.px(10)),
            fg=theme.DIM,
            bg=theme.BG,
        )
        self.log_count.pack(side="left")
        self.btn_copy_log = widgets.ShardButton(
            self.log_bar,
            self.t("action.copy_log"),
            self.copy_log,
            kind="quiet",
            m=self.m,
            font_px=10,
            height=22,
            padx=10,
            tracking=0.8,
        )
        self.btn_copy_log.pack(side="right", padx=(0, self.m.px(26)))
        self.log_body = tk.Frame(self.log_panel, bg=theme.BG)
        self.log_text = tk.Text(
            self.log_body,
            bg=theme.BG_DEEP,
            fg=theme.DIM,
            insertbackground=theme.CYAN,
            relief="flat",
            bd=0,
            highlightthickness=0,
            wrap="none",
            font=fonts.font(
                self.root,
                "Consolas" if platform_win.is_windows() else "DejaVu Sans Mono",
                self.m.px(10),
            ),
            padx=self.m.px(26),
            pady=self.m.px(6),
        )
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

    # --- help --------------------------------------------------------------

    def _build_help(self) -> None:
        self.help_panel = tk.Frame(self.container, bg=theme.PANEL_HI, highlightthickness=0)
        self.help_edge = tk.Frame(
            self.help_panel, bg=theme.blend(theme.CYAN, theme.PANEL_HI, 0.5), width=1
        )
        self.help_edge.pack(side="left", fill="y")
        content = tk.Frame(self.help_panel, bg=theme.PANEL_HI)
        content.pack(side="left", fill="both", expand=True, padx=self.m.px(22), pady=self.m.px(22))
        title_font = fonts.display(self.root, self.m.px(18))
        head = tk.Label(
            content,
            text=self.t("help.title"),
            font=title_font,
            fg=theme.WHITE,
            bg=theme.PANEL_HI,
            anchor="w",
        )
        head.pack(fill="x")
        tk.Frame(content, bg=theme.CYAN, height=2).pack(
            fill="x", pady=(self.m.px(8), self.m.px(14))
        )

        mono_family = "Consolas" if platform_win.is_windows() else "DejaVu Sans Mono"

        def block(key: str, accent: str = theme.CYAN, *, mono: bool = False) -> None:
            tk.Label(
                content,
                text=self.t(f"help.{key}.title"),
                font=fonts.font(self.root, theme.FONT_SEMI, self.m.px(12)),
                fg=accent,
                bg=theme.PANEL_HI,
                anchor="w",
            ).pack(fill="x", pady=(self.m.px(10), self.m.px(2)))
            tk.Label(
                content,
                text=self.t(f"help.{key}.body"),
                font=fonts.font(
                    self.root,
                    mono_family if mono else theme.FONT_TEXT,
                    self.m.px(10 if mono else 11),
                ),
                fg=theme.DIM,
                bg=theme.PANEL_HI,
                anchor="w",
                justify="left",
                wraplength=self.m.px(310),
            ).pack(fill="x")

        block("step1")
        block("step2")
        block("step3")
        block("files", mono=True)
        block("warn", theme.PINK)

        about = tk.Label(
            content,
            text=self.t("help.about", version=__version__),
            font=fonts.text(self.root, self.m.px(10)),
            fg=theme.DIM,
            bg=theme.PANEL,
            anchor="w",
            justify="left",
            wraplength=self.m.px(380),
        )
        about.pack(anchor="w", pady=(self.m.px(16), 0))

        self.btn_help_close = widgets.ShardButton(
            content,
            self.t("help.close"),
            self.toggle_help,
            kind="ghost",
            m=self.m,
            font_px=12,
            height=32,
            padx=18,
            min_width=140,
        )
        self.btn_help_close.pack(anchor="w", pady=(self.m.px(18), 0))
        self.help_panel.place_forget()

    # --- background art ----------------------------------------------------

    def _draw_background(self) -> None:
        canvas = self.bg
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width <= 1:
            return
        grid = theme.blend(theme.WHITE, theme.BG, 0.035)
        step = self.m.px(44)
        for x in range(0, width, step):
            canvas.create_line(x, 0, x, height, fill=grid)
        for y in range(0, height, step):
            canvas.create_line(0, y, width, y, fill=grid)
        # one large diagonal shard of light
        canvas.create_polygon(
            width * 0.55,
            0,
            width * 0.78,
            0,
            width * 0.34,
            height,
            width * 0.12,
            height,
            fill=theme.blend(theme.BLUE, theme.BG, 0.032),
            outline="",
        )
        canvas.create_polygon(
            width * 0.80,
            0,
            width * 0.83,
            0,
            width * 0.39,
            height,
            width * 0.36,
            height,
            fill=theme.blend(theme.CYAN, theme.BG, 0.038),
            outline="",
        )
        # faint scanlines
        scan = theme.blend(theme.WHITE, theme.BG, 0.028)
        for y in range(0, height, 3):
            canvas.create_line(0, y, width, y, fill=scan)
        # sparse noise
        rng = random.Random(7)
        for _ in range(int(width * height / 9000)):
            x, y = rng.randrange(width), rng.randrange(height)
            canvas.create_line(x, y, x + 1, y, fill=theme.blend(theme.WHITE, theme.BG, 0.05))
        canvas.tag_lower("all")

    # ---------------------------------------------------------------- layout

    def _relayout(self) -> None:
        width = self.container.winfo_width()
        height = self.container.winfo_height()
        if width <= 1:
            return
        m = self.m
        header_h = m.px(70)
        pad = m.px(24)
        gap = m.px(8)
        source_h = m.px(124)
        footer_h = max(m.px(126), self.footer.winfo_reqheight() + m.px(8))
        log_h = m.px(196) if self.log_open else m.px(30)

        self.header.place(x=0, y=0, width=width, height=header_h)
        top = header_h + m.px(10)
        self.source_panel.place(x=pad, y=top, width=width - 2 * pad, height=source_h)
        footer_y = height - log_h - footer_h
        list_y = top + source_h + gap
        self.list_panel.place(
            x=pad, y=list_y, width=width - 2 * pad, height=max(m.px(120), footer_y - list_y - gap)
        )
        self.footer.place(x=0, y=footer_y, width=width, height=footer_h)
        self.log_panel.place(x=0, y=height - log_h, width=width, height=log_h)
        if self.log_open:
            self.log_body.pack(fill="both", expand=True)
        else:
            self.log_body.pack_forget()
        help_top = header_h
        self.help_panel.place(x=width, y=help_top, width=m.px(360), height=height - help_top)
        if self.help_open:
            self.help_panel.place(
                x=width - m.px(360), y=help_top, width=m.px(360), height=height - help_top
            )
            self.help_panel.lift()
        self._position_account_widgets()
        self._place_rows()

    def _position_account_widgets(self) -> None:
        has_many = len(self.accounts) > 1
        if has_many:
            self.account_label.pack(side="left", padx=(self.m.px(14), self.m.px(6)))
            self.account_box.pack(side="left")
            self.account_box.configure(values=[a["id"] for a in self.accounts])
            if self.steam_id:
                self.account_box.set(self.steam_id)
        else:
            self.account_label.pack_forget()
            self.account_box.pack_forget()

    def _place_rows(self) -> None:
        for child in self.scroll.inner.winfo_children():
            width = max(1, self.scroll.inner.winfo_width())
            if isinstance(child, SaveRow):
                child.place_badge(width)
                child.configure(width=width)

    # ------------------------------------------------------------------ data

    def is_selected(self, path: str) -> bool:
        return path in self.selected

    def toggle_item(self, path: str) -> None:
        if path in self.selected:
            self.selected.discard(path)
        else:
            self.selected.add(path)
        for row in getattr(self, "_save_rows", []):
            row.sync_check()
        self._update_actions()

    def select_all(self, value: bool) -> None:
        self.selected = {item["path"] for item in self.saves} if value else set()
        for row in getattr(self, "_save_rows", []):
            row.sync_check()
        self._update_actions()

    def _update_actions(self) -> None:
        busy = self.mode in {"scan", "run"}
        if busy:
            self.btn_convert.set_text(self.t("action.cancel"))
            self.btn_convert.set_kind("danger")
            self.btn_convert.set_min_width(200)
            self.btn_convert.set_enabled(True)
            self.reason_label.configure(text=self.t("reason.busy"))
        elif self.mode == "result":
            self.btn_convert.set_text(self.t("action.back"))
            self.btn_convert.set_kind("ghost")
            self.btn_convert.set_min_width(160)
            self.btn_convert.set_enabled(True)
            self.reason_label.configure(text="")
        else:
            reason_key = None
            if not self.saves:
                reason_key = "reason.no_source"
            elif not self.dest_path.strip():
                reason_key = "reason.no_dest"
            elif not self.selected:
                reason_key = "reason.no_selection"
            self.btn_convert.set_text(self.t("action.convert"))
            self.btn_convert.set_kind("primary")
            self.btn_convert.set_min_width(300)
            self.btn_convert.set_enabled(reason_key is None)
            self.reason_label.configure(
                text=self.t(reason_key)
                if reason_key
                else self.t("table.selected", count=len(self.selected), total=len(self.saves))
            )
        self._update_open_button()
        if self.mode == "result" and self.last_summary:
            self.status_label.configure(text=self._summary_text(self.last_summary), fg=theme.CYAN)
        elif self.mode == "run":
            self.status_label.configure(text=self.t("run.preparing"), fg=theme.DIM)
        elif self.mode == "idle":
            self.status_label.configure(text="", fg=theme.DIM)

    def _update_open_button(self) -> None:
        if not hasattr(self, "btn_open"):
            return
        if self.mode == "result" and self.dest_path.strip():
            if not self.btn_open.winfo_ismapped():
                self.btn_open.pack(side="right", padx=(self.m.px(8), 0))
        else:
            self.btn_open.pack_forget()

    def primary_action(self) -> None:
        if self.mode in {"scan", "run"}:
            self.cancel_current()
            return
        if self.mode == "result":
            self.back_to_list()
            return
        self.on_convert()

    def _summary_text(self, summary: dict) -> str:
        if summary.get("cancelled"):
            return self.t("run.cancelled")
        if summary.get("failed"):
            return self.t(
                "run.failed", converted=summary.get("converted", 0), failed=summary["failed"]
            )
        return self.t(
            "run.summary",
            converted=summary.get("converted", 0),
            skipped=summary.get("skipped", len(self.skipped)),
        )

    # --------------------------------------------------------------- commands

    def auto_find(self) -> None:
        info = self.bridge.default_source()
        if not info.get("ok"):
            self._log(f"! {info.get('error')}")
            return
        if not info.get("exists"):
            self._log(self.t("table.empty_body"))
            self.saves = []
            self.selected = set()
            self._render_saves()
            self._update_actions()
            return
        self.load_source(info["path"])

    def choose_folder(self) -> None:
        initial = self.source_path or str(Path.home())
        chosen = filedialog.askdirectory(
            parent=self.root, title=self.t("source.choose"), initialdir=initial, mustexist=True
        )
        if chosen:
            self.load_source(chosen)

    def choose_destination(self) -> None:
        initial = self.dest_path or str(Path.home())
        chosen = filedialog.askdirectory(
            parent=self.root, title=self.t("dest.browse"), initialdir=initial
        )
        if chosen:
            self.dest_path = chosen
            self._set_dest_text(chosen)
            self._remember(destination=chosen)
            self._update_actions()

    def _set_dest_text(self, value: str) -> None:
        self._placeholder_active = False
        self.dest_entry.configure(fg=theme.WHITE)
        self.dest_entry.delete(0, "end")
        if value:
            self.dest_entry.insert(0, value)
        else:
            self._show_placeholder()

    def _show_placeholder(self) -> None:
        self._placeholder_active = True
        self.dest_entry.delete(0, "end")
        self.dest_entry.insert(0, self.t("dest.ph"))
        self.dest_entry.configure(fg=theme.DIM)

    def _on_dest_focus_in(self, _event=None) -> None:
        if self._placeholder_active:
            self._placeholder_active = False
            self.dest_entry.delete(0, "end")
            self.dest_entry.configure(fg=theme.WHITE)

    def _on_dest_focus_out(self, _event=None) -> None:
        if not self.dest_entry.get().strip():
            self._show_placeholder()
        self._on_dest_edited()

    def _on_dest_edited(self) -> None:
        value = "" if self._placeholder_active else self.dest_entry.get().strip()
        if value != self.dest_path:
            self.dest_path = value
            self._remember(destination=value)
            self._update_actions()

    def _on_account_selected(self, _event=None) -> None:
        choice = self.account_box.get()
        match = [a for a in self.accounts if a["id"] == choice]
        if match:
            self.steam_id = choice
            self.dest_path = match[0]["path"]
            self._set_dest_text(self.dest_path)
            self._remember(steam_id=choice, destination=self.dest_path)
            self._update_actions()

    def _on_backup(self, value: bool) -> None:
        self._remember(backup=value)

    def _on_dry(self, value: bool) -> None:
        self._remember(dry_run=value)

    def _remember(self, **values: object) -> None:
        if self.cfg is not None:
            self.cfg.values.update(values)
            self.cfg.save()

    def load_source(self, path: str) -> None:
        self.source_path = path
        self.mode = "scan"
        self._update_drop_text()
        self._set_busy_ui(True)
        self.saves = []
        self.selected = set()
        self._render_saves(loading=True)
        self._run_worker(lambda: self.bridge.scan(path), self._on_scan_done)

    def _on_drop(self, paths: list[str]) -> None:
        if not paths:
            return
        self.root.after(0, lambda: self._handle_drop(paths))

    def _handle_drop(self, paths: list[str]) -> None:
        target = Path(paths[0])
        if (
            target.is_file()
            and target.suffix.lower() != ".sav"
            and not target.name.startswith("SaveData")
        ):
            self.saves = []
            self._render_saves()
            self._update_actions()
            return
        self.load_source(str(target))

    # --- scan --------------------------------------------------------------

    def _run_worker(self, fn, on_done) -> None:
        self._cancel.clear()
        self._worker = threading.Thread(target=self._worker_main, args=(fn, on_done), daemon=True)
        self._worker.start()

    def _worker_main(self, fn, on_done) -> None:
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 - bridge already guards; belt and braces
            result = {"ok": False, "error": str(exc)}
        self._queue.put(("done", on_done, result))

    def _poll_queue(self) -> None:
        if getattr(self, "_closing", False):
            return
        try:
            while True:
                kind, *payload = self._queue.get_nowait()
                if kind == "done":
                    callback, result = payload
                    try:
                        callback(result)
                    except Exception:  # noqa: BLE001 - never wedge the queue
                        log.exception("Ошибка обработчика результата")
                elif kind == "progress":
                    self._on_progress(payload[0])
                elif kind == "log":
                    self._append_log(payload[0])
        except queue.Empty:
            pass
        self._poll_job = self.root.after(60, self._poll_queue)

    def _on_scan_done(self, result: dict) -> None:
        self.mode = "idle"
        self._set_busy_ui(False)
        if not result.get("ok"):
            self.saves = []
            self.selected = set()
            self._render_saves(error=result.get("error"))
            self._update_actions()
            self._log(f"! {result.get('error')}")
            return
        self.saves = result.get("saves", [])
        self.skipped = result.get("skipped", []) or []
        self.metadata_count = int(result.get("metadata", 0) or 0)
        self.selected = {item["path"] for item in self.saves}
        if self.skipped:
            self._log(f"пропущено файлов: {len(self.skipped)}")
        self.source_path = result.get("source", self.source_path)
        self._remember(source=self.source_path)
        self._render_saves()
        self._update_actions()
        self._update_drop_text()
        self.detect_destination()

    def detect_destination(self) -> None:
        info = self.bridge.detect_dest(self.steam_id or None)
        if not info.get("ok"):
            return
        self.accounts = info.get("accounts", [])
        if not self.dest_path.strip() and info.get("path"):
            self.dest_path = info["path"]
            self._set_dest_text(self.dest_path)
        if len(self.accounts) == 1:
            self.steam_id = self.accounts[0]["id"]
            if not info.get("path"):
                self.dest_path = self.accounts[0]["path"]
        self._position_account_widgets()
        self._update_actions()

    # --- rendering ---------------------------------------------------------

    def _clear_rows(self) -> None:
        for child in self.scroll.inner.winfo_children():
            child.destroy()

    def _render_saves(self, *, loading: bool = False, error: str | None = None) -> None:
        if not hasattr(self, "scroll"):
            return
        self._clear_rows()
        self.empty_frame.place_forget()
        self._save_rows: list[SaveRow] = []
        if loading:
            self.scroll.place_forget()
            self._show_empty(self.t("table.loading"), "")
            return
        if error:
            self.scroll.place_forget()
            self._show_empty(self.t("error.unknown"), error)
            return
        if not self.saves:
            self.scroll.place_forget()
            if self.skipped:
                reasons: dict[str, int] = {}
                for item in self.skipped:
                    key = str(item.get("reason", "?"))
                    reasons[key] = reasons.get(key, 0) + 1
                listing = "\n".join(
                    f"{reason}: {count}" for reason, count in sorted(reasons.items())
                )
                self._show_empty(
                    self.t("table.empty_title"),
                    self.t("table.empty_skipped", count=len(self.skipped), reasons=listing),
                )
            else:
                self._show_empty(self.t("table.empty_title"), self.t("table.empty_body"))
            return
        self._show_scroll()
        width = max(self.m.px(600), self.scroll.canvas.winfo_width())
        for item in self.saves:
            row = SaveRow(self.scroll.inner, self, item, m=self.m)
            row.configure(width=width)
            row.pack(fill="x", pady=(0, self.m.px(2)))
            self._save_rows.append(row)
        self.root.update_idletasks()
        for row in self._save_rows:
            row.place_badge(max(1, row.winfo_width()))
        self._render_skipped(width)
        self.scroll.refresh()

    def _render_skipped(self, width: int) -> None:
        """Dimmed "Пропущено (N)" block under the list; never an error state."""
        if not self.skipped:
            return
        header = tk.Label(
            self.scroll.inner,
            text=self.t("table.skipped_title", count=len(self.skipped)),
            font=fonts.medium(self.root, self.m.px(11)),
            fg=theme.DIM,
            bg=theme.PANEL,
            anchor="w",
        )
        header.configure(width=1)
        header.pack(fill="x", pady=(self.m.px(14), self.m.px(4)))
        for item in self.skipped[:40]:
            label = tk.Label(
                self.scroll.inner,
                text=self.t(
                    "table.skipped_row", name=item.get("name", "?"), reason=item.get("reason", "")
                ),
                font=fonts.text(self.root, self.m.px(11)),
                fg=theme.DIM,
                bg=theme.PANEL,
                anchor="w",
                justify="left",
                wraplength=max(self.m.px(400), width - self.m.px(40)),
            )
            label.pack(fill="x")
        if len(self.skipped) > 40:
            more = tk.Label(
                self.scroll.inner,
                text=self.t("table.skipped_more", count=len(self.skipped) - 40),
                font=fonts.text(self.root, self.m.px(11)),
                fg=theme.DIM,
                bg=theme.PANEL,
                anchor="w",
            )
            more.pack(fill="x")

    def _show_scroll(self) -> None:
        self._layout_list_body()

    def _show_empty(self, title: str, body: str) -> None:
        self.empty_title.configure(text=title)
        self.empty_body.configure(text=body)
        if self.skipped:
            self.empty_log_btn.pack(anchor="w", pady=(self.m.px(12), 0))
        else:
            self.empty_log_btn.pack_forget()
        self.empty_frame.place(relx=0.5, rely=0.5, anchor="center")

    # --- convert -----------------------------------------------------------

    def on_convert(self) -> None:
        if self.mode in {"scan", "run"}:
            return
        self._on_dest_edited()
        items = [item for item in self.saves if item["path"] in self.selected]
        opts = {
            "backup": self.toggle_backup.value,
            "dry_run": self.toggle_dry.value,
            "force": False,
        }
        plan = self.bridge.plan_convert(items, self.dest_path, opts)
        if not plan.get("ok"):
            self._log(f"! {plan.get('error')}")
            self.status_label.configure(text=plan.get("error", ""), fg=theme.PINK)
            return
        conflicts = plan.get("conflicts", [])
        if conflicts:
            self._show_overwrite_modal(conflicts, lambda: self._start_convert(items, force=True))
            return
        self._start_convert(items, force=False)

    def _start_convert(self, items: list[dict], *, force: bool) -> None:
        self.mode = "run"
        self.last_summary = None
        self.run_items = [
            {"path": item["path"], "slot": item.get("slot"), "status": "pending"} for item in items
        ]
        self._render_run()
        self._set_busy_ui(True)
        opts = {
            "backup": self.toggle_backup.value,
            "dry_run": self.toggle_dry.value,
            "force": force,
        }
        self._log(f"convert: {len(items)} files -> {self.dest_path}")

        def work():
            return self.bridge.convert(
                items,
                self.dest_path,
                opts,
                on_progress=lambda event: self._queue.put(("progress", event)),
                should_cancel=self._cancel.is_set,
            )

        self._run_worker(work, self._on_convert_done)

    def _clear_run_widgets(self) -> None:
        for name in ("run_progress", "run_counter"):
            widget = getattr(self, name, None)
            if widget is not None:
                with contextlib.suppress(tk.TclError):
                    widget.destroy()
                setattr(self, name, None)

    def _render_run(self) -> None:
        self._clear_rows()
        self._clear_run_widgets()
        self.empty_frame.place_forget()
        self.run_rows = {}
        self.run_progress = widgets.ProgressBar(self.list_panel.body, m=self.m)
        self.run_counter = tk.Label(
            self.list_panel.body,
            text=self.t("run.preparing"),
            font=fonts.font(self.root, theme.FONT_TEXT, self.m.px(11)),
            fg=theme.DIM,
            bg=theme.PANEL,
            anchor="e",
        )
        len(self.run_items)
        width = max(self.m.px(600), self.scroll.canvas.winfo_width())
        for item in self.run_items:
            row = RunRow(self.scroll.inner, self, item, m=self.m)
            row.configure(width=width)
            row.pack(fill="x", pady=(0, self.m.px(2)))
            row.update_state("pending", None, None, self._status_texts(), self.m)
            self.run_rows[item["path"]] = row
        self.run_progress.set(0)
        self._layout_list_body()
        self.scroll.refresh()

    def _status_texts(self) -> dict[str, str]:
        return {
            key: self.t(f"status.{key}")
            for key in ("pending", "running", "ok", "error", "cancelled")
        }

    def _on_progress(self, event: dict) -> None:
        kind = event.get("event")
        if kind == "item":
            path = event.get("path")
            row = self.run_rows.get(path)
            if row is not None:
                row.update_state(
                    event.get("status", "pending"),
                    event.get("target"),
                    event.get("error"),
                    self._status_texts(),
                    self.m,
                )
            total = max(1, event.get("total", 1))
            self.run_progress.set(event.get("index", 0) / total)
            self.run_counter.configure(
                text=self.t(
                    "run.progress", done=event.get("index", 0), total=event.get("total", 0)
                ),
                fg=theme.DIM,
            )
            self.scroll.refresh()
        elif kind == "done":
            pass

    def _on_convert_done(self, summary: dict) -> None:
        self.mode = "result"
        self.last_summary = summary
        self._set_busy_ui(False)
        if not summary.get("ok"):
            self.status_label.configure(text=summary.get("error", ""), fg=theme.PINK)
            self._log(f"! {summary.get('error')}")
            return
        for item in summary.get("items", []):
            row = self.run_rows.get(item["path"])
            if row is not None:
                row.update_state(
                    item.get("status", "pending"),
                    item.get("target"),
                    item.get("error"),
                    self._status_texts(),
                    self.m,
                )
        if getattr(self, "run_progress", None) is not None:
            done = summary.get("converted", 0) + summary.get("failed", 0)
            self.run_progress.set(
                1.0 if not summary.get("cancelled") else done / max(1, len(self.run_items))
            )
        if getattr(self, "run_counter", None) is not None:
            suffix = ""
            if summary.get("backup"):
                suffix = "  ·  " + self.t("run.backup", path=Path(summary["backup"]).name)
            if summary.get("dry_run"):
                suffix = "  ·  " + self.t("run.dry_note")
            self.run_counter.configure(
                text=self._summary_text(summary) + suffix,
                fg=theme.PINK if summary.get("failed") else theme.CYAN,
            )
        self._update_actions()
        self.status_label.configure(text=self._summary_text(summary), fg=theme.CYAN)
        self.scroll.refresh()

    def back_to_list(self) -> None:
        self.mode = "idle"
        self.last_summary = None
        if hasattr(self, "run_progress"):
            self.run_progress.destroy()
        if getattr(self, "run_counter", None) is not None:
            self.run_counter.destroy()
        self._render_saves()
        self._update_actions()

    def toggle_run(self) -> None:
        if self.mode == "result":
            self.back_to_list()
        else:
            self.on_convert()

    # --- misc --------------------------------------------------------------

    def _set_busy_ui(self, busy: bool) -> None:
        self.btn_auto.set_enabled(not busy)
        self.btn_choose.set_enabled(not busy)
        self._update_actions()

    def cancel_current(self) -> None:
        if self.mode in {"scan", "run"}:
            self._cancel.set()
            self._log(self.t("action.cancel"))
            self.status_label.configure(text=self.t("status.cancelled"), fg=theme.PINK)

    def open_result_folder(self) -> None:
        self.bridge.open_folder(self.dest_path)

    def open_folder(self, path: str) -> None:
        self.bridge.open_folder(path)

    def toggle_language(self) -> None:
        lang = "en" if self.i18n.lang == "ru" else "ru"
        self.i18n.set_lang(lang)
        self._remember(lang=lang)
        self._rebuild()

    def _rebuild(self) -> None:
        state = (
            self.saves,
            set(self.selected),
            self.source_path,
            self.dest_path,
            self.mode,
            self.last_summary,
        )
        self.root.title(self.window_title)
        self._build()
        (
            self.saves,
            self.selected,
            self.source_path,
            self.dest_path,
            self.mode,
            self.last_summary,
        ) = state
        self._render_saves()
        self._update_actions()
        if self.mode == "result" and self.last_summary:
            self.status_label.configure(text=self._summary_text(self.last_summary), fg=theme.CYAN)

    def toggle_help(self) -> None:
        self.help_open = not self.help_open
        self._relayout()
        if self.help_open:
            self.btn_help_close.focus_set()

    def toggle_log(self) -> None:
        self.log_open = not self.log_open
        self._relayout()
        if self.log_open:
            self.log_text.see("end")

    def copy_log(self) -> None:
        text = "\n".join(self._logs)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._log(self.t("log.copied"))

    def _log(self, message: str) -> None:
        log.info(message)
        self._append_log(message)

    def _append_log(self, message: str) -> None:
        self._logs.append(message)
        if len(self._logs) > 500:
            del self._logs[:100]
        self._update_log_view()

    def _update_log_view(self) -> None:
        if not hasattr(self, "log_text"):
            return
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("end", "\n".join(self._logs) if self._logs else self.t("log.empty"))
        self.log_text.configure(state="disabled")
        self.log_count.configure(text=str(len(self._logs)))

    # --- modal -------------------------------------------------------------

    def _show_overwrite_modal(self, conflicts: list[str], on_confirm) -> None:
        if self._modal is not None:
            return
        self._dim_background()
        modal = tk.Toplevel(self.root)
        self._modal = modal
        modal.withdraw()
        modal.transient(self.root)
        modal.configure(bg=theme.BG)
        modal.overrideredirect(True)
        width, height = self.m.px(520), self.m.px(320)
        modal.geometry(f"{width}x{height}")
        canvas = tk.Canvas(modal, bg=theme.BG, highlightthickness=0, bd=0)
        canvas.pack(fill="both", expand=True)
        canvas.create_polygon(
            widgets.panel_points(1, 1, width - 1, height - 1, self.m.px(18)),
            fill=theme.PANEL,
            outline=theme.blend(theme.PINK, theme.PANEL, 0.45),
        )
        canvas.create_polygon(
            width - self.m.px(18),
            1,
            width - 1,
            1,
            width - 1,
            self.m.px(18),
            fill=theme.PINK,
            outline="",
        )
        title_font = fonts.display(self.root, self.m.px(20))
        body_font = fonts.font(self.root, theme.FONT_TEXT, self.m.px(12))
        widgets.tracked_text(
            canvas,
            self.m.px(26),
            self.m.px(34),
            self.t("modal.title"),
            title_font,
            theme.WHITE,
            self.m.px(1.5),
            "w",
        )
        dry = self.toggle_dry.value
        canvas.create_text(
            self.m.px(26),
            self.m.px(66),
            text=self.t("modal.body_dry") if dry else self.t("modal.body"),
            font=body_font,
            fill=theme.DIM,
            anchor="nw",
            width=width - self.m.px(52),
        )
        shown = conflicts[:6]
        y = self.m.px(104)
        for name in shown:
            canvas.create_text(
                self.m.px(28), y, text="▸ " + name, font=body_font, fill=theme.PINK, anchor="w"
            )
            y += self.m.px(18)
        if len(conflicts) > len(shown):
            canvas.create_text(
                self.m.px(28),
                y,
                text=self.t("modal.more", count=len(conflicts) - len(shown)),
                font=body_font,
                fill=theme.DIM,
                anchor="w",
            )
        buttons = tk.Frame(modal, bg=theme.PANEL)
        buttons.place(x=self.m.px(26), rely=1.0, y=-self.m.px(20) - self.m.px(38))
        cancel = widgets.ShardButton(
            buttons,
            self.t("modal.cancel"),
            lambda: self._close_modal(),
            kind="ghost",
            m=self.m,
            font_px=12,
            height=36,
            padx=20,
            min_width=150,
        )
        cancel.pack(side="left")
        confirm = widgets.ShardButton(
            buttons,
            self.t("modal.overwrite"),
            lambda: (self._close_modal(), on_confirm()),
            kind="danger",
            m=self.m,
            font_px=12,
            height=36,
            padx=20,
            min_width=190,
        )
        confirm.pack(side="left", padx=(self.m.px(12), 0))

        tk.Label(
            buttons,
            text=self.t("modal.esc"),
            font=fonts.font(self.root, theme.FONT_TEXT, self.m.px(10)),
            fg=theme.DIM,
            bg=theme.PANEL,
        ).pack(side="left", padx=(self.m.px(14), 0))
        modal.bind("<Escape>", lambda _e: self._close_modal())
        modal.protocol("WM_DELETE_WINDOW", self._close_modal)
        self._center_modal(modal, width, height)
        modal.deiconify()
        modal.grab_set()
        modal.focus_force()
        cancel.focus_set()

    def _dim_background(self) -> None:
        """Dim the window behind the modal (Windows: a translucent overlay)."""
        if getattr(self, "_dim", None) is not None or not platform_win.is_windows():
            return
        try:
            dim = tk.Toplevel(self.root)
            dim.overrideredirect(True)
            dim.configure(bg="#000000")
            dim.geometry(
                f"{self.root.winfo_width()}x{self.root.winfo_height()}"
                f"+{self.root.winfo_rootx()}+{self.root.winfo_rooty()}"
            )
            dim.wm_attributes("-alpha", 0.6)
            dim.transient(self.root)
            self._dim = dim
        except tk.TclError:
            log.debug("Затемнение фона недоступно", exc_info=True)
            self._dim = None

    def _undim_background(self) -> None:
        dim = getattr(self, "_dim", None)
        if dim is not None:
            with contextlib.suppress(tk.TclError):
                dim.destroy()
            self._dim = None

    def _center_modal(self, modal: tk.Toplevel, width: int, height: int) -> None:
        root_x = self.root.winfo_rootx()
        root_y = self.root.winfo_rooty()
        root_w = self.root.winfo_width()
        root_h = self.root.winfo_height()
        x = root_x + (root_w - width) // 2
        y = root_y + (root_h - height) // 2
        modal.geometry(f"{width}x{height}+{x}+{y}")

    def _close_modal(self) -> None:
        if self._modal is not None:
            try:
                self._modal.grab_release()
                self._modal.destroy()
            except tk.TclError:
                pass
            self._modal = None
        self._undim_background()
        self.btn_convert.focus_set()

    # --- closing -----------------------------------------------------------

    def close(self) -> None:
        self._closing = True
        handler = getattr(self, "_log_handler", None)
        if handler is not None:
            logging.getLogger().removeHandler(handler)
        if getattr(self, "_poll_job", None) is not None:
            with contextlib.suppress(tk.TclError):
                self.root.after_cancel(self._poll_job)
            self._poll_job = None
        if self.mode in {"scan", "run"}:
            self._cancel.set()
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=2.0)  # do not leak a thread into the next window
        self._drop.detach()
        if self.cfg is not None:
            self.cfg.values.update(
                source=self.source_path,
                destination=self.dest_path,
                steam_id=self.steam_id,
                backup=self.toggle_backup.value,
                dry_run=self.toggle_dry.value,
                lang=self.i18n.lang,
            )
            self.cfg.save()
        self.root.destroy()


_PROCESS_START = time.perf_counter()


def build_root(scale_override: float | None = None) -> tuple[tk.Tk, App]:
    dpi_scale = platform_win.enable_dpi_awareness()
    scale = (scale_override or dpi_scale) * theme.ui_scale()
    root = tk.Tk(className="P3RSaveTransfer")
    root.tk.call("tk", "scaling", 96.0 / 72.0 * scale)
    app = App(root)
    app.start(scale)
    return root, app


def run() -> None:
    if sys.stderr is None:  # windowed PyInstaller build
        logging.basicConfig(handlers=[logging.NullHandler()], level=logging.INFO)
    else:
        logging.basicConfig(
            level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", force=True
        )
    root, _app = build_root()
    root.mainloop()
