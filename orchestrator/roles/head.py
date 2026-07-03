"""The head agent — phase 2: reads every card on the board and, for any card
that doesn't already have one, asks the LLM for a short implementation plan,
then writes it back onto the card.

Deliberately does *not* touch code or the filesystem — planning and doing are
separate steps (phase 3 is the "doing" role) so a human can review a plan
before any sub-agent starts editing files.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..board_client import BoardClient, Item
from ..llm_client import LLMClient

SYSTEM_PROMPT = """\
You are the planning lead for Collaberry, a real-time project workspace app.
The stack: four FastAPI microservices (auth, workspace, presence, \
notification) behind an Envoy gateway, MongoDB for persistent data, Redis for \
live presence/locks/pub-sub, and a React Native (Expo) mobile client.

Given one task card from the team's Kanban board, write a short, concrete plan \
to get it done: 3-6 numbered steps, each naming the actual file(s) or service \
likely involved where you can reasonably guess. Do not write code. Be direct \
and specific rather than generic — assume the reader already knows the stack."""


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


def plan_board(client: BoardClient, llm: LLMClient) -> list[PlannedCard]:
    """Plan every card that doesn't already have an ``agent_plan``.

    Returns the cards it actually planned this run (already-planned cards are
    skipped so re-running is safe and cheap).
    """
    planned: list[PlannedCard] = []
    for item in client.list_items():
        if client.has_agent_plan(item):
            continue

        user_prompt = (
            f"Card: {item.title}\n"
            f"Type: {item.type}\n"
            f"Current content:\n{_describe_content(item)}"
        )
        plan_text = llm.chat("head", system=SYSTEM_PROMPT, user=user_prompt)
        updated = client.append_agent_plan(item, plan_text)
        planned.append(PlannedCard(item=updated, plan=plan_text))

    return planned
