"""emit() must never break a CLI command just because the dashboard isn't
running - proven here by pointing it at a port nothing is listening on.
"""

from __future__ import annotations

from orchestrator.config import Settings
from orchestrator.dashboard.events import emit


def test_emit_is_silent_when_dashboard_is_unreachable():
    settings = Settings(dashboard_url="http://127.0.0.1:1")  # port 1: never a real HTTP server
    emit(settings, role="head", action="planned card", card_title="x")  # must not raise
