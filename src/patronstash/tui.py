"""A full-screen dashboard for PatronStash.

`layout()` turns a DashboardState into the screen's lines without touching
curses, so it can be tested; `run_dashboard()` draws those lines with curses
and handles the keys. Built for long creator lists: a totals line, a
scrollable table, and a pinned line for the file being downloaded.
"""

from __future__ import annotations

import curses
import time
from dataclasses import dataclass, field

from . import __version__
from .fmt import human_size, plural

BAR_WIDTH = 10


@dataclass
class CreatorRow:
    name: str
    kind: str  # "ok", "active", "error", "waiting", "locked"
    status: str
    posts: int = 0
    size: int = 0
    newest: str = "-"
    activity: str = ""


@dataclass
class ActiveDownload:
    creator: str
    filename: str
    done: int
    total: int | None
    speed: int


@dataclass
class DashboardState:
    rows: list[CreatorRow] = field(default_factory=list)
    active: ActiveDownload | None = None
    checking: str | None = None  # creator being checked, when nothing downloads
    next_pass_in: int | None = None  # seconds; None while a pass runs
    pass_progress: tuple[int, int] | None = None  # (creators done, total)
    notice: str = ""


@dataclass
class ViewState:
    selected: int = 0
    scroll: int = 0


# ── columns ─────────────────────────────────────────────────────────

# (title, width, right-aligned, value); the first two are always shown
_FIXED = [
    ("Creator", 20, False, lambda r: r.name),
    ("Status", 15, False, lambda r: r.status),
]
# optional columns, in the order they're dropped when space runs out
_OPTIONAL = [
    ("Newest", 10, False, lambda r: r.newest),
    ("Size", 9, True, lambda r: human_size(r.size) if r.size else "-"),
    ("Posts", 7, True, lambda r: f"{r.posts:,}"),
]
ACTIVITY_MIN = 12


def _columns(width: int):
    used = 1 + sum(w + 2 for _, w, _, _ in _FIXED)
    optional = list(reversed(_OPTIONAL))  # Posts, Size, Newest: kept in this order
    shown = []
    for col in optional:
        if used + col[1] + 2 + ACTIVITY_MIN <= width:
            shown.append(col)
            used += col[1] + 2
    order = {c[0]: i for i, c in enumerate(reversed(_OPTIONAL))}
    shown.sort(key=lambda c: order[c[0]])
    activity = width - used
    cols = _FIXED + shown
    if activity >= ACTIVITY_MIN:
        cols = cols + [("Activity", activity, False, lambda r: r.activity)]
    return cols


def _cell(text: str, width: int, right: bool) -> str:
    text = text if len(text) <= width else text[: max(width - 1, 0)] + "…"
    return text.rjust(width) if right else text.ljust(width)


def _row_text(values, cols) -> str:
    return " " + "  ".join(
        _cell(v, w, right) for v, (_, w, right, _) in zip(values, cols, strict=True)
    )


def _fit(text: str, width: int) -> str:
    return text[:width].ljust(width)


# ── layout ──────────────────────────────────────────────────────────


def _totals(state: DashboardState) -> str:
    rows = state.rows
    parts = [plural(len(rows), "creator")]
    active = sum(r.kind == "active" for r in rows)
    errors = sum(r.kind == "error" for r in rows)
    if active:
        parts.append(f"{active} active")
    if errors:
        parts.append(plural(errors, "error"))
    posts = sum(r.posts for r in rows)
    parts.append(f"{posts:,} post{'s' if posts != 1 else ''}")
    parts.append(human_size(sum(r.size for r in rows)))
    return " " + " • ".join(parts)


def _now_line(state: DashboardState) -> tuple[str, tuple]:
    a = state.active
    if a is not None:
        if a.total:
            fraction = min(a.done / a.total, 1.0)
            filled = int(fraction * BAR_WIDTH)
            bar = "#" * filled + "-" * (BAR_WIDTH - filled)
            amount = (
                f"[{bar}] {int(fraction * 100):>3}%  "
                f"{human_size(a.done)} / {human_size(a.total)}"
            )
        else:
            amount = human_size(a.done)
        return (
            f" ↓ {a.creator}  {a.filename}  {amount}  {human_size(a.speed)}/s",
            ("active",),
        )
    if state.checking:
        return f" … checking {state.checking}", ("dim",)
    return " Idle: nothing downloading", ("dim",)


def _footer(state: DashboardState) -> str:
    if state.pass_progress is not None:
        done, total = state.pass_progress
        first = f"pass running ({done}/{total} creators)"
    elif state.next_pass_in is not None:
        s = max(0, state.next_pass_in)
        first = (
            f"next pass in {s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
            if s >= 3600
            else f"next pass in {s // 60}:{s % 60:02d}"
        )
    else:
        first = "idle"
    return f" {first} • r pass now • ↑↓ PgUp PgDn scroll • q quit"


def layout(state: DashboardState, view: ViewState, width: int, height: int):
    """The screen as `height` lines of (text, tags), each at most `width` wide.

    Also keeps `view` valid: the selection in range and visible.
    """
    width = max(width, 1)
    title = f" PatronStash {__version__} — Patreon archive"
    top = [
        (_fit(title, width), ("title",)),
        (_fit(_totals(state), width), ("dim",)),
        (
            _fit(state.notice and " " + state.notice, width),
            ("error",) if state.notice else (),
        ),
    ]
    cols = _columns(width)
    header_text = _row_text([c[0] for c in cols], cols)

    now_text, now_tags = _now_line(state)
    bottom = [
        None,  # the separator, filled in once scrolling is known
        (_fit(now_text, width), now_tags),
        (_fit(_footer(state), width), ("dim",)),
    ]
    list_height = max(0, height - len(top) - 1 - len(bottom))

    rows = state.rows
    if rows:
        view.selected = min(max(view.selected, 0), len(rows) - 1)
    else:
        view.selected = 0
    if list_height:
        if view.selected < view.scroll:
            view.scroll = view.selected
        elif view.selected >= view.scroll + list_height:
            view.scroll = view.selected - list_height + 1
    view.scroll = max(0, min(view.scroll, max(0, len(rows) - list_height)))

    above = view.scroll
    below = max(0, len(rows) - view.scroll - list_height)
    more_up = f"↑ {above} more " if above else ""
    header = (
        _fit(
            header_text[: width - len(more_up)].ljust(width - len(more_up)) + more_up,
            width,
        ),
        ("header",),
    )
    more_down = f" ↓ {below} more " if below else ""
    bottom[0] = (_fit("─" * (width - len(more_down)) + more_down, width), ("dim",))

    body = []
    for index in range(view.scroll, min(len(rows), view.scroll + list_height)):
        row = rows[index]
        text = _fit(_row_text([c[3](row) for c in cols], cols), width)
        tags = (row.kind,) + (("selected",) if index == view.selected else ())
        body.append((text, tags))
    while len(body) < list_height:
        body.append((" " * width, ()))

    lines = top + [header] + body + bottom
    if len(lines) > height:  # a very small terminal: keep the top and the footer
        lines = lines[: max(height - 1, 0)] + lines[-1:] if height else []
    return [(_fit(text, width), tags) for text, tags in lines[:height]]


# ── curses ──────────────────────────────────────────────────────────

_COLORS = {
    "ok": curses.COLOR_GREEN,
    "active": curses.COLOR_YELLOW,
    "error": curses.COLOR_RED,
    "title": curses.COLOR_CYAN,
    "header": curses.COLOR_CYAN,
    "locked": curses.COLOR_MAGENTA,
}


def _attributes():
    attrs = {}
    curses.start_color()
    try:
        curses.use_default_colors()
        background = -1
    except curses.error:
        background = curses.COLOR_BLACK
    for number, (tag, color) in enumerate(_COLORS.items(), 1):
        curses.init_pair(number, color, background)
        attrs[tag] = curses.color_pair(number)
    attrs["title"] |= curses.A_BOLD
    attrs["header"] |= curses.A_BOLD
    attrs["dim"] = curses.A_DIM
    attrs["waiting"] = curses.A_DIM
    attrs["selected"] = curses.A_REVERSE
    return attrs


def _draw(screen, lines, attrs):
    last = len(lines) - 1
    for y, (text, tags) in enumerate(lines):
        attr = 0
        for tag in tags:
            attr |= attrs.get(tag, 0)
        if y == last:
            text = text[:-1]  # writing the bottom-right cell can scroll the screen
        try:
            screen.addstr(y, 0, text, attr)
        except curses.error:
            pass  # a terminal smaller than it reported; skip the line


KEYS = {
    curses.KEY_UP: -1,
    ord("k"): -1,
    curses.KEY_DOWN: 1,
    ord("j"): 1,
}


def run_dashboard(get_state, on_key=None, refresh=0.2) -> None:
    """Draw `get_state()` until the user presses q.

    `on_key(key)` gets any key the dashboard doesn't handle itself.
    """

    def main(screen):
        curses.curs_set(0)
        screen.nodelay(True)
        attrs = _attributes()
        view = ViewState()
        while True:
            height, width = screen.getmaxyx()
            state = get_state()
            screen.erase()
            _draw(screen, layout(state, view, width, height), attrs)
            screen.refresh()
            deadline = time.monotonic() + refresh
            while time.monotonic() < deadline:
                key = screen.getch()
                if key == -1:
                    time.sleep(0.02)
                    continue
                page = max(1, height - 8)
                if key in (ord("q"), ord("Q")):
                    return
                if key in KEYS:
                    view.selected += KEYS[key]
                elif key == curses.KEY_NPAGE:
                    view.selected += page
                elif key == curses.KEY_PPAGE:
                    view.selected -= page
                elif key == curses.KEY_HOME:
                    view.selected = 0
                elif key == curses.KEY_END:
                    view.selected = len(state.rows) - 1
                elif on_key is not None:
                    on_key(key)
                break  # redraw straight away after a key

    curses.wrapper(main)
