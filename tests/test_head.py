"""Head agent planning logic, against fakes — no network, no real LLM.

Covers the two things that actually matter here: cards that already have a
plan are skipped (idempotency, so re-running `orchestrator plan` is cheap),
and writing a plan never clobbers a card's existing content (the data-replace
landmine in Collaberry's PATCH semantics).
"""

from __future__ import annotations

from orchestrator.board_client import Item
from orchestrator.roles.head import _describe_content, plan_board


class FakeBoardClient:
    """Mimics just enough of BoardClient for plan_board to operate on."""

    def __init__(self, items: list[Item]) -> None:
        self._items = items
        self.updates: list[tuple[str, dict]] = []

    def list_items(self) -> list[Item]:
        return self._items

    def has_agent_plan(self, item: Item) -> bool:
        return bool(item.data.get("agent_plan"))

    def append_agent_plan(self, item: Item, plan: str) -> Item:
        merged = {**item.data, "agent_plan": plan}
        self.updates.append((item.id, merged))
        item.data = merged  # mirror the real client's return value in place
        return item


class FakeLLMClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def chat(self, role: str, *, system: str, user: str) -> str:
        self.calls.append((role, system, user))
        return "1. do the thing\n2. test the thing"


def _card(id_: str, title: str, description: str = "", agent_plan: str | None = None) -> Item:
    data = {"description": description}
    if agent_plan:
        data["agent_plan"] = agent_plan
    return Item(
        id=id_, board_id="b1", column_id="c1", type="card", title=title,
        order=1.0, data=data, assignees=[], tags=[],
    )


def test_already_planned_cards_are_skipped():
    already = _card("1", "old card", agent_plan="existing plan")
    fresh = _card("2", "new card", description="needs doing")
    client = FakeBoardClient([already, fresh])
    llm = FakeLLMClient()

    planned = plan_board(client, llm)

    assert [p.item.id for p in planned] == ["2"]
    assert len(llm.calls) == 1  # only asked the LLM about the unplanned card


def test_planning_preserves_existing_data():
    card = _card("1", "bug fix", description="the login button is broken")
    client = FakeBoardClient([card])
    llm = FakeLLMClient()

    plan_board(client, llm)

    _, merged_data = client.updates[0]
    assert merged_data["description"] == "the login button is broken"  # untouched
    assert "do the thing" in merged_data["agent_plan"]  # plan added


def test_rerunning_plan_board_is_a_noop_once_everything_is_planned():
    card = _card("1", "bug fix", description="x")
    client = FakeBoardClient([card])
    llm = FakeLLMClient()

    first = plan_board(client, llm)
    second = plan_board(client, llm)

    assert len(first) == 1
    assert len(second) == 0  # card now has agent_plan set, from the mutation above
    assert len(llm.calls) == 1


def test_repo_map_is_included_in_the_system_prompt(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "workspace_thing.py").write_text("x = 1\n")

    card = _card("1", "fix the thing", description="broken")
    client = FakeBoardClient([card])
    llm = FakeLLMClient()

    plan_board(client, llm, repo_path=str(tmp_path))

    system_prompt = llm.calls[0][1]
    assert "workspace_thing.py" in system_prompt  # the real file, not a guess


def test_no_repo_path_still_works(tmp_path):
    # repo_path is optional - omitting it must not crash, just skip grounding.
    card = _card("1", "fix the thing", description="broken")
    client = FakeBoardClient([card])
    llm = FakeLLMClient()

    planned = plan_board(client, llm)  # no repo_path
    assert len(planned) == 1


def test_on_progress_is_called_for_each_card():
    events: list[tuple[str, str | None]] = []
    card = _card("1", "fix the thing", description="broken")
    client = FakeBoardClient([card])
    llm = FakeLLMClient()

    plan_board(client, llm, on_progress=lambda msg, item: events.append((msg, item.title if item else None)))

    # at least: a summary line, a "planning: ..." line, and a "planned: ..." line
    assert any("need a plan" in msg for msg, _ in events)
    assert any(msg.startswith("planning:") and title == "fix the thing" for msg, title in events)
    assert any(msg.startswith("planned:") and title == "fix the thing" for msg, title in events)


def test_parallel_planning_plans_everything_in_board_order():
    cards = [_card(str(i), f"card {i}", description="x") for i in range(6)]
    client = FakeBoardClient(cards)
    llm = FakeLLMClient()

    planned = plan_board(client, llm, concurrency=3)

    assert [p.item.id for p in planned] == [str(i) for i in range(6)]  # order kept
    assert len(llm.calls) == 6
    assert all(client.has_agent_plan(c) for c in cards)


def test_parallel_planning_keeps_finished_plans_when_one_card_fails():
    class FlakyLLM(FakeLLMClient):
        def chat(self, role, *, system, user):
            if "card 1" in user:
                raise RuntimeError("route died")
            return super().chat(role, system=system, user=user)

    cards = [_card(str(i), f"card {i}", description="x") for i in range(3)]
    client = FakeBoardClient(cards)
    llm = FlakyLLM()

    import pytest
    with pytest.raises(RuntimeError, match="route died"):
        plan_board(client, llm, concurrency=3)

    # cards 0 and 2 finished and their plans were written despite card 1 dying;
    # a re-run would only retry the failed one.
    assert client.has_agent_plan(cards[0])
    assert not client.has_agent_plan(cards[1])
    assert client.has_agent_plan(cards[2])


def test_describe_content_handles_all_item_types():
    checklist = Item(
        id="c", board_id="b", column_id="x", type="checklist", title="t", order=1.0,
        data={"entries": [{"text": "step one", "done": True}, {"text": "step two", "done": False}]},
        assignees=[], tags=[],
    )
    document = Item(
        id="d", board_id="b", column_id="x", type="document", title="t", order=1.0,
        data={"blocks": [{"type": "paragraph", "text": "hello"}]}, assignees=[], tags=[],
    )
    empty_card = Item(
        id="e", board_id="b", column_id="x", type="card", title="t", order=1.0,
        data={}, assignees=[], tags=[],
    )

    assert "[x] step one" in _describe_content(checklist)
    assert "[ ] step two" in _describe_content(checklist)
    assert "hello" in _describe_content(document)
    assert _describe_content(empty_card) == "(no description)"
