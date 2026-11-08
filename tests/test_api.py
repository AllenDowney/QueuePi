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


# --- choosing the address for the QR code -------------------------------------

IP_OUTPUT = """\
3: wlo1    inet 192.168.1.7/24 brd 192.168.1.255 scope global dynamic noprefixroute wlo1\\       valid_lft 80000sec preferred_lft 80000sec
5: tun0    inet 10.31.18.118/24 scope global tun0\\       valid_lft forever preferred_lft forever
6: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\\       valid_lft forever preferred_lft forever
"""


def test_lan_address_skips_vpn_and_docker():
    from queuepi.server import pick_lan_address
    assert pick_lan_address(IP_OUTPUT) == "192.168.1.7"


def test_lan_address_access_point():
    from queuepi.server import pick_lan_address
    out = "3: wlan0    inet 10.42.0.1/24 brd 10.42.0.255 scope global noprefixroute wlan0\\n"
    assert pick_lan_address(out) == "10.42.0.1"


def test_lan_address_prefers_private():
    from queuepi.server import pick_lan_address
    out = ("2: eth0    inet 203.0.113.5/24 scope global eth0\n"
           "3: wlan0    inet 10.42.0.1/24 scope global wlan0\n")
    assert pick_lan_address(out) == "10.42.0.1"


def test_lan_address_none():
    from queuepi.server import pick_lan_address
    assert pick_lan_address("") is None
    assert pick_lan_address("5: tun0    inet 10.8.0.2/24 scope global tun0\n") is None


def test_client_log(client, caplog):
    r = client.post("/api/client-log", json={"page": "/register", "message": "SyntaxError: boom"})
    assert r.status_code == 200
    assert "SyntaxError: boom" in caplog.text


def test_client_log_rate_limited(client, caplog):
    for i in range(40):
        client.post("/api/client-log", json={"message": f"err{i}"})
    assert "err29" in caplog.text and "err30" not in caplog.text


def test_report_script_served(client):
    assert client.get("/report.js").status_code == 200
    assert '<script src="/report.js">' in client.get("/register").text


# --- running without a PIN ----------------------------------------------------


@pytest.fixture
def open_client(tmp_path, store):
    from queuepi.config import Config
    return TestClient(create_app(Config(data_dir=tmp_path, require_pin=False), store))


def test_no_pin_needed(open_client):
    assert open_client.get("/api/me").json() == {"staff": True}
    r = open_client.post("/api/orders", json={"number": "12"})
    assert r.status_code == 201
    assert open_client.get("/api/recent").status_code == 200


def test_config_defaults_without_file(monkeypatch, tmp_path):
    from queuepi import config
    monkeypatch.delenv("QUEUEPI_CONFIG", raising=False)
    monkeypatch.setattr(config, "SYSTEM_CONFIG", tmp_path / "missing.toml")
    assert config.load().require_pin is False


def test_config_file_requires_pin(tmp_path):
    from queuepi import config
    path = tmp_path / "c.toml"
    path.write_text('pin = "4821"\n')
    assert config.load(path).require_pin is True
    path.write_text('pin = "4821"\nrequire_pin = false\n')
    assert config.load(path).require_pin is False


# --- shop logo ----------------------------------------------------------------


def test_no_logo(client):
    assert client.get("/api/board").json()["logo"] is None
    assert client.get("/api/logo").status_code == 404


def test_logo(client, cfg):
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.logo_path.write_bytes(b"\x89PNG fake")
    version = client.get("/api/board").json()["logo"]
    assert version
    r = client.get("/api/logo")
    assert r.status_code == 200 and r.content == b"\x89PNG fake"
    assert r.headers["content-type"] == "image/png"
    cfg.logo_path.write_bytes(b"\x89PNG different")
    assert client.get("/api/board").json()["logo"] != version


def test_wifi_join_text_escapes():
    from queuepi.server import wifi_join_text

    assert wifi_join_text("QueuePi", "abc") == "WIFI:T:WPA;S:QueuePi;P:abc;;"
    assert wifi_join_text('a;b,c:d"e\\f', "p;w") == r'WIFI:T:WPA;S:a\;b\,c\:d\"e\\f;P:p\;w;;'
    assert wifi_join_text("Open", None) == "WIFI:T:nopass;S:Open;;"


def test_setup_mode_shows_then_hides(cfg, store):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from queuepi.server import create_app

    c = TestClient(create_app(replace(cfg, wifi_ssid="QueuePi", wifi_password="secret1234",
                                      public_url="http://10.42.0.1/", require_pin=False), store))
    setup = c.get("/api/board").json()["setup"]
    assert setup == {"url": "http://10.42.0.1/", "ssid": "QueuePi",
                     "wifi": "WIFI:T:WPA;S:QueuePi;P:secret1234;;"}
    assert c.post("/api/setup", json={"show": False}).json() == {"setup": False}
    assert c.get("/api/board").json()["setup"] is None
    c.post("/api/setup", json={"show": True})
    assert c.get("/api/board").json()["setup"]["ssid"] == "QueuePi"


def test_setup_without_wifi(client):
    setup = client.get("/api/board").json()["setup"]
    assert setup["wifi"] is None and setup["url"].startswith("http://")


def test_setup_needs_staff(cfg, store):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from queuepi.server import create_app

    c = TestClient(create_app(replace(cfg, require_pin=True), store))
    assert c.post("/api/setup", json={"show": False}).status_code == 401


def test_receipt_picture(client, cfg):
    assert client.get("/api/board").json()["receipt"] is None
    assert client.get("/api/receipt").status_code == 404
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.receipt_path.write_bytes(b"\x89PNG receipt")
    assert client.get("/api/board").json()["receipt"]
    r = client.get("/api/receipt")
    assert r.status_code == 200 and r.content == b"\x89PNG receipt"
