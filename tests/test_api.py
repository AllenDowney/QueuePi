"""The HTTP API: routes, status codes, and the staff PIN."""

import pytest
from fastapi.testclient import TestClient

from queuepi.config import Config
from queuepi.server import COOKIE, create_app

from conftest import PIN


def test_board_is_open(client):
    r = client.get("/api/board")
    assert r.status_code == 200
    assert r.json()["waiting"] == [] and r.json()["ready"] == []


def test_healthz(client):
    assert client.get("/healthz").json()["ok"] is True


def test_writes_need_pin(client):
    assert client.post("/api/orders", json={"number": "42"}).status_code == 401
    assert client.post("/api/reset", json={"confirm": True}).status_code == 401
    assert client.post("/api/orders/1/transition", json={"to": "ready", "expect": "waiting"}).status_code == 401
    assert client.post("/api/events/1/undo").status_code == 401
    assert client.get("/api/recent").status_code == 401
    assert client.get("/api/board").json()["waiting"] == []


def test_wrong_pin(client):
    assert client.post("/api/login", json={"pin": "0000"}).status_code == 401
    assert client.get("/api/me").json() == {"staff": False}


def test_forged_cookie(client):
    client.cookies.set(COOKIE, "abcd1234.deadbeef")
    assert client.post("/api/orders", json={"number": "42"}).status_code == 401


def test_login_and_logout(client):
    assert client.post("/api/login", json={"pin": PIN}).status_code == 200
    assert client.get("/api/me").json() == {"staff": True}
    client.post("/api/logout")
    assert client.get("/api/me").json() == {"staff": False}


def test_changing_pin_logs_everyone_out(cfg, store, staff):
    cookie = staff.cookies.get(COOKIE)
    new_cfg = Config(pin="9999", data_dir=cfg.data_dir)
    c = TestClient(create_app(new_cfg, store))
    c.cookies.set(COOKIE, cookie)
    assert c.post("/api/orders", json={"number": "42"}).status_code == 401


def test_login_survives_restart(cfg, store, staff):
    """The signing key is kept in the data dir, so a restarted server accepts
    the same cookie."""
    cookie = staff.cookies.get(COOKIE)
    c = TestClient(create_app(cfg, store))
    c.cookies.set(COOKIE, cookie)
    assert c.post("/api/orders", json={"number": "42"}).status_code == 201


def test_add_keeps_leading_zero(staff):
    r = staff.post("/api/orders", json={"number": "07"})
    assert r.status_code == 201
    assert r.json()["number"] == "07"
    assert staff.get("/api/board").json()["waiting"][0]["number"] == "07"


def test_add_duplicate(staff):
    staff.post("/api/orders", json={"number": "42"})
    r = staff.post("/api/orders", json={"number": "42"})
    assert r.status_code == 409
    assert r.json()["code"] == "duplicate"
    assert "42" in r.json()["detail"]


def test_add_invalid(staff):
    r = staff.post("/api/orders", json={"number": "123"})
    assert r.status_code == 422
    assert r.json()["code"] == "invalid_number"


def test_transitions(staff):
    o = staff.post("/api/orders", json={"number": "42"}).json()
    url = f"/api/orders/{o['id']}/transition"
    r = staff.post(url, json={"to": "ready", "expect": "waiting"})
    assert r.status_code == 200 and r.json()["status"] == "ready"
    # a second phone with a stale view
    r = staff.post(url, json={"to": "ready", "expect": "waiting"})
    assert r.status_code == 409 and r.json()["code"] == "conflict"
    r = staff.post(url, json={"to": "cancelled", "expect": "ready"})
    assert r.status_code == 422 and r.json()["code"] == "invalid_transition"
    r = staff.post(url, json={"to": "picked_up", "expect": "ready"})
    assert r.status_code == 200
    board = staff.get("/api/board").json()
    assert board["waiting"] == [] and board["ready"] == []


def test_transition_unknown_order(staff):
    r = staff.post("/api/orders/999/transition", json={"to": "ready", "expect": "waiting"})
    assert r.status_code == 404


def test_recent_and_undo(staff):
    o = staff.post("/api/orders", json={"number": "42"}).json()
    staff.post(f"/api/orders/{o['id']}/transition", json={"to": "ready", "expect": "waiting"})
    recent = staff.get("/api/recent").json()
    assert [(e["number"], e["to_status"]) for e in recent] == [("42", "ready"), ("42", "waiting")]
    assert recent[0]["undoable"]
    r = staff.post(f"/api/events/{recent[0]['id']}/undo")
    assert r.status_code == 200 and r.json()["status"] == "waiting"
    r = staff.post(f"/api/events/{recent[0]['id']}/undo")
    assert r.status_code == 409


def test_reset_needs_confirm(staff):
    staff.post("/api/orders", json={"number": "42"})
    assert staff.post("/api/reset", json={}).status_code == 400
    assert len(staff.get("/api/board").json()["waiting"]) == 1
    r = staff.post("/api/reset", json={"confirm": True})
    assert r.json() == {"cleared": 1}
    assert staff.get("/api/board").json()["waiting"] == []


def test_events_record_device(staff, store):
    staff.post("/api/orders", json={"number": "42"})
    client_tag = store.recent_events()[0].client
    assert client_tag and staff.cookies.get(COOKIE).startswith(client_tag + ".")


def test_static_index(client):
    r = client.get("/")
    assert r.status_code == 200 and "QueuePi" in r.text


@pytest.mark.parametrize("path", ["/register", "/kitchen", "/staff"])
def test_staff_pages(client, path):
    r = client.get(path)
    assert r.status_code == 200 and "staff.js" in r.text
    assert r.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("path", ["/staff.js", "/staff.css", "/manifest.webmanifest", "/icon-192.png"])
def test_static_assets(client, path):
    assert client.get(path).status_code == 200


def test_api_not_cached(client):
    assert client.get("/api/board").headers["cache-control"] == "no-store"


def test_responses_carry_event_for_undo(staff):
    o = staff.post("/api/orders", json={"number": "42"}).json()
    r = staff.post(f"/api/events/{o['last_event_id']}/undo")
    assert r.json()["status"] == "cancelled"
    o2 = staff.post(f"/api/orders/{o['id']}/transition", json={"to": "waiting", "expect": "cancelled"}).json()
    assert o2["last_event_id"] > r.json()["last_event_id"]
    ready = staff.post(f"/api/orders/{o['id']}/transition", json={"to": "ready", "expect": "waiting"}).json()
    assert staff.post(f"/api/events/{ready['last_event_id']}/undo").json()["status"] == "waiting"


def test_board_has_join_url(client):
    url = client.get("/api/board").json()["join_url"]
    assert url.startswith("http://") and url.endswith(":8000/")


def test_join_url_from_config(cfg, store):
    cfg.public_url = "http://queuepi.local/"
    c = TestClient(create_app(cfg, store))
    assert c.get("/api/board").json()["join_url"] == "http://queuepi.local/"


def test_join_url_drops_port_80(cfg):
    from queuepi.server import join_url

    cfg.port = 80
    assert not join_url(cfg).rstrip("/").split("//")[1].endswith(":80")
