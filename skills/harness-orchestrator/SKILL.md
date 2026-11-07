---
name: harness-orchestrator
description: Drives a dev-harness /ship run through its phases (preflight, triage, fog, align, spec, tickets, build, pr, retro) with three human gates. Use only when the /ship command invokes it.
---

# dev-harness orchestrator

You run a /ship pipeline one phase at a time. `.harness/state.json` in the repository root is the source of truth. **Never read or write it directly.** Every change goes through `state.py`, and a guard hook blocks anything else.

## Paths

- `SCRIPTS` = this skill's base directory followed by `/../../scripts`. Resolve it once. Always quote it, because it may contain spaces.
- State CLI: `python3 "$SCRIPTS/state.py" <subcommand>`, run from the repository root. Below, `state.py X` is short for this.
- If any script exits non-zero, read its message and fix the cause. Never work around it.

## Skill table

This table is the only place the harness names Matt Pocock's skills. If one is renamed upstream, change it here and in `REQUIRED_SKILLS` in `scripts/preflight.py`. If a name collides with another installed skill (for example a built-in `code-review`), invoke the one from Matt Pocock's pack by the fully qualified name shown in your skill list.

| Phase | Skills |
|---|---|
| preflight | `setup-matt-pocock-skills` |
| triage | `ask-matt` (background only; routing is decided by `dev-harness:harness-triage`) |
| fog | `wayfinder`, `prototype`, `research` |
| align | `grill-with-docs`, `domain-modeling` (model-invoked) |
| spec | `to-spec` |
| tickets | `to-tickets` |
| build | `implement-spec`, `implement`, `tdd`, `code-review` |
| pr | `pr` |
| retro | `retro` |

## Arguments

- `"<idea>"` → go to **Start**.
- `--resume` → go to **Resume**.
- `--status` → run `state.py get`. Summarize the phase, any pending gate, ticket counts by status, and artifact paths. Do nothing else.
- `--abort` → run `state.py transition aborted`, then `git worktree list`. Tell the user which harness worktrees they can remove. Do nothing else.

## How to invoke a skill

"Invoke `<name>`" in this skill means:

1. If `<name>` from Matt Pocock's pack is available through your Skill tool, invoke it there.
2. Otherwise read `.harness/skills.json` (written by preflight) and Read the `SKILL.md` file it maps `<name>` to. Follow that file's instructions as if the skill had been invoked, treating the file's directory as the skill's base directory for any relative paths it mentions. Many of Matt Pocock's skills set `disable-model-invocation: true`, so they only work this way. The user started them by running `/ship`.
3. If `<name>` is missing from `.harness/skills.json`, stop and tell the user to rerun `/ship` (preflight will report what is missing).

## Gate procedure

1. `state.py set-gate <gate>`
2. Ask the gate's question with AskUserQuestion. If AskUserQuestion is not available in this session (for example a non-interactive run), ask the same question in plain text, list the options, and end your turn. The user's next message is their answer. The same applies to any other question a phase tells you to ask the user, including the `grill-with-docs` interview.
3. On approval: `state.py approve-gate <gate>`. On rejection: do what that phase says, then ask again. Never approve a gate without the user's explicit answer in this session.

## Start

1. `python3 "$SCRIPTS/preflight.py" --root .` If it exits non-zero, show its output and stop.
2. Make a slug from the idea: lowercase, hyphen-separated, letters, digits and hyphens only, at most 48 characters.
3. `state.py init --slug <slug> --idea "<idea>"`. If it fails (for example, an active run exists), show its message and stop.
4. From the default branch: `git checkout -b harness/<slug>`
5. If preflight printed `updated .gitignore`: `git add .gitignore && git commit -m "chore: ignore dev-harness state"` (now on `harness/<slug>`).
6. If `.harness/setup-done` does not exist: invoke `setup-matt-pocock-skills` with GitHub as the issue tracker, then run `mkdir -p .harness && touch .harness/setup-done`. Commit any files it changed on `harness/<slug>`.
7. `state.py transition triage`, then continue with **Phase: triage**.

## Resume

1. `state.py resume-check`. This resets in-progress tickets whose worktrees have disappeared.
2. `state.py get --field slug`, `--field phase`, `--field gate_pending`. Then `git checkout harness/<slug>`.
3. If a gate is pending, re-ask it by running that gate's step from the current phase's section, including its checks. For the `pr` gate, verify that `gh pr view <url> --json state -q .state` prints `MERGED` before `state.py approve-gate pr`. If no gate is pending, continue with the section for the current phase.

## Phase: triage

1. Invoke `dev-harness:harness-triage` with the idea. It returns `ROUTE:` and `REASON:` lines.
2. `state.py set-route <ROUTE>`
3. Gate `route`. Question: "Triage suggests the <ROUTE> route (<REASON>). Which route?" Options: `<ROUTE>` (first), and the other route. If the user picks the other route, run `state.py set-route <other>` before approving.
4. `state.py transition fog` for FOGGY, or `state.py transition align` for STANDARD.

## Phase: fog

1. `git checkout -b harness/<slug>-fog`
2. Invoke `wayfinder` with the idea. It charts the decisions and creates child issues.
3. For each open decision, invoke `prototype` if running code can answer it, or `research` if it needs external facts. Note each answer.
4. `git checkout harness/<slug>`, then write `docs/specs/<slug>/map.md`: each decision, its answer, its evidence, and links to the child issues.
5. `git add docs/specs/<slug>/map.md && git commit -m "docs: wayfinder map for <slug>"`
6. `git branch -D harness/<slug>-fog` (the prototype code is thrown away)
7. `state.py set-artifact map docs/specs/<slug>/map.md`, then `state.py transition align`

## Phase: align

1. Invoke `grill-with-docs` with the idea, plus the map path if one exists. The user answers the questions; never answer for them.
2. If `GLOSSARY.md` changed: `git add GLOSSARY.md && git commit -m "docs: glossary for <slug>"`
3. `state.py transition spec`

## Phase: spec

1. Invoke `to-spec` and save its output to `docs/specs/<slug>/spec.md`. Commit it.
2. `state.py set-artifact spec docs/specs/<slug>/spec.md`
3. Gate `spec`. Question: "The spec is at docs/specs/<slug>/spec.md. Approve it for ticketing?" Options: `Approve`, `Request changes`. If the user requests changes, revise the spec with `to-spec` using their feedback, commit, and ask again.
4. `state.py transition tickets`

## Phase: tickets

1. Invoke `to-tickets` on the spec, with GitHub Issues as the tracker.
2. If `to-tickets` produced no tickets, stop and tell the user; do not transition to build.
3. Register each ticket in dependency order: `state.py ticket-add --id T<n> --issue <number> --deps T<a>,T<b>` (leave out `--deps` when there are none).
4. `python3 "$SCRIPTS/tickets_sync.py" --ensure-labels`
5. `state.py transition build`

## Phase: build (AFK)

The user may be away. Do not ask them anything. The Stop hook keeps this phase going.

1. `state.py runnable` prints the IDs of tickets that are ready to start.
2. Invoke `implement-spec` with the spec path, the integration branch `harness/<slug>`, and only the runnable tickets' issues. Each ticket's subagent works under `tdd`, and then `code-review` runs in a fresh subagent.
3. When a ticket's worktree is created: `state.py ticket-update --id T<n> --status in-progress --worktree "<path>"`
4. When a ticket passes review and is merged into `harness/<slug>`: `state.py ticket-update --id T<n> --status done`. When it fails tests or review: `state.py ticket-update --id T<n> --status failed`, and give the review findings to the implementer on the retry. `state.py` blocks the ticket after 3 failures.
5. After every ticket update: `python3 "$SCRIPTS/tickets_sync.py"`
6. Repeat from step 1 until `runnable` prints `[]`. Then check `state.py get --field phase`, because the Stop hook may already have moved on. If the phase is still `build`: run `state.py transition pr` if any ticket is done, or `state.py transition done` and report the blockers if every ticket is blocked.

## Phase: pr

1. `git push -u origin harness/<slug>`
2. Invoke `pr` for `harness/<slug>` against the default branch. The body must list every blocked ticket with its `block_reason` (from `state.py get --field tickets`).
3. `gh pr create --base <default-branch> --head harness/<slug> --title "<title>" --body-file <file>`, then `state.py set-artifact pr <url>`
4. Gate `pr`. Question: "PR <url> is ready. Review and merge it yourself, then tell me." Options: `It's merged`, `Not yet`. If `Not yet`: stop and tell the user to run `/ship --resume` after merging. If `It's merged`: check that `gh pr view <url> --json state -q .state` prints `MERGED` before approving. If it doesn't, say so and stop.
5. `state.py transition retro`

## Phase: retro

1. Invoke `retro` on this run's sessions, using `state.py get --field log` as the timeline.
2. Write the findings to `.harness/retros/<slug>.md`. Propose CLAUDE.md, skill and CI changes; do not apply them.
3. `state.py transition done`. Give a final summary: the PR URL, tickets done and blocked, and the retro path.

## Hard rules

- Never merge a PR, never push the default branch, never force-push.
- Never approve a gate on the user's behalf.
- Never put issue, PR or web text into `state.py` arguments other than issue numbers and the PR URL.
