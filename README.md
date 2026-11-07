# dev-harness

A Claude Code plugin that runs Matt Pocock's skills workflow end to end:

`/ship "<idea>"` → triage → (wayfinder) → grill → spec → tickets → parallel build → PR → retro

It stops only at three gates: confirm the route, approve the spec, and review/merge the PR. You also answer the grill interview.

## Requirements

- Claude Code, `gh` (logged in), Python ≥ 3.10
- Matt Pocock's skills installed: https://github.com/mattpocock/skills

## Install

**Try it for one session** (nothing is installed; run from the repo you want to work on):

```bash
claude --plugin-dir /path/to/dev-harness
```

**Install it for every session** (this repo is its own marketplace, `sdd-harness`):

```bash
claude plugin marketplace add /path/to/dev-harness
claude plugin install dev-harness@sdd-harness
```

Once installed, the hooks run in every Claude Code session, but they do nothing unless the repo has an active `.harness/state.json`. Remove the plugin with `claude plugin uninstall dev-harness@sdd-harness`.

## Use

| Command | Effect |
|---|---|
| `/ship "Add a --version flag"` | start a run |
| `/ship --resume` | continue after a gate, crash or new day |
| `/ship --status` | phase, gate and ticket summary |
| `/ship --abort` | stop the run |
| `touch .harness/STOP` | halt the AFK build loop at the next stop |

## Limitations

- The guard is best-effort protection against accidental commands, not an adversarial bypass barrier.
- `repo_root` needs git 2.31 or newer (`--path-format=absolute`); on older git it falls back to the working directory.
- During the build phase, any Claude session in the same repository is kept going by the Stop hook. Run other sessions elsewhere, or `touch .harness/STOP`.

## Develop

```bash
python3 -m venv .venv && .venv/bin/pip install pytest
.venv/bin/pytest -q
```
