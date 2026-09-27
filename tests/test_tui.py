from patronstash.tui import (
    ActiveDownload,
    CreatorRow,
    DashboardState,
    ViewState,
    layout,
)


def make_state(n=40, active=None, **kwargs):
    rows = [
        CreatorRow(
            f"creator{i:02d}",
            "ok",
            "✓ Up to date",
            posts=i * 10,
            size=i * 1_000_000_000,
            newest="2026-09-2" + str(i % 10),
            activity="nothing new",
        )
        for i in range(n)
    ]
    return DashboardState(rows=rows, active=active, **kwargs)


def text_of(lines):
    return [text for text, _ in lines]


def test_fills_the_screen_exactly():
    lines = layout(make_state(), ViewState(), width=100, height=30)
    assert len(lines) == 30
    assert all(len(text) <= 100 for text, _ in lines)


def test_header_and_totals():
    state = make_state(3)
    state.rows[1].kind, state.rows[1].status = "error", "✗ Error"
    lines = text_of(layout(state, ViewState(), width=100, height=20))
    assert lines[0].startswith(" PatronStash")
    assert "3 creators" in lines[1] and "1 error" in lines[1]
    assert "30 posts" in lines[1]  # 0 + 10 + 20


def test_long_list_scrolls():
    state = make_state(40)
    view = ViewState(selected=35)
    lines = text_of(layout(state, view, width=100, height=20))
    assert any("creator35" in line for line in lines)
    assert not any("creator00" in line for line in lines)
    assert any("↑" in line for line in lines)  # more above


def test_selection_is_highlighted():
    lines = layout(make_state(5), ViewState(selected=2), width=100, height=20)
    selected = [text for text, attr in lines if "selected" in attr]
    assert len(selected) == 1 and "creator02" in selected[0]


def test_narrow_terminal_drops_columns():
    wide = text_of(layout(make_state(3), ViewState(), width=110, height=12))
    narrow = text_of(layout(make_state(3), ViewState(), width=50, height=12))
    header_wide = next(line for line in wide if "Creator" in line)
    header_narrow = next(line for line in narrow if "Creator" in line)
    assert "Newest" in header_wide and "Size" in header_wide
    assert "Newest" not in header_narrow
    assert all(len(line) <= 50 for line in narrow)


def test_active_download_line():
    active = ActiveDownload(
        "creator07", "youtube-AbCdEfGhIjK.mkv", 331_000_000, 541_000_000, 4_200_000
    )
    lines = text_of(layout(make_state(40, active=active), ViewState(), 100, 20))
    now = next(line for line in lines if "youtube-AbCdEfGhIjK.mkv" in line)
    assert "creator07" in now and "61%" in now and "4.2 MB/s" in now


def test_footer_shows_countdown_and_keys():
    state = make_state(3, next_pass_in=41)
    last = text_of(layout(state, ViewState(), 100, 20))[-1]
    assert "next pass in 0:41" in last and "q quit" in last


def test_tiny_terminal_does_not_crash():
    lines = layout(make_state(40), ViewState(selected=10), width=20, height=5)
    assert len(lines) == 5 and all(len(text) <= 20 for text, _ in lines)


def test_one_post_is_singular():
    state = DashboardState(rows=[CreatorRow("a", "ok", "✓", posts=1)])
    assert "1 post •" in text_of(layout(state, ViewState(), 100, 12))[1]
