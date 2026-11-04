# QueuePi: plan

A lightweight, self-hosted order queue and pickup display for small restaurants. First user: a sandwich shop. A Raspberry Pi 4 hosts a small web app for employees' phones and drives a 24-inch HDMI monitor that shows customers which orders are waiting and which are ready.

**Board:** Tasks 1–11 in `PROJECT_BOARD.md` (local, gitignored).
**Brief:** `docs/brief.md` (the original requirements). This plan records how we are interpreting them and what has been decided.

**Scope rule:** one small job done well. No POS integration, no user accounts, no time estimates, no cloud. Don't expand scope until the pilot (Milestone 4) has tested the basic workflow.

---

## Architecture (proposed)

```
 phones / tablet ──HTTP──▶ ┌──────────────────────────┐
  (register, kitchen)      │  queuepi server          │
                           │  FastAPI + uvicorn       │──▶ SQLite (WAL)
 Pygame display ──HTTP───▶ │  GET /api/board (public) │    queuepi.db
  (read-only, polls 1 s)   │  POST /api/... (PIN)     │
                           └──────────────────────────┘
```

Two processes, two systemd units: `queuepi-server` and `queuepi-display`. They share nothing but HTTP.

- **Server:** FastAPI, one uvicorn worker (SQLite and a single writer keep this simple). Serves the API, the employee pages (plain HTML/CSS/JS from `static/`, no build step, no CDN), and writes a small rotating log.
- **Database:** SQLite in WAL mode, `synchronous=FULL` (cheap at this write rate; safer on power loss). Each write is one transaction.
- **Display:** a separate Python process that polls `GET /api/board` and renders with Pygame. It never opens the database. See `docs/display_design.md` for how it reaches the HDMI output.
- **Employee pages:** poll the same board endpoint (every 1–2 s) so several phones stay in sync. No WebSockets or SSE at first; polling a tiny JSON document on a LAN is simpler and recovers by itself.

### Why the display uses HTTP, not SQLite

Recommended, pending confirmation:
- The display is exactly another client; one read path means one place to get "what's on the board" right (sorting, overflow, staleness).
- It can be developed against a fake or remote server, and the server can be tested without a display.
- A dead server is detectable (request fails), so the display can show a connection warning. Reading a SQLite file can't tell you whether anyone is still writing it.
- Cost: one HTTP request per second on localhost. Negligible.

## Order model

Table `orders`:

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | internal unique id |
| `number` | INTEGER 0–99 | `CHECK (number BETWEEN 0 AND 99)`; displayed as `f"{n:02d}"` |
| `status` | TEXT | `CHECK (status IN ('waiting','ready','picked_up','cancelled','cleared'))` |
| `created_at` | TEXT (UTC ISO) | |
| `ready_at` | TEXT, nullable | |
| `picked_up_at` | TEXT, nullable | |
| `cancelled_at` | TEXT, nullable | correction of a mistaken entry |
| `cleared_at` | TEXT, nullable | still active at the end-of-day reset |

- **No duplicate active numbers:** partial unique index `UNIQUE (number) WHERE status IN ('waiting','ready')`. The database enforces it, so two phones racing to add `42` can't both succeed. Once `42` is picked up or cancelled, `42` can be reused (wraparound).
- **Numbers are strings at the edges.** The API accepts and returns `"07"`, never `7`, so leading zeros can't be lost in JS or JSON. Internally an integer.
- **`picked_up` is archival.** Picked-up orders leave the board but stay in the table for timing data.
- **`cancelled`** is a proposed fourth status (not in the brief): the register's "correct a mistake" for an order entered in error. It keeps the record (useful for the pilot: how often do mistakes happen?) and is never displayed. Alternative: hard delete. To confirm.

### Transitions

| From → To | Action | Who |
|---|---|---|
| (new) → waiting | Add | register |
| waiting → ready | Mark ready | kitchen |
| ready → picked_up | Picked up | kitchen / counter |
| waiting → cancelled | Remove (entered by mistake) | register |
| ready → waiting | Undo ready | kitchen |
| picked_up → ready | Undo pickup (recent only) | kitchen |
| cancelled → waiting | Undo remove (recent only) | register |

Everything else is illegal and returns 409. Undo is "apply the reverse transition and clear the timestamp", guarded by the expected current status, so it's safe with several phones. Undoing back into an active state re-checks the duplicate-number constraint (e.g. `42` was picked up and a new `42` was added since).

**Concurrency:** every write names the order **and** the status the client saw (`POST /api/orders/{id}/ready` with `expect: "waiting"`). If two employees tap the same order, the second gets 409 and its page refreshes. No locks, no lost updates.

**Event log (proposed):** an append-only `events` table (order id, from, to, time, client tag) costs little and gives an audit trail for the pilot and a basis for an "undo last action" button. Decide in Task 4.

### End-of-day reset

One action (behind the PIN, with a confirmation step) that moves all active orders to `cleared` (distinct from `cancelled`, so the data tells them apart). Nothing is deleted. Not undoable. There's no business-day column: the day is derivable from `created_at`. A separate script can prune or export old days.

## API sketch

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/board` | none | `{waiting: [...], ready: [...], server_time, version}` — the display's only call |
| POST | `/api/orders` | PIN | add `{number: "42"}` → 201, or 409 if active |
| POST | `/api/orders/{id}/transition` | PIN | `{to, expect}` → 200 or 409 |
| GET | `/api/recent` | PIN | recently picked-up / cancelled, for undo |
| POST | `/api/reset` | PIN | end of day |
| GET | `/healthz` | none | systemd / monitoring |

`/api/board` is read-only and exposes nothing but numbers and statuses, so it can stay open on the LAN.

## Authentication

Staff PIN, set in a config file (`/etc/queuepi/config.toml` on the Pi, a local file in development). Entering the PIN once on a phone sets a long-lived signed cookie; write endpoints require it. No accounts. Not real security, just a guard against customers on the same Wi-Fi poking the API. If the Pi runs its own access point (Task 10), the Wi-Fi password is the first line of defense.

## Employee interfaces

- **Register page** (`/register`): one big numeric field (`inputmode="numeric"`, `pattern="[0-9]*"`, max 2 chars — a text input, not `type=number`, which drops leading zeros), a big Add button, Enter submits. After submit: clear, refocus, flash "Added 07". Duplicate → red "07 is already in the queue". Below: the last few added orders, each with a Remove button for mistakes.
- **Kitchen page** (`/kitchen`): Waiting orders as big buttons in creation order; tap → Ready. Ready orders in a separate section; tap → Picked up. A short "recent" strip with Undo. Undo rather than "are you sure?" dialogs: fast in the common case, recoverable in the rare one.
- **Combined page** (`/staff`, for a tablet): both on one screen. Likely cheap if the pages share one JS module and one CSS file; decide in Task 6.

## Customer display

1920×1080, dark background, very large light numerals.
- **Top band:** "Your order number is the last two digits of your receipt." With an example receipt, e.g. `Receipt #10347` with **47** highlighted.
- **Ready for pickup:** the largest numbers, in a distinct color (e.g. green), most prominent position.
- **Waiting:** smaller, neutral color.
- **Overflow (prototype):** waiting numbers in a fixed grid sized for readability (e.g. 5×4). If more, either show them in pages that rotate every few seconds ("1/2"), or shrink once to a second size and then page. Sort waiting numbers **numerically**, not by entry time, so the display doesn't imply a FIFO order. Customers look for their own number, and a numeric sort makes that easy. To try both in Task 7/8.
- **Staleness:** if the last successful poll is more than ~5 s old, show a clear banner ("Connection lost, updating…"); after ~30 s, hide the lists rather than show possibly wrong data.
- **Dev mode:** `--windowed` (scaled window) on the desktop; full screen on the Pi.
- **Join QR code:** the display shows a QR code for the staff pages (plus the address in text), because until a first phone is connected there is no other way in.
  - *Prototype:* always on screen, in a corner, so we can connect a phone at any time.
  - *Appliance (Task 9):* shown from boot until a logged-in phone dismisses it (`POST /api/setup/dismiss`). A staff-page button brings it back for adding another phone. A restart shows it again, which also reveals the address if the Pi's address ever changes.
  - The server reports the URL in `/api/board` (`join_url`): the `public_url` setting if set, else the LAN address of its default interface. The display draws the QR itself (`segno`, pure Python).

## Project structure (proposed)

```
QueuePi/
├── README.md  LICENSE  pyproject.toml  .gitignore
├── src/queuepi/
│   ├── __init__.py
│   ├── db.py          # schema, connection, transitions (the core logic)
│   ├── server.py      # FastAPI app, routes, auth
│   ├── config.py
│   ├── display.py     # Pygame client
│   └── static/        # register.html, kitchen.html, staff.js, staff.css
├── tests/
├── scripts/simulate.py   # fixture / busy-period generator
├── deploy/systemd/       # queuepi-server.service, queuepi-display.service
└── docs/
```

Drop `models.py` and `templates/` unless we need them: Pydantic request/response models can live in `server.py`, and the pages are static files that call the API.

**Dependencies:** `fastapi`, `uvicorn`, `pygame` (display only, optional extra `[display]`), `pytest`, `httpx` (tests). Python's built-in `sqlite3`, no ORM.

## Milestones

1. **Working local prototype** (Tasks 1–7): skeleton, backend, tests, register and kitchen pages, Pygame display in a window, simulation script. Done when the register and kitchen pages in two browser windows drive the Pygame window.
2. **Interface refinement** (Task 8): usability, layout, busy periods, error handling and undo, several simultaneous clients.
3. **Raspberry Pi deployment** (Tasks 3, 9, 10): Pi OS Lite, install procedure, systemd, full-screen display at boot, Wi-Fi, recovery testing.
4. **Pilot** (Task 11): observe, measure, simplify.

## Decisions

| Date | Decision | Notes |
|---|---|---|
| 2026-11-03 | License: MIT | |
| 2026-11-03 | Local git now; create public GitHub repo `AllenDowney/QueuePi` once the Milestone 1 prototype runs | |
| 2026-11-03 | Mistaken entries get status `cancelled` (kept, never displayed, undoable), not deleted | |
| 2026-11-03 | Display reads `GET /api/board` over HTTP; never opens SQLite | |
| 2026-11-03 | Waiting numbers on the display sorted numerically (no implied FIFO) | |
| 2026-11-03 | Append-only `events` table logs every status change (order, from, to, time, client tag) | Audit trail for the pilot; basis for "undo last action" |
| 2026-11-03 | Clients poll `/api/board` (display ~1 s, phones ~2 s; a write's response updates the acting page at once); no WebSockets/SSE | Recovers by itself after restarts, Wi-Fi drops and phone sleep; staleness is easy to detect. Revisit SSE if the pilot shows lag matters |
| 2026-11-04 | Phones join by scanning a QR code on the customer display. Prototype: always shown. Appliance: shown from boot until a staff phone dismisses it; staff page can bring it back. Then "Add to Home Screen" | Until a first phone is connected, no staff button can exist; see "Customer display" |
| 2026-11-04 | On the Pi: port 80, a reserved address on the shop router, and `queuepi.local` (mDNS) as a bonus | Android's `.local` support to test |
| 2026-11-03 | Pygame source on the Pi (apt vs. PyPI wheel) decided after the Task 3 hardware test | |
| 2026-11-03 | Hardware: 24" monitor arrives 2026-11-04, Pi 4 a few days later. Until then, run the display full screen on the monitor attached to the dev laptop, and prototype the staff pages on real phones over the LAN | Display path (Task 3) waits for the Pi |

## Open questions (for Allen)

1. ~~License~~ (decided: MIT)
2. ~~GitHub repo~~ (decided: after the prototype runs)
3. **Display access path:** SDL KMS/DRM directly vs. a kiosk Wayland compositor (`cage`). See `docs/display_design.md`; to settle with the spike in Task 3.
4. ~~Display reads via HTTP~~ (decided: yes)
5. ~~`cancelled` status~~ (decided: yes)
6. ~~Waiting-list sort~~ (decided: numeric)
7. **Python version and Pygame flavor** (deferred to Task 3): system Python and Debian's `python3-pygame` on the Pi (linked against Debian's SDL2, which includes KMS/DRM support) vs. a venv with PyPI wheels. Tied to item 3.
8. ~~Hardware~~ (monitor 2026-11-04, Pi a few days later; see Decisions)

## Testing before the Pi arrives

- **Display on the real monitor:** plug the 24" monitor into the laptop's HDMI port and run the display full screen on that output (`--display 1` or `SDL_VIDEO_FULLSCREEN_DISPLAY`; same 1920×1080 resolution as the Pi). That checks layout, font sizes, and legibility from across the room, which is what matters most. It doesn't test the Pi graphics path (Task 3); the laptop goes through its own desktop compositor.
- **Laptop screen only:** `--windowed` with scaling is fine for layout work, but can't judge readability at distance.
- **Phones:** run the server bound to `0.0.0.0` on the laptop and open `http://<laptop-ip>:8000/register` on phones on the same Wi-Fi. Tests real keyboards, focus behavior (iOS), and several clients at once. The laptop firewall may need to allow the port.

## To consider later

- Server-sent events instead of polling, if polling ever shows lag or load.
- Sound or chime when an order turns ready.
- Preparation-time estimates, only after the pilot shows the data is reliable.
- A "Preparing" status, only if the pilot shows it helps.
- Export of timing data (CSV) for analysis.
- Read-only filesystem / overlayfs on the Pi to survive power cuts (Pi OS `raspi-config` supports an overlay); conflicts with writing the database, so the DB would go on a separate writable partition or USB stick.

## Reference

- `decisionsciencecorp/orderboard` (PHP/JS, AGPLv3): ideas only (high-contrast public view, separate admin view, SQLite, lifecycle tests). **Don't copy code**; don't even keep a clone in this repo.

## Log

- 2026-11-03: Brief received (`docs/brief.md`). Board, plan, and display design question drafted.
- 2026-11-03: Architecture decisions recorded. Skeleton, `db.py`, `config.py`, `server.py`, and 75 tests (all pass); smoke-tested the real server with curl. Undo works on any recent event (`POST /api/events/{id}/undo`), using the `events` table. Dev env: `mamba env create -f environment.yml` (Python 3.11).
