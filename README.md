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
queuepi-display --windowed              # a resizable window; Esc or q quits
queuepi-display --display 1             # full screen on a second monitor
python scripts/simulate.py fill --waiting 25 --ready 4   # a lunch rush
python scripts/simulate.py run --speed 10                # a live queue, 10x speed
python scripts/simulate.py reset                         # clear the board
```

In VS Code, the same commands are tasks (Terminal > Run Task…, all named `QueuePi: …`); `QueuePi: start` runs the server and the display together.

Staff pages: open `http://localhost:8000/` and choose Register, Kitchen, or Both (`/register`, `/kitchen`, `/staff`). Phones on the same network can scan the QR code on the display.

With no config file, the server uses development defaults: no PIN (anyone on the network can use the staff pages), database in `./data/`, listening on all interfaces at port 8000, so phones on the same Wi-Fi can reach it at `http://<laptop-ip>:8000/`.

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
| GET | `/api/board` | no | waiting and ready orders, and a version number that changes with every update |
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
scripts/simulate.py     made-up orders for testing
src/queuepi/static/     staff web pages
tests/                  pytest
docs/                   plan and design notes
```

## License

MIT. See `LICENSE`.
