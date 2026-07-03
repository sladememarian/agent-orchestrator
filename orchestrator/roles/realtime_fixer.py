"""The real-time fixer — phase 4, matching the card the user wrote for
themselves: "just in case other agents made a bug this one must solve it fast."

Takes a worktree an earlier role already left with a failing test run, and
does exactly one fast, bounded fix pass: it only sees the files the previous
commit actually touched (via ``git diff --name-only``), asks for corrected
whole-file contents, re-runs the same test command, and commits the result as
a new commit on top — never amending, so the history shows what the developer
role did and what the fixer changed as two distinct steps.

If the fix doesn't make the tests pass, this stops after one attempt rather
than looping indefinitely against a live LLM — a human decides what happens
next, same "supervised" rule as every other role here.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..edit_protocol import EditProtocolError, parse_llm_file_edits, read_candidate_files
from ..git_worktree import Worktree, commit_all
from ..llm_client import LLMClient

SYSTEM_PROMPT = """\
You are the real-time bug-fixer for Collaberry, a real-time project workspace \
app (FastAPI microservices, MongoDB, Redis, Envoy gateway, React Native/Expo \
frontend). Another agent just made a change and its test run failed.

You will be given the failing test output and the complete current contents \
of the files that change touched. Make the SMALLEST possible correction that \
fixes the failure - do not refactor, do not touch anything unrelated to the \
failure. Respond with ONLY a JSON object mapping each file path (exactly as \
given) to that file's COMPLETE corrected content. Do not include files you \
did not change, files you weren't shown, or any text outside the JSON object."""


class RealtimeFixerError(RuntimeError):
    pass


@dataclass(slots=True)
class FixResult:
    worktree: Worktree
    commit_hash: str | None
    changed_files: list[str]
    test_passed: bool
    test_output: str = ""
    attempted: bool = True


def changed_files_in_last_commit(worktree: Worktree) -> list[str]:
    """The files the previous agent commit touched — the fixer's bounded
    scope. Never the whole repo; only what might actually be at fault.
    """
    result = subprocess.run(
        ["git", "-C", str(worktree.path), "diff", "--name-only", "HEAD~1..HEAD"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RealtimeFixerError(f"Could not list changed files: {result.stderr}")
    return [line for line in result.stdout.splitlines() if line.strip()]


def fix_failing_tests(
    *,
    worktree: Worktree,
    llm: LLMClient,
    test_command: list[str],
    failing_output: str,
    candidate_files: list[str] | None = None,
) -> FixResult:
    """Attempt exactly one fix pass. ``candidate_files`` defaults to whatever
    the last commit in this worktree touched - pass an explicit list to widen
    or narrow that scope.
    """
    files = candidate_files if candidate_files is not None else changed_files_in_last_commit(worktree)
    if not files:
        raise RealtimeFixerError("No candidate files to fix (empty diff and none supplied).")

    try:
        current = read_candidate_files(worktree.path, files)
    except EditProtocolError as exc:
        raise RealtimeFixerError(str(exc)) from exc

    files_block = "\n\n".join(f"--- {path} ---\n{content}" for path, content in current.items())
    user_prompt = f"Failing test output:\n{failing_output[-3000:]}\n\nCurrent files:\n\n{files_block}"

    reply = llm.chat("realtime_fixer", system=SYSTEM_PROMPT, user=user_prompt)
    try:
        edits = parse_llm_file_edits(reply, allowed_paths=set(files))
    except EditProtocolError as exc:
        raise RealtimeFixerError(str(exc)) from exc

    for rel_path, new_content in edits.items():
        target = worktree.path / rel_path
        target.write_text(new_content, encoding="utf-8")

    proc = subprocess.run(test_command, cwd=worktree.path, capture_output=True, text=True)
    test_passed = proc.returncode == 0
    test_output = (proc.stdout + proc.stderr)[-4000:]

    commit_hash = commit_all(worktree, f"agent(realtime-fixer): attempt to fix failing tests")

    return FixResult(
        worktree=worktree, commit_hash=commit_hash, changed_files=sorted(edits),
        test_passed=test_passed, test_output=test_output,
    )
