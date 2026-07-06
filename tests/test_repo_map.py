"""repo_map: a real file tree walk against a throwaway directory - no mocking,
this is cheap and deterministic enough to just run for real.
"""

from __future__ import annotations

from orchestrator.repo_map import build_repo_map


def test_lists_real_files_and_directories(tmp_path):
    (tmp_path / "services" / "auth").mkdir(parents=True)
    (tmp_path / "services" / "auth" / "main.py").write_text("x")
    (tmp_path / "README.md").write_text("x")

    listing = build_repo_map(tmp_path)

    assert "services/" in listing or "services" in listing
    assert "main.py" in listing
    assert "README.md" in listing


def test_skips_noise_directories(tmp_path):
    (tmp_path / "node_modules" / "some-pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "some-pkg" / "index.js").write_text("x")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("x")
    (tmp_path / "real.py").write_text("x")

    listing = build_repo_map(tmp_path)

    assert "node_modules" not in listing
    assert "index.js" not in listing
    assert ".git" not in listing
    assert "real.py" in listing


def test_caps_at_max_entries(tmp_path):
    for i in range(50):
        (tmp_path / f"file_{i}.txt").write_text("x")

    listing = build_repo_map(tmp_path, max_entries=10)

    lines = listing.splitlines()
    assert len(lines) <= 11  # 10 entries + the truncation note
    assert "truncated" in listing
