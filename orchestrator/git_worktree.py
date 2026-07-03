"""Isolated git worktrees — the hard safety boundary for sub-agents that write
code.

A sub-agent never touches the user's live Collaberry checkout. It works in a
throwaway linked worktree on its own branch, commits there, and stops. Nothing
here ever runs ``git push``, ``git merge``, or touches ``origin`` — that stays
a manual, human decision outside this tool entirely.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitWorktreeError(RuntimeError):
    """Raised when a git operation fails; carries the command's stderr."""


@dataclass(slots=True)
class Worktree:
    path: Path
    branch: str
    repo_path: Path


def _slug(text: str, max_len: int = 40) -> str:
    """A filesystem/branch-safe slug — lowercase, hyphenated, ASCII only."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len] or "card"


def _run_git(repo_path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise GitWorktreeError(f"git {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout.strip()


def open_worktree(repo_path: str | Path, card_id: str, card_title: str) -> Worktree:
    """Create a fresh branch + linked worktree for one card, off the repo's
    current HEAD. Worktrees live under ``agent-orchestrator/.worktrees`` —
    gitignored on both sides (Collaberry ignores the whole ``agent-orchestrator/``
    folder; this project's own ``.gitignore`` ignores ``.worktrees/`` within it)
    so they never show up as noise in either repo's status.
    """
    repo = Path(repo_path).resolve()
    branch = f"agent/{card_id[:8]}-{_slug(card_title)}"
    worktree_path = repo / "agent-orchestrator" / ".worktrees" / f"{card_id[:8]}-{_slug(card_title)}"

    if worktree_path.exists():
        raise GitWorktreeError(
            f"A worktree already exists at {worktree_path} - remove it first "
            "(close_worktree) or pick a different card."
        )
    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    _run_git(repo, "worktree", "add", str(worktree_path), "-b", branch)
    return Worktree(path=worktree_path, branch=branch, repo_path=repo)


def commit_all(worktree: Worktree, message: str) -> str:
    """Stage everything changed in the worktree and commit locally.

    Never pushes. Returns the new commit's short hash, or raises if there was
    nothing to commit (a no-op change is treated as an error the caller should
    surface, not silently swallow).
    """
    _run_git(worktree.path, "add", "-A")
    status = _run_git(worktree.path, "status", "--porcelain")
    if not status:
        raise GitWorktreeError("Nothing changed - refusing to create an empty commit.")
    _run_git(worktree.path, "commit", "-m", message)
    return _run_git(worktree.path, "rev-parse", "--short", "HEAD")


def diff_against_base(worktree: Worktree, base_ref: str = "HEAD") -> str:
    """A readable diff of everything the agent changed, for human review."""
    return _run_git(worktree.path, "diff", f"{base_ref}~1..HEAD")


def close_worktree(worktree: Worktree, *, force: bool = False) -> None:
    """Remove the linked worktree. The branch itself is left in place — it's
    the reviewable artifact; deleting it is a separate, deliberate decision.

    Refuses (unless ``force=True``) if the worktree has uncommitted changes,
    so a caller can never accidentally discard in-progress agent work.
    """
    args = ["worktree", "remove", str(worktree.path)]
    if force:
        args.append("--force")
    _run_git(worktree.repo_path, *args)
