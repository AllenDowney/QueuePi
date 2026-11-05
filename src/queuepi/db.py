"""Order storage and the order lifecycle.

All the rules live here; the web server is a thin layer on top.

An order has a two-digit number (the last two digits of the receipt) and a
status. Active orders (waiting or ready) appear on the board. Picked-up,
cancelled, and cleared orders are archived: they stay in the table so we can
measure preparation and pickup times later.

The database enforces the invariants itself, so they hold even when several
phones act at once:

- numbers are 0-99;
- statuses are from a fixed set;
- at most one *active* order per number (a partial unique index), so a number
  can be reused once its earlier order is picked up or cancelled.

Every change is one transaction that updates the order and appends a row to
`events`. A transition names the status the client expected to see, so if two
employees tap the same order, the second one gets a `Conflict` instead of
silently overwriting the first.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

WAITING = "waiting"
READY = "ready"
PICKED_UP = "picked_up"
CANCELLED = "cancelled"  # entered by mistake
CLEARED = "cleared"  # still active at the end-of-day reset

STATUSES = (WAITING, READY, PICKED_UP, CANCELLED, CLEARED)
ACTIVE = (WAITING, READY)

# Legal transitions, and the timestamp column each one sets (or clears, when
# it's an undo). Anything not listed is illegal.
TRANSITIONS: dict[tuple[str, str], tuple[str, str]] = {
    (WAITING, READY): ("set", "ready_at"),
    (READY, PICKED_UP): ("set", "picked_up_at"),
    (WAITING, CANCELLED): ("set", "cancelled_at"),
    # undo
    (READY, WAITING): ("clear", "ready_at"),
    (PICKED_UP, READY): ("clear", "picked_up_at"),
    (CANCELLED, WAITING): ("clear", "cancelled_at"),
}

SCHEMA_VERSION = 1

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS orders (
    id           INTEGER PRIMARY KEY,
    number       INTEGER NOT NULL CHECK (number BETWEEN 0 AND 99),
    status       TEXT    NOT NULL CHECK (status IN {STATUSES!r}),
    created_at   TEXT    NOT NULL,
    ready_at     TEXT,
    picked_up_at TEXT,
    cancelled_at TEXT,
    cleared_at   TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS one_active_order_per_number
    ON orders (number) WHERE status IN {ACTIVE!r};

CREATE INDEX IF NOT EXISTS orders_by_status ON orders (status);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    order_id    INTEGER NOT NULL REFERENCES orders (id),
    from_status TEXT,             -- NULL when the order was created
    to_status   TEXT NOT NULL,
    at          TEXT NOT NULL,
    client      TEXT              -- which device did it, if known
);
"""


# --- errors ------------------------------------------------------------------


class QueueError(Exception):
    """Base class for errors the API reports to clients."""

    code = "error"


class InvalidNumber(QueueError):
    code = "invalid_number"


class NotFound(QueueError):
    code = "not_found"


class InvalidTransition(QueueError):
    code = "invalid_transition"


class Conflict(QueueError):
    """The order's status isn't what the client expected (someone else got there first)."""

    code = "conflict"


class DuplicateNumber(QueueError):
    """The number is already on the board."""

    code = "duplicate"


# --- numbers -----------------------------------------------------------------


def parse_number(text: str | int) -> int:
    """Parse a receipt number as an employee types it: one or two digits.

    "7" and "07" both mean order 07. Anything else is an error. Integers are
    accepted too, for convenience in tests and scripts.
    """
    if isinstance(text, bool):
        raise InvalidNumber(f"not an order number: {text!r}")
    if isinstance(text, int):
        n = text
    else:
        s = str(text).strip()
        if not (1 <= len(s) <= 2 and s.isascii() and s.isdigit()):
            raise InvalidNumber(f"order number must be one or two digits, got {text!r}")
        n = int(s)
    if not 0 <= n <= 99:
        raise InvalidNumber(f"order number must be 00-99, got {text!r}")
    return n


def format_number(n: int) -> str:
    """Format an order number for display: always two digits."""
    return f"{n:02d}"


NEXT_COUNT = 3


def next_numbers(last: int | None, active: set[int], count: int = NEXT_COUNT) -> list[str]:
    """The numbers the register is most likely to add next: the ones after
    the last order added, wrapping 99 -> 00, skipping numbers already on the
    board. Receipts are numbered in sequence, but not every receipt becomes
    an order, so offer a few. Empty if nothing has been added yet."""
    if last is None:
        return []
    out = []
    n = last
    for _ in range(100):
        n = (n + 1) % 100
        if n not in active:
            out.append(format_number(n))
            if len(out) == count:
                break
    return out


# --- records -----------------------------------------------------------------


@dataclass(frozen=True)
class Order:
    id: int
    number: str  # two digits, e.g. "07"
    status: str
    created_at: str
    ready_at: str | None
    picked_up_at: str | None
    cancelled_at: str | None
    cleared_at: str | None
    # The latest change to this order, so a client can offer to undo what it
    # just did. Filled in by single-order lookups; None in board listings.
    last_event_id: int | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Order:
        d = dict(row)
        d["number"] = format_number(d["number"])
        return cls(**d)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Event:
    id: int
    order_id: int
    number: str
    from_status: str | None
    to_status: str
    at: str
    client: str | None
    undoable: bool

    def to_dict(self) -> dict:
        return asdict(self)


def utc_now() -> str:
    """Current time as an ISO 8601 UTC string with milliseconds."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# --- the store ---------------------------------------------------------------


class OrderStore:
    """The order database.

    Opens a new SQLite connection for each operation, so one store can be
    shared by the web server's worker threads.
    """

    def __init__(self, path: str | Path, clock: Callable[[], str] = utc_now):
        self.path = str(path)
        self.clock = clock
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"{self.path} has schema version {version}; "
                    f"this QueuePi understands up to {SCHEMA_VERSION}"
                )
            conn.executescript(SCHEMA)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            # FULL: each commit is on disk before we report success. Cheap at
            # a few writes a minute, and safer if the power is pulled.
            conn.execute("PRAGMA synchronous = FULL")
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """A write transaction. IMMEDIATE takes the write lock up front, so
        concurrent writers queue (up to the timeout) instead of failing."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    # --- reads ---

    def get(self, order_id: int) -> Order:
        with self._connect() as conn:
            return self._get(conn, order_id)

    def board(self) -> dict:
        """Everything on the board: waiting orders in the order they were
        entered, ready orders in the order they became ready.

        `version` increases with every change, so clients can skip redrawing
        when nothing changed.
        """
        with self._connect() as conn:
            # One read transaction, so the lists and version are consistent.
            conn.execute("BEGIN")
            waiting = conn.execute(
                "SELECT * FROM orders WHERE status = ? ORDER BY created_at, id", (WAITING,)
            ).fetchall()
            ready = conn.execute(
                "SELECT * FROM orders WHERE status = ? ORDER BY ready_at, id", (READY,)
            ).fetchall()
            version = conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
            # The last real order (a mistaken one, now cancelled, doesn't count).
            last = conn.execute(
                "SELECT number FROM orders WHERE status != ? ORDER BY id DESC LIMIT 1",
                (CANCELLED,),
            ).fetchone()
            conn.execute("COMMIT")
        active = {r["number"] for r in waiting} | {r["number"] for r in ready}
        return {
            "waiting": [Order.from_row(r).to_dict() for r in waiting],
            "ready": [Order.from_row(r).to_dict() for r in ready],
            "next": next_numbers(last[0] if last else None, active),
            "version": version,
            "server_time": self.clock(),
        }

    def recent_events(self, limit: int = 10) -> list[Event]:
        """The most recent changes, newest first, marking which can be undone.

        An event can be undone if the order is still in the state that event
        left it in, and the reverse transition is legal.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT e.*, o.number, o.status AS current_status
                FROM events e JOIN orders o ON o.id = e.order_id
                ORDER BY e.id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            Event(
                id=r["id"],
                order_id=r["order_id"],
                number=format_number(r["number"]),
                from_status=r["from_status"],
                to_status=r["to_status"],
                at=r["at"],
                client=r["client"],
                undoable=(
                    r["current_status"] == r["to_status"]
                    and _undo_target(r["from_status"], r["to_status"]) is not None
                ),
            )
            for r in rows
        ]

    # --- writes ---

    def add(self, number: str | int, client: str | None = None) -> Order:
        """Put a new order on the board, as waiting."""
        n = parse_number(number)
        now = self.clock()
        with self._transaction() as conn:
            try:
                cur = conn.execute(
                    "INSERT INTO orders (number, status, created_at) VALUES (?, ?, ?)",
                    (n, WAITING, now),
                )
            except sqlite3.IntegrityError as e:
                raise DuplicateNumber(f"{format_number(n)} is already on the board") from e
            order_id = cur.lastrowid
            self._log(conn, order_id, None, WAITING, now, client)
            return self._get(conn, order_id)

    def transition(
        self, order_id: int, to: str, expect: str, client: str | None = None
    ) -> Order:
        """Move an order from `expect` to `to`.

        Raises InvalidTransition if that move isn't allowed, NotFound if there
        is no such order, Conflict if the order isn't in state `expect`
        (someone else changed it first), and DuplicateNumber if an undo would
        put a second order with the same number on the board.
        """
        rule = TRANSITIONS.get((expect, to))
        if rule is None:
            raise InvalidTransition(f"can't go from {expect} to {to}")
        action, column = rule
        now = self.clock()
        value = now if action == "set" else None
        with self._transaction() as conn:
            try:
                cur = conn.execute(
                    f"UPDATE orders SET status = ?, {column} = ? WHERE id = ? AND status = ?",
                    (to, value, order_id, expect),
                )
            except sqlite3.IntegrityError as e:
                number = self._get(conn, order_id).number
                raise DuplicateNumber(
                    f"can't restore {number}: another order {number} is on the board"
                ) from e
            if cur.rowcount == 0:
                current = self._get(conn, order_id)  # raises NotFound
                raise Conflict(
                    f"order {current.number} is {current.status}, not {expect}"
                )
            self._log(conn, order_id, expect, to, now, client)
            return self._get(conn, order_id)

    def undo(self, event_id: int, client: str | None = None) -> Order:
        """Reverse one event, if the order is still where that event left it."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise NotFound(f"no event {event_id}")
        target = _undo_target(row["from_status"], row["to_status"])
        if target is None:
            raise InvalidTransition(f"can't undo {row['from_status']} -> {row['to_status']}")
        return self.transition(row["order_id"], target, expect=row["to_status"], client=client)

    def reset(self, client: str | None = None) -> int:
        """End of day: archive every active order as cleared. Returns how many.

        Nothing is deleted; cleared orders stay in the table with their times.
        """
        now = self.clock()
        with self._transaction() as conn:
            rows = conn.execute(
                f"SELECT id, status FROM orders WHERE status IN {ACTIVE!r}"
            ).fetchall()
            for r in rows:
                conn.execute(
                    "UPDATE orders SET status = ?, cleared_at = ? WHERE id = ?",
                    (CLEARED, now, r["id"]),
                )
                self._log(conn, r["id"], r["status"], CLEARED, now, client)
        return len(rows)

    # --- helpers ---

    @staticmethod
    def _get(conn: sqlite3.Connection, order_id: int) -> Order:
        row = conn.execute(
            "SELECT *, (SELECT MAX(id) FROM events WHERE order_id = orders.id) AS last_event_id"
            " FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()
        if row is None:
            raise NotFound(f"no order {order_id}")
        return Order.from_row(row)

    @staticmethod
    def _log(
        conn: sqlite3.Connection,
        order_id: int,
        from_status: str | None,
        to_status: str,
        at: str,
        client: str | None,
    ) -> None:
        conn.execute(
            "INSERT INTO events (order_id, from_status, to_status, at, client)"
            " VALUES (?, ?, ?, ?, ?)",
            (order_id, from_status, to_status, at, client),
        )


def _undo_target(from_status: str | None, to_status: str) -> str | None:
    """The status that undoes the change from_status -> to_status, or None.

    Creating an order is undone by cancelling it. An undo is itself undoable
    (the forward transition again).
    """
    if from_status is None:
        return CANCELLED if to_status == WAITING else None
    if (to_status, from_status) in TRANSITIONS:
        return from_status
    return None
