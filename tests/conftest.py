import itertools
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from queuepi.config import Config
from queuepi.db import OrderStore
from queuepi.server import create_app

PIN = "4821"


class FakeClock:
    """A clock that moves one second each time it's read, so every timestamp
    is distinct and ordering by time is deterministic."""

    def __init__(self):
        self.start = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
        self.ticks = itertools.count()

    def __call__(self) -> str:
        t = self.start + timedelta(seconds=next(self.ticks))
        return t.isoformat(timespec="milliseconds")


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "queuepi.db"


@pytest.fixture
def store(db_path):
    return OrderStore(db_path, clock=FakeClock())


@pytest.fixture
def cfg(tmp_path):
    return Config(pin=PIN, data_dir=tmp_path)


@pytest.fixture
def client(cfg, store):
    """An API client that has not logged in."""
    return TestClient(create_app(cfg, store))


@pytest.fixture
def staff(cfg, store):
    """An API client logged in with the PIN."""
    c = TestClient(create_app(cfg, store))
    assert c.post("/api/login", json={"pin": PIN}).status_code == 200
    return c
