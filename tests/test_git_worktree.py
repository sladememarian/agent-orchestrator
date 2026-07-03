"""git_worktree against a real, throwaway git repository — no mocking. This is
the safety-critical module (isolates sub-agents from the user's live checkout
and guarantees nothing gets pushed), so it's worth the cost of running real git
commands rather than trusting a fake.
"""

from __future__ import annotations

import subprocess

import pytest

from orchestrator.git_worktree import (
    GitWorktreeError,
    close_worktree,
    commit_all,
    open_worktree,
)


@pytest.fixture()
def scratch_repo(tmp_path):
    repo = tmp_path / "scratch-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo, check=True)
    return repo


def test_open_worktree_creates_isolated_branch_and_checkout(scratch_repo):
    wt = open_worktree(scratch_repo, card_id="abc123def456", card_title="Fix the login bug!")

    assert wt.path.exists()
    assert wt.branch == "agent/abc123de-fix-the-login-bug"
    # the worktree is a real, separate checkout - editing it doesn't touch the main one
    (wt.path / "README.md").write_text("changed in the worktree\n")
    assert (scratch_repo / "README.md").read_text() == "hello\n"

    close_worktree(wt, force=True)  # uncommitted on purpose, for this check


def test_commit_all_stages_and_commits(scratch_repo):
    wt = open_worktree(scratch_repo, card_id="xyz789", card_title="Add a feature")
    (wt.path / "new_file.txt").write_text("agent-written content\n")

    short_hash = commit_all(wt, "agent: add a feature")

    assert len(short_hash) >= 7
    log = subprocess.run(
        ["git", "-C", str(wt.path), "log", "-1", "--pretty=%s"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert log == "agent: add a feature"

    close_worktree(wt)


def test_commit_all_refuses_empty_commit(scratch_repo):
    wt = open_worktree(scratch_repo, card_id="empty1", card_title="Nothing to do")

    with pytest.raises(GitWorktreeError, match="Nothing changed"):
        commit_all(wt, "should not happen")

    close_worktree(wt)


def test_close_worktree_refuses_to_discard_uncommitted_work(scratch_repo):
    wt = open_worktree(scratch_repo, card_id="dirty1", card_title="Work in progress")
    (wt.path / "uncommitted.txt").write_text("not committed yet\n")

    with pytest.raises(GitWorktreeError):
        close_worktree(wt)  # must refuse — this would silently discard the change

    # force=True is the explicit, deliberate override
    close_worktree(wt, force=True)


def test_open_worktree_refuses_duplicate_path(scratch_repo):
    wt = open_worktree(scratch_repo, card_id="dup12345", card_title="Same card")
    with pytest.raises(GitWorktreeError, match="already exists"):
        open_worktree(scratch_repo, card_id="dup12345", card_title="Same card")
    close_worktree(wt)


def test_worktree_never_touches_main_checkout_branch(scratch_repo):
    main_branch_before = subprocess.run(
        ["git", "-C", str(scratch_repo), "branch", "--show-current"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    wt = open_worktree(scratch_repo, card_id="branch01", card_title="Branch safety")
    (wt.path / "new_file.txt").write_text("agent work\n")
    commit_all(wt, "agent commit")

    main_branch_after = subprocess.run(
        ["git", "-C", str(scratch_repo), "branch", "--show-current"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    assert main_branch_before == main_branch_after  # the main worktree's branch never moved
    close_worktree(wt)
