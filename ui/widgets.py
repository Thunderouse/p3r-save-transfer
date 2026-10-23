"""Hand-drawn Tk widgets: shards, toggles, badges, progress.

There is no ttk chrome here - every control is a Canvas polygon so the look
stays consistent on Windows 10 and 11 regardless of the native theme.  All
controls are focusable and operable with Space/Enter.
"""

from __future__ import annotations

import contextlib
import tkinter as tk
import tkinter.font as tkfont
from collections.abc import Callable

from . import fonts, platform_win, theme
from .theme import Metrics

# --- geometry helpers --------------------------------------------------------


def panel_points(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    cut: float,
    *,
    top_right: bool = True,
    bottom_left: bool = True,
) -> list[float]:
    points = [x0, y0, x1, y0, x1, y1, x0, y1]
    if top_right:
        points = [x0, y0, x1 - cut, y0, x1, y0 + cut, x1, y1, x0, y1]
    if bottom_left:
        if top_right:
            points = [
                x0,
                y0,
                x1 - cut,
                y0,
                x1,
                y0 + cut,
                x1,
                y1,
                x0 + cut,
                y1,
                x0,
                y1 - cut,
            ]
        else:
            points = [x0, y0, x1, y0, x1, y1, x0 + cut, y1, x0, y1 - cut]
    return points


def tracked_text(
    canvas: tk.Canvas,
    x: float,
    y: float,
    text: str,
    fnt: tkfont.Font,
    fill: str,
    tracking: float = 0.0,
    anchor: str = "w",
    tags: tuple[str, ...] = (),
) -> float:
    """Draw caps with letter-spacing (Tk has no tracking of its own)."""
    if not text:
        return 0.0
    widths = [fnt.measure(char) for char in text]
    total = sum(widths) + tracking * (len(text) - 1)
    if anchor == "center":
        x -= total / 2
    elif anchor == "e":
        x -= total
    cursor = x
    for char, width in zip(text, widths, strict=False):
        canvas.create_text(cursor, y, text=char, font=fnt, fill=fill, anchor="w", tags=tags)
        cursor += width + tracking
    return total


def text_width(fnt: tkfont.Font, text: str, tracking: float = 0.0) -> float:
    if not text:
        return 0.0
    return sum(fnt.measure(char) for char in text) + tracking * (len(text) - 1)


def elide(fnt: tkfont.Font, text: str, max_width: float) -> str:
    if fnt.measure(text) <= max_width:
        return text
    ellipsis = "…"
    budget = max_width - fnt.measure(ellipsis)
    result = ""
    for char in text:
        if fnt.measure(result + char) > budget:
            break
        result += char
    return result + ellipsis


def _parent_bg(widget: tk.Misc) -> str:
    try:
        return str(widget.cget("bg"))
    except tk.TclError:
        return theme.PANEL


class _CanvasBase(tk.Canvas):
    """Canvas with the parent's background so the shapes look transparent."""

    def __init__(self, master: tk.Misc, **kwargs) -> None:
        self.ui_bg = _parent_bg(master)
        super().__init__(
            master,
            bg=self.ui_bg,
            highlightthickness=0,
            bd=0,
            takefocus=0,
            **kwargs,
        )

    def set_bg(self, color: str) -> None:
        """Follow a hovered row's background without redrawing the parent."""
        self.ui_bg = color
        self.configure(bg=color)
        draw = getattr(self, "_draw", None)
        if callable(draw):
            draw()


# --- buttons -----------------------------------------------------------------

BUTTON_KINDS = {
    "primary": {"fill": theme.BLUE, "text": theme.WHITE, "border": theme.BLUE, "hover": theme.CYAN},
    "ghost": {
        "fill": theme.PANEL,
        "text": theme.WHITE,
        "border": theme.blend(theme.WHITE, theme.PANEL, 0.14),
        "hover": theme.CYAN,
    },
    "danger": {"fill": theme.PINK, "text": theme.WHITE, "border": theme.PINK, "hover": "#ff5c8a"},
    "quiet": {
        "fill": theme.PANEL,
        "text": theme.DIM,
        "border": theme.blend(theme.WHITE, theme.PANEL, 0.08),
        "hover": theme.CYAN,
    },
}


class ShardButton(_CanvasBase):
    """A slanted, keyboard-operable button."""

    ANIM_MS = 140

    def __init__(
        self,
        master: tk.Misc,
        text: str,
        command: Callable[[], None] | None = None,
        *,
        kind: str = "ghost",
        m: Metrics,
        font_family: str = theme.FONT_DISPLAY,
        font_px: float = 13,
        height: float = 34,
        padx: float = 18,
        min_width: float = 0,
        tracking: float = 1.2,
        cut: float = 9,
    ) -> None:
        self.m = m
        self.kind = kind
        self.command = command
        self.font_px = font_px
        self.tracking = m.px(tracking) if tracking < 3 else tracking
        self.cut = m.px(cut)
        self.padx = m.px(padx)
        self.min_width = m.px(min_width)
        self.h = m.px(height)
        self.ui_font_family = font_family
        super().__init__(master, height=self.h)
        self._font = fonts.font(self, font_family, m.px(font_px))
        self._text = text
        self._enabled = True
        self._hover = False
        self._pressed = False
        self._focus = False
        self._anim_job: str | None = None
        self._anim_step = 0

        self.configure(takefocus=1, cursor="hand2")
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<FocusIn>", self._on_focus)
        self.bind("<FocusOut>", self._on_focus)
        self.bind("<Return>", self._activate)
        self.bind("<space>", self._activate)
        self.bind("<Configure>", lambda _event: self._draw())
        self._resize()

    # --- public ------------------------------------------------------------

    def set_text(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self._resize()

    def set_min_width(self, pixels: float) -> None:
        self.min_width = self.m.px(pixels)
        self._resize()

    def set_kind(self, kind: str) -> None:
        if kind != self.kind:
            self.kind = kind
            self._anim_step = 0
            self._draw()

    def set_enabled(self, enabled: bool) -> None:
        if enabled == self._enabled:
            return
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow", takefocus=1 if enabled else 0)
        if not enabled:
            self._hover = self._pressed = False
        self._draw()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def focus_set(self) -> None:  # noqa: D401 - Tk API spelling
        super().focus_set()

    # --- geometry ----------------------------------------------------------

    def _width(self) -> int:
        content = text_width(self._font, self._text, self.tracking)
        return max(self.min_width, int(content + self.padx * 2))

    def _resize(self) -> None:
        self.configure(width=self._width(), height=self.h)
        self._draw()

    # --- events ------------------------------------------------------------

    def _on_enter(self, _event=None) -> None:
        if not self._enabled:
            return
        self._hover = True
        self._animate(True)

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._animate(False)

    def _on_press(self, _event=None) -> None:
        if not self._enabled:
            return
        self.focus_set()
        self._pressed = True
        self._draw()

    def _on_release(self, event=None) -> None:
        if not self._enabled or not self._pressed:
            return
        self._pressed = False
        inside = event is None or (
            0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        )
        self._draw()
        if inside and self.command is not None:
            self.command()

    def _on_focus(self, _event=None) -> None:
        self._focus = self.focus_get() is self
        self._draw()

    def _activate(self, _event=None):
        if self._enabled and self.command is not None:
            self.command()
        return "break"

    # --- painting ----------------------------------------------------------

    def _animate(self, forward: bool) -> None:
        if not self.winfo_exists():
            return
        if not platform_win.animations_allowed():
            self._anim_step = 8 if forward else 0
            self._draw()
            return
        if self._anim_job is not None:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._anim_job)
            self._anim_job = None
        target = 8 if forward else 0
        step = 1 if target > self._anim_step else -1
        self._anim_step += step
        self._draw()
        if self._anim_step != target:
            self._anim_job = self.after(self.ANIM_MS // 8, lambda: self._animate(forward))

    def _colours(self) -> tuple[str, str, str]:
        spec = BUTTON_KINDS[self.kind]
        if not self._enabled:
            return (
                theme.shade(theme.PANEL, 0.1),
                theme.DIM,
                theme.blend(theme.WHITE, theme.PANEL, 0.06),
            )
        t = self._anim_step / 8
        fill = spec["fill"]
        hover_fill = (
            theme.blend(spec["hover"], fill, 0.22)
            if self.kind != "primary"
            else theme.mix(fill, spec["hover"], 0.35)
        )
        fill = theme.mix(fill, hover_fill, t)
        if self._pressed:
            fill = theme.shade(fill, 0.18)
        text = spec["text"] if self.kind != "ghost" else theme.mix(spec["text"], spec["hover"], t)
        border = theme.mix(spec["border"], spec["hover"], t)
        return fill, text, border

    def _draw(self) -> None:
        self.delete("all")
        width, height = self._width(), self.h
        fill, text, border = self._colours()
        points = panel_points(0.5, 0.5, width - 0.5, height - 0.5, self.cut)
        self.create_polygon(points, fill=fill, outline=border, width=1)
        if self._focus:
            ring = panel_points(2, 2, width - 2, height - 2, max(2, self.cut - 1))
            self.create_polygon(ring, fill="", outline=theme.CYAN, width=max(1, self.m.px(1)))
        tracking = self.tracking
        display_text = self._text
        available = width - self.padx * 2
        while display_text and text_width(self._font, display_text, tracking) > available:
            display_text = display_text[:-1]
        tracked_text(
            self,
            width / 2,
            height / 2 + 1,
            display_text,
            self._font,
            text,
            tracking=tracking,
            anchor="center",
        )


class IconButton(ShardButton):
    """Small square button for “?” / language toggle."""

    def __init__(
        self,
        master: tk.Misc,
        text: str,
        command,
        *,
        m: Metrics,
        kind: str = "quiet",
        size: float = 30,
    ):
        super().__init__(
            master,
            text,
            command,
            kind=kind,
            m=m,
            font_px=12,
            height=size,
            padx=8,
            min_width=size,
            tracking=0,
            cut=6,
        )


# --- toggles -----------------------------------------------------------------


class Toggle(_CanvasBase):
    ANIM_MS = 120

    def __init__(
        self,
        master: tk.Misc,
        text: str,
        value: bool,
        command: Callable[[bool], None] | None,
        *,
        m: Metrics,
    ) -> None:
        self.m = m
        self._value = value
        self._command = command
        self._track_w, self._track_h = m.px(34), m.px(18)
        self._label_gap = m.px(10)
        self._enabled = True
        self._focus = False
        self._hover = False
        self._knob = 1.0 if value else 0.0
        self._anim_job: str | None = None
        super().__init__(master)
        self._font = fonts.font(self, theme.FONT_TEXT, m.px(13))
        self.configure(takefocus=1, cursor="hand2", height=self._track_h + m.px(8))
        self._text = text
        self.bind("<Button-1>", self._toggle)
        self.bind("<space>", self._toggle)
        self.bind("<Return>", self._toggle)
        self.bind("<FocusIn>", self._focus_in)
        self.bind("<FocusOut>", self._focus_out)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self._resize()

    def _width(self) -> int:
        return int(self._track_w + self._label_gap + text_width(self._font, self._text))

    def _resize(self) -> None:
        self.configure(width=self._width(), height=self._track_h + self.m.px(8))
        self._draw()

    def set_text(self, text: str) -> None:
        self._text = text
        self._resize()

    def set_value(self, value: bool, notify: bool = False) -> None:
        if value == self._value:
            return
        self._value = value
        self._animate()
        if notify and self._command is not None:
            self._command(value)

    @property
    def value(self) -> bool:
        return self._value

    def _toggle(self, _event=None):
        if not self._enabled:
            return "break"
        self.focus_set()
        self._value = not self._value
        self._animate()
        if self._command is not None:
            self._command(self._value)
        return "break"

    def _focus_in(self, _event=None) -> None:
        self._focus = True
        self._draw()

    def _focus_out(self, _event=None) -> None:
        self._focus = False
        self._draw()

    def _enter(self, _event=None) -> None:
        self._hover = True
        self._draw()

    def _leave(self, _event=None) -> None:
        self._hover = False
        self._draw()

    def _animate(self) -> None:
        if not self.winfo_exists():
            return
        target = 1.0 if self._value else 0.0
        if not platform_win.animations_allowed():
            self._knob = target
            self._draw()
            return
        if self._anim_job is not None:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._anim_job)
            self._anim_job = None
        step = 0.125 if target > self._knob else -0.125
        self._knob = max(0.0, min(1.0, self._knob + step))
        self._draw()
        if abs(self._knob - target) > 1e-6:
            self._anim_job = self.after(self.ANIM_MS // 8, self._animate)

    def _draw(self) -> None:
        self.delete("all")
        h = self._track_h
        y = (self.winfo_reqheight() - h) / 2
        points = panel_points(0.5, y + 0.5, self._track_w - 0.5, y + h - 0.5, self.m.px(6))
        on = self._value
        fill = theme.BLUE if on else theme.shade(theme.PANEL, 0.15)
        border = (
            theme.CYAN
            if (self._focus or self._hover)
            else theme.blend(theme.WHITE, theme.PANEL, 0.16)
        )
        self.create_polygon(points, fill=fill, outline=border, width=1)
        knob_d = h - self.m.px(6)
        x0 = self.m.px(3) + self._knob * (self._track_w - knob_d - self.m.px(6))
        self.create_rectangle(
            x0,
            y + self.m.px(3),
            x0 + knob_d,
            y + self.m.px(3) + knob_d,
            fill=theme.WHITE if on else theme.DIM,
            outline="",
        )
        self.create_text(
            self._track_w + self._label_gap,
            self.winfo_reqheight() / 2,
            text=self._text,
            anchor="w",
            fill=theme.WHITE if on else theme.DIM,
            font=self._font,
        )


# --- checkbox ----------------------------------------------------------------


class Check(_CanvasBase):
    def __init__(
        self, master: tk.Misc, value: bool, command: Callable[[bool], None] | None, *, m: Metrics
    ):
        self.m = m
        self.size = m.px(16)
        self._value = value
        self._command = command
        self._focus = False
        self._hover = False
        super().__init__(master, width=self.size + 2, height=self.size + 2)
        self.configure(takefocus=1, cursor="hand2")
        self.bind("<Button-1>", self._toggle)
        self.bind("<space>", self._toggle)
        self.bind("<Return>", self._toggle)
        self.bind("<FocusIn>", self._focus_in)
        self.bind("<FocusOut>", self._focus_out)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self._draw()

    @property
    def value(self) -> bool:
        return self._value

    def set_value(self, value: bool, notify: bool = False) -> None:
        self._value = value
        self._draw()
        if notify and self._command is not None:
            self._command(value)

    def _toggle(self, _event=None):
        self._value = not self._value
        self._draw()
        if self._command is not None:
            self._command(self._value)
        return "break"

    def _focus_in(self, _event=None) -> None:
        self._focus = True
        self._draw()

    def _focus_out(self, _event=None) -> None:
        self._focus = False
        self._draw()

    def _enter(self, _event=None) -> None:
        self._hover = True
        self._draw()

    def _leave(self, _event=None) -> None:
        self._hover = False
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        size = self.size
        points = panel_points(1, 1, size + 1, size + 1, self.m.px(4))
        border = (
            theme.CYAN
            if (self._focus or self._hover)
            else theme.blend(theme.WHITE, theme.PANEL, 0.22)
        )
        fill = theme.CYAN if self._value else theme.PANEL
        self.create_polygon(points, fill=fill, outline=border, width=1)
        if self._value:
            self.create_line(
                4, size / 2, size / 2 - 1, size - 4, fill=theme.BG, width=max(2, self.m.px(2))
            )
            self.create_line(
                size / 2 - 1, size - 4, size - 3, 4, fill=theme.BG, width=max(2, self.m.px(2))
            )


# --- badge -------------------------------------------------------------------


class Badge(_CanvasBase):
    def __init__(self, master: tk.Misc, text: str, *, m: Metrics, color: str = theme.CYAN):
        self.m = m
        self._text = text
        self._color = color
        super().__init__(master)
        self._font = fonts.font(self, theme.FONT_SEMI, m.px(10))
        self._resize()

    def set(self, text: str, color: str) -> None:
        self._text, self._color = text, color
        self._resize()

    def _resize(self) -> None:
        width = text_width(self._font, self._text, self.m.px(1.2)) + self.m.px(14)
        self.configure(width=int(width), height=self.m.px(20))
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        w, h = int(self.cget("width")), int(self.cget("height"))
        points = panel_points(0.5, 0.5, w - 0.5, h - 0.5, self.m.px(5))
        self.create_polygon(
            points, fill=theme.blend(self._color, theme.PANEL, 0.12), outline=self._color, width=1
        )
        tracked_text(
            self, w / 2, h / 2 + 1, self._text, self._font, self._color, self.m.px(1.2), "center"
        )


# --- progress ----------------------------------------------------------------


class ProgressBar(_CanvasBase):
    def __init__(self, master: tk.Misc, *, m: Metrics, height: float = 6):
        self.m = m
        self.h = m.px(height)
        self._value = 0.0
        super().__init__(master, height=self.h)
        self.bind("<Configure>", lambda _event: self._draw())
        self._draw()

    def set(self, value: float) -> None:
        self._value = max(0.0, min(1.0, value))
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        w = self.winfo_width() or self.winfo_reqwidth()
        h = self.h
        self.create_rectangle(0, 0, w, h, fill=theme.shade(theme.PANEL, 0.2), outline="")
        filled = max(0, int(w * self._value))
        if filled:
            slant = min(filled, self.m.px(8))
            self.create_polygon(filled, 0, filled, h, 0, h, 0, 0, fill=theme.BLUE, outline="")
            self.create_polygon(
                max(0, filled - slant),
                0,
                filled,
                0,
                filled,
                h,
                filled - slant,
                h,
                fill=theme.CYAN,
                outline="",
            )


# --- panel -------------------------------------------------------------------


class ShardPanel(_CanvasBase):
    """A cut-corner surface with a title and a body frame for real widgets."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        m: Metrics,
        title: str = "",
        cut: float = 16,
        fill: str = theme.PANEL,
        accent: bool = True,
    ) -> None:
        self.m = m
        self.title = title
        self.cut = m.px(cut)
        self.fill = fill
        self._accent = accent
        super().__init__(master)
        self._title_font = fonts.font(self, theme.FONT_DISPLAY, m.px(11))
        self.body = tk.Frame(self, bg=fill)
        self._window = self.create_window(0, 0, window=self.body, anchor="nw")
        self.bind("<Configure>", self._on_configure)

    def set_title(self, title: str) -> None:
        self.title = title
        self._draw()

    def _on_configure(self, _event=None) -> None:
        self._draw()

    def _draw(self) -> None:
        width = self.winfo_width()
        height = self.winfo_height()
        if width <= 1 or height <= 1:
            return
        self.delete("bg")
        points = panel_points(0.5, 0.5, width - 0.5, height - 0.5, self.cut)
        self.create_polygon(
            points,
            fill=self.fill,
            outline=theme.blend(theme.WHITE, self.fill, 0.14),
            width=1,
            tags="bg",
        )
        if self._accent:
            # thin electric shard on the cut edge
            self.create_polygon(
                width - self.cut,
                0,
                width,
                0,
                width,
                self.cut,
                fill=theme.blend(theme.CYAN, self.fill, 0.75),
                outline="",
                tags="bg",
            )
        pad = self.m.px(16)
        top = pad
        if self.title:
            tracked_text(
                self,
                pad,
                self.m.px(18),
                self.title,
                self._title_font,
                theme.DIM,
                self.m.px(1.6),
                "w",
                ("bg",),
            )
            self.create_line(
                pad + text_width(self._title_font, self.title, self.m.px(1.6)) + self.m.px(12),
                self.m.px(18),
                width - self.m.px(18),
                self.m.px(18),
                fill=theme.blend(theme.WHITE, self.fill, 0.08),
                width=1,
                tags="bg",
            )
            top = self.m.px(32)
        self.coords(self._window, pad, top)
        self.itemconfigure(
            self._window, width=max(1, width - pad * 2), height=max(1, height - top - pad)
        )
        self.tag_lower("bg")


# --- scroll area -------------------------------------------------------------


class ScrollArea(tk.Frame):
    """A dark scroll region with a thin custom scrollbar."""

    def __init__(self, master: tk.Misc, *, m: Metrics, bg: str = theme.PANEL) -> None:
        super().__init__(master, bg=bg)
        self.m = m
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar = tk.Canvas(self, bg=bg, width=m.px(6), highlightthickness=0, bd=0)
        self.bar.pack(side="right", fill="y", padx=(m.px(6), 0))
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.bar.bind("<Button-1>", self._bar_click)
        self.bar.bind("<B1-Motion>", self._bar_drag)
        self._bind_wheel(self)
        self._dragging = False

    def _bind_wheel(self, widget: tk.Misc) -> None:
        widget.bind("<MouseWheel>", self._wheel, add="+")
        widget.bind("<Button-4>", lambda e: self._scroll(-1), add="+")
        widget.bind("<Button-5>", lambda e: self._scroll(1), add="+")

    def _on_inner(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._draw_bar()

    def _on_canvas(self, event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)
        self._draw_bar()

    def _wheel(self, event) -> None:
        delta = -1 if event.delta > 0 else 1
        self._scroll(delta)

    def _scroll(self, steps: int) -> None:
        self.canvas.yview_scroll(steps, "units")
        self._draw_bar()

    def _fraction(self) -> tuple[float, float]:
        try:
            return self.canvas.yview()
        except tk.TclError:
            return 0.0, 1.0

    def _draw_bar(self) -> None:
        self.bar.delete("all")
        height = self.bar.winfo_height()
        if height <= 1:
            return
        first, last = self._fraction()
        if last - first >= 0.999:
            return
        top = first * height
        bottom = max(top + self.m.px(18), last * height)
        self.bar.create_rectangle(
            self.m.px(2),
            top,
            self.m.px(5),
            bottom,
            fill=theme.blend(theme.CYAN, theme.PANEL, 0.6),
            outline="",
        )

    def _bar_click(self, event) -> None:
        self._dragging = True
        self._jump(event.y)

    def _bar_drag(self, event) -> None:
        if self._dragging:
            self._jump(event.y)

    def _jump(self, y: int) -> None:
        height = max(1, self.bar.winfo_height())
        self.canvas.yview_moveto(max(0.0, min(1.0, y / height)))
        self._draw_bar()

    def refresh(self) -> None:
        self.update_idletasks()
        self._on_inner()
        self._draw_bar()
