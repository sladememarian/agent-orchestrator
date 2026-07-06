"""The whole-file edit protocol: the block-delimiter primary format plus the
JSON fallback, and the shared safety filter (path-set + escape rejection).
"""

from __future__ import annotations

import json

import pytest

from orchestrator.edit_protocol import EditProtocolError, parse_llm_file_edits


def test_block_format_single_file():
    raw = "<<<FILE app.py>>>\ndef f():\n    return 1\n<<<END>>>"
    edits = parse_llm_file_edits(raw, {"app.py"})
    assert edits == {"app.py": "def f():\n    return 1"}


def test_block_format_multiple_files_and_surrounding_prose():
    # A chatty model wraps the blocks in explanation - the prose is ignored,
    # both blocks are still parsed. This is the exact failure the first live
    # run hit (prose + snippets), now handled.
    raw = (
        "Sure! Here are the changes:\n\n"
        "<<<FILE a.py>>>\nA = 1\n<<<END>>>\n\n"
        "and the second file:\n\n"
        "<<<FILE b.py>>>\nB = 2\n<<<END>>>\n\n"
        "Let me know if you want anything else."
    )
    edits = parse_llm_file_edits(raw, {"a.py", "b.py"})
    assert edits == {"a.py": "A = 1", "b.py": "B = 2"}


def test_block_content_with_braces_and_quotes_needs_no_escaping():
    # The whole point vs JSON: TSX/JSON-ish content survives verbatim.
    body = 'const x = {"a": 1};\nAlert.alert("Delete?", undefined, []);'
    raw = f"<<<FILE screen.tsx>>>\n{body}\n<<<END>>>"
    edits = parse_llm_file_edits(raw, {"screen.tsx"})
    assert edits["screen.tsx"] == body


def test_json_fallback_still_works():
    raw = json.dumps({"app.py": "x = 1"})
    assert parse_llm_file_edits(raw, {"app.py"}) == {"app.py": "x = 1"}


def test_paths_outside_the_allowed_set_are_dropped():
    raw = "<<<FILE evil.py>>>\nx=1\n<<<END>>>\n<<<FILE ok.py>>>\ny=2\n<<<END>>>"
    edits = parse_llm_file_edits(raw, {"ok.py"})
    assert edits == {"ok.py": "y=2"}


def test_path_escape_is_refused():
    raw = "<<<FILE ../../etc/passwd>>>\npwned\n<<<END>>>"
    with pytest.raises(EditProtocolError, match="path-escaping"):
        parse_llm_file_edits(raw, {"../../etc/passwd"})


def test_prose_only_reply_with_no_blocks_or_json_errors_clearly():
    raw = "```typescript\nimport { Alert } from 'react-native';\n```\nThen wrap onDelete..."
    with pytest.raises(EditProtocolError, match="neither the <<<FILE>>> block format nor valid JSON"):
        parse_llm_file_edits(raw, {"screen.tsx"})


def test_no_matching_files_errors():
    raw = "<<<FILE other.py>>>\nx=1\n<<<END>>>"
    with pytest.raises(EditProtocolError, match="no usable edits"):
        parse_llm_file_edits(raw, {"wanted.py"})
