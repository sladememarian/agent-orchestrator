# Agent Orchestrator

A supervised, hierarchical multi-agent system that reads its own task board
from a real running app ([Collaberry](../)) and does the work on it — planning,
then writing real code, on isolated git branches, for a human to review.

Every LLM call is routed through **9router**, a local OpenAI-compatible proxy
that fans out to many providers — so the "head" planner and each specialist
sub-agent can run on a different model, chosen for the job rather than a single
one-size-fits-all choice.

> **[→ Read the case study](CASE_STUDY.md)** — the résumé-oriented write-up of
> what this demonstrates: managing a fleet of sub-agents against a live product
> board, and the real failures (an "agent-runtime" masquerading as a model, a
> brittle output protocol, rate-limited routes) that shaped the design.

## Why this exists

Most agent demos operate on a to-do list you type into the terminal. This one
reads its tasks from a real Kanban board in a real production-shaped app
(FastAPI microservices, MongoDB, Redis, WebSockets, an Envoy gateway) over that
app's own public API — the same one its mobile client uses. Nothing here talks
to the database directly; it's a proper external integration.

## How it works

```
   9router (localhost:20128)
        │  OpenAI-compatible chat completions, 18 connected providers
        ▼
┌───────────────────┐        ┌──────────────────────────────┐
│   Head agent       │──────▶│  Collaberry board (via API)   │
│  reads the board,   │        │  cards this system works from │
│  plans, assigns     │◀──────│  and writes plans back onto    │
└─────────┬───────────┘        └──────────────────────────────┘
          │ assigns
          ▼
┌───────────────────────────────────────────────────────┐
│  Sub-agents (one role each, own model choice)           │
│  developer · real-time bug-fixer · makefile tester       │
│  each works in an isolated git worktree — never the       │
│  live checkout, never pushes, never opens a PR alone       │
└───────────────────────────────────────────────────────┘
```

Every sub-agent stops after committing locally. Pushing, opening a PR, or
merging is a separate, explicit human step — this system prepares work for
review, it doesn't ship it unsupervised.

## Status

Actively being built in phases:

- [x] Phase 1 — read the live board, print it
- [x] Phase 2 — head agent plans each card, writes the plan back
- [x] Phase 3 — one sub-agent implements a card for real, in an isolated worktree
- [x] Phase 4 — specialist roles (real-time bug-fixer, Makefile tester)
- [x] Phase 5 — live dashboard visualizing agents at work

## Running it

```bash
pip install -e .
cp .env.example .env   # fill in your Collaberry + 9router details

orchestrator board                                     # phase 1: print the live Kanban board
orchestrator invite-bot                                # one-time: let the bot see your board
orchestrator plan                                      # phase 2: plan every unplanned card
orchestrator implement "bug fix" \                      # phase 3: implement one planned card
  --files services/workspace/app/main.py \
  --test "python -m pytest services/workspace/tests"
```

`implement` always works in an isolated git worktree under
`agent-orchestrator/.worktrees/` and stops after a **local** commit — nothing is
ever pushed, merged, or opened as a PR automatically. Review the printed branch
by hand (`git diff HEAD~1..HEAD` inside the worktree path it prints) before
deciding what to do with it.

If `implement --test` reports failing tests, hand that same worktree to the
real-time fixer for one bounded attempt:

```bash
orchestrator fix-tests agent-orchestrator/.worktrees/<card>-<slug> \
  --test "python -m pytest services/workspace/tests"
```

It only sees the files the previous commit touched, tries exactly one fix, and
commits the attempt as a new commit either way (so the history shows what was
tried) — it does not loop or keep calling the LLM until something works.

Or check the Makefile targets with no LLM call at all:

```bash
orchestrator make-test "test-unit,fe-check"
```

## Watching it work: the live dashboard

```bash
orch dashboard
```

Opens a small live feed at `http://127.0.0.1:8800` — every role, every card,
every commit hash and pass/fail, as it happens. `board`/`plan`/`implement`/
`fix-tests`/`make-test` all best-effort report their activity here; if the
dashboard isn't running, those commands are unaffected (a half-second timeout,
then silent no-op) — it's a window to watch through, not a dependency.

Both `orchestrator` and the shorter `orch` work identically for every command
above; run `orch help` (or bare `orch`) to list them all.
