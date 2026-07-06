"""The live dashboard - phase 5, matching the "agent workspace" card:
"visualizing agents workspace and making that resume too."

A small FastAPI app, entirely in-process and in-memory - this is a
single-operator dev tool watching one machine's agent runs, not a multi-user
production service, so there's no database and no auth: just an event log
that CLI commands best-effort POST to, and a WebSocket that streams new events
to whatever browser tab has the page open.

Run it with `orch dashboard` (see cli.py), then open http://127.0.0.1:8800.
"""

from __future__ import annotations

import time
from typing import Literal

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

MAX_EVENTS = 500

Status = Literal["info", "success", "error"]


class AgentEvent(BaseModel):
    role: str  # "head" | "developer" | "realtime_fixer" | "makefile_tester"
    action: str  # short verb phrase, e.g. "planning card", "tests failed"
    card_title: str | None = None
    detail: str = ""
    status: Status = "info"
    ts: float = Field(default_factory=time.time)


class EventBus:
    """The in-memory event log plus the set of live WebSocket subscribers.

    Deliberately not thread-safe beyond what asyncio's single-threaded event
    loop already gives us for free - this is a local dev tool, not a service
    under concurrent load from multiple processes.
    """

    def __init__(self) -> None:
        self.events: list[AgentEvent] = []
        self._subscribers: set[WebSocket] = set()

    async def publish(self, event: AgentEvent) -> None:
        self.events.append(event)
        if len(self.events) > MAX_EVENTS:
            self.events = self.events[-MAX_EVENTS:]
        dead: list[WebSocket] = []
        for ws in self._subscribers:
            try:
                await ws.send_json(event.model_dump())
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._subscribers.discard(ws)

    def subscribe(self, ws: WebSocket) -> None:
        self._subscribers.add(ws)

    def unsubscribe(self, ws: WebSocket) -> None:
        self._subscribers.discard(ws)


bus = EventBus()
app = FastAPI(title="Agent Orchestrator Dashboard")


@app.get("/events")
async def list_events() -> list[dict]:
    return [e.model_dump() for e in bus.events]


@app.post("/events")
async def post_event(event: AgentEvent) -> dict:
    await bus.publish(event)
    return {"ok": True}


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    bus.subscribe(websocket)
    try:
        # Replay history first so a tab opened mid-run isn't starting blind.
        for event in bus.events:
            await websocket.send_json(event.model_dump())
        while True:
            # This dashboard is push-only from the server's side; we just
            # need to notice when the client goes away.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(websocket)


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _PAGE


_PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Agent Orchestrator</title>
<style>
  :root {
    --bg: #0a0a0c; --surface: #121216; --raised: #17171e; --border: #23232d;
    --text-hi: #f3f4f6; --text-mid: #b4b7c2; --text-low: #7a7e8c;
    --purple: #a855f7; --blue: #3b82f6; --green: #34d399; --red: #f87171;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text-hi);
    font-family: -apple-system, Segoe UI, sans-serif; padding: 24px;
  }
  h1 { font-size: 20px; font-weight: 700; margin: 0 0 4px; }
  .sub { color: var(--text-low); font-size: 13px; margin-bottom: 20px; }
  #status { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; background: var(--red); }
  #status.connected { background: var(--green); }
  .roles { display: flex; gap: 10px; margin-bottom: 20px; flex-wrap: wrap; }
  .role-card {
    background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
    padding: 12px 16px; min-width: 140px;
  }
  .role-card .name { font-size: 12px; color: var(--text-low); text-transform: uppercase; letter-spacing: 0.4px; }
  .role-card .count { font-size: 22px; font-weight: 700; margin-top: 2px; }
  #feed { display: flex; flex-direction: column-reverse; gap: 6px; max-width: 900px; }
  .event {
    background: var(--surface); border: 1px solid var(--border); border-left: 3px solid var(--text-low);
    border-radius: 6px; padding: 10px 14px; font-size: 13px; display: flex; gap: 10px; align-items: baseline;
  }
  .event.success { border-left-color: var(--green); }
  .event.error { border-left-color: var(--red); }
  .event .role { font-weight: 700; color: var(--purple); min-width: 110px; }
  .event .time { color: var(--text-low); font-size: 11px; min-width: 70px; }
  .event .card { color: var(--blue); }
  .event .detail { color: var(--text-mid); white-space: pre-wrap; }
  .event details summary { color: var(--blue); cursor: pointer; font-size: 12px; margin-top: 4px; }
  .event details pre {
    background: var(--raised); border: 1px solid var(--border); border-radius: 6px;
    padding: 10px 12px; margin: 6px 0 0; font-size: 12px; line-height: 1.5;
    white-space: pre-wrap; max-height: 400px; overflow-y: auto;
  }
</style>
</head>
<body>
  <h1>Agent Orchestrator</h1>
  <div class="sub"><span id="status"></span><span id="status-text">connecting...</span></div>
  <div class="roles" id="roles"></div>
  <div id="feed"></div>

<script>
const roleCounts = {};
const rolesEl = document.getElementById('roles');
const feedEl = document.getElementById('feed');
const statusEl = document.getElementById('status');
const statusTextEl = document.getElementById('status-text');

// Event fields (card titles, LLM-generated plan text) are untrusted input -
// escape everything before it touches innerHTML, or a plan containing markup
// would execute in this tab.
function esc(s) {
  return String(s ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#39;');
}

function renderRoles() {
  rolesEl.innerHTML = '';
  for (const [role, count] of Object.entries(roleCounts)) {
    const div = document.createElement('div');
    div.className = 'role-card';
    div.innerHTML = `<div class="name">${esc(role)}</div><div class="count">${count}</div>`;
    rolesEl.appendChild(div);
  }
}

function addEvent(ev) {
  roleCounts[ev.role] = (roleCounts[ev.role] || 0) + 1;
  renderRoles();
  const div = document.createElement('div');
  div.className = 'event ' + esc(ev.status);
  const time = new Date(ev.ts * 1000).toLocaleTimeString();
  const cardHtml = ev.card_title ? `<span class="card">${esc(ev.card_title)}</span>` : '';
  // Long detail (e.g. a full multi-step plan) collapses behind a click so the
  // feed stays scannable; short detail renders inline as before.
  let detailHtml;
  if (ev.detail && ev.detail.length > 120) {
    detailHtml = `<span class="detail">${esc(ev.action)}<details><summary>show full text</summary><pre>${esc(ev.detail)}</pre></details></span>`;
  } else {
    detailHtml = `<span class="detail">${esc(ev.action)}${ev.detail ? ' - ' + esc(ev.detail) : ''}</span>`;
  }
  div.innerHTML = `<span class="time">${time}</span><span class="role">${esc(ev.role)}</span>${cardHtml}${detailHtml}`;
  feedEl.appendChild(div);
}

function connect() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => { statusEl.classList.add('connected'); statusTextEl.textContent = 'live'; };
  ws.onclose = () => {
    statusEl.classList.remove('connected'); statusTextEl.textContent = 'reconnecting...';
    setTimeout(connect, 1500);
  };
  ws.onmessage = (msg) => addEvent(JSON.parse(msg.data));
}
connect();
</script>
</body>
</html>
"""
