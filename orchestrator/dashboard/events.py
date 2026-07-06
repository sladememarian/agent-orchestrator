"""The CLI side of the dashboard wiring: a best-effort event emitter.

CLI commands call ``emit`` whenever something dashboard-worthy happens (a role
starting, finishing, or failing). If the dashboard isn't running, this is a
silent no-op with a short timeout - a `orch plan` run must never fail, hang,
or even print a warning just because nobody opened the dashboard tab.
"""

from __future__ import annotations

import httpx

from ..config import Settings

_TIMEOUT = 0.5  # the dashboard is optional; never let it slow down a real command


def emit(
    settings: Settings,
    *,
    role: str,
    action: str,
    card_title: str | None = None,
    detail: str = "",
    status: str = "info",
) -> None:
    try:
        httpx.post(
            f"{settings.dashboard_url}/events",
            json={"role": role, "action": action, "card_title": card_title, "detail": detail, "status": status},
            timeout=_TIMEOUT,
            trust_env=False,
        )
    except httpx.HTTPError:
        pass  # dashboard not running (or not reachable) - fine, it's optional
