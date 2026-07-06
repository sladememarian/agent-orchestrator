"""A compact listing of the real Collaberry repo, fed into the head agent's
prompt so plans reference files that actually exist instead of a plausible-
sounding guess (early runs invented paths like ``mobile/src/screens/...`` when
the real folder is ``frontend/``, and ``services/workspace/models/card.py``
when the real file is ``services/workspace/app/repository.py``).

Kept intentionally cheap: just a file tree, no file contents - enough for the
model to ground itself in the actual project layout without blowing up the
prompt size or cost.
"""

from __future__ import annotations

from pathlib import Path

_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", ".expo",
    ".worktrees", ".pytest_cache", ".mypy_cache", "agent-orchestrator",
    ".idea", ".vscode", "keys", "web-build",
}
_SKIP_SUFFIXES = {".pyc", ".egg-info"}
MAX_ENTRIES = 400  # a hard cap so a huge repo can't blow up the prompt


def build_repo_map(repo_path: str | Path, *, max_entries: int = MAX_ENTRIES) -> str:
    """Return an indented file tree as plain text, rooted at ``repo_path``."""
    root = Path(repo_path).resolve()
    lines: list[str] = []
    _walk(root, root, lines, max_entries)
    if len(lines) >= max_entries:
        lines.append("... (truncated)")
    return "\n".join(lines)


def _walk(root: Path, current: Path, lines: list[str], max_entries: int) -> None:
    if len(lines) >= max_entries:
        return
    try:
        entries = sorted(current.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except (PermissionError, OSError):
        return

    for entry in entries:
        if len(lines) >= max_entries:
            return
        if entry.name in _SKIP_DIRS or any(entry.name.endswith(s) for s in _SKIP_SUFFIXES):
            continue
        depth = len(entry.relative_to(root).parts) - 1
        indent = "  " * depth
        if entry.is_dir():
            lines.append(f"{indent}{entry.name}/")
            _walk(root, entry, lines, max_entries)
        else:
            lines.append(f"{indent}{entry.name}")
