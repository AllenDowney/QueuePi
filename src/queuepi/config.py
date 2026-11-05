"""Configuration, read from a small TOML file.

The file is found from `$QUEUEPI_CONFIG`, else `/etc/queuepi/config.toml` if it
exists, else nothing (development defaults: no PIN needed). Example:

    pin = "4821"
    require_pin = true                     # the default with a config file
    data_dir = "/var/lib/queuepi"
    host = "0.0.0.0"
    port = 8000
    log_file = "/var/log/queuepi/queuepi.log"
    public_url = "http://queuepi.local/"   # optional; for the join QR code
"""

from __future__ import annotations

import os
import secrets
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

SYSTEM_CONFIG = Path("/etc/queuepi/config.toml")
DEV_PIN = "1234"


@dataclass
class Config:
    pin: str = DEV_PIN
    # False: anyone on the network can use the staff pages, no PIN.
    require_pin: bool = True
    data_dir: Path = Path("data")
    host: str = "0.0.0.0"
    port: int = 8000
    log_file: Path | None = None  # None: log to stderr only
    # The address phones should use, shown as a QR code on the display.
    # None: work it out from this computer's LAN address and the port.
    public_url: str | None = None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "queuepi.db"

    @property
    def secret_path(self) -> Path:
        return self.data_dir / "secret.key"

    def secret_key(self) -> bytes:
        """The key that signs staff cookies. Made on first run and kept in the
        data directory, so staff stay logged in across restarts."""
        path = self.secret_path
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(secrets.token_hex(32))
            path.chmod(0o600)
        return bytes.fromhex(path.read_text().strip())


def load(path: str | Path | None = None) -> Config:
    """Load the config file, or return development defaults if there is none."""
    if path is None:
        path = os.environ.get("QUEUEPI_CONFIG")
    if path is None and SYSTEM_CONFIG.exists():
        path = SYSTEM_CONFIG
    if path is None:
        return Config(require_pin=False)

    with open(path, "rb") as f:
        raw = tomllib.load(f)
    known = {f.name for f in fields(Config)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"{path}: unknown settings {sorted(unknown)}")
    if "pin" in raw:
        raw["pin"] = str(raw["pin"])
    for key in ("data_dir", "log_file"):
        if raw.get(key) is not None:
            raw[key] = Path(raw[key])
    return Config(**raw)
