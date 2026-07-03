"""Shared "propose whole-file edits as JSON" protocol used by any role that
asks the LLM to change files (developer, real-time fixer). One parser, one set
of safety rules, so every role gets the same guarantees: the model can only
touch files it was explicitly shown, and no path can escape the worktree.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


class EditProtocolError(RuntimeError):
    pass


def parse_llm_file_edits(raw: str, allowed_paths: set[str]) -> dict[str, str]:
    """Extract the {path: content} JSON the model returned, tolerating a
    ```json ... ``` fence some models wrap responses in anyway, and rejecting
    anything outside the allowed file set or that looks like a path escape.
    """
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EditProtocolError(f"Model reply wasn't valid JSON: {exc}\n---\n{raw[:500]}") from exc

    if not isinstance(parsed, dict):
        raise EditProtocolError("Model reply must be a JSON object of {path: content}")

    edits: dict[str, str] = {}
    for path, content in parsed.items():
        if path not in allowed_paths:
            continue  # silently drop anything outside the bounded file set
        if ".." in Path(path).parts or Path(path).is_absolute():
            raise EditProtocolError(f"Refusing a path-escaping edit: {path!r}")
        if not isinstance(content, str):
            raise EditProtocolError(f"Content for {path!r} must be a string")
        edits[path] = content

    if not edits:
        raise EditProtocolError("Model made no edits to any of the candidate files")
    return edits


def read_candidate_files(repo_root: Path, relative_paths: list[str]) -> dict[str, str]:
    contents: dict[str, str] = {}
    for rel in relative_paths:
        full = repo_root / rel
        if not full.is_file():
            raise EditProtocolError(f"Candidate file does not exist in the repo: {rel}")
        contents[rel] = full.read_text(encoding="utf-8", errors="replace")
    return contents
