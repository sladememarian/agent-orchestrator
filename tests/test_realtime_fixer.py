"""Real-time fixer: real git worktree with a genuinely broken commit already
in it, fake LLM providing the fix. Proves the fixer scopes itself to only the
files the previous commit touched, and that it commits as a new commit rather
than rewriting history.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from orchestrator.git_worktree import close_worktree, open_worktree
from orchestrator.roles.realtime_fixer import (
    RealtimeFixerError,
    changed_files_in_last_commit,
    fix_failing_tests,
)


@pytest.fixture()
def scratch_repo(tmp_path):
    repo = tmp_path / "scratch-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "app.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "unrelated.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo, check=True)
    return repo


@pytest.fixture()
def broken_worktree(scratch_repo):
    """Simulates the developer role having just committed a bug, touching
    only app.py."""
    wt = open_worktree(scratch_repo, card_id="bug00001", card_title="Broken add")
    (wt.path / "app.py").write_text("def add(a, b):\n    return a - b  # bug\n")
    subprocess.run(["git", "add", "-A"], cwd=wt.path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "agent(developer): introduces a bug"], cwd=wt.path, check=True)
    yield wt
    close_worktree(wt, force=True)


class FixedReplyLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    def chat(self, role: str, *, system: str, user: str) -> str:
        self.calls += 1
        return self.reply


def test_scope_is_limited_to_the_last_commits_files(broken_worktree):
    assert changed_files_in_last_commit(broken_worktree) == ["app.py"]  # not unrelated.py


def test_fix_failing_tests_repairs_and_commits(broken_worktree):
    fix_json = json.dumps({"app.py": "def add(a, b):\n    return a + b  # fixed\n"})
    llm = FixedReplyLLM(fix_json)

    before_hash = subprocess.run(
        ["git", "-C", str(broken_worktree.path), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    result = fix_failing_tests(
        worktree=broken_worktree, llm=llm,
        test_command=["python", "-c", "from app import add; assert add(2, 3) == 5"],
        failing_output="AssertionError: add(2, 3) != 5",
    )

    assert result.test_passed is True
    assert result.changed_files == ["app.py"]
    assert (broken_worktree.path / "app.py").read_text() == "def add(a, b):\n    return a + b  # fixed\n"

    after_hash = subprocess.run(
        ["git", "-C", str(broken_worktree.path), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert after_hash != before_hash  # a NEW commit, not an amend

    log_count = subprocess.run(
        ["git", "-C", str(broken_worktree.path), "rev-list", "--count", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert log_count == "3"  # initial + buggy commit + fix commit, history preserved


def test_fix_failing_tests_reports_when_the_fix_didnt_work(broken_worktree):
    # Model "fixes" it but doesn't actually fix it.
    still_broken = json.dumps({"app.py": "def add(a, b):\n    return a - b  # still broken\n"})
    llm = FixedReplyLLM(still_broken)

    result = fix_failing_tests(
        worktree=broken_worktree, llm=llm,
        test_command=["python", "-c", "from app import add; assert add(2, 3) == 5"],
        failing_output="AssertionError",
    )

    assert result.test_passed is False
    # It still commits its attempt - a human needs to see what it tried.
    assert result.commit_hash is not None


def test_fix_cannot_touch_files_outside_the_last_commit(broken_worktree):
    # The model tries to sneak in an edit to unrelated.py; it must be dropped
    # since only app.py was in scope, leaving zero valid edits -> error.
    sneaky = json.dumps({"unrelated.py": "x = 999"})
    llm = FixedReplyLLM(sneaky)

    with pytest.raises(RealtimeFixerError, match="no usable edits"):
        fix_failing_tests(
            worktree=broken_worktree, llm=llm,
            test_command=["python", "-c", "pass"],
            failing_output="whatever",
        )


def test_explicit_candidate_files_override_the_default_scope(broken_worktree):
    fix_json = json.dumps({"unrelated.py": "x = 2\n"})
    llm = FixedReplyLLM(fix_json)

    result = fix_failing_tests(
        worktree=broken_worktree, llm=llm,
        test_command=["python", "-c", "pass"],
        failing_output="n/a",
        candidate_files=["unrelated.py"],
    )

    assert result.changed_files == ["unrelated.py"]
