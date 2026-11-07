# E2E dry run — 2026-11-07

**Setup:** Claude Code 2.1.319 (native install), `mattpocock-skills` 1.3.2 installed from the `mattpocock/skills` marketplace, private sandbox `Aragami007/harness-sandbox` (a 9-line Python CLI). The plugin was loaded with `claude --plugin-dir`. Sessions ran headless (`claude -p --permission-mode auto`), one turn per message, with the tester answering every question the harness asked, as the user would.

**Runs:**
- **STANDARD:** `/ship "Add a --version flag that prints 0.1.0"`. Every phase ran, ending at `done` with PR #3 merged by the human.
- **FOGGY:** `/ship "Add pluggable output formats (json, yaml)"`. It ran triage → fog → align, then `/ship --abort`.

Total model cost was about $6.

| # | Check | Expected | Result |
|---|---|---|---|
| 1 | Preflight on sandbox | all `[ok]`, `.gitignore` updated and committed | ✅ Passed. `.gitignore` committed on `harness/<slug>`, not `main`. |
| 2 | Gate 1 asked | route question shown; `approvals` includes `route` | ✅ Passed. Shown as plain text, since headless runs have no question dialog. |
| 3 | Grill interview | questions come to the user; GLOSSARY.md committed | ✅ Passed. 8 questions over 2 rounds. No glossary terms came up in STANDARD; FOGGY's align phase proposed three. |
| 4 | Gate 2 asked | spec at `docs/specs/<slug>/spec.md`; approval recorded | ✅ Passed. The spec was also published as issue #1 by `to-spec`. |
| 5 | Tickets | issues labeled `harness:ticket` + `harness:ready`; `tickets_sync.py` exits 0 on fresh issues | ✅ Passed. Issue #2 ended labeled `harness:ticket,harness:done`. `--remove-label` for absent labels did not error. |
| 6 | AFK build | Stop hook continues without user input; `build_iterations` increases | ⚠️ Not exercised in the real run: one ticket finished inside a single turn, so the hook had nothing to continue. Verified separately in a scratch repo: with `build_iterations=49` the hook continued the session once (Claude got the build-loop message listing `T1`), reached 50, logged `cap_reached` and allowed the stop. |
| 7 | STOP file | halts at the next stop; `halted` in log | ✅ Passed (scratch repo). The session stopped after one turn and `halted` was logged. |
| 8 | Guard | `git push origin main` denied with a dev-harness message | ✅ Passed. Also denied, each with the right message: `git push --force …` and `gh pr merge 3`. |
| 9 | Guard | direct edit of `.harness/state.json` denied | ✅ Passed, for both the shell redirect and the Write tool. The state file was intact afterwards. |
| 10 | PR | PR from `harness/<slug>` with structured body; Gate 3 asked; harness does not merge | ✅ Passed. PR #3 had a summary, diff, before/after evidence, two-way door rating, blast radius and blocked tickets. |
| 11 | Resume | after a manual merge, the MERGED check runs, then retro | ✅ Passed. The gate was approved only after `gh pr view` reported `MERGED`. |
| 12 | Retro | `.harness/retros/<slug>.md` exists; nothing applied automatically | ✅ Passed. 56 lines, proposals only. |
| 13 | FOGGY route | triage → fog; `harness/<slug>-fog` deleted; `map.md` committed on `harness/<slug>` | ✅ Passed. Wayfinder created map issue #4 with decision tickets #5 and #6. A real prototype showed a naive YAML emitter round-trips 2 of 16 tricky strings, so PyYAML was chosen. |
| 14 | Skill-name collisions | note conflicts and how they were resolved | ✅ Passed. Matt's skills are namespaced `mattpocock-skills:*` when installed as a plugin, so `code-review` and `pr` did not collide with built-ins. |

## Failures found and fixed

1. **Most of Matt Pocock's workflow skills can't be invoked by the model** (critical; first attempt stopped at setup). Nine skills set `disable-model-invocation: true`: `setup-matt-pocock-skills`, `ask-matt`, `wayfinder`, `grill-with-docs`, `to-spec`, `to-tickets`, `implement`, `implement-spec` and `retro`. Preflight only checked that their files exist, so it passed.
   - **Fix:** preflight writes `.harness/skills.json`, which maps each skill to its `SKILL.md` and prefers the installed `mattpocock` copy. The orchestrator follows that file whenever the Skill tool can't invoke the skill.
2. **Gates assumed AskUserQuestion was available.** Headless sessions don't have it.
   - **Fix:** gates and interviews fall back to asking in plain text and ending the turn. The user's next message is taken as the answer.

## Follow-ups (not fixed)

- `to-tickets` asks for sign-off on the ticket breakdown. That is Matt's own human check, not a harness gate, so a run has four human touchpoints, not three. Consider documenting it in the README, or folding it into the spec gate for single-ticket work (the retro also suggested this).
- The spec issue (#1) stays open after the PR merges, because the PR only closes the ticket issues. The pr phase could close the spec issue once every ticket is done.
- The retro is written to `.harness/retros/` and left untracked, which is by design, but nothing tells the user to commit it.
- The sandbox's tests needed `uv run --with pytest`, because pytest isn't in the system Python. That is a property of the target repo, not of the harness, and the retro flagged it as a CI gap.
- Unrelated: another installed plugin (`everything-claude-code`) logs `node: not found` from its SessionEnd hooks on this machine.
