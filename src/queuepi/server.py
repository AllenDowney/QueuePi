"""The QueuePi web server: the JSON API and the staff pages.

Reading the board is open to anyone on the network (it shows only numbers).
Every change needs a staff cookie, which a phone gets by entering the PIN once.

Run with `queuepi-server` (or `python -m queuepi.server`).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import logging.handlers
import secrets
import socket
import sys
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from . import config as config_module
from .config import Config
from .db import (
    Conflict,
    DuplicateNumber,
    InvalidNumber,
    InvalidTransition,
    NotFound,
    OrderStore,
    QueueError,
)

log = logging.getLogger("queuepi")

COOKIE = "queuepi_staff"
COOKIE_MAX_AGE = 365 * 24 * 3600  # a year: log in once per phone

STATUS_CODES: dict[type[QueueError], int] = {
    InvalidNumber: 422,
    InvalidTransition: 422,
    NotFound: 404,
    Conflict: 409,
    DuplicateNumber: 409,
}

STATIC_DIR = Path(__file__).parent / "static"


class AddRequest(BaseModel):
    number: str  # a string, so leading zeros survive JSON


class TransitionRequest(BaseModel):
    to: str
    expect: str


class LoginRequest(BaseModel):
    pin: str


class ResetRequest(BaseModel):
    confirm: bool = False


# --- staff cookies -----------------------------------------------------------
#
# The cookie is "<device id>.<signature>". The signature covers the device id
# and the PIN, so changing the PIN logs every phone out. The device id goes in
# the event log, so we can tell which phone did what.


def _sign(key: bytes, device: str, pin: str) -> str:
    return hmac.new(key, f"{device}:{pin}".encode(), hashlib.sha256).hexdigest()


def make_cookie(key: bytes, pin: str) -> str:
    device = secrets.token_hex(4)
    return f"{device}.{_sign(key, device, pin)}"


def check_cookie(key: bytes, pin: str, value: str | None) -> str | None:
    """Return the device id if the cookie is valid, else None."""
    if not value or "." not in value:
        return None
    device, sig = value.split(".", 1)
    if hmac.compare_digest(sig, _sign(key, device, pin)):
        return device
    return None


# --- join URL ----------------------------------------------------------------


def lan_address() -> str:
    """This computer's address on the local network: the one its default
    route uses. Connecting a UDP socket sends nothing; it just picks the
    interface. Falls back to localhost if there's no network."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1: never routed anywhere real
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def join_url(cfg: Config) -> str:
    """The URL phones use to reach the staff pages (for the display's QR code)."""
    if cfg.public_url:
        return cfg.public_url
    port = "" if cfg.port == 80 else f":{cfg.port}"
    return f"http://{lan_address()}{port}/"


# --- the app -----------------------------------------------------------------


def create_app(cfg: Config | None = None, store: OrderStore | None = None) -> FastAPI:
    cfg = cfg or config_module.load()
    store = store or OrderStore(cfg.db_path)
    key = cfg.secret_key()

    app = FastAPI(title="QueuePi", version=__version__, docs_url=None, redoc_url=None)
    app.state.store = store
    app.state.config = cfg

    @app.exception_handler(QueueError)
    async def queue_error(request: Request, exc: QueueError) -> JSONResponse:
        status = STATUS_CODES.get(type(exc), 400)
        return JSONResponse({"detail": str(exc), "code": exc.code}, status_code=status)

    def staff(request: Request) -> str:
        device = check_cookie(key, cfg.pin, request.cookies.get(COOKIE))
        if device is None:
            raise HTTPException(401, "staff PIN required")
        return device

    # --- open ---

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "version": __version__}

    @app.get("/api/board")
    def board() -> dict:
        return {**store.board(), "join_url": join_url(cfg)}

    @app.post("/api/login")
    def login(body: LoginRequest, response: Response) -> dict:
        if not hmac.compare_digest(body.pin.encode(), cfg.pin.encode()):
            log.warning("wrong PIN")
            raise HTTPException(401, "wrong PIN")
        response.set_cookie(
            COOKIE,
            make_cookie(key, cfg.pin),
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="strict",
        )
        return {"ok": True}

    @app.post("/api/logout")
    def logout(response: Response) -> dict:
        response.delete_cookie(COOKIE)
        return {"ok": True}

    @app.get("/api/me")
    def me(request: Request) -> dict:
        device = check_cookie(key, cfg.pin, request.cookies.get(COOKIE))
        return {"staff": device is not None}

    # --- staff only ---

    @app.post("/api/orders", status_code=201)
    def add(body: AddRequest, device: str = Depends(staff)) -> dict:
        order = store.add(body.number, client=device)
        log.info("add %s (order %d) by %s", order.number, order.id, device)
        return order.to_dict()

    @app.post("/api/orders/{order_id}/transition")
    def transition(order_id: int, body: TransitionRequest, device: str = Depends(staff)) -> dict:
        order = store.transition(order_id, body.to, body.expect, client=device)
        log.info("%s %s -> %s by %s", order.number, body.expect, body.to, device)
        return order.to_dict()

    @app.get("/api/recent")
    def recent(limit: int = 10, device: str = Depends(staff)) -> list[dict]:
        return [e.to_dict() for e in store.recent_events(min(limit, 50))]

    @app.post("/api/events/{event_id}/undo")
    def undo(event_id: int, device: str = Depends(staff)) -> dict:
        order = store.undo(event_id, client=device)
        log.info("undo event %d: %s now %s by %s", event_id, order.number, order.status, device)
        return order.to_dict()

    @app.post("/api/reset")
    def reset(body: ResetRequest, device: str = Depends(staff)) -> dict:
        if not body.confirm:
            raise HTTPException(400, "reset needs confirm: true")
        n = store.reset(client=device)
        log.info("end-of-day reset: cleared %d orders, by %s", n, device)
        return {"cleared": n}

    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app


def setup_logging(cfg: Config) -> None:
    """Log to stderr (journald on the Pi) and, if configured, a small rotating file."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if cfg.log_file is not None:
        cfg.log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(cfg.log_file, maxBytes=1_000_000, backupCount=3)
        )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def main() -> None:
    import uvicorn

    cfg = config_module.load()
    setup_logging(cfg)
    if cfg.pin == config_module.DEV_PIN:
        log.warning("using the development PIN %s; set `pin` in the config file", cfg.pin)
    app = create_app(cfg)
    log.info("QueuePi %s: database %s, listening on %s:%d", __version__, cfg.db_path, cfg.host, cfg.port)
    log.info("phones join at %s", join_url(cfg))
    # One worker: SQLite has one writer anyway, and this keeps memory small on the Pi.
    uvicorn.run(app, host=cfg.host, port=cfg.port, workers=1, log_level="warning")


if __name__ == "__main__":
    main()
