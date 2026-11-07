# dev-harness

A Claude Code plugin that runs Matt Pocock's skills workflow end to end:

`/ship "<idea>"` → triage → (wayfinder) → grill → spec → tickets → parallel build → PR → retro

It stops only at three gates: confirm the route, approve the spec, and review/merge the PR. You also answer the grill interview.

## Requirements

- Claude Code, `gh` (logged in), Python ≥ 3.10
- Matt Pocock's skills installed: https://github.com/mattpocock/skills

## Install (local development)

```bash
claude --plugin-dir /path/to/dev-harness
```

## Use

| Command | Effect |
|---|---|
| `/ship "Add a --version flag"` | start a run |
| `/ship --resume` | continue after a gate, crash or new day |
| `/ship --status` | phase, gate and ticket summary |
| `/ship --abort` | stop the run |
| `touch .harness/STOP` | halt the AFK build loop at the next stop |

## Develop

```bash
python3 -m venv .venv && .venv/bin/pip install pytest
.venv/bin/pytest -q
```
