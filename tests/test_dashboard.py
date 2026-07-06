"""Dashboard server: REST + WebSocket event flow, via FastAPI's TestClient -
no real network, no real browser needed.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from orchestrator.dashboard.server import app, bus


def setup_function() -> None:
    bus.events.clear()  # each test starts from a clean event log


def test_posting_an_event_appears_in_the_list():
    client = TestClient(app)
    r = client.post("/events", json={"role": "head", "action": "planned card", "card_title": "Fix bug", "status": "success"})
    assert r.status_code == 200

    events = client.get("/events").json()
    assert len(events) == 1
    assert events[0]["role"] == "head"
    assert events[0]["card_title"] == "Fix bug"


def test_event_defaults_status_to_info():
    client = TestClient(app)
    client.post("/events", json={"role": "developer", "action": "started"})
    events = client.get("/events").json()
    assert events[0]["status"] == "info"


def test_websocket_receives_posted_events_live():
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        client.post("/events", json={"role": "makefile_tester", "action": "make test-unit", "status": "success"})
        received = ws.receive_json()
        assert received["role"] == "makefile_tester"
        assert received["status"] == "success"


def test_websocket_replays_history_on_connect():
    client = TestClient(app)
    client.post("/events", json={"role": "head", "action": "planned card", "card_title": "Old event"})

    with client.websocket_connect("/ws") as ws:
        replayed = ws.receive_json()
        assert replayed["card_title"] == "Old event"


def test_event_log_is_bounded():
    client = TestClient(app)
    for i in range(510):
        client.post("/events", json={"role": "head", "action": f"event {i}"})
    events = client.get("/events").json()
    assert len(events) == 500  # MAX_EVENTS, not 510
    assert events[-1]["action"] == "event 509"  # oldest ones dropped, newest kept


def test_index_page_loads():
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "Agent Orchestrator" in r.text


def test_page_escapes_untrusted_event_fields():
    # Card titles and LLM plan text are untrusted; the page must escape them
    # before innerHTML, and every interpolation site must go through esc().
    client = TestClient(app)
    page = client.get("/").text
    assert "function esc(" in page
    assert "${esc(ev.card_title)}" in page
    assert "${esc(ev.detail)}" in page
    assert "${ev.card_title}" not in page  # no raw, unescaped interpolation left
    assert "${ev.detail}" not in page
