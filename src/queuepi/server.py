"""The QueuePi web server: the JSON API and the staff pages.

Reading the board is open to anyone on the network (it shows only numbers).
Every change needs a staff cookie, which a phone gets by entering the PIN once.

Run with `queuepi-server` (or `python -m queuepi.server`).
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import logging.handlers
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
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


class ClientLog(BaseModel):
    page: str = ""
    message: str = ""


CLIENT_LOG_PER_MINUTE = 30  # so a page stuck in an error loop can't flood the log


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


# Interfaces phones can't reach: VPNs, containers, virtual bridges.
VIRTUAL_PREFIXES = ("tun", "tap", "wg", "ppp", "docker", "br-", "virbr", "veth", "lo")


def pick_lan_address(ip_output: str) -> str | None:
    """Choose the address phones should use from `ip -4 -o addr` output:
    the first one on a real (Wi-Fi or Ethernet) interface, preferring
    private addresses. Returns None if there isn't one."""
    found = []
    for line in ip_output.splitlines():
        fields = line.split()
        if "inet" not in fields:
            continue
        iface = fields[1]
        addr = fields[fields.index("inet") + 1].split("/")[0]
        if iface.startswith(VIRTUAL_PREFIXES):
            continue
        try:
            private = ipaddress.ip_address(addr).is_private
        except ValueError:
            continue
        found.append((not private, addr))
    return min(found)[1] if found else None


def lan_address() -> str:
    """This computer's address on the local network, for the QR code.

    Asks `ip` for the addresses of real interfaces, so a VPN doesn't win.
    If that fails (no `ip` command), uses the interface of the default route:
    connecting a UDP socket sends nothing, it just picks the interface.
    Falls back to localhost if there's no network."""
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show", "scope", "global"],
                             capture_output=True, text=True, timeout=2).stdout
        addr = pick_lan_address(out)
        if addr:
            return addr
    except (OSError, subprocess.SubprocessError):
        pass
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1: never routed anywhere real
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def client_host(request: Request) -> str:
    return request.client.host if request.client else "?"


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
        cookie = request.cookies.get(COOKIE)
        device = check_cookie(key, cfg.pin, cookie)
        if device is None and not cfg.require_pin:
            return client_host(request)
        if device is None:
            log.info("%s %s from %s: not logged in (%s)", request.method, request.url.path,
                     client_host(request), "bad cookie" if cookie else "no cookie")
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
    def login(body: LoginRequest, request: Request, response: Response) -> dict:
        if not hmac.compare_digest(body.pin.encode(), cfg.pin.encode()):
            log.warning("wrong PIN from %s", client_host(request))
            raise HTTPException(401, "wrong PIN")
        response.set_cookie(
            COOKIE,
            make_cookie(key, cfg.pin),
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="strict",
        )
        log.info("login from %s [%s]", client_host(request),
                 request.headers.get("user-agent", "")[:200])
        return {"ok": True}

    client_log_times: list[float] = []

    @app.post("/api/client-log")
    def client_log(body: ClientLog, request: Request) -> dict:
        """JavaScript errors from the staff pages, so they show up in the log."""
        now = time.monotonic()
        client_log_times[:] = [t for t in client_log_times if now - t < 60]
        if len(client_log_times) < CLIENT_LOG_PER_MINUTE:
            client_log_times.append(now)
            host = client_host(request)
            agent = request.headers.get("user-agent", "")[:200]
            log.warning("browser error from %s on %s: %s [%s]",
                        host, body.page[:100], body.message[:500], agent)
        return {"ok": True}

    @app.post("/api/logout")
    def logout(response: Response) -> dict:
        response.delete_cookie(COOKIE)
        return {"ok": True}

    @app.get("/api/me")
    def me(request: Request) -> dict:
        device = check_cookie(key, cfg.pin, request.cookies.get(COOKIE))
        return {"staff": device is not None or not cfg.require_pin}

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

    # The staff pages: one page, three layouts (chosen by the path).
    for path in ("/register", "/kitchen", "/staff"):
        app.add_api_route(path, lambda: FileResponse(STATIC_DIR / "staff.html"), include_in_schema=False)

    @app.middleware("http")
    async def no_stale_pages(request: Request, call_next):
        # Make browsers revalidate pages and scripts, so devices pick up a new
        # version after an update instead of running a cached old one.
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-cache")
        else:
            response.headers.setdefault("Cache-Control", "no-store")
        return response

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
    if not cfg.require_pin:
        log.warning("no PIN required: anyone on the network can use the staff pages")
    elif cfg.pin == config_module.DEV_PIN:
        log.warning("using the development PIN %s; set `pin` in the config file", cfg.pin)
    app = create_app(cfg)
    log.info("QueuePi %s: database %s, listening on %s:%d", __version__, cfg.db_path, cfg.host, cfg.port)
    log.info("phones join at %s", join_url(cfg))
    # One worker: SQLite has one writer anyway, and this keeps memory small on the Pi.
    uvicorn.run(app, host=cfg.host, port=cfg.port, workers=1, log_level="warning")


if __name__ == "__main__":
    main()
