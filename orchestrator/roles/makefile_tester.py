"""The Makefile tester — phase 4, matching the card "I need one agent testing
the make files."

Deliberately makes **no LLM call at all**. Running `make test-unit` and
reporting pass/fail is a mechanical task an LLM adds cost and latency to
without adding value - this role is a plain subprocess wrapper. (The
``MODEL_MAKEFILE_TESTER`` setting still exists for a future summarisation
step, but the core pass/fail loop never needs to spend a token.)
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class TargetResult:
    target: str
    passed: bool
    output: str


@dataclass(slots=True)
class MakefileTestReport:
    results: list[TargetResult]

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    def summary_line(self) -> str:
        parts = [f"{r.target}: {'PASS' if r.passed else 'FAIL'}" for r in self.results]
        return " | ".join(parts)


def run_make_targets(repo_path: str | Path, targets: list[str]) -> MakefileTestReport:
    """Run each ``make <target>`` in the given repo, in order, capturing
    output for each. Does not stop early on a failing target - a caller wants
    to know the status of every target it asked about, not just the first
    failure.
    """
    repo = Path(repo_path).resolve()
    results: list[TargetResult] = []
    for target in targets:
        proc = subprocess.run(
            ["make", target], cwd=repo, capture_output=True, text=True,
        )
        output = (proc.stdout + proc.stderr)[-4000:]
        results.append(TargetResult(target=target, passed=proc.returncode == 0, output=output))
    return MakefileTestReport(results=results)
