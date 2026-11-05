"""The order lifecycle, tested directly against OrderStore."""

import threading

import pytest

from queuepi.db import (
    CANCELLED,
    CLEARED,
    PICKED_UP,
    READY,
    WAITING,
    Conflict,
    DuplicateNumber,
    InvalidNumber,
    InvalidTransition,
    NotFound,
    OrderStore,
    format_number,
    parse_number,
)


def numbers(orders):
    return [o["number"] for o in orders]


# --- adding orders ---


def test_add_order(store):
    order = store.add("42")
    assert order.number == "42"
    assert order.status == WAITING
    assert order.created_at is not None
    assert order.ready_at is None and order.picked_up_at is None

    board = store.board()
    assert numbers(board["waiting"]) == ["42"]
    assert board["ready"] == []


def test_waiting_in_order_of_creation(store):
    for n in ["17", "03", "88", "50"]:
        store.add(n)
    assert numbers(store.board()["waiting"]) == ["17", "03", "88", "50"]


# --- two-digit numbers and leading zeros ---


@pytest.mark.parametrize(
    "text, expected",
    [("00", "00"), ("07", "07"), ("7", "07"), ("0", "00"), ("99", "99"), (" 42 ", "42"), (5, "05")],
)
def test_leading_zeros(store, text, expected):
    assert store.add(text).number == expected
    assert numbers(store.board()["waiting"]) == [expected]


@pytest.mark.parametrize("bad", ["", "100", "007", "-1", "4a", "١٢", "1.5", 100, -1, True, None])
def test_invalid_numbers(store, bad):
    with pytest.raises(InvalidNumber):
        store.add(bad)
    assert store.board()["waiting"] == []


def test_format_and_parse():
    assert [format_number(n) for n in (0, 7, 10, 99)] == ["00", "07", "10", "99"]
    assert parse_number("07") == parse_number("7") == 7


# --- duplicate active numbers and wraparound ---


def test_duplicate_waiting_rejected(store):
    store.add("42")
    with pytest.raises(DuplicateNumber):
        store.add("42")
    assert numbers(store.board()["waiting"]) == ["42"]


def test_duplicate_ready_rejected(store):
    o = store.add("42")
    store.transition(o.id, READY, expect=WAITING)
    with pytest.raises(DuplicateNumber):
        store.add("42")


def test_7_and_07_are_the_same_number(store):
    store.add("07")
    with pytest.raises(DuplicateNumber):
        store.add("7")


@pytest.mark.parametrize("finish", ["picked_up", "cancelled", "cleared"])
def test_number_reusable_after_order_done(store, finish):
    o = store.add("42")
    if finish == "picked_up":
        store.transition(o.id, READY, expect=WAITING)
        store.transition(o.id, PICKED_UP, expect=READY)
    elif finish == "cancelled":
        store.transition(o.id, CANCELLED, expect=WAITING)
    else:
        store.reset()
    again = store.add("42")
    assert again.id != o.id
    assert numbers(store.board()["waiting"]) == ["42"]


def test_wraparound_through_a_day(store):
    """Receipts 1000-1249: the last two digits wrap around 2.5 times. Each order
    is picked up before its number comes round again."""
    for receipt in range(1000, 1250):
        o = store.add(str(receipt)[-2:])
        store.transition(o.id, READY, expect=WAITING)
        store.transition(o.id, PICKED_UP, expect=READY)
    board = store.board()
    assert board["waiting"] == [] and board["ready"] == []


# --- transitions ---


def test_full_lifecycle_sets_timestamps(store):
    o = store.add("42")
    o = store.transition(o.id, READY, expect=WAITING)
    assert o.status == READY and o.ready_at > o.created_at
    o = store.transition(o.id, PICKED_UP, expect=READY)
    assert o.status == PICKED_UP and o.picked_up_at > o.ready_at
    board = store.board()
    assert board["waiting"] == [] and board["ready"] == []


def test_ready_orders_listed_separately(store):
    a, b, c = (store.add(n) for n in ["10", "20", "30"])
    store.transition(b.id, READY, expect=WAITING)
    board = store.board()
    assert numbers(board["waiting"]) == ["10", "30"]
    assert numbers(board["ready"]) == ["20"]


@pytest.mark.parametrize(
    "start, to",
    [
        (WAITING, PICKED_UP),  # must be ready first
        (READY, CANCELLED),
        (PICKED_UP, WAITING),
        (PICKED_UP, CANCELLED),
        (CANCELLED, READY),
        (WAITING, WAITING),
        (WAITING, CLEARED),  # only reset() clears
        (WAITING, "preparing"),
    ],
)
def test_illegal_transitions(store, start, to):
    o = store.add("42")
    if start in (READY, PICKED_UP):
        store.transition(o.id, READY, expect=WAITING)
    if start == PICKED_UP:
        store.transition(o.id, PICKED_UP, expect=READY)
    if start == CANCELLED:
        store.transition(o.id, CANCELLED, expect=WAITING)
    with pytest.raises(InvalidTransition):
        store.transition(o.id, to, expect=start)
    assert store.get(o.id).status == start


def test_stale_expect_is_a_conflict(store):
    o = store.add("42")
    store.transition(o.id, READY, expect=WAITING)
    with pytest.raises(Conflict):
        store.transition(o.id, READY, expect=WAITING)  # someone already did it
    assert store.get(o.id).status == READY


def test_unknown_order(store):
    with pytest.raises(NotFound):
        store.transition(999, READY, expect=WAITING)
    with pytest.raises(NotFound):
        store.get(999)


def test_out_of_order_completion(store):
    orders = [store.add(n) for n in ["01", "02", "03", "04"]]
    store.transition(orders[2].id, READY, expect=WAITING)
    store.transition(orders[0].id, READY, expect=WAITING)
    store.transition(orders[2].id, PICKED_UP, expect=READY)
    board = store.board()
    assert numbers(board["waiting"]) == ["02", "04"]
    assert numbers(board["ready"]) == ["01"]


def test_ready_listed_in_order_they_became_ready(store):
    a, b, c = (store.add(n) for n in ["10", "20", "30"])
    for o in (c, a, b):
        store.transition(o.id, READY, expect=WAITING)
    assert numbers(store.board()["ready"]) == ["30", "10", "20"]


# --- undo ---


def test_undo_ready(store):
    o = store.add("42")
    store.transition(o.id, READY, expect=WAITING)
    event = store.recent_events()[0]
    assert event.to_status == READY and event.undoable
    o = store.undo(event.id)
    assert o.status == WAITING and o.ready_at is None


def test_undo_pickup(store):
    o = store.add("42")
    store.transition(o.id, READY, expect=WAITING)
    store.transition(o.id, PICKED_UP, expect=READY)
    o = store.undo(store.recent_events()[0].id)
    assert o.status == READY and o.picked_up_at is None and o.ready_at is not None


def test_undo_add_cancels_and_redo_restores(store):
    o = store.add("42")
    o = store.undo(store.recent_events()[0].id)
    assert o.status == CANCELLED
    assert store.board()["waiting"] == []
    o = store.undo(store.recent_events()[0].id)  # undo the undo
    assert o.status == WAITING and o.cancelled_at is None


def test_undo_stale_event_is_a_conflict(store):
    o = store.add("42")
    store.transition(o.id, READY, expect=WAITING)
    ready_event = store.recent_events()[0]
    store.transition(o.id, PICKED_UP, expect=READY)
    assert not store.recent_events()[1].undoable
    with pytest.raises(Conflict):
        store.undo(ready_event.id)
    assert store.get(o.id).status == PICKED_UP


def test_undo_twice_is_a_conflict(store):
    o = store.add("42")
    store.transition(o.id, READY, expect=WAITING)
    event = store.recent_events()[0]
    store.undo(event.id)
    with pytest.raises(Conflict):
        store.undo(event.id)


def test_undo_cannot_create_duplicate(store):
    """42 is picked up, a new 42 comes in, then someone undoes the pickup."""
    old = store.add("42")
    store.transition(old.id, READY, expect=WAITING)
    store.transition(old.id, PICKED_UP, expect=READY)
    pickup = store.recent_events()[0]
    store.add("42")
    with pytest.raises(DuplicateNumber):
        store.undo(pickup.id)
    assert store.get(old.id).status == PICKED_UP


def test_reset_is_not_undoable(store):
    store.add("42")
    store.reset()
    event = store.recent_events()[0]
    assert event.to_status == CLEARED and not event.undoable
    with pytest.raises(InvalidTransition):
        store.undo(event.id)


def test_unknown_event(store):
    with pytest.raises(NotFound):
        store.undo(999)


def test_events_record_client(store):
    o = store.add("42", client="phone-a")
    store.transition(o.id, READY, expect=WAITING, client="phone-b")
    assert [e.client for e in store.recent_events()] == ["phone-b", "phone-a"]


# --- simultaneous employees ---


def race(n_threads, fn):
    """Run fn(i) in n_threads threads released at the same moment. Returns
    the results and the exceptions."""
    barrier = threading.Barrier(n_threads)
    results, errors = [], []
    lock = threading.Lock()

    def run(i):
        barrier.wait()
        try:
            r = fn(i)
            with lock:
                results.append(r)
        except Exception as e:
            with lock:
                errors.append(e)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results, errors


def test_simultaneous_add_same_number(db_path):
    """Several phones, each with its own store (connection), add 42 at once."""
    stores = [OrderStore(db_path) for _ in range(8)]
    results, errors = race(8, lambda i: stores[i].add("42", client=f"p{i}"))
    assert len(results) == 1
    assert len(errors) == 7 and all(isinstance(e, DuplicateNumber) for e in errors)
    assert numbers(stores[0].board()["waiting"]) == ["42"]


def test_simultaneous_mark_ready(db_path):
    stores = [OrderStore(db_path) for _ in range(8)]
    o = stores[0].add("42")
    results, errors = race(8, lambda i: stores[i].transition(o.id, READY, expect=WAITING))
    assert len(results) == 1
    assert len(errors) == 7 and all(isinstance(e, Conflict) for e in errors)
    events = [e for e in stores[0].recent_events() if e.to_status == READY]
    assert len(events) == 1


def test_simultaneous_different_orders(db_path):
    """Many phones adding different numbers: nothing lost."""
    stores = [OrderStore(db_path) for _ in range(10)]
    results, errors = race(10, lambda i: stores[i].add(str(i * 7)))
    assert errors == [] and len(results) == 10
    assert len(stores[0].board()["waiting"]) == 10


# --- restart ---


def test_orders_survive_restart(db_path):
    store = OrderStore(db_path)
    a = store.add("05")
    store.add("06")
    store.transition(a.id, READY, expect=WAITING)
    before = store.board()
    del store

    store = OrderStore(db_path)  # as after a crash or reboot
    after = store.board()
    assert after["waiting"] == before["waiting"]
    assert after["ready"] == before["ready"]
    assert after["version"] == before["version"]
    with pytest.raises(DuplicateNumber):
        store.add("06")


def test_newer_schema_refused(db_path):
    import sqlite3

    OrderStore(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with pytest.raises(RuntimeError):
        OrderStore(db_path)


# --- end of day ---


def test_reset(store):
    a, b, c = (store.add(n) for n in ["01", "02", "03"])
    store.transition(a.id, READY, expect=WAITING)
    store.transition(b.id, READY, expect=WAITING)
    store.transition(b.id, PICKED_UP, expect=READY)
    assert store.reset() == 2  # 01 (ready) and 03 (waiting)
    board = store.board()
    assert board["waiting"] == [] and board["ready"] == []
    assert store.get(a.id).status == CLEARED and store.get(a.id).cleared_at
    assert store.get(b.id).status == PICKED_UP  # archived orders untouched
    assert store.reset() == 0


# --- version ---


def test_version_increases_on_every_change(store):
    v0 = store.board()["version"]
    o = store.add("42")
    v1 = store.board()["version"]
    store.transition(o.id, READY, expect=WAITING)
    v2 = store.board()["version"]
    assert v0 < v1 < v2
    with pytest.raises(DuplicateNumber):
        store.add("42")
    assert store.board()["version"] == v2  # failed writes change nothing


# --- suggested next numbers ---------------------------------------------------


def test_next_numbers_pure():
    from queuepi.db import next_numbers
    assert next_numbers(None, set()) == []
    assert next_numbers(47, set()) == ["48", "49", "50"]
    assert next_numbers(98, set()) == ["99", "00", "01"]
    assert next_numbers(47, {48, 50}) == ["49", "51", "52"]
    assert next_numbers(5, set(range(100)) - {7}) == ["07"]
    assert next_numbers(5, set(range(100))) == []


def test_board_next_follows_last_added(store):
    assert store.board()["next"] == []
    store.add("47", "t")
    assert store.board()["next"] == ["48", "49", "50"]
    store.add("49", "t")  # a receipt skipped
    assert store.board()["next"] == ["50", "51", "52"]


def test_board_next_skips_numbers_on_board(store):
    store.add("49", "t")
    store.add("47", "t")
    assert store.board()["next"] == ["48", "50", "51"]


def test_board_next_ignores_mistakes(store):
    store.add("47", "t")
    bad = store.add("74", "t")
    store.transition(bad.id, "cancelled", expect="waiting", client="t")
    assert store.board()["next"] == ["48", "49", "50"]


def test_board_next_survives_pickup_and_reset(store):
    o = store.add("47", "t")
    store.transition(o.id, "ready", expect="waiting", client="t")
    store.transition(o.id, "picked_up", expect="ready", client="t")
    assert store.board()["next"] == ["48", "49", "50"]
    store.add("48", "t")
    store.reset("t")
    assert store.board()["next"] == ["49", "50", "51"]
