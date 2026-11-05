# QueuePi

A lightweight, self-hosted order queue and pickup display for small restaurants.

Customers see which order numbers are waiting and which are ready on a monitor; staff add orders and mark them ready from their phones. The order number is the last two digits of the receipt, so there's no point-of-sale integration. Everything runs on one Raspberry Pi 4 (or any Linux computer) with no internet or cloud services.

**Status:** early development: a working prototype of the server, staff pages, and customer display, not yet tried on a Raspberry Pi.

## Running locally

```bash
mamba env create -f environment.yml     # once
conda activate QueuePi                  # mamba 2 can't activate until `mamba shell init`
queuepi-server                          # http://localhost:8000
pytest                                  # run the tests
```

In a second terminal, the customer display and some made-up orders:

```bash
queuepi-display --windowed              # a window; F or F11 full screen, Esc back, q quits
queuepi-display --display 1             # full screen on a second monitor
queuepi-display --no-animation          # no celebration when an order is ready
python scripts/simulate.py fill --waiting 25 --ready 4   # a lunch rush
python scripts/simulate.py run --speed 10                # a live queue, 10x speed
python scripts/simulate.py ready --batch 3               # watch the display's ready animation
python scripts/simulate.py reset                         # clear the board
```

In VS Code, Ctrl+Shift+B runs the server and the display together. The other commands are tasks too: Ctrl+Shift+P, then "Tasks: Run Task" (all named `QueuePi: …`).

Staff pages: open `http://localhost:8000/` and choose Everything, Register only, or Kitchen only (`/staff`, `/register`, `/kitchen`). Phones on the same network can scan the QR code on the display.

With no config file, the server uses development defaults: no PIN (anyone on the network can use the staff pages), database in `./data/`, listening on all interfaces at port 8000, so phones on the same Wi-Fi can reach it at `http://<laptop-ip>:8000/`.

To show the shop's logo on the display and staff pages, put a `logo.png` (transparent background works best) in the data directory. It isn't part of the repository.

To change these, write a TOML file and point `QUEUEPI_CONFIG` at it (on the Pi, `/etc/queuepi/config.toml` is read automatically):

```toml
pin = "4821"                  # with a config file, staff pages need this PIN
require_pin = true            # the default; false turns the PIN off
data_dir = "/var/lib/queuepi"
host = "0.0.0.0"
port = 8000
log_file = "/var/log/queuepi/queuepi.log"
```

## API

| Method | Path | Staff PIN | Purpose |
|---|---|---|---|
| GET | `/api/board` | no | waiting and ready orders, the next likely numbers (`next`), and a version number that changes with every update |
| POST | `/api/login` | — | `{"pin": "..."}`: sets a cookie, once per phone |
| POST | `/api/orders` | yes | `{"number": "07"}`: add an order; 409 if that number is already on the board |
| POST | `/api/orders/{id}/transition` | yes | `{"to": "ready", "expect": "waiting"}`; 409 if someone else changed it first |
| GET | `/api/recent` | yes | recent changes, with which ones can be undone |
| POST | `/api/events/{id}/undo` | yes | undo one change |
| POST | `/api/reset` | yes | `{"confirm": true}`: end of day, clears the board |
| GET | `/healthz` | no | health check |

Order numbers are always two-character strings (`"07"`), so leading zeros survive.

## Layout

```
src/queuepi/db.py       order storage and lifecycle rules (SQLite)
src/queuepi/server.py   FastAPI app: API, staff PIN, static pages
src/queuepi/config.py   config file
src/queuepi/display.py  customer display (Pygame), a client of /api/board
src/queuepi/animation.py  the display's "order ready" animation (timing, geometry, confetti)
scripts/simulate.py     made-up orders for testing
src/queuepi/static/     staff web pages
tests/                  pytest
docs/                   plan and design notes
```

## License

MIT. See `LICENSE`.
