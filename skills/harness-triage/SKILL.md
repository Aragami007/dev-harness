---
name: harness-triage
description: Decides whether a dev-harness run takes the FOGGY route (wayfinder first) or the STANDARD route (straight to alignment). Use only when the harness-orchestrator skill invokes it.
---

# dev-harness triage

Ask all three questions in a single AskUserQuestion call. Do not pre-select or recommend an answer.

1. **Size** — "How big is this effort?" Options: `One session`, `A few sessions`, `Many sessions`.
2. **Architecture** — "How well is the target architecture known?" Options: `Known`, `Partly known`, `Unknown`.
3. **Unknowns** — "Are there open external unknowns (APIs, libraries, infrastructure)?" Options: `None`, `Some`, `Major`.

## Rule (first match wins)

1. Architecture = Unknown → FOGGY
2. Unknowns = Major → FOGGY
3. Size ≠ One session AND (Architecture = Partly known OR Unknowns = Some) → FOGGY
4. Otherwise → STANDARD

## Output

Reply with exactly two lines and nothing else:

    ROUTE: <FOGGY|STANDARD>
    REASON: <the rule number and the answers that triggered it, in one sentence>
