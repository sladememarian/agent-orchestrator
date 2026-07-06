# Case Study — Managing a Fleet of Sub-Agents Against a Live Product Board

> A résumé-oriented write-up of what this project demonstrates. For how to run
> it, see [README.md](README.md).

## One-line summary

I built a **hierarchical multi-agent system** that reads its own backlog from a
real running application's Kanban board (over that app's public REST API),
dispatches specialised LLM sub-agents to plan and implement each card in
isolated git branches, and stays under human supervision — then I acted as the
**engineering manager** over that fleet: reviewing the agents' output, hardening
the orchestrator when they failed, and shipping the features they couldn't.

## The setup

- **The product** — *Collaberry*, a real, production-shaped app I built:
  four FastAPI microservices (auth, workspace, presence, notification) behind an
  Envoy gateway, MongoDB + Redis, WebSocket live-sync, a React Native (Expo)
  client. It has a real Kanban board with real task cards.
- **The orchestrator** — a separate Python system that treats that board as its
  work queue. It never touches the database directly; it authenticates as a bot
  user and drives the same public API the mobile app uses.
- **The models** — every LLM call routes through **9router**, a local
  OpenAI-compatible gateway fanning out to many providers, so each agent role
  runs on a model chosen for its job: an **Opus-class head planner**, and
  **Sonnet-class workers**.

## The agent hierarchy (prompt engineering)

| Role | Job | Key prompt-engineering decision |
|---|---|---|
| **Head planner** | Reads each card, writes a concrete implementation plan back onto it | Prompt is grounded in the repo's **real file tree** so plans cite files that exist, not plausible guesses |
| **Developer** | Implements one card in an isolated git worktree | Emits whole-file edits in a **block-delimiter protocol** (not JSON) — chosen after a live failure, see below |
| **Real-time fixer** | One bounded pass to fix a failing test the developer left | Scoped to *only* the files the last commit touched; commits its attempt either way so history shows what it tried |
| **Makefile tester** | Runs `make` targets, reports pass/fail | Makes **zero LLM calls** — a mechanical task shouldn't cost a token |

**Hard safety rail:** sub-agents work in throwaway git worktrees, commit
locally, and **never push, merge, or open a PR**. The system prepares work for
review; a human ships it. This is enforced in code and covered by tests, not
just asked for in a prompt.

## Acting as the manager — three real findings

Running the fleet against live models surfaced problems a toy demo never would.
Each became an engineering decision:

1. **A "model" was actually an agent runtime.** One provider route
   (`ac-prod/claude-sonnet-5`) replied to a plain completion request with
   Claude-Code-style *tool-call traces* (`Search(...)`, `Read(...)`) against an
   imagined filesystem, instead of an answer. Diagnosis: that tier is a wrapped
   coding-agent session, incompatible with a stateless orchestrator. I detect
   this failure mode explicitly and surface a clear error pointing at the fix,
   rather than crashing on an empty response.

2. **Strict JSON output is brittle for whole-file edits.** The first developer
   run produced a *correct design* but wrapped it in prose and ` ```typescript `
   snippets instead of the required JSON object — unparseable. Rather than fight
   the model, I **replaced the protocol** with a block-delimiter format
   (`<<<FILE …>>> … <<<END>>>`) that needs no escaping, parses each file
   independently, and tolerates surrounding prose. JSON stays as a fallback.

3. **Free routes are rate-limited under real load.** A worker that passed a
   tiny probe got a 403 with a two-minute cooldown on the full-size request. The
   orchestrator already had bounded retries + timeouts; the manager's move was
   to **unblock by delivering** the feature myself — exactly what a lead does
   when a contractor is stuck on infrastructure.

## Outcome

- The head planner produced genuinely grounded plans (it even flagged an
  ambiguous card as under-specified and asked for clarification instead of
  inventing work — the correct behaviour).
- Two board cards were taken to **Done** end-to-end: an item-**priority** field
  (shipped through models → repository → API → React Native, with unit + e2e
  tests) and a delete-**confirmation** dialog (worker's design, manager's
  delivery), both verified by the full test suite (`tsc --noEmit` clean, e2e
  green through Envoy).
- The orchestrator itself was hardened along the way: parallel planning,
  per-request timeouts, retry-with-backoff, output-token budgets, an
  XSS-safe live dashboard, and the edit-protocol rewrite — all test-covered.

## What this demonstrates

Distributed-systems design, LLM/agent orchestration, prompt engineering under
real (not ideal) conditions, and the judgement to know when to fix the tool,
when to switch models, and when to stop delegating and ship.
