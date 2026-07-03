"""Command-line entry point.

    orchestrator board         - print the live Kanban board (phase 1)
    orchestrator plan          - have the head agent plan every unplanned card (phase 2)
    orchestrator implement     - have the developer role implement one card, in an
                                  isolated worktree, and stop for human review (phase 3)
    orchestrator fix-tests     - have the real-time fixer take one failed-test attempt
                                  at an existing worktree (phase 4)
    orchestrator make-test     - run Makefile targets against the Collaberry repo and
                                  report pass/fail, no LLM involved (phase 4)
    orchestrator invite-bot    - grant the bot account access to your board

All of these talk to the real, running Collaberry stack over its public API.
"""

from __future__ import annotations

import argparse
import getpass
import shlex
import subprocess
import sys
from pathlib import Path

import httpx
from rich.console import Console
from rich.table import Table

from .board_client import BoardClient, BoardClientError
from .config import get_settings
from .git_worktree import GitWorktreeError, Worktree
from .llm_client import LLMClient, LLMClientError
from .roles.developer import DeveloperError, implement_card
from .roles.head import plan_board
from .roles.makefile_tester import run_make_targets
from .roles.realtime_fixer import RealtimeFixerError, fix_failing_tests

console = Console()


def cmd_board(_: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        with BoardClient(settings) as client:
            client.connect()
            items = client.list_items()
    except BoardClientError as exc:
        console.print(f"[bold red]Can't read the board:[/] {exc}")
        console.print(
            "\nIf this is a fresh bot account, it needs to be invited to the "
            "workspace that owns this board first - run:\n"
            "  [cyan]orchestrator invite-bot[/]"
        )
        return 1
    except httpx.HTTPError as exc:
        console.print(f"[bold red]Couldn't reach Collaberry at {settings.collaberry_gateway_url}:[/] {exc}")
        console.print("Is the stack up? (`make up` in the Collaberry repo)")
        return 1

    board = client.board
    assert board is not None
    table = Table(title=f"{board.name}  ({len(items)} cards)")
    for col in sorted(board.columns, key=lambda c: c.order):
        table.add_column(col.name, overflow="fold")

    # Plain ASCII tags, not unicode icons — some Windows console codepages
    # (e.g. cp1256) can't encode box-drawing/checkbox glyphs and crash rich's
    # legacy renderer outright.
    by_column: dict[str, list[str]] = {c.id: [] for c in board.columns}
    for item in sorted(items, key=lambda i: i.order):
        tag = {"card": "[card]", "document": "[doc]", "checklist": "[list]"}.get(item.type, "[?]")
        by_column.setdefault(item.column_id, []).append(f"{tag} {item.title}")

    columns_sorted = sorted(board.columns, key=lambda c: c.order)
    max_rows = max((len(by_column[c.id]) for c in columns_sorted), default=0)
    for row_idx in range(max_rows):
        table.add_row(*[
            by_column[c.id][row_idx] if row_idx < len(by_column[c.id]) else ""
            for c in columns_sorted
        ])

    console.print(table)
    return 0


def cmd_plan(_: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        with BoardClient(settings) as client:
            client.connect()
            llm = LLMClient(settings)
            planned = plan_board(client, llm)
    except BoardClientError as exc:
        console.print(f"[bold red]Can't read the board:[/] {exc}")
        return 1
    except LLMClientError as exc:
        console.print(f"[bold red]9router error:[/] {exc}")
        return 1
    except httpx.HTTPError as exc:
        console.print(f"[bold red]Couldn't reach Collaberry at {settings.collaberry_gateway_url}:[/] {exc}")
        return 1

    if not planned:
        console.print("Every card already has a plan. Nothing to do.")
        return 0

    for pc in planned:
        console.print(f"\n[bold cyan]{pc.item.title}[/]  ({pc.item.type})")
        console.print(pc.plan)
    console.print(f"\n[bold green]Planned {len(planned)} card(s).[/]")
    return 0


def cmd_implement(args: argparse.Namespace) -> int:
    settings = get_settings()
    candidate_files = [f.strip() for f in args.files.split(",") if f.strip()]
    test_command = shlex.split(args.test) if args.test else None

    try:
        with BoardClient(settings) as client:
            client.connect()
            items = client.list_items()
            matches = [i for i in items if args.title.lower() in i.title.lower()]

            if not matches:
                console.print(f"[bold red]No card title contains {args.title!r}.[/]")
                return 1
            if len(matches) > 1:
                console.print(f"[bold red]{len(matches)} cards match {args.title!r} - be more specific:[/]")
                for m in matches:
                    console.print(f"  - {m.title}")
                return 1

            item = matches[0]
            if not client.has_agent_plan(item):
                console.print(
                    f"[bold red]{item.title!r} has no plan yet.[/] Run "
                    f"[cyan]orchestrator plan[/] first."
                )
                return 1

            llm = LLMClient(settings)
            console.print(f"Implementing [cyan]{item.title}[/] in an isolated worktree...")
            result = implement_card(
                item=item, client=client, llm=llm,
                repo_path=settings.collaberry_repo_path,
                candidate_files=candidate_files, test_command=test_command,
            )
    except BoardClientError as exc:
        console.print(f"[bold red]Can't read the board:[/] {exc}")
        return 1
    except LLMClientError as exc:
        console.print(f"[bold red]9router error:[/] {exc}")
        return 1
    except DeveloperError as exc:
        console.print(f"[bold red]Couldn't implement this card:[/] {exc}")
        return 1
    except httpx.HTTPError as exc:
        console.print(f"[bold red]Couldn't reach Collaberry at {settings.collaberry_gateway_url}:[/] {exc}")
        return 1

    console.print(f"\n[bold green]Committed locally[/] ({result.commit_hash}) on branch [cyan]{result.worktree.branch}[/]")
    console.print(f"Changed files: {', '.join(result.changed_files)}")
    if result.test_command:
        verdict = "[bold green]passed[/]" if result.test_passed else "[bold red]FAILED[/]"
        console.print(f"Test command ({result.test_command}): {verdict}")
        if not result.test_passed:
            console.print(result.test_output)
    console.print(f"\nNothing was pushed. Review it yourself:")
    console.print(f"  cd \"{result.worktree.path}\"")
    console.print(f"  git diff HEAD~1..HEAD")
    console.print(f"\nWhen you're done reviewing, clean up the worktree from the Collaberry repo with:")
    console.print(f"  git -C \"{settings.collaberry_repo_path}\" worktree remove \"{result.worktree.path}\"")
    return 0


def _reopen_worktree(worktree_path: str, repo_path: str) -> Worktree:
    """Reconstruct a Worktree handle for an existing linked worktree on disk
    (e.g. one `orchestrator implement` left behind), so `fix-tests` can be run
    as a separate step without threading the object through a long-lived
    process.
    """
    path = Path(worktree_path).resolve()
    if not path.is_dir():
        raise GitWorktreeError(f"No such worktree directory: {path}")
    branch = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()
    if not branch:
        raise GitWorktreeError(f"{path} doesn't look like a git worktree.")
    return Worktree(path=path, branch=branch, repo_path=Path(repo_path).resolve())


def cmd_fix_tests(args: argparse.Namespace) -> int:
    settings = get_settings()
    test_command = shlex.split(args.test)
    candidate_files = [f.strip() for f in args.files.split(",")] if args.files else None

    try:
        worktree = _reopen_worktree(args.worktree, settings.collaberry_repo_path)
    except GitWorktreeError as exc:
        console.print(f"[bold red]{exc}[/]")
        return 1

    first_run = subprocess.run(test_command, cwd=worktree.path, capture_output=True, text=True)
    if first_run.returncode == 0:
        console.print("Tests already pass in this worktree - nothing to fix.")
        return 0

    failing_output = (first_run.stdout + first_run.stderr)[-3000:]
    llm = LLMClient(settings)
    console.print(f"Tests are failing on branch [cyan]{worktree.branch}[/]. Attempting one fix pass...")

    try:
        result = fix_failing_tests(
            worktree=worktree, llm=llm, test_command=test_command,
            failing_output=failing_output, candidate_files=candidate_files,
        )
    except LLMClientError as exc:
        console.print(f"[bold red]9router error:[/] {exc}")
        return 1
    except RealtimeFixerError as exc:
        console.print(f"[bold red]Couldn't attempt a fix:[/] {exc}")
        return 1

    verdict = "[bold green]now passing[/]" if result.test_passed else "[bold red]still failing[/]"
    console.print(f"\nAfter one fix attempt: tests are {verdict}")
    console.print(f"Changed files: {', '.join(result.changed_files)}")
    console.print(f"Committed as {result.commit_hash} on top of the existing branch history.")
    if not result.test_passed:
        console.print(result.test_output)
        console.print("\nThis needs a human now - the automatic fix pass didn't work.")
    return 0 if result.test_passed else 1


def cmd_make_test(args: argparse.Namespace) -> int:
    settings = get_settings()
    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    report = run_make_targets(settings.collaberry_repo_path, targets)

    for r in report.results:
        verdict = "[bold green]PASS[/]" if r.passed else "[bold red]FAIL[/]"
        console.print(f"{r.target}: {verdict}")
        if not r.passed:
            console.print(r.output)

    console.print(f"\n{report.summary_line()}")
    return 0 if report.all_passed else 1


def cmd_invite_bot(_: argparse.Namespace) -> int:
    settings = get_settings()
    console.print(f"Inviting the bot account [cyan]{settings.collaberry_bot_email}[/] to "
                  f"the workspace that owns board [cyan]{settings.collaberry_board_name!r}[/].\n")
    owner_email = input("Your Collaberry email: ").strip()
    owner_password = getpass.getpass("Your Collaberry password: ")

    http = httpx.Client(base_url=settings.collaberry_gateway_url, trust_env=False, timeout=20.0)

    owner_login = http.post("/api/v1/auth/login", json={"email": owner_email, "password": owner_password})
    if owner_login.status_code != 200:
        console.print(f"[bold red]Login failed[/] ({owner_login.status_code}): {owner_login.text}")
        return 1
    owner_token = owner_login.json()["access_token"]

    # Make sure the bot account exists so we know its user id.
    with BoardClient(settings) as bot_client:
        bot_token = bot_client._login_or_register()  # noqa: SLF001 — internal reuse is fine here
    bot_user_id = httpx.get(
        f"{settings.collaberry_gateway_url}/api/v1/auth/me",
        headers={"Authorization": f"Bearer {bot_token}"},
        trust_env=False,
    ).json()["id"]

    http.headers["Authorization"] = f"Bearer {owner_token}"
    workspaces = http.get("/api/v1/workspace/workspaces").json()
    target_ws = None
    for ws in workspaces:
        boards = http.get(f"/api/v1/workspace/workspaces/{ws['id']}/boards").json()
        if any(b["name"] == settings.collaberry_board_name for b in boards):
            target_ws = ws
            break

    if target_ws is None:
        console.print(f"[bold red]No workspace of yours has a board named "
                       f"{settings.collaberry_board_name!r}.[/]")
        return 1

    add = http.post(
        f"/api/v1/workspace/workspaces/{target_ws['id']}/members",
        json={"user_id": bot_user_id, "role": "editor"},
    )
    if add.status_code != 200:
        console.print(f"[bold red]Couldn't add the bot as a member[/] ({add.status_code}): {add.text}")
        return 1

    console.print(f"[bold green]Done.[/] The bot can now see workspace {target_ws['name']!r}.")
    console.print("Try: [cyan]orchestrator board[/]")
    return 0


def app() -> None:
    parser = argparse.ArgumentParser(prog="orchestrator")
    sub = parser.add_subparsers(required=True)

    p_board = sub.add_parser("board", help="print the live Kanban board")
    p_board.set_defaults(func=cmd_board)

    p_plan = sub.add_parser("plan", help="have the head agent plan every unplanned card")
    p_plan.set_defaults(func=cmd_plan)

    p_implement = sub.add_parser(
        "implement", help="have the developer role implement one planned card in an isolated worktree"
    )
    p_implement.add_argument("title", help="a substring that uniquely matches one card's title")
    p_implement.add_argument(
        "--files", required=True,
        help="comma-separated repo-relative paths the developer role may edit, e.g. "
             "services/workspace/app/main.py,services/workspace/tests/test_workspace_api.py",
    )
    p_implement.add_argument(
        "--test", default=None,
        help="optional shell command to run inside the worktree after editing, e.g. "
             '"python -m pytest services/workspace/tests"',
    )
    p_implement.set_defaults(func=cmd_implement)

    p_fix = sub.add_parser("fix-tests", help="have the real-time fixer take one attempt at an existing worktree's failing tests")
    p_fix.add_argument("worktree", help="path to an existing worktree (printed by `orchestrator implement`)")
    p_fix.add_argument("--test", required=True, help='the same test command used with `implement`, e.g. "python -m pytest services/workspace/tests"')
    p_fix.add_argument("--files", default=None, help="optional comma-separated override of which files the fixer may touch (defaults to whatever the last commit changed)")
    p_fix.set_defaults(func=cmd_fix_tests)

    p_make = sub.add_parser("make-test", help="run Makefile targets against the Collaberry repo (no LLM call)")
    p_make.add_argument("targets", help='comma-separated make targets, e.g. "test-unit,fe-check"')
    p_make.set_defaults(func=cmd_make_test)

    p_invite = sub.add_parser("invite-bot", help="invite the bot account into your board's workspace")
    p_invite.set_defaults(func=cmd_invite_bot)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    app()
