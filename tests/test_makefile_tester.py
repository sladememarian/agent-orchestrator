"""Makefile tester: real subprocess + a real, throwaway Makefile. No LLM
involved at all, so these tests (and the role itself) cost nothing to run.
"""

from __future__ import annotations

import shutil

import pytest

from orchestrator.roles.makefile_tester import run_make_targets

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed on this machine")


@pytest.fixture()
def repo_with_makefile(tmp_path):
    (tmp_path / "Makefile").write_text(
        "test-ok:\n\t@exit 0\n\ntest-fail:\n\t@exit 1\n\ntest-echo:\n\t@echo hello-from-make\n"
    )
    return tmp_path


def test_passing_target_is_reported_as_passed(repo_with_makefile):
    report = run_make_targets(repo_with_makefile, ["test-ok"])
    assert report.all_passed is True
    assert report.results[0].passed is True


def test_failing_target_is_reported_as_failed_without_stopping(repo_with_makefile):
    report = run_make_targets(repo_with_makefile, ["test-fail", "test-ok"])
    assert report.all_passed is False
    assert report.results[0].passed is False
    assert report.results[1].passed is True  # kept running after the failure


def test_output_is_captured(repo_with_makefile):
    report = run_make_targets(repo_with_makefile, ["test-echo"])
    assert "hello-from-make" in report.results[0].output


def test_summary_line_format(repo_with_makefile):
    report = run_make_targets(repo_with_makefile, ["test-ok", "test-fail"])
    assert report.summary_line() == "test-ok: PASS | test-fail: FAIL"
