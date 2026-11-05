"""The ready animation's timing, geometry and bookkeeping (no window)."""

import random

from queuepi import animation as a


def board(*ready, waiting=()):
    mk = lambda items, st: [{"id": i, "number": n, "status": st} for i, n in items]
    return {"ready": mk(ready, "ready"), "waiting": mk(waiting, "waiting")}


def test_watcher_first_board_is_baseline():
    w = a.ReadyWatcher()
    assert w.update(board((1, "47"), (2, "13")), True) == []


def test_watcher_reports_new_ready_in_order():
    w = a.ReadyWatcher()
    w.update(board((1, "47")), True)
    assert w.update(board((1, "47"), (2, "13"), (3, "88")), True) == ["13", "88"]
    assert w.update(board((1, "47"), (2, "13"), (3, "88")), True) == []


def test_watcher_ignores_pickup_and_undo_but_celebrates_again():
    w = a.ReadyWatcher()
    w.update(board((1, "47"), (2, "13")), True)
    assert w.update(board((2, "13")), True) == []  # 47 picked up
    assert w.update(board(), True) == []  # 13 sent back (undo)
    assert w.update(board((2, "13")), True) == ["13"]  # ready again


def test_watcher_rebaselines_after_disconnect():
    w = a.ReadyWatcher()
    w.update(board((1, "47")), True)
    assert w.update(board((1, "47")), False) == []
    # Back online with orders that became ready while we were away: no fuss.
    assert w.update(board((1, "47"), (2, "13")), True) == []
    assert w.update(board((1, "47"), (2, "13"), (3, "88")), True) == ["88"]


def test_center_layout_fits_and_shrinks_with_count():
    area = (0, 250, 1920, 830)
    (c,), one = a.center_layout(1, area)
    assert c == (960, 665) and one == a.MAX_CENTER_SIZE
    centers, three = a.center_layout(3, area)
    assert three < one
    assert [round(x) for x, _ in centers] == sorted(round(x) for x, _ in centers)
    assert all(three * 1.1 <= (1920 - 120) / 3 for _ in centers)


def test_flight_path():
    f = a.Flight("47", start=(100, 600), start_size=100, center=(960, 665), center_size=600)
    end, end_size = (1500, 400), 200
    assert f.at(0, end, end_size) == (100, 600, 100)
    x, y, size = f.at(a.GROW + a.HOLD / 2, end, end_size)
    assert (x, y) == (960, 665) and size > 600  # swelling at the center
    assert f.at(a.GROW + a.HOLD + a.FLY, end, end_size) == (1500, 400, 200)


def test_celebration_timeline():
    cel = a.plan(["47", "13"], {"47": ((100, 600), 100)}, ((500, 600), 80),
                 (0, 250, 1920, 830), now=10.0, rng=random.Random(0), colors=4)
    assert cel.numbers == ["47", "13"]
    assert cel.flights[1].start == (500, 600)  # not in the waiting panel: fallback
    assert cel.dim(10.0) == 0 and cel.dim(10.0 + a.GROW + 0.1) == a.DIM_ALPHA
    assert cel.confetti_time(10.0) is None
    assert abs(cel.confetti_time(10.0 + a.GROW + 0.5) - 0.5) < 1e-9
    assert not cel.landed(10.0 + a.GROW + a.HOLD)
    assert cel.landed(10.0 + a.GROW + a.HOLD + a.FLY + 1e-9)
    assert cel.done(10.0 + a.TOTAL + 1e-9) and not cel.done(10.0 + a.TOTAL - 0.01)
    assert 0 < len(cel.confetti) <= 240


def test_confetti_rises_then_falls():
    p = a.Particle(x=0, y=0, vx=0, vy=-1000, angle=0, spin=1, color=0, w=10, h=6)
    assert p.at(0) == (0, 0, 0)
    assert p.at(0.3)[1] < 0  # going up
    assert p.at(3.0)[1] > p.at(0.3)[1]  # and coming down
