# QueuePi

A lightweight, self-hosted order queue and pickup display for small restaurants.

Customers see which order numbers are waiting and which are ready on a monitor; staff add orders and mark them ready from their phones. The order number is the last two digits of the receipt, so there's no point-of-sale integration. Everything runs on one Raspberry Pi 4 (or any Linux computer) with no internet or cloud services.

**Status:** early development. The backend and its tests work; the staff pages and the customer display are next.

## Running locally

```bash
mamba env create -f environment.yml     # once
mamba activate QueuePi
queuepi-server                          # http://localhost:8000
pytest                                  # run the tests
```

With no config file, the server uses development defaults: PIN `1234`, database in `./data/`, listening on all interfaces at port 8000, so phones on the same Wi-Fi can reach it at `http://<laptop-ip>:8000/`.

To change these, write a TOML file and point `QUEUEPI_CONFIG` at it (on the Pi, `/etc/queuepi/config.toml` is read automatically):

```toml
pin = "4821"
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
src/queuepi/static/     staff web pages
tests/                  pytest
docs/                   plan and design notes
```

## License

MIT. See `LICENSE`.
