"""Developer role: real git (scratch repo + real worktrees), fake LLM and
fake board client (the LLM's output is deterministic in the fakes below so
these stay fast and reproducible; the git side is exercised for real since
that's the safety-critical part).
"""

from __future__ import annotations

import json
import subprocess

import pytest

from orchestrator.board_client import Item
from orchestrator.git_worktree import close_worktree
from orchestrator.roles.developer import DeveloperError, implement_card


@pytest.fixture()
def scratch_repo(tmp_path):
    repo = tmp_path / "scratch-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "app.py").write_text("def add(a, b):\n    return a - b  # bug: should be +\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo, check=True)
    return repo


class FakeBoardClient:
    def __init__(self) -> None:
        self.moves: list[tuple[str, str]] = []
        self.title_updates: list[str] = []

    def move_item_to_column(self, item_id: str, column_name: str) -> None:
        self.moves.append((item_id, column_name))

    def update_item(self, item_id: str, **patch) -> None:
        if "title" in patch:
            self.title_updates.append(patch["title"])


class FixedReplyLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    def chat(self, role: str, *, system: str, user: str) -> str:
        self.calls += 1
        return self.reply


def _card_with_plan(title: str = "Fix the add bug") -> Item:
    return Item(
        id="card123456", board_id="b1", column_id="c1", type="card", title=title,
        order=1.0, data={"description": "add() is subtracting", "agent_plan": "1. fix the operator"},
        assignees=[], tags=[],
    )


def test_implement_card_writes_the_fix_and_commits(scratch_repo):
    fix_json = json.dumps({"app.py": "def add(a, b):\n    return a + b  # fixed\n"})
    llm = FixedReplyLLM(fix_json)
    client = FakeBoardClient()
    item = _card_with_plan()

    result = implement_card(
        item=item, client=client, llm=llm, repo_path=scratch_repo,
        candidate_files=["app.py"],
    )

    assert result.changed_files == ["app.py"]
    assert (result.worktree.path / "app.py").read_text() == "def add(a, b):\n    return a + b  # fixed\n"
    assert client.moves == [("card123456", "In progress")]
    assert client.title_updates[-1].startswith("[needs review]")

    close_worktree(result.worktree, force=True)


def test_implement_card_runs_tests_and_reports_failure(scratch_repo):
    # The "fix" is deliberately still wrong, so the test command should fail.
    fix_json = json.dumps({"app.py": "def add(a, b):\n    return a - b  # still wrong\n"})
    llm = FixedReplyLLM(fix_json)
    client = FakeBoardClient()
    item = _card_with_plan()

    result = implement_card(
        item=item, client=client, llm=llm, repo_path=scratch_repo,
        candidate_files=["app.py"],
        test_command=["python", "-c", "from app import add; assert add(2, 3) == 5"],
    )

    assert result.test_passed is False
    assert client.title_updates[-1].startswith("[needs review - tests failed]")
    close_worktree(result.worktree, force=True)


def test_implement_card_requires_an_existing_plan(scratch_repo):
    client = FakeBoardClient()
    llm = FixedReplyLLM("{}")
    unplanned = Item(
        id="noplan1", board_id="b1", column_id="c1", type="card", title="No plan yet",
        order=1.0, data={}, assignees=[], tags=[],
    )

    with pytest.raises(DeveloperError, match="no agent_plan"):
        implement_card(item=unplanned, client=client, llm=llm, repo_path=scratch_repo, candidate_files=[])


def test_model_cannot_escape_the_candidate_file_set(scratch_repo):
    # The model tries to edit a file it wasn't shown — must be silently dropped,
    # and since that leaves no edits at all, this should error out rather than
    # commit nothing.
    sneaky = json.dumps({"../../etc/passwd": "pwned", "not_a_candidate.py": "x = 1"})
    llm = FixedReplyLLM(sneaky)
    client = FakeBoardClient()
    item = _card_with_plan()

    with pytest.raises(DeveloperError, match="no edits"):
        implement_card(item=item, client=client, llm=llm, repo_path=scratch_repo, candidate_files=["app.py"])


def test_missing_candidate_file_fails_fast(scratch_repo):
    llm = FixedReplyLLM("{}")
    client = FakeBoardClient()
    item = _card_with_plan()

    with pytest.raises(DeveloperError, match="does not exist"):
        implement_card(item=item, client=client, llm=llm, repo_path=scratch_repo, candidate_files=["nope.py"])


def test_json_wrapped_in_markdown_fence_is_still_parsed(scratch_repo):
    fenced = "```json\n" + json.dumps({"app.py": "def add(a, b):\n    return a + b\n"}) + "\n```"
    llm = FixedReplyLLM(fenced)
    client = FakeBoardClient()
    item = _card_with_plan()

    result = implement_card(item=item, client=client, llm=llm, repo_path=scratch_repo, candidate_files=["app.py"])

    assert "app.py" in result.changed_files
    close_worktree(result.worktree, force=True)
