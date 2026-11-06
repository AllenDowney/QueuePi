"""The display's decisions about what to show (no window needed)."""

import pytest

from queuepi.display import (
    LOST_SECONDS,
    PAGE_SECONDS,
    STALE_SECONDS,
    WAITING_GRIDS,
    Grid,
    choose_grid,
    connection_state,
    make_view,
    page_of,
    sort_waiting,
)


def order(n):
    return {"number": n}


def board(waiting=(), ready=()):
    return {
        "waiting": [order(n) for n in waiting],
        "ready": [order(n) for n in ready],
        "version": 1,
        "join_url": "http://10.42.0.1:8000/",
    }


def test_waiting_sorted_numerically():
    assert sort_waiting(["42", "07", "99", "00", "10"]) == ["00", "07", "10", "42", "99"]


def test_ready_keeps_server_order():
    view = make_view(board(ready=["42", "07"]), last_ok=0.0, now=1.0)
    assert view.ready.numbers == ("42", "07")


@pytest.mark.parametrize(
    "n, grid",
    [
        (0, (4, 4, 1, 16)),
        (16, (4, 4, 1, 16)),
        (17, (5, 5, 1, 25)),
        (25, (5, 5, 1, 25)),
        (30, (6, 5, 1, 30)),
        (31, (4, 4, 2, 16)),  # two pages of 16 and 15, at the biggest size
        (34, (5, 5, 2, 17)),
        (61, (5, 5, 3, 21)),
    ],
)
def test_choose_grid(n, grid):
    assert choose_grid(n, WAITING_GRIDS) == Grid(*grid)


@pytest.mark.parametrize("n", range(0, 120))
def test_pages_hold_everything(n):
    grid = choose_grid(n, WAITING_GRIDS)
    assert grid.per_page * grid.pages >= n
    assert grid.per_page <= grid.cols * grid.rows


def test_pages_rotate_and_cover_everything():
    items = [f"{i:02d}" for i in range(40)]
    grid = choose_grid(len(items), WAITING_GRIDS)
    assert grid.pages == 2 and grid.per_page == 20
    first, p0 = page_of(items, grid, now=0.0)
    second, p1 = page_of(items, grid, now=PAGE_SECONDS + 0.1)
    again, p2 = page_of(items, grid, now=2 * PAGE_SECONDS + 0.1)
    assert (p0, p1, p2) == (0, 1, 0)
    assert first + second == items and again == first


def test_connection_states():
    assert connection_state(None, 100.0) == "connecting"
    assert connection_state(100.0, 100.0 + STALE_SECONDS - 1) == "ok"
    assert connection_state(100.0, 100.0 + STALE_SECONDS + 1) == "stale"
    assert connection_state(100.0, 100.0 + LOST_SECONDS + 1) == "lost"


def test_stale_still_shows_lists():
    view = make_view(board(waiting=["05"]), last_ok=0.0, now=STALE_SECONDS + 1)
    assert view.state == "stale" and view.waiting.numbers == ("05",)


def test_lost_hides_lists():
    view = make_view(board(waiting=["05"], ready=["06"]), last_ok=0.0, now=LOST_SECONDS + 1)
    assert view.state == "lost"
    assert view.waiting.numbers == () and view.ready.numbers == ()


def test_never_connected():
    view = make_view(None, last_ok=None, now=5.0)
    assert view.state == "connecting" and view.join_url is None


def test_same_board_same_view():
    """The main loop redraws only when the view changes."""
    b = board(waiting=["05", "17"], ready=["42"])
    assert make_view(b, 0.0, 1.0) == make_view(b, 0.5, 1.5)
    assert make_view(b, 0.0, 1.0) != make_view(board(waiting=["05"], ready=["42"]), 0.0, 1.0)


def test_poller_fetches_logo_once_per_version(monkeypatch):
    import io
    from queuepi import display

    calls = []

    def fake_urlopen(url, timeout):
        calls.append(url)
        if url.endswith("/api/board"):
            return io.BytesIO(b'{"waiting": [], "ready": [], "logo": "v1"}')
        return io.BytesIO(b"PNGDATA")

    monkeypatch.setattr(display.urllib.request, "urlopen", fake_urlopen)
    p = display.Poller("http://x")
    p.fetch_once()
    p.fetch_once()
    assert p.logo == ("v1", b"PNGDATA")
    assert calls.count("http://x/api/logo") == 1


def test_parse_xrandr_monitors():
    from queuepi.display import parse_xrandr_monitors
    text = """Monitors: 2
 0: +*eDP-1-1 1920/309x1080/173+0+0  eDP-1-1
 1: +DP-1-3 1920/527x1080/296+1920+0  DP-1-3
"""
    assert parse_xrandr_monitors(text) == [(1920, 1080, 0, 0), (1920, 1080, 1920, 0)]
    assert parse_xrandr_monitors("") == []
    assert parse_xrandr_monitors(" 0: +HDMI-1 1280/300x720/200+-1280+0  HDMI-1") == [(1280, 720, -1280, 0)]
