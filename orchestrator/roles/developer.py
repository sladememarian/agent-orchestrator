"""The developer role — phase 3: takes one planned card and actually
implements it, for real, in an isolated git worktree.

Design choice: the LLM is asked for **complete new file contents**, not a
diff. A single non-agentic call can't reliably produce a correct unified diff
against files it hasn't interactively browsed line-by-line; asking for whole
files for an explicit, caller-supplied set of paths is far more robust for a
first pass. It also bounds the blast radius: the model can only touch files it
was shown, never invent arbitrary new ones outside that list (enforced below,
not just requested via prompt).

This role never pushes, merges, or touches ``origin`` — see git_worktree.py.
It also never runs a second LLM call to "fix" a failing test; that's the
real-time fixer's job (phase 4), kept separate on purpose.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..board_client import BoardClient, Item
from ..edit_protocol import (
    EDIT_FORMAT_INSTRUCTIONS,
    EditProtocolError,
    parse_llm_file_edits,
    read_candidate_files,
)
from ..git_worktree import Worktree, close_worktree, commit_all, open_worktree
from ..llm_client import LLMClient

SYSTEM_PROMPT = f"""\
You are a software engineer implementing one task card for Collaberry, a \
real-time project workspace app (FastAPI microservices, MongoDB, Redis, \
Envoy gateway, React Native/Expo frontend).

You will be given a card's title, its plan, and the full current contents of \
a fixed set of files. Make the change the card asks for, touching only those \
files.

{EDIT_FORMAT_INSTRUCTIONS}"""


class DeveloperError(RuntimeError):
    pass


@dataclass(slots=True)
class DeveloperResult:
    item: Item
    worktree: Worktree
    commit_hash: str
    changed_files: list[str]
    test_command: str | None
    test_passed: bool | None
    test_output: str = ""


def implement_card(
    *,
    item: Item,
    client: BoardClient,
    llm: LLMClient,
    repo_path: str | Path,
    candidate_files: list[str],
    test_command: list[str] | None = None,
) -> DeveloperResult:
    """Implement one card in an isolated worktree and commit the result
    locally. Never pushes. The worktree is left open on disk for a human to
    review (``git diff``, run it, decide) — call ``close_worktree`` yourself
    once you're done with it.
    """
    plan = item.data.get("agent_plan")
    if not plan:
        raise DeveloperError(f"Card {item.title!r} has no agent_plan yet — run `orchestrator plan` first.")

    client.move_item_to_column(item.id, "In progress")

    repo_root = Path(repo_path).resolve()
    worktree = open_worktree(repo_root, card_id=item.id, card_title=item.title)

    try:
        try:
            current = read_candidate_files(worktree.path, candidate_files)
        except EditProtocolError as exc:
            raise DeveloperError(str(exc)) from exc
        files_block = "\n\n".join(f"--- {path} ---\n{content}" for path, content in current.items())
        user_prompt = f"Card: {item.title}\n\nPlan:\n{plan}\n\nCurrent files:\n\n{files_block}"

        reply = llm.chat("developer", system=SYSTEM_PROMPT, user=user_prompt)
        try:
            edits = parse_llm_file_edits(reply, allowed_paths=set(candidate_files))
        except EditProtocolError as exc:
            raise DeveloperError(str(exc)) from exc

        for rel_path, new_content in edits.items():
            target = worktree.path / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(new_content, encoding="utf-8")

        test_passed: bool | None = None
        test_output = ""
        if test_command:
            proc = subprocess.run(test_command, cwd=worktree.path, capture_output=True, text=True)
            test_passed = proc.returncode == 0
            test_output = (proc.stdout + proc.stderr)[-4000:]  # bounded, not the whole firehose

        commit_message = f"agent(developer): {item.title}\n\ncard: {item.id}\nplan:\n{plan}"
        commit_hash = commit_all(worktree, commit_message)

        status = "[needs review]" if test_passed is not False else "[needs review - tests failed]"
        client.update_item(item.id, title=f"{status} {item.title}")

        return DeveloperResult(
            item=item, worktree=worktree, commit_hash=commit_hash,
            changed_files=sorted(edits), test_command=" ".join(test_command) if test_command else None,
            test_passed=test_passed, test_output=test_output,
        )
    except Exception:
        # Leave the worktree in place on failure too - useful for debugging
        # what the agent attempted - but make sure the card doesn't stay
        # silently stuck "In progress" forever.
        client.update_item(item.id, title=f"[agent error] {item.title}")
        raise
