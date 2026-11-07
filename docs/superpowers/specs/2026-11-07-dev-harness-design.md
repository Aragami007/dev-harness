# dev-harness — Design Spec

**Date:** 2026-11-07
**Status:** Approved in brainstorming; awaiting written-spec review

## 1. Goal

A reusable Claude Code plugin that automates the full Matt Pocock skills development workflow
(setup → foggy planning → alignment → spec → tickets → parallel implementation → PR → retro)
behind a single `/ship` command, stopping only at three human gates.

## 2. Decisions

| Topic | Decision |
|---|---|
| Autonomy | Gated: the human answers the grill interview, approves the route, approves the spec, and reviews/merges the PR. All other phases run unattended. |
| Runtime | Claude Code plugin (commands, skills, hooks). No separate runtime. |
| Phase skills | Wrap Matt Pocock's published skills; the harness does not reimplement them. |
| Tracker | GitHub Issues via the `gh` CLI. |
| Foggy routing | Auto-triage questionnaire; route confirmed at Gate 1. |
| Location | Standalone plugin repo (`dev-harness/`), installable into any project. |
| Orchestration | `/ship` + state file is the source of truth; a Stop hook provides the AFK loop **only** during the build phase. GitHub labels mirror state but never drive it. |

## 3. Phase flow

| # | Phase | Skill(s) invoked | Output artifact | Gate |
|---|---|---|---|---|
| 0 | Preflight | `/setup-matt-pocock-skills` (first run per repo only) | `.harness/state.json` initialized | — |
| 1 | Triage | `harness-triage` (ask-matt style) | `route: FOGGY \| STANDARD` | **Gate 1: confirm route** |
| 2 | Fog (FOGGY only) | `/wayfinder`, then `/prototype` and/or `/research` per open decision | `docs/specs/<slug>/map.md`, Wayfinder child issues | — |
| 3 | Align | `/grill-with-docs` (human answers); `domain-modeling` fires as the model decides | `GLOSSARY.md` updates | (interactive by nature) |
| 4 | Spec | `/to-spec` | `docs/specs/<slug>/spec.md` | **Gate 2: approve spec** |
| 5 | Tickets | `/to-tickets` | GitHub issues labeled `harness:ticket`; dependency graph stored in state | — |
| 6 | Build | `/implement-spec` (parallel worktree subagents; `tdd` + `code-review` per ticket) | branches/commits merged to an integration branch `harness/<slug>` | — |
| 7 | PR | `pr` skill | PR from `harness/<slug>` to the default branch | **Gate 3: human reviews/merges** |
| 8 | Retro | `/retro` | `.harness/retros/<slug>.md` with proposed CLAUDE.md / CI changes (proposals only, not applied) | — |

Phase 2 is skipped when `route = STANDARD`. Prototype code from phase 2 is created on a throwaway branch and deleted by the end of phase 2.

## 4. Components

```
dev-harness/
  .claude-plugin/plugin.json
  commands/ship.md
  skills/harness-orchestrator/SKILL.md
  skills/harness-triage/SKILL.md
  hooks/hooks.json
  hooks/build_loop.py
  hooks/guard.py
  scripts/state.py
  scripts/tickets_sync.py
  scripts/preflight.py
  tests/
```

- **`commands/ship.md`** — entry point. Forms: `/ship "<idea>"` (start), `/ship --resume`, `/ship --status`, `/ship --abort`. Loads the orchestrator skill and delegates.
- **`skills/harness-orchestrator`** — the phase table above, gate rules, and the instruction that every state change goes through `scripts/state.py`. At a gate it uses AskUserQuestion and records the answer.
- **`skills/harness-triage`** — fixed questionnaire: expected size (sessions), is the architecture known, are there unresolved external unknowns. Any "unknown architecture" or ">1 session with open decisions" answer → FOGGY.
- **`scripts/state.py`** — the sole writer of `.harness/state.json`. Subcommands: `init`, `get`, `transition <phase>`, `set-gate`, `clear-gate`, `ticket-update`, `log`. Validates transitions against the legal-transition table; illegal transitions exit non-zero with a message.
- **`scripts/tickets_sync.py`** — mirrors ticket status to GitHub labels (`harness:ready`, `harness:in-progress`, `harness:blocked`, `harness:done`) via `gh`. One-directional: state → GitHub.
- **`scripts/preflight.py`** — checks: inside a git repo, clean working tree, `gh auth status` ok, Matt's skills discoverable. On failure, prints the exact fix command and exits non-zero.
- **`hooks/build_loop.py`** (Stop hook) — see §6.
- **`hooks/guard.py`** (PreToolUse) — blocks: direct `Write`/`Edit` to `.harness/state.json`; `git push --force`; pushes or merges to the default branch; file edits outside the active worktree during phase 6.

## 5. State file

`.harness/state.json` in the target repo (`.harness/` added to `.gitignore` by preflight, except `retros/`).

```json
{
  "version": 1,
  "slug": "add-version-flag",
  "idea": "Add a --version flag",
  "phase": "build",
  "route": "STANDARD",
  "gate_pending": null,
  "artifacts": { "map": null, "spec": "docs/specs/add-version-flag/spec.md", "pr": null },
  "integration_branch": "harness/add-version-flag",
  "tickets": [
    { "id": "T1", "issue": 42, "deps": [], "status": "done", "attempts": 1, "worktree": null }
  ],
  "build_iterations": 7,
  "log": [ { "ts": "2026-11-07T10:00:00Z", "event": "transition", "from": "tickets", "to": "build" } ]
}
```

Legal transitions: `preflight→triage→(fog|align)`, `fog→align→spec→tickets→build→pr→retro→done`, any phase→`aborted`. `spec→tickets` requires the Gate 2 approval to be recorded; `triage→*` requires Gate 1.

Ticket statuses: `ready → in-progress → (done | failed)`; `failed` with `attempts ≥ 3` → `blocked`.

## 6. Build loop (Stop hook)

When Claude stops, `build_loop.py` reads state and:

1. If `.harness/STOP` exists → allow the stop and log `halted`.
2. If `phase != "build"` or `gate_pending` is set → allow the stop.
3. If `build_iterations ≥ 50` → allow the stop and log `cap_reached`.
4. If any ticket is `ready` or `in-progress` (deps satisfied) → increment `build_iterations`, return `{"decision":"block","reason":<fixed template>}`. The template names ticket IDs and the spec path **from state only**; no issue or PR text is inserted.
5. Otherwise (all tickets `done`/`blocked`) → transition to `pr` and block once with the "run the pr phase" template.

## 7. Failure handling

- Ticket fails tests or `code-review` → retried with the reviewer's findings, up to 3 attempts. Then `blocked`: labeled `harness:blocked`, its transitive dependents marked `blocked` (reason: dependency), and the run continues with independent tickets.
- If every ticket is blocked, phase 7 is skipped and the run stops with a summary of blockers.
- Preflight failure → stop with the fix command; no state is written beyond `init`.
- `/ship --resume` re-enters at `phase`; an in-progress ticket whose worktree is missing is reset to `ready`.
- `/ship --abort` → transition to `aborted`; worktrees are left in place and listed for the user to clean up.

## 8. Testing

- **Unit (pytest):** `state.py` legal/illegal transitions and gate requirements; `build_loop.py` decision for each branch of §6 (fixture state files, stdin hook payloads); `guard.py` allow/deny cases; `tickets_sync.py` with `gh` mocked.
- **End-to-end dry run:** a toy Python CLI repo; `/ship "Add a --version flag"` through every phase on STANDARD, with a forced FOGGY triage run separately to exercise phase 2. Success: PR opened with the structured body, retro file written, no guard violations.

## 9. Out of scope

- Auto-merging PRs or auto-applying retro recommendations.
- Trackers other than GitHub Issues.
- Running outside Claude Code (CI/cron/SDK).
- Reimplementing any Matt Pocock skill.

## 10. Open risks

- Matt's skill names and arguments may change upstream; the orchestrator skill pins the expected names in one table so a rename is a one-line fix.
- `/implement-spec` already orchestrates worktrees; the harness relies on it rather than managing worktrees itself, and only reads its results into state.
