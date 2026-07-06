"""The head agent — phase 2: reads every card on the board and, for any card
that doesn't already have one, asks the LLM for a short implementation plan,
then writes it back onto the card.

Deliberately does *not* touch code or the filesystem — planning and doing are
separate steps (phase 3 is the "doing" role) so a human can review a plan
before any sub-agent starts editing files.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

from ..board_client import BoardClient, Item
from ..llm_client import LLMClient
from ..repo_map import build_repo_map

SYSTEM_PROMPT_TEMPLATE = """\
You are the planning lead for Collaberry, a real-time project workspace app.
The stack: four FastAPI microservices (auth, workspace, presence, \
notification) behind an Envoy gateway, MongoDB for persistent data, Redis for \
live presence/locks/pub-sub, and a React Native (Expo) mobile client.

Here is the REAL current file tree of the repo - only reference paths that \
actually appear in it. Do not invent plausible-sounding paths from a typical \
project of this shape; if you're not sure a file exists, say so instead of \
guessing:

{repo_map}

Given one task card from the team's Kanban board, write a short, concrete plan \
to get it done: 3-6 numbered steps, each naming the actual file(s) from the \
tree above wherever possible. Do not write code. Be direct and specific rather \
than generic — assume the reader already knows the stack."""


@dataclass(slots=True)
class PlannedCard:
    item: Item
    plan: str


def _describe_content(item: Item) -> str:
    """A short text summary of whatever's already on the card, for the prompt."""
    if item.type == "card":
        return item.data.get("description") or "(no description)"
    if item.type == "checklist":
        entries = item.data.get("entries", [])
        return "\n".join(f"- [{'x' if e.get('done') else ' '}] {e.get('text', '')}" for e in entries) or "(empty checklist)"
    if item.type == "document":
        blocks = item.data.get("blocks", [])
        return "\n".join(b.get("text", "") for b in blocks) or "(empty document)"
    return "(unknown card type)"


ProgressCallback = Callable[[str, Item | None], None]


def plan_board(
    client: BoardClient,
    llm: LLMClient,
    *,
    repo_path: str | None = None,
    on_progress: ProgressCallback | None = None,
    concurrency: int = 1,
) -> list[PlannedCard]:
    """Plan every card that doesn't already have an ``agent_plan``.

    Returns the cards it actually planned this run (already-planned cards are
    skipped so re-running is safe and cheap). ``repo_path`` grounds the prompt
    in the repo's real file tree (see repo_map.py) so plans reference paths
    that actually exist rather than a plausible-sounding guess. ``on_progress``
    is called with a short status string (and the current item, if any) before
    and after each card - purely for a caller to show a live spinner/log; with
    ``concurrency > 1`` it fires from worker threads, so callers must be
    thread-safe (rich's Console is).

    ``concurrency`` plans that many cards at once - LLM calls dominate the
    wall clock (15-120s each against slow combo routes), and both the OpenAI
    and httpx clients are thread-safe, so the speedup is near-linear until the
    router itself saturates. Each plan is written back the moment it finishes,
    so if one card ultimately fails, the finished ones stick and a re-run only
    retries what's missing.
    """
    def report(message: str, item: Item | None = None) -> None:
        if on_progress:
            on_progress(message, item)

    repo_map = build_repo_map(repo_path) if repo_path else "(no repo map available)"
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(repo_map=repo_map)

    items = client.list_items()
    to_plan = [i for i in items if not client.has_agent_plan(i)]
    report(f"{len(to_plan)} card(s) need a plan out of {len(items)} total")

    def plan_one(item: Item) -> PlannedCard:
        report(f"planning: {item.title}", item)
        user_prompt = (
            f"Card: {item.title}\n"
            f"Type: {item.type}\n"
            f"Current content:\n{_describe_content(item)}"
        )
        plan_text = llm.chat("head", system=system_prompt, user=user_prompt)
        updated = client.append_agent_plan(item, plan_text)
        report(f"planned: {item.title}", updated)
        return PlannedCard(item=updated, plan=plan_text)

    if concurrency <= 1 or len(to_plan) <= 1:
        return [plan_one(item) for item in to_plan]

    # Board order is preserved in the returned list regardless of which card
    # finishes first. A failed card surfaces after the others complete (their
    # plans are already saved), keeping partial progress instead of losing it.
    planned: list[PlannedCard] = []
    first_error: Exception | None = None
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(plan_one, item) for item in to_plan]
        for future, item in zip(futures, to_plan):
            try:
                planned.append(future.result())
            except Exception as exc:  # noqa: BLE001 - reported, then re-raised below
                report(f"failed: {item.title} - {exc}", item)
                if first_error is None:
                    first_error = exc
    if first_error is not None:
        raise first_error
    return planned
