"""The whole-file edit protocol shared by any role that asks the LLM to change
files (developer, real-time fixer). One parser, one set of safety rules, so
every role gets the same guarantees: the model can only touch files it was
explicitly shown, and no path can escape the worktree.

**Why a delimiter format instead of JSON.** The first live run asked Sonnet 5
for a JSON object mapping paths to whole-file contents. It replied with prose
and ```typescript snippets - a correct *design* but an unparseable *response*.
Strict JSON is genuinely hard for chat models to emit for a 300-line file:
every quote, backslash and newline has to be escaped, and the whole object has
to stay valid to parse at all. A block-delimiter format sidesteps all of that -
each file block parses independently, needs no escaping, and models follow it
far more reliably. JSON is kept as a fallback for older prompts / other models.

Format the model is asked to produce:

    <<<FILE path/relative/to/repo>>>
    ...entire new file content, verbatim...
    <<<END>>>

Repeat one block per changed file. Anything outside the blocks is ignored, so a
model that also writes an explanation doesn't break parsing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# Instruction snippet roles paste into their system prompt so the model and the
# parser always agree on the exact shape. Kept here next to the parser.
EDIT_FORMAT_INSTRUCTIONS = (
    "For every file you change, output a block in EXACTLY this form and nothing "
    "else around it:\n"
    "<<<FILE path/relative/to/repo>>>\n"
    "the file's COMPLETE new content, verbatim\n"
    "<<<END>>>\n"
    "Repeat one block per changed file. Use only the file paths you were given. "
    "Do not use markdown code fences. Do not truncate or abbreviate file "
    "content with comments like '// unchanged' - always output the whole file."
)

_BLOCK_RE = re.compile(
    r"<<<FILE\s+(?P<path>.+?)\s*>>>\r?\n(?P<body>.*?)\r?\n?<<<END>>>",
    re.DOTALL,
)


class EditProtocolError(RuntimeError):
    pass


def parse_llm_file_edits(raw: str, allowed_paths: set[str]) -> dict[str, str]:
    """Parse the model's reply into ``{path: new_content}``.

    Tries the block-delimiter format first, then falls back to a JSON object
    (possibly wrapped in a ```json fence). Applies the same safety filter to
    both: silently drop any path outside ``allowed_paths``, and hard-refuse any
    path that tries to escape the worktree.
    """
    edits = _parse_blocks(raw)
    if edits is None:
        edits = _parse_json(raw)

    safe: dict[str, str] = {}
    for path, content in edits.items():
        path = path.strip()
        if path not in allowed_paths:
            continue  # outside the bounded set the role explicitly showed the model
        if ".." in Path(path).parts or Path(path).is_absolute():
            raise EditProtocolError(f"Refusing a path-escaping edit: {path!r}")
        safe[path] = content

    if not safe:
        raise EditProtocolError(
            "Model made no usable edits to any of the candidate files.\n"
            f"---\n{raw[:600]}"
        )
    return safe


def _parse_blocks(raw: str) -> dict[str, str] | None:
    """Return the delimiter blocks, or None if the reply isn't in that format."""
    matches = list(_BLOCK_RE.finditer(raw))
    if not matches:
        return None
    return {m.group("path"): m.group("body") for m in matches}


def _parse_json(raw: str) -> dict[str, str]:
    """Fallback: a JSON object of {path: content}, optionally fenced."""
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EditProtocolError(
            "Model reply was neither the <<<FILE>>> block format nor valid JSON "
            f"({exc}).\n---\n{raw[:600]}"
        ) from exc
    if not isinstance(parsed, dict):
        raise EditProtocolError("JSON reply must be an object of {path: content}")
    for path, content in parsed.items():
        if not isinstance(content, str):
            raise EditProtocolError(f"Content for {path!r} must be a string")
    return parsed


def read_candidate_files(repo_root: Path, relative_paths: list[str]) -> dict[str, str]:
    contents: dict[str, str] = {}
    for rel in relative_paths:
        full = repo_root / rel
        if not full.is_file():
            raise EditProtocolError(f"Candidate file does not exist in the repo: {rel}")
        contents[rel] = full.read_text(encoding="utf-8", errors="replace")
    return contents
