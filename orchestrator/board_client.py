"""A thin client for Collaberry's own REST API — the same one the mobile app
uses, reached through the Envoy gateway. Nothing here touches MongoDB directly:
every read and write is a normal authenticated HTTP call, so every change this
system makes shows up as a real, live-broadcast event in the app itself.

Endpoint shapes mirror ``docs/workspace-service.md`` and
``docs/auth-service.md`` in the Collaberry repo, and this is deliberately
parallel to ``frontend/src/api/endpoints.ts`` — same calls, different language.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings


class BoardClientError(RuntimeError):
    """Raised when the Collaberry API rejects a call outright (not a 404 we
    can route around)."""


@dataclass(slots=True)
class Column:
    id: str
    name: str
    order: int


@dataclass(slots=True)
class Board:
    id: str
    workspace_id: str
    name: str
    columns: list[Column]

    def column_by_name(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)


@dataclass(slots=True)
class Item:
    id: str
    board_id: str
    column_id: str
    type: str
    title: str
    order: float
    data: dict[str, Any]
    assignees: list[str]
    tags: list[str]


class BoardClient:
    """One authenticated session against Collaberry, scoped to one board.

    ``connect()`` logs the bot account in (registering it on first use) and
    locates the configured board by name. Every other method assumes that's
    already happened.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # trust_env=False bypasses any machine-level HTTP proxy that would
        # otherwise intercept localhost — the same fix Collaberry's own e2e
        # tests needed on this machine (see tests/e2e/conftest.py).
        self._http = httpx.Client(
            base_url=settings.collaberry_gateway_url, trust_env=False, timeout=20.0
        )
        self.board: Board | None = None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "BoardClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- auth --------------------------------------------------------------
    def connect(self) -> None:
        token = self._login_or_register()
        self._http.headers["Authorization"] = f"Bearer {token}"
        self.board = self._find_board(self._settings.collaberry_board_name)

    def _login_or_register(self) -> str:
        s = self._settings
        login = self._http.post(
            "/api/v1/auth/login", json={"email": s.collaberry_bot_email, "password": s.collaberry_bot_password}
        )
        if login.status_code == 200:
            return login.json()["access_token"]

        register = self._http.post(
            "/api/v1/auth/register",
            json={
                "email": s.collaberry_bot_email,
                "password": s.collaberry_bot_password,
                "display_name": s.collaberry_bot_display_name,
            },
        )
        if register.status_code == 201:
            return register.json()["access_token"]
        raise BoardClientError(
            f"Could not authenticate the bot account (login {login.status_code}, "
            f"register {register.status_code}): {register.text}"
        )

    def _find_board(self, name: str) -> Board:
        for ws in self._get("/api/v1/workspace/workspaces"):
            for b in self._get(f"/api/v1/workspace/workspaces/{ws['id']}/boards"):
                if b["name"] == name:
                    return _to_board(b)
        raise BoardClientError(
            f"No board named {name!r} is visible to {self._settings.collaberry_bot_email}. "
            "Invite the bot account as a member of the workspace that has it."
        )

    # ---- reads ---------------------------------------------------------------
    def list_items(self) -> list[Item]:
        assert self.board is not None, "call connect() first"
        return [_to_item(i) for i in self._get(f"/api/v1/workspace/boards/{self.board.id}/items")]

    # ---- writes --------------------------------------------------------------
    def update_item(self, item_id: str, **patch: Any) -> Item:
        r = self._http.patch(f"/api/v1/workspace/items/{item_id}", json=patch)
        r.raise_for_status()
        return _to_item(r.json())

    def append_agent_plan(self, item: Item, plan: str) -> Item:
        """Attach an agent-authored plan to a card without disturbing its
        existing content.

        Collaberry's ``PATCH /items/{id}`` replaces the whole ``data`` object
        rather than merging it (see ``services/workspace/app/repository.py``,
        ``update_item``) — so this always starts from the item's current
        ``data`` and only adds/overwrites the ``agent_plan`` key on top. Safe
        for every item type: cards keep their ``description``, documents keep
        their ``blocks``, checklists keep their ``entries``.
        """
        merged = {**item.data, "agent_plan": plan}
        return self.update_item(item.id, data=merged)

    def has_agent_plan(self, item: Item) -> bool:
        return bool(item.data.get("agent_plan"))

    def move_item_to_column(self, item_id: str, column_name: str) -> Item:
        assert self.board is not None, "call connect() first"
        col = self.board.column_by_name(column_name)
        if col is None:
            raise BoardClientError(f"Board {self.board.name!r} has no column named {column_name!r}")
        return self.update_item(item_id, column_id=col.id)

    # ---- internals -------------------------------------------------------
    def _get(self, path: str) -> Any:
        r = self._http.get(path)
        r.raise_for_status()
        return r.json()


def _to_board(raw: dict) -> Board:
    return Board(
        id=raw["id"],
        workspace_id=raw["workspace_id"],
        name=raw["name"],
        columns=[Column(**c) for c in raw["columns"]],
    )


def _to_item(raw: dict) -> Item:
    return Item(
        id=raw["id"],
        board_id=raw["board_id"],
        column_id=raw["column_id"],
        type=raw["type"],
        title=raw["title"],
        order=raw["order"],
        data=raw["data"],
        assignees=raw["assignees"],
        tags=raw["tags"],
    )
