"""Makefile tester: real subprocess + a real, throwaway Makefile. No LLM
involved at all, so these tests (and the role itself) cost nothing to run.
"""

from __future__ import annotations

import shutil
from unittest.mock import patch

import pytest

from orchestrator.roles.makefile_tester import run_make_targets


def test_missing_make_binary_is_reported_not_crashed(tmp_path):
    # Simulated regardless of whether `make` happens to be installed on the
    # machine running this test, so it's not environment-dependent - this is
    # what happens on a fresh Windows machine with no `make` on PATH at all,
    # which previously crashed the whole CLI command with an unhandled
    # FileNotFoundError instead of reporting a clean failure.
    with patch("subprocess.run", side_effect=FileNotFoundError("no such file")):
        report = run_make_targets(tmp_path, ["test-unit"])

    assert report.all_passed is False
    assert report.results[0].passed is False
    assert "not installed" in report.results[0].output


needs_real_make = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed on this machine")


@pytest.fixture()
def repo_with_makefile(tmp_path):
    (tmp_path / "Makefile").write_text(
        "test-ok:\n\t@exit 0\n\ntest-fail:\n\t@exit 1\n\ntest-echo:\n\t@echo hello-from-make\n"
    )
    return tmp_path


@needs_real_make
def test_passing_target_is_reported_as_passed(repo_with_makefile):
    report = run_make_targets(repo_with_makefile, ["test-ok"])
    assert report.all_passed is True
    assert report.results[0].passed is True


@needs_real_make
def test_failing_target_is_reported_as_failed_without_stopping(repo_with_makefile):
    report = run_make_targets(repo_with_makefile, ["test-fail", "test-ok"])
    assert report.all_passed is False
    assert report.results[0].passed is False
    assert report.results[1].passed is True  # kept running after the failure


@needs_real_make
def test_output_is_captured(repo_with_makefile):
    report = run_make_targets(repo_with_makefile, ["test-echo"])
    assert "hello-from-make" in report.results[0].output


@needs_real_make
def test_summary_line_format(repo_with_makefile):
    report = run_make_targets(repo_with_makefile, ["test-ok", "test-fail"])
    assert report.summary_line() == "test-ok: PASS | test-fail: FAIL"
