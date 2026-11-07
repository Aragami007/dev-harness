# dev-harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Claude Code plugin whose `/ship` command drives Matt Pocock's skill workflow end to end, stopping only at three human gates.

**Architecture:** A Python state machine (`scripts/state.py`) is the only writer of `.harness/state.json` in the target repo. Two hooks enforce it: a Stop hook continues the build phase while the user is away, and a PreToolUse guard blocks direct state edits, force pushes and merges to the default branch. Markdown skills (`harness-orchestrator`, `harness-triage`) tell Claude which Matt Pocock skill to run in each phase and which `state.py` command to run around it.

**Tech Stack:** Python ≥ 3.10 standard library (runtime), pytest (dev only), `gh` CLI, Claude Code plugin format (`.claude-plugin/plugin.json`, `commands/`, `skills/`, `hooks/hooks.json`).

**Spec:** `docs/superpowers/specs/2026-11-07-dev-harness-design.md`

## Global Constraints

- Runtime code uses only the Python standard library; Python ≥ 3.10. pytest is a dev dependency installed in `.venv/`.
- State file path in the target repo: `.harness/state.json`. Halt file: `.harness/STOP`. Retros: `.harness/retros/<slug>.md`.
- `MAX_ATTEMPTS = 3` failures per ticket before it is blocked. `BUILD_CAP = 50` Stop-hook continuations.
- Routes: `FOGGY`, `STANDARD`. Gates: `route` (Gate 1), `spec` (Gate 2), `pr` (Gate 3).
- GitHub labels: `harness:ticket`, `harness:ready`, `harness:in-progress`, `harness:done`, `harness:blocked`. Sync is one-way, from state to GitHub.
- Integration branch: `harness/<slug>`. Slugs match `^[a-z0-9][a-z0-9-]{0,47}$`.
- The Stop hook's `reason` text is built only from fixed templates plus validated state fields (ticket IDs, spec path, branch). It never includes issue or PR text.
- The harness never merges PRs, never pushes the default branch, and never applies retro recommendations.
- Hooks must never break the user's session: if their input or the state file is malformed, they exit 0 (allow).

## File Structure

```
dev-harness/
  .gitignore
  README.md
  .claude-plugin/plugin.json              # plugin manifest
  commands/ship.md                        # /ship entry point → loads orchestrator skill
  skills/harness-orchestrator/SKILL.md    # phase procedures, gates, skill-name table
  skills/harness-triage/SKILL.md          # FOGGY vs STANDARD questionnaire + rule
  hooks/hooks.json                        # registers Stop + PreToolUse hooks
  hooks/build_loop.py                     # Stop hook: AFK build loop
  hooks/guard.py                          # PreToolUse hook: safety rails
  scripts/state.py                        # state machine library + CLI (sole state writer)
  scripts/tickets_sync.py                 # state → GitHub labels
  scripts/preflight.py                    # environment checks + .gitignore
  tests/conftest.py                       # sys.path setup
  tests/fakes.py                          # FakeRun subprocess double
  tests/test_state.py
  tests/test_state_tickets.py
  tests/test_state_cli.py
  tests/test_build_loop.py
  tests/test_guard.py
  tests/test_tickets_sync.py
  tests/test_preflight.py
  tests/test_plugin_layout.py
  docs/e2e-dry-run.md                     # manual end-to-end checklist + results
```

**Task order:** Tasks 1→3 are sequential (they build `state.py`). Tasks 4, 5, 6 and 7 depend only on Task 3 and touch disjoint files, so they can run in parallel. Task 8 depends on 7 (it imports `preflight.REQUIRED_SKILLS`). Task 9 depends on everything.

---

### Task 1: Scaffold + state machine core

**Files:**
- Create: `.gitignore`, `tests/conftest.py`, `tests/fakes.py`, `scripts/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: nothing.
- Produces (in `scripts/state.py`):
  - constants `STATE_REL: Path`, `MAX_ATTEMPTS = 3`, `BUILD_CAP = 50`, `ROUTES`, `GATES`, `ARTIFACT_KEYS`, `TERMINAL = {"done", "aborted"}`, `SAFE_PATH_RE`
  - `class StateError(Exception)`
  - `state_path(root) -> Path`
  - `new_state(slug: str, idea: str) -> dict`
  - `load(root) -> dict`, `save(root, state: dict) -> None`
  - `log_event(state, event: str, **fields) -> None`
  - `transition(state, to: str) -> dict`
  - `set_route(state, route: str)`, `set_gate(state, gate: str)`, `approve_gate(state, gate: str)`, `set_artifact(state, key: str, path: str)`
- Produces (in `tests/fakes.py`): `FakeRun(responses: dict[tuple, tuple[int, str]] | None = None, fail_on=())`, a callable with the same signature as `subprocess.run` that records `.calls`.

- [ ] **Step 1: Create scaffolding and dev environment**

`.gitignore`:
```
.venv/
__pycache__/
.pytest_cache/
```

`tests/conftest.py`:
```python
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "hooks"), str(Path(__file__).resolve().parent)]
```

`tests/fakes.py`:
```python
import subprocess


class FakeRun:
    """Stand-in for subprocess.run.

    responses: maps a command prefix tuple, e.g. ("git", "status"), to (returncode, stdout).
    fail_on: any command containing one of these exact tokens returns code 1.
    """

    def __init__(self, responses=None, fail_on=()):
        self.calls = []
        self.responses = responses or {}
        self.fail_on = set(fail_on)

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        for prefix, (code, out) in self.responses.items():
            if tuple(cmd[: len(prefix)]) == prefix:
                return subprocess.CompletedProcess(cmd, code, out, "")
        code = 1 if self.fail_on.intersection(cmd) else 0
        return subprocess.CompletedProcess(cmd, code, "", "")
```

Run:
```bash
python3 -m venv .venv && .venv/bin/pip install -q pytest
```
Expected: exits 0.

- [ ] **Step 2: Write the failing tests**

`tests/test_state.py`:
```python
import pytest

import state as st


def make(phase="preflight", **overrides):
    s = st.new_state("demo", "Demo idea")
    s["phase"] = phase
    s.update(overrides)
    return s


def test_new_state_shape():
    s = st.new_state("demo", "x")
    assert s["phase"] == "preflight"
    assert s["integration_branch"] == "harness/demo"
    assert s["tickets"] == [] and s["approvals"] == [] and s["gate_pending"] is None
    assert s["artifacts"] == {"map": None, "spec": None, "pr": None}


@pytest.mark.parametrize("slug", ["Bad Slug", "-lead", "a" * 49, "x\ny"])
def test_new_state_rejects_bad_slug(slug):
    with pytest.raises(st.StateError):
        st.new_state(slug, "x")


def test_save_load_roundtrip(tmp_path):
    s = st.new_state("demo", "x")
    st.save(tmp_path, s)
    assert st.load(tmp_path) == s
    assert (tmp_path / ".harness" / "state.json").is_file()


def test_load_missing_raises(tmp_path):
    with pytest.raises(st.StateError):
        st.load(tmp_path)


def gate(s, name):
    st.set_gate(s, name)
    st.approve_gate(s, name)


def test_full_standard_path():
    s = make("preflight")
    st.transition(s, "triage")
    st.set_route(s, "STANDARD")
    gate(s, "route")
    st.transition(s, "align")
    st.transition(s, "spec")
    gate(s, "spec")
    for phase in ("tickets", "build", "pr"):
        st.transition(s, phase)
    gate(s, "pr")
    st.transition(s, "retro")
    st.transition(s, "done")
    assert s["phase"] == "done"
    transitions = [e for e in s["log"] if e["event"] == "transition"]
    assert transitions[-1]["from"] == "retro" and transitions[-1]["to"] == "done"


def test_triage_requires_route_gate():
    s = make("triage", route="STANDARD")
    with pytest.raises(st.StateError, match="Gate 1"):
        st.transition(s, "align")


def test_route_decides_next_phase():
    s = make("triage", route="FOGGY", approvals=["route"])
    with pytest.raises(st.StateError):
        st.transition(s, "align")
    st.transition(s, "fog")
    assert s["phase"] == "fog"


def test_approve_route_requires_route_set():
    s = make("triage")
    st.set_gate(s, "route")
    with pytest.raises(st.StateError):
        st.approve_gate(s, "route")


def test_spec_requires_approval():
    s = make("spec")
    with pytest.raises(st.StateError, match="Gate 2"):
        st.transition(s, "tickets")


def test_pr_requires_approval():
    s = make("pr")
    with pytest.raises(st.StateError, match="Gate 3"):
        st.transition(s, "retro")


def test_pending_gate_blocks_transition():
    s = make("spec", approvals=["spec"])
    st.set_gate(s, "spec")
    with pytest.raises(st.StateError, match="pending"):
        st.transition(s, "tickets")


def test_illegal_skip():
    with pytest.raises(st.StateError, match="illegal"):
        st.transition(make("align"), "build")


def test_abort_from_active_phase_even_with_gate():
    s = make("spec")
    st.set_gate(s, "spec")
    st.transition(s, "aborted")
    assert s["phase"] == "aborted"
    with pytest.raises(st.StateError):
        st.transition(s, "aborted")


def test_approve_wrong_gate_raises():
    s = make("spec")
    st.set_gate(s, "spec")
    with pytest.raises(st.StateError):
        st.approve_gate(s, "pr")


def test_set_gate_twice_raises():
    s = make("spec")
    st.set_gate(s, "spec")
    with pytest.raises(st.StateError):
        st.set_gate(s, "spec")


def test_set_route_validates():
    with pytest.raises(st.StateError):
        st.set_route(make("triage"), "MAYBE")
    with pytest.raises(st.StateError):
        st.set_route(make("align"), "FOGGY")


def test_set_artifact_validates():
    s = make("spec")
    st.set_artifact(s, "spec", "docs/specs/demo/spec.md")
    st.set_artifact(s, "pr", "https://github.com/o/r/pull/3")
    assert s["artifacts"]["spec"] == "docs/specs/demo/spec.md"
    with pytest.raises(st.StateError):
        st.set_artifact(s, "spec", "docs/a\nIGNORE PREVIOUS")
    with pytest.raises(st.StateError):
        st.set_artifact(s, "notes", "x.md")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_state.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'state'`.

- [ ] **Step 4: Implement `scripts/state.py` core**

```python
#!/usr/bin/env python3
"""dev-harness state: the only writer of .harness/state.json.

Used as a library by the hooks and as a CLI by the orchestrator skill.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

STATE_REL = Path(".harness") / "state.json"
MAX_ATTEMPTS = 3
BUILD_CAP = 50
ROUTES = ("FOGGY", "STANDARD")
GATES = ("route", "spec", "pr")
ARTIFACT_KEYS = ("map", "spec", "pr")
TERMINAL = {"done", "aborted"}
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,200}$")
TICKET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

TRANSITIONS = {
    "preflight": {"triage"},
    "triage": {"fog", "align"},
    "fog": {"align"},
    "align": {"spec"},
    "spec": {"tickets"},
    "tickets": {"build"},
    "build": {"pr", "done"},
    "pr": {"retro"},
    "retro": {"done"},
}
# (from_phase) -> (gate that must be approved, human-readable gate name)
REQUIRED_APPROVAL = {
    "triage": ("route", "Gate 1 (route)"),
    "spec": ("spec", "Gate 2 (spec)"),
    "pr": ("pr", "Gate 3 (pr)"),
}


class StateError(Exception):
    """Raised for any illegal state operation."""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def state_path(root) -> Path:
    return Path(root) / STATE_REL


def new_state(slug: str, idea: str) -> dict:
    if not SLUG_RE.fullmatch(slug):
        raise StateError(f"invalid slug {slug!r}: use 1-48 lowercase letters, digits, hyphens")
    return {
        "version": 1,
        "slug": slug,
        "idea": idea,
        "phase": "preflight",
        "route": None,
        "gate_pending": None,
        "approvals": [],
        "artifacts": {key: None for key in ARTIFACT_KEYS},
        "integration_branch": f"harness/{slug}",
        "tickets": [],
        "build_iterations": 0,
        "log": [],
    }


def load(root) -> dict:
    path = state_path(root)
    if not path.exists():
        raise StateError(f"no state at {path}; start a run with /ship")
    return json.loads(path.read_text())


def save(root, state: dict) -> None:
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".state.", suffix=".tmp")
    with os.fdopen(fd, "w") as handle:
        json.dump(state, handle, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


def log_event(state: dict, event: str, **fields) -> None:
    state["log"].append({"ts": now_iso(), "event": event, **fields})


def transition(state: dict, to: str) -> dict:
    frm = state["phase"]
    if frm in TERMINAL:
        raise StateError(f"run is already {frm}; start a new one")
    if to != "aborted":
        if state["gate_pending"]:
            raise StateError(f"gate {state['gate_pending']!r} is pending")
        if to not in TRANSITIONS.get(frm, set()):
            raise StateError(f"illegal transition {frm} -> {to}")
        if frm in REQUIRED_APPROVAL:
            gate, label = REQUIRED_APPROVAL[frm]
            if gate not in state["approvals"]:
                raise StateError(f"{label} has not been approved")
        if frm == "triage":
            expected = "fog" if state["route"] == "FOGGY" else "align"
            if to != expected:
                raise StateError(f"route {state['route']} requires triage -> {expected}")
    state["phase"] = to
    log_event(state, "transition", **{"from": frm, "to": to})
    return state


def set_route(state: dict, route: str) -> None:
    if state["phase"] != "triage":
        raise StateError("route can only be set during triage")
    if route not in ROUTES:
        raise StateError(f"route must be one of {ROUTES}")
    state["route"] = route
    log_event(state, "route", route=route)


def set_gate(state: dict, gate: str) -> None:
    if gate not in GATES:
        raise StateError(f"gate must be one of {GATES}")
    if state["gate_pending"]:
        raise StateError(f"gate {state['gate_pending']!r} is already pending")
    state["gate_pending"] = gate
    log_event(state, "gate_set", gate=gate)


def approve_gate(state: dict, gate: str) -> None:
    if state["gate_pending"] != gate:
        raise StateError(f"gate {gate!r} is not pending")
    if gate == "route" and state["route"] is None:
        raise StateError("set a route before approving Gate 1")
    state["gate_pending"] = None
    if gate not in state["approvals"]:
        state["approvals"].append(gate)
    log_event(state, "gate_approved", gate=gate)


def set_artifact(state: dict, key: str, path: str) -> None:
    if key not in ARTIFACT_KEYS:
        raise StateError(f"artifact key must be one of {ARTIFACT_KEYS}")
    if not SAFE_PATH_RE.fullmatch(path):
        raise StateError(f"unsafe artifact path {path!r}")
    state["artifacts"][key] = path
    log_event(state, "artifact", key=key, path=path)
```

(`argparse` and `sys` are imported now and used by the CLI in Task 3.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_state.py -q`
Expected: all tests pass, `0 failed`.

- [ ] **Step 6: Commit**

```bash
git add .gitignore tests/conftest.py tests/fakes.py tests/test_state.py scripts/state.py
git commit -m "feat: state machine core with gate enforcement"
```

---

### Task 2: Ticket tracking in the state machine

**Files:**
- Modify: `scripts/state.py` (append after `set_artifact`)
- Test: `tests/test_state_tickets.py`

**Interfaces:**
- Consumes: everything from Task 1.
- Produces (in `scripts/state.py`):
  - `TICKET_UPDATE_STATUSES = ("ready", "in-progress", "done", "failed")`, `SETTLED = {"done", "blocked"}`
  - `find_ticket(state, tid) -> dict`
  - `add_ticket(state, tid: str, issue: int | None, deps: list[str]) -> None`; a ticket dict is `{"id", "issue", "deps", "status", "attempts", "worktree", "block_reason"}`
  - `update_ticket(state, tid: str, status: str, worktree: str | None = None) -> None`
  - `runnable_tickets(state) -> list[dict]`
  - `all_settled(state) -> bool`
  - `reset_missing_worktrees(state) -> list[str]`

- [ ] **Step 1: Write the failing tests**

`tests/test_state_tickets.py`:
```python
import pytest

import state as st


def ticket_state(*specs):
    s = st.new_state("demo", "x")
    s["phase"] = "tickets"
    for tid, deps in specs:
        st.add_ticket(s, tid, 40 + len(s["tickets"]), deps)
    return s


def status(s, tid):
    return st.find_ticket(s, tid)["status"]


def test_add_ticket_defaults():
    s = ticket_state(("T1", []))
    assert st.find_ticket(s, "T1") == {
        "id": "T1", "issue": 40, "deps": [], "status": "ready",
        "attempts": 0, "worktree": None, "block_reason": None,
    }


def test_add_ticket_validates():
    s = ticket_state(("T1", []))
    with pytest.raises(st.StateError):
        st.add_ticket(s, "T1", 99, [])            # duplicate
    with pytest.raises(st.StateError):
        st.add_ticket(s, "T2", 99, ["T9"])        # unknown dep
    with pytest.raises(st.StateError):
        st.add_ticket(s, "bad id\n", 99, [])      # unsafe id
    s["phase"] = "align"
    with pytest.raises(st.StateError):
        st.add_ticket(s, "T3", 99, [])            # wrong phase


def test_runnable_respects_deps():
    s = ticket_state(("T1", []), ("T2", ["T1"]))
    assert [t["id"] for t in st.runnable_tickets(s)] == ["T1"]
    st.update_ticket(s, "T1", "done")
    assert [t["id"] for t in st.runnable_tickets(s)] == ["T2"]


def test_in_progress_records_worktree_with_spaces():
    s = ticket_state(("T1", []))
    st.update_ticket(s, "T1", "in-progress", worktree="/home/u/PvP Coach/.wt/T1")
    t = st.find_ticket(s, "T1")
    assert t["status"] == "in-progress" and t["worktree"] == "/home/u/PvP Coach/.wt/T1"
    with pytest.raises(st.StateError):
        st.update_ticket(s, "T1", "in-progress", worktree="/a\n/b")


def test_failed_retries_then_blocks_and_cascades():
    s = ticket_state(("T1", []), ("T2", ["T1"]), ("T3", ["T2"]), ("T4", []))
    st.update_ticket(s, "T1", "failed")
    st.update_ticket(s, "T1", "failed")
    assert status(s, "T1") == "ready" and st.find_ticket(s, "T1")["attempts"] == 2
    st.update_ticket(s, "T1", "failed")
    assert status(s, "T1") == "blocked"
    assert st.find_ticket(s, "T1")["block_reason"] == "failed 3 attempts"
    assert status(s, "T2") == "blocked" and st.find_ticket(s, "T2")["block_reason"] == "dependency T1"
    assert status(s, "T3") == "blocked" and st.find_ticket(s, "T3")["block_reason"] == "dependency T2"
    assert status(s, "T4") == "ready"


def test_settled_ticket_cannot_change():
    s = ticket_state(("T1", []))
    st.update_ticket(s, "T1", "done")
    with pytest.raises(st.StateError):
        st.update_ticket(s, "T1", "ready")


def test_update_rejects_unknown_status():
    with pytest.raises(st.StateError):
        st.update_ticket(ticket_state(("T1", [])), "T1", "blocked")


def test_all_settled():
    s = ticket_state(("T1", []), ("T2", []))
    assert not st.all_settled(s)
    st.update_ticket(s, "T1", "done")
    for _ in range(3):
        st.update_ticket(s, "T2", "failed")
    assert st.all_settled(s)


def test_reset_missing_worktrees(tmp_path):
    s = ticket_state(("T1", []), ("T2", []))
    existing = tmp_path / "wt-T2"
    existing.mkdir()
    st.update_ticket(s, "T1", "in-progress", worktree=str(tmp_path / "gone"))
    st.update_ticket(s, "T2", "in-progress", worktree=str(existing))
    assert st.reset_missing_worktrees(s) == ["T1"]
    assert status(s, "T1") == "ready" and st.find_ticket(s, "T1")["worktree"] is None
    assert status(s, "T2") == "in-progress"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_state_tickets.py -q`
Expected: FAIL with `AttributeError: module 'state' has no attribute 'add_ticket'`.

- [ ] **Step 3: Append ticket functions to `scripts/state.py`**

```python
TICKET_UPDATE_STATUSES = ("ready", "in-progress", "done", "failed")
SETTLED = {"done", "blocked"}


def find_ticket(state: dict, tid: str) -> dict:
    for ticket in state["tickets"]:
        if ticket["id"] == tid:
            return ticket
    raise StateError(f"unknown ticket {tid!r}")


def add_ticket(state: dict, tid: str, issue: int | None, deps: list[str]) -> None:
    if state["phase"] not in ("tickets", "build"):
        raise StateError("tickets can only be added in the tickets or build phase")
    if not TICKET_ID_RE.fullmatch(tid):
        raise StateError(f"invalid ticket id {tid!r}")
    if any(t["id"] == tid for t in state["tickets"]):
        raise StateError(f"ticket {tid!r} already exists")
    for dep in deps:
        find_ticket(state, dep)
    state["tickets"].append({
        "id": tid,
        "issue": int(issue) if issue is not None else None,
        "deps": list(deps),
        "status": "ready",
        "attempts": 0,
        "worktree": None,
        "block_reason": None,
    })
    log_event(state, "ticket_added", id=tid, issue=issue, deps=list(deps))


def _block(state: dict, ticket: dict, reason: str) -> None:
    ticket["status"] = "blocked"
    ticket["block_reason"] = reason
    queue = [ticket["id"]]
    while queue:
        blocked_id = queue.pop()
        for other in state["tickets"]:
            if blocked_id in other["deps"] and other["status"] not in SETTLED:
                other["status"] = "blocked"
                other["block_reason"] = f"dependency {blocked_id}"
                queue.append(other["id"])


def update_ticket(state: dict, tid: str, status: str, worktree: str | None = None) -> None:
    if status not in TICKET_UPDATE_STATUSES:
        raise StateError(f"status must be one of {TICKET_UPDATE_STATUSES}")
    ticket = find_ticket(state, tid)
    if ticket["status"] in SETTLED:
        raise StateError(f"ticket {tid} is already {ticket['status']}")
    if worktree is not None:
        if "\n" in worktree or "\r" in worktree:
            raise StateError("worktree path must be a single line")
        ticket["worktree"] = worktree
    if status == "failed":
        ticket["attempts"] += 1
        if ticket["attempts"] >= MAX_ATTEMPTS:
            _block(state, ticket, f"failed {ticket['attempts']} attempts")
        else:
            ticket["status"] = "ready"
    else:
        ticket["status"] = status
    log_event(state, "ticket", id=tid, status=ticket["status"], attempts=ticket["attempts"])


def runnable_tickets(state: dict) -> list[dict]:
    done = {t["id"] for t in state["tickets"] if t["status"] == "done"}
    return [
        t for t in state["tickets"]
        if t["status"] in ("ready", "in-progress") and all(d in done for d in t["deps"])
    ]


def all_settled(state: dict) -> bool:
    return all(t["status"] in SETTLED for t in state["tickets"])


def reset_missing_worktrees(state: dict) -> list[str]:
    reset = []
    for ticket in state["tickets"]:
        if ticket["status"] == "in-progress" and ticket["worktree"] and not Path(ticket["worktree"]).exists():
            ticket["status"] = "ready"
            ticket["worktree"] = None
            reset.append(ticket["id"])
            log_event(state, "ticket_reset", id=ticket["id"])
    return reset
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/state.py tests/test_state_tickets.py
git commit -m "feat: ticket tracking with retry, block and cascade"
```

---

### Task 3: state.py command-line interface

**Files:**
- Modify: `scripts/state.py` (append at end of file)
- Test: `tests/test_state_cli.py`

**Interfaces:**
- Consumes: all library functions from Tasks 1–2.
- Produces: `state.main(argv: list[str] | None = None) -> int`. CLI form: `python3 state.py [--root DIR] <subcommand>`. Subcommands:
  `init --slug S --idea I` · `get [--field F]` · `transition TO` · `set-route ROUTE` · `set-gate GATE` · `approve-gate GATE` · `set-artifact KEY PATH` · `ticket-add --id ID [--issue N] [--deps A,B]` · `ticket-update --id ID --status S [--worktree PATH]` · `runnable` (prints JSON list of IDs) · `resume-check` (prints JSON list of reset IDs) · `log EVENT [--note TEXT]`.
  Exit 0 on success; exit 1 with `state.py: <message>` on stderr on `StateError`.

- [ ] **Step 1: Write the failing tests**

`tests/test_state_cli.py`:
```python
import json

import state as st


def run(tmp_path, *args):
    return st.main(["--root", str(tmp_path), *args])


def test_init_and_get_field(tmp_path, capsys):
    assert run(tmp_path, "init", "--slug", "demo", "--idea", "Add flag") == 0
    capsys.readouterr()
    assert run(tmp_path, "get", "--field", "phase") == 0
    assert capsys.readouterr().out.strip() == "preflight"


def test_init_refuses_active_run(tmp_path, capsys):
    run(tmp_path, "init", "--slug", "demo", "--idea", "x")
    assert run(tmp_path, "init", "--slug", "other", "--idea", "y") == 1
    assert "active run" in capsys.readouterr().err


def test_init_allowed_after_abort(tmp_path):
    run(tmp_path, "init", "--slug", "demo", "--idea", "x")
    run(tmp_path, "transition", "aborted")
    assert run(tmp_path, "init", "--slug", "next", "--idea", "y") == 0
    assert st.load(tmp_path)["slug"] == "next"


def test_illegal_transition_exits_1(tmp_path, capsys):
    run(tmp_path, "init", "--slug", "demo", "--idea", "x")
    assert run(tmp_path, "transition", "build") == 1
    assert "illegal transition" in capsys.readouterr().err
    assert st.load(tmp_path)["phase"] == "preflight"


def test_gate_and_route_flow(tmp_path):
    run(tmp_path, "init", "--slug", "demo", "--idea", "x")
    for args in (["transition", "triage"], ["set-route", "FOGGY"], ["set-gate", "route"],
                 ["approve-gate", "route"], ["transition", "fog"]):
        assert run(tmp_path, *args) == 0, args
    assert st.load(tmp_path)["phase"] == "fog"


def test_ticket_commands(tmp_path, capsys):
    s = st.new_state("demo", "x")
    s["phase"] = "tickets"
    st.save(tmp_path, s)
    assert run(tmp_path, "ticket-add", "--id", "T1", "--issue", "41") == 0
    assert run(tmp_path, "ticket-add", "--id", "T2", "--issue", "42", "--deps", "T1") == 0
    capsys.readouterr()
    assert run(tmp_path, "runnable") == 0
    assert json.loads(capsys.readouterr().out) == ["T1"]
    assert run(tmp_path, "ticket-update", "--id", "T1", "--status", "in-progress",
               "--worktree", str(tmp_path / "missing")) == 0
    capsys.readouterr()
    assert run(tmp_path, "resume-check") == 0
    assert json.loads(capsys.readouterr().out) == ["T1"]


def test_get_unknown_field_exits_1(tmp_path):
    run(tmp_path, "init", "--slug", "demo", "--idea", "x")
    assert run(tmp_path, "get", "--field", "nope") == 1


def test_log_command(tmp_path):
    run(tmp_path, "init", "--slug", "demo", "--idea", "x")
    assert run(tmp_path, "log", "note", "--note", "hello") == 0
    assert st.load(tmp_path)["log"][-1]["note"] == "hello"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_state_cli.py -q`
Expected: FAIL with `AttributeError: module 'state' has no attribute 'main'`.

- [ ] **Step 3: Append the CLI to `scripts/state.py`**

```python
def _parse_deps(raw: str) -> list[str]:
    return [d.strip() for d in raw.split(",") if d.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="state.py", description="dev-harness state machine")
    parser.add_argument("--root", default=".", help="repository root (default: .)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    init = sub.add_parser("init")
    init.add_argument("--slug", required=True)
    init.add_argument("--idea", required=True)
    sub.add_parser("get").add_argument("--field")
    sub.add_parser("transition").add_argument("to")
    sub.add_parser("set-route").add_argument("route")
    sub.add_parser("set-gate").add_argument("gate")
    sub.add_parser("approve-gate").add_argument("gate")
    art = sub.add_parser("set-artifact")
    art.add_argument("key")
    art.add_argument("path")
    tadd = sub.add_parser("ticket-add")
    tadd.add_argument("--id", required=True)
    tadd.add_argument("--issue", type=int)
    tadd.add_argument("--deps", default="")
    tupd = sub.add_parser("ticket-update")
    tupd.add_argument("--id", required=True)
    tupd.add_argument("--status", required=True)
    tupd.add_argument("--worktree")
    sub.add_parser("runnable")
    sub.add_parser("resume-check")
    logp = sub.add_parser("log")
    logp.add_argument("event")
    logp.add_argument("--note", default="")
    return parser


def _print_value(value) -> None:
    print(value if isinstance(value, str) else json.dumps(value, indent=2))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root)
    try:
        if args.cmd == "init":
            if state_path(root).exists() and load(root)["phase"] not in TERMINAL:
                raise StateError("an active run exists; use /ship --resume or /ship --abort")
            state = new_state(args.slug, args.idea)
            log_event(state, "init")
            save(root, state)
            print(state_path(root))
            return 0
        state = load(root)
        if args.cmd == "get":
            if args.field and args.field not in state:
                raise StateError(f"unknown field {args.field!r}")
            _print_value(state[args.field] if args.field else state)
            return 0
        if args.cmd == "transition":
            transition(state, args.to)
        elif args.cmd == "set-route":
            set_route(state, args.route)
        elif args.cmd == "set-gate":
            set_gate(state, args.gate)
        elif args.cmd == "approve-gate":
            approve_gate(state, args.gate)
        elif args.cmd == "set-artifact":
            set_artifact(state, args.key, args.path)
        elif args.cmd == "ticket-add":
            add_ticket(state, args.id, args.issue, _parse_deps(args.deps))
        elif args.cmd == "ticket-update":
            update_ticket(state, args.id, args.status, args.worktree)
        elif args.cmd == "runnable":
            print(json.dumps([t["id"] for t in runnable_tickets(state)]))
            return 0
        elif args.cmd == "resume-check":
            print(json.dumps(reset_missing_worktrees(state)))
        elif args.cmd == "log":
            log_event(state, args.event, note=args.note)
        save(root, state)
        return 0
    except StateError as exc:
        print(f"state.py: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/state.py tests/test_state_cli.py
git commit -m "feat: state.py CLI for the orchestrator"
```

---

### Task 4: Stop hook — AFK build loop

**Files:**
- Create: `hooks/build_loop.py`
- Test: `tests/test_build_loop.py`

**Interfaces:**
- Consumes: `state.load`, `state.save`, `state.state_path`, `state.log_event`, `state.transition`, `state.runnable_tickets`, `state.all_settled`, `state.BUILD_CAP`, `state.add_ticket`, `state.update_ticket`.
- Produces: `build_loop.decide(root: Path, state: dict) -> dict | None` (mutates `state`; the caller saves). `build_loop.main() -> int` reads the hook payload JSON from stdin (`cwd` key). Constants `BUILD_REASON`, `PR_REASON`.

Decision order (implements spec §6; phase/gate is checked before STOP so `halted` is only logged during build):
1. phase ≠ `build` or a gate is pending → allow (None).
2. `.harness/STOP` exists → log `halted`, allow.
3. `build_iterations ≥ BUILD_CAP` → log `cap_reached`, allow.
4. runnable tickets exist → increment `build_iterations`, block with `BUILD_REASON`.
5. not all settled (only possible with a dependency cycle) → log `stalled`, allow.
6. any ticket `done` → transition to `pr`, block with `PR_REASON`.
7. otherwise (all blocked) → transition to `done`, log `all_blocked`, allow.

- [ ] **Step 1: Write the failing tests**

`tests/test_build_loop.py`:
```python
import io
import json

import build_loop as bl
import state as st


def build_state(*specs):
    s = st.new_state("demo", "x")
    s["phase"] = "build"
    s["artifacts"]["spec"] = "docs/specs/demo/spec.md"
    for tid, deps in specs:
        st.add_ticket(s, tid, None, deps)
    return s


def events(s):
    return [e["event"] for e in s["log"]]


def test_allows_stop_outside_build(tmp_path):
    s = build_state(("T1", []))
    s["phase"] = "spec"
    assert bl.decide(tmp_path, s) is None


def test_allows_stop_when_gate_pending(tmp_path):
    s = build_state(("T1", []))
    s["gate_pending"] = "pr"
    assert bl.decide(tmp_path, s) is None


def test_stop_file_halts(tmp_path):
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "STOP").touch()
    s = build_state(("T1", []))
    assert bl.decide(tmp_path, s) is None
    assert "halted" in events(s)


def test_cap_reached(tmp_path):
    s = build_state(("T1", []))
    s["build_iterations"] = st.BUILD_CAP
    assert bl.decide(tmp_path, s) is None
    assert "cap_reached" in events(s)


def test_blocks_with_runnable_tickets(tmp_path):
    s = build_state(("T1", []), ("T2", ["T1"]), ("T3", []))
    result = bl.decide(tmp_path, s)
    assert result["decision"] == "block"
    assert "T1, T3" in result["reason"] and "T2" not in result["reason"]
    assert "docs/specs/demo/spec.md" in result["reason"]
    assert s["build_iterations"] == 1


def test_moves_to_pr_when_settled(tmp_path):
    s = build_state(("T1", []), ("T2", []))
    st.update_ticket(s, "T1", "done")
    for _ in range(3):
        st.update_ticket(s, "T2", "failed")
    result = bl.decide(tmp_path, s)
    assert s["phase"] == "pr"
    assert result["decision"] == "block" and "harness/demo" in result["reason"]


def test_all_blocked_finishes_run(tmp_path):
    s = build_state(("T1", []))
    for _ in range(3):
        st.update_ticket(s, "T1", "failed")
    assert bl.decide(tmp_path, s) is None
    assert s["phase"] == "done" and "all_blocked" in events(s)


def test_cycle_is_reported_as_stalled(tmp_path):
    s = build_state(("T1", []))
    st.find_ticket(s, "T1")["deps"] = ["T1"]
    assert bl.decide(tmp_path, s) is None
    assert "stalled" in events(s)


def run_main(monkeypatch, capsys, stdin_text):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    code = bl.main()
    return code, capsys.readouterr().out


def test_main_without_state_is_silent(tmp_path, monkeypatch, capsys):
    assert run_main(monkeypatch, capsys, json.dumps({"cwd": str(tmp_path)})) == (0, "")


def test_main_with_bad_stdin_is_silent(monkeypatch, capsys):
    assert run_main(monkeypatch, capsys, "not json") == (0, "")


def test_main_blocks_and_saves(tmp_path, monkeypatch, capsys):
    st.save(tmp_path, build_state(("T1", [])))
    code, out = run_main(monkeypatch, capsys, json.dumps({"cwd": str(tmp_path)}))
    assert code == 0 and json.loads(out)["decision"] == "block"
    assert st.load(tmp_path)["build_iterations"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_build_loop.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'build_loop'`.

- [ ] **Step 3: Implement `hooks/build_loop.py`**

```python
#!/usr/bin/env python3
"""Stop hook: keeps Claude working through the build phase while the user is away.

Prints {"decision": "block", "reason": ...} to continue; prints nothing to allow the stop.
The reason is built only from fixed templates and validated state fields
(ticket IDs, spec path, branch), never from issue or PR text.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import state as st  # noqa: E402

BUILD_REASON = (
    "[dev-harness build loop {n}/{cap}] Continue the build phase of the "
    "dev-harness:harness-orchestrator skill. Runnable tickets: {ids}. Spec: {spec}. "
    "Record every outcome with state.py ticket-update. Do not ask the user anything."
)
PR_REASON = (
    "[dev-harness] All tickets are settled. Run the pr phase of the "
    "dev-harness:harness-orchestrator skill for branch {branch}."
)


def stop_file(root) -> Path:
    return Path(root) / ".harness" / "STOP"


def decide(root, state: dict) -> dict | None:
    if state["phase"] != "build" or state["gate_pending"]:
        return None
    if stop_file(root).exists():
        st.log_event(state, "halted")
        return None
    if state["build_iterations"] >= st.BUILD_CAP:
        st.log_event(state, "cap_reached")
        return None
    runnable = st.runnable_tickets(state)
    if runnable:
        state["build_iterations"] += 1
        reason = BUILD_REASON.format(
            n=state["build_iterations"],
            cap=st.BUILD_CAP,
            ids=", ".join(t["id"] for t in runnable),
            spec=state["artifacts"]["spec"],
        )
        return {"decision": "block", "reason": reason}
    if not st.all_settled(state):
        st.log_event(state, "stalled")
        return None
    if any(t["status"] == "done" for t in state["tickets"]):
        st.transition(state, "pr")
        return {"decision": "block", "reason": PR_REASON.format(branch=state["integration_branch"])}
    st.transition(state, "done")
    st.log_event(state, "all_blocked")
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    root = Path(payload.get("cwd") or ".")
    if not st.state_path(root).exists():
        return 0
    try:
        state = st.load(root)
    except (st.StateError, ValueError, OSError):
        return 0
    result = decide(root, state)
    st.save(root, state)
    if result:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add hooks/build_loop.py tests/test_build_loop.py
git commit -m "feat: Stop hook driving the AFK build loop"
```

---

### Task 5: PreToolUse guard hook

**Files:**
- Create: `hooks/guard.py`
- Test: `tests/test_guard.py`

**Interfaces:**
- Consumes: `state.load`, `state.state_path`, `state.TERMINAL`, `state.new_state`.
- Produces: `guard.check(payload: dict, state: dict | None, default_branch: str, current_branch: str) -> str | None` (a denial reason, or None to allow). `guard.main() -> int` exits 2 with the reason on stderr to deny, which is the Claude Code PreToolUse convention.

Rules:
- Always, even with no active run: deny `Write`/`Edit`/`MultiEdit`/`NotebookEdit` targeting `.harness/state.json`, and deny Bash commands mentioning `.harness/state.json` unless they also mention `state.py`.
- Only while a run is active (state exists and phase not in `TERMINAL`): deny force pushes (`--force*`, `-f`, `+refspec`), pushes whose destination is the default branch, a bare `git push` while on the default branch, `gh pr merge`, and `git merge` while on the default branch.
- During `build`: deny file edits outside the session cwd and outside every recorded ticket worktree.

- [ ] **Step 1: Write the failing tests**

`tests/test_guard.py`:
```python
import pytest

import guard
import state as st


def payload(tool, cwd, **tool_input):
    return {"tool_name": tool, "cwd": str(cwd), "tool_input": tool_input}


def run_state(phase="align", tickets=()):
    s = st.new_state("demo", "x")
    s["phase"] = phase
    s["tickets"] = list(tickets)
    return s


def bash(cwd, cmd, state=None, current="harness/demo"):
    return guard.check(payload("Bash", cwd, command=cmd), state, "main", current)


def test_blocks_direct_state_write_without_run(tmp_path):
    reason = guard.check(payload("Write", tmp_path, file_path=".harness/state.json"), None, "main", "main")
    assert reason and "state.py" in reason


def test_blocks_absolute_state_edit(tmp_path):
    target = str(tmp_path / ".harness" / "state.json")
    assert guard.check(payload("Edit", tmp_path, file_path=target), run_state(), "main", "x")


def test_allows_normal_write(tmp_path):
    assert guard.check(payload("Write", tmp_path, file_path="src/app.py"), run_state(), "main", "x") is None


def test_bash_state_redirect_blocked(tmp_path):
    assert bash(tmp_path, "echo {} > .harness/state.json")


def test_bash_via_state_py_allowed(tmp_path):
    assert bash(tmp_path, "python3 scripts/state.py get  # reads .harness/state.json", run_state()) is None


@pytest.mark.parametrize("cmd", [
    "git push --force origin harness/demo",
    "git push -f origin harness/demo",
    "git push origin +harness/demo",
    "git push --force-with-lease",
])
def test_blocks_force_push(tmp_path, cmd):
    assert "force" in bash(tmp_path, cmd, run_state())


@pytest.mark.parametrize("cmd", [
    "git push origin main",
    "git push origin HEAD:main",
    "git push origin harness/demo:refs/heads/main",
    "cd sub && git push origin main",
])
def test_blocks_push_to_default(tmp_path, cmd):
    assert "main" in bash(tmp_path, cmd, run_state())


def test_blocks_bare_push_on_default(tmp_path):
    assert bash(tmp_path, "git push", run_state(), current="main")


def test_allows_integration_branch_push(tmp_path):
    assert bash(tmp_path, "git push -u origin harness/fix-main-menu", run_state()) is None


def test_blocks_gh_pr_merge(tmp_path):
    assert bash(tmp_path, "gh pr merge 12 --squash", run_state())


def test_git_merge_only_blocked_on_default(tmp_path):
    assert bash(tmp_path, "git merge harness/demo", run_state(), current="main")
    assert bash(tmp_path, "git merge wt-T1", run_state(), current="harness/demo") is None


def test_inactive_run_allows_git(tmp_path):
    assert bash(tmp_path, "git push --force origin main", run_state("done")) is None
    assert bash(tmp_path, "git push --force origin main", None) is None


def test_build_blocks_edit_outside_repo(tmp_path):
    outside = tmp_path.parent / "elsewhere.py"
    reason = guard.check(payload("Write", tmp_path, file_path=str(outside)), run_state("build"), "main", "x")
    assert reason and "worktree" in reason


def test_build_allows_edit_in_ticket_worktree(tmp_path):
    wt = tmp_path.parent / "wt-T1"
    ticket = {"id": "T1", "issue": None, "deps": [], "status": "in-progress",
              "attempts": 0, "worktree": str(wt), "block_reason": None}
    s = run_state("build", [ticket])
    assert guard.check(payload("Edit", tmp_path, file_path=str(wt / "a.py")), s, "main", "x") is None


def test_other_tools_allowed(tmp_path):
    assert guard.check(payload("Read", tmp_path, file_path=".harness/state.json"), run_state(), "main", "x") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_guard.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'guard'`.

- [ ] **Step 3: Implement `hooks/guard.py`**

```python
#!/usr/bin/env python3
"""PreToolUse hook: blocks actions that would break dev-harness guarantees.

Exit code 2 with a message on stderr denies the tool call; Claude sees the message.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import state as st  # noqa: E402

EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SEPARATORS = {"&&", "||", ";", "|"}
STATE_MSG = "write .harness/state.json only through scripts/state.py"


def _tokens(cmd: str) -> list[str]:
    try:
        return shlex.split(cmd)
    except ValueError:
        return cmd.split()


def _resolve(cwd: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    return (path if path.is_absolute() else cwd / path).resolve()


def _is_state_file(path: Path) -> bool:
    return path.name == "state.json" and path.parent.name == ".harness"


def _push_problem(toks: list[str], default_branch: str, current_branch: str) -> str | None:
    for i in range(len(toks) - 1):
        if toks[i] != "git" or toks[i + 1] != "push":
            continue
        positional = []
        for tok in toks[i + 2:]:
            if tok in SEPARATORS:
                break
            if tok.startswith("--force") or tok == "-f":
                return "force-push is blocked by dev-harness"
            if tok.startswith("-"):
                continue
            positional.append(tok)
        refspecs = positional[1:]
        if not refspecs and current_branch == default_branch:
            return f"pushing {default_branch} is blocked by dev-harness"
        for ref in refspecs:
            if ref.startswith("+"):
                return "force-push is blocked by dev-harness"
            dest = ref.split(":")[-1].removeprefix("refs/heads/")
            if dest == default_branch:
                return f"pushing to {default_branch} is blocked by dev-harness; the human merges the PR"
    return None


def _bash_problem(cmd: str, active: bool, default_branch: str, current_branch: str) -> str | None:
    if ".harness/state.json" in cmd and "state.py" not in cmd:
        return STATE_MSG
    if not active:
        return None
    toks = _tokens(cmd)
    problem = _push_problem(toks, default_branch, current_branch)
    if problem:
        return problem
    for i in range(len(toks) - 1):
        if toks[i] == "gh" and toks[i + 1] == "pr" and i + 2 < len(toks) and toks[i + 2] == "merge":
            return "merging PRs is the human's job (Gate 3)"
        if toks[i] == "git" and toks[i + 1] == "merge" and current_branch == default_branch:
            return f"merging into {default_branch} is blocked by dev-harness"
    return None


def check(payload: dict, state: dict | None, default_branch: str, current_branch: str) -> str | None:
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    cwd = Path(payload.get("cwd") or ".").resolve()
    active = state is not None and state.get("phase") not in st.TERMINAL
    if tool in EDIT_TOOLS:
        raw = tool_input.get("file_path") or tool_input.get("notebook_path")
        if not raw:
            return None
        target = _resolve(cwd, raw)
        if _is_state_file(target):
            return STATE_MSG
        if active and state["phase"] == "build":
            allowed = [cwd] + [_resolve(cwd, t["worktree"]) for t in state["tickets"] if t.get("worktree")]
            if not any(target == a or a in target.parents for a in allowed):
                return f"during build, edits must stay inside the repo or a ticket worktree (got {target})"
        return None
    if tool == "Bash":
        return _bash_problem(tool_input.get("command", ""), active, default_branch, current_branch)
    return None


def _git(args: list[str], cwd) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def repo_root(cwd) -> Path:
    common = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd)
    return Path(common).parent if common else Path(cwd)


def default_branch(cwd) -> str:
    ref = _git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd)
    return ref.split("/", 1)[1] if "/" in ref else "main"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    cwd = payload.get("cwd") or "."
    root = repo_root(cwd)
    state = None
    if st.state_path(root).exists():
        try:
            state = st.load(root)
        except (st.StateError, ValueError, OSError):
            state = None
    is_bash = payload.get("tool_name") == "Bash"
    reason = check(
        payload,
        state,
        default_branch(cwd) if is_bash else "main",
        _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd) if is_bash else "",
    )
    if reason:
        print(f"dev-harness guard: {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`repo_root` uses the git common dir so a subagent running inside a worktree still finds the main repo's `.harness/state.json`. Git calls run only for Bash tool calls, which keeps the hook cheap on every edit.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add hooks/guard.py tests/test_guard.py
git commit -m "feat: PreToolUse guard for state, pushes and merges"
```

---

### Task 6: GitHub label sync

**Files:**
- Create: `scripts/tickets_sync.py`
- Test: `tests/test_tickets_sync.py`

**Interfaces:**
- Consumes: `state.load`, `state.StateError`, `state.new_state`, `state.add_ticket` (tests), `fakes.FakeRun`.
- Produces: `TICKET_LABEL`, `STATUS_LABELS: dict[str, str]`, `LABEL_COLORS`, `ensure_labels(run=subprocess.run) -> list[str]` (labels it failed to create), `label_command(issue: int, status: str) -> list[str]`, `sync(state, run=subprocess.run) -> list[int]` (issue numbers that failed), `main(argv=None) -> int`. CLI: `python3 tickets_sync.py [--root DIR] [--ensure-labels]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_tickets_sync.py`:
```python
import state as st
import tickets_sync as ts
from fakes import FakeRun


def make_state():
    s = st.new_state("demo", "x")
    s["phase"] = "tickets"
    st.add_ticket(s, "T1", 41, [])
    st.add_ticket(s, "T2", 42, ["T1"])
    st.add_ticket(s, "T3", None, [])
    st.update_ticket(s, "T1", "done")
    return s


def test_label_command():
    assert ts.label_command(41, "done") == [
        "gh", "issue", "edit", "41",
        "--add-label", "harness:ticket,harness:done",
        "--remove-label", "harness:ready,harness:in-progress,harness:blocked",
    ]


def test_sync_skips_tickets_without_issue():
    run = FakeRun()
    assert ts.sync(make_state(), run) == []
    assert [c[3] for c in run.calls] == ["41", "42"]
    assert "harness:ready" in run.calls[1][5]


def test_sync_reports_failures():
    assert ts.sync(make_state(), FakeRun(fail_on=["42"])) == [42]


def test_ensure_labels_creates_all_with_force():
    run = FakeRun()
    assert ts.ensure_labels(run) == []
    assert len(run.calls) == len(ts.LABEL_COLORS)
    assert all(c[:3] == ["gh", "label", "create"] and "--force" in c for c in run.calls)


def test_ensure_labels_reports_failures():
    assert ts.ensure_labels(FakeRun(fail_on=["harness:done"])) == ["harness:done"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_tickets_sync.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'tickets_sync'`.

- [ ] **Step 3: Implement `scripts/tickets_sync.py`**

```python
#!/usr/bin/env python3
"""Mirror ticket status from .harness/state.json to GitHub issue labels (one-way)."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import state as st  # noqa: E402

TICKET_LABEL = "harness:ticket"
STATUS_LABELS = {
    "ready": "harness:ready",
    "in-progress": "harness:in-progress",
    "done": "harness:done",
    "blocked": "harness:blocked",
}
LABEL_COLORS = {
    TICKET_LABEL: "5319e7",
    "harness:ready": "0e8a16",
    "harness:in-progress": "fbca04",
    "harness:done": "1d76db",
    "harness:blocked": "d93f0b",
}


def ensure_labels(run=subprocess.run) -> list[str]:
    failed = []
    for name, color in LABEL_COLORS.items():
        result = run(["gh", "label", "create", name, "--color", color, "--force"],
                     capture_output=True, text=True)
        if result.returncode != 0:
            failed.append(name)
    return failed


def label_command(issue: int, status: str) -> list[str]:
    want = STATUS_LABELS[status]
    others = [label for label in STATUS_LABELS.values() if label != want]
    return ["gh", "issue", "edit", str(issue),
            "--add-label", f"{TICKET_LABEL},{want}",
            "--remove-label", ",".join(others)]


def sync(state: dict, run=subprocess.run) -> list[int]:
    failed = []
    for ticket in state["tickets"]:
        if ticket.get("issue") is None:
            continue
        result = run(label_command(ticket["issue"], ticket["status"]), capture_output=True, text=True)
        if result.returncode != 0:
            failed.append(ticket["issue"])
    return failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tickets_sync.py")
    parser.add_argument("--root", default=".")
    parser.add_argument("--ensure-labels", action="store_true")
    args = parser.parse_args(argv)
    if args.ensure_labels:
        missing = ensure_labels()
        if missing:
            print(f"tickets_sync: could not create labels: {', '.join(missing)}", file=sys.stderr)
            return 1
    try:
        state = st.load(Path(args.root))
    except st.StateError as exc:
        print(f"tickets_sync: {exc}", file=sys.stderr)
        return 1
    failed = sync(state)
    if failed:
        print(f"tickets_sync: failed to label issues {failed}", file=sys.stderr)
        return 1
    count = sum(1 for t in state["tickets"] if t.get("issue") is not None)
    print(f"synced {count} issue(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/tickets_sync.py tests/test_tickets_sync.py
git commit -m "feat: one-way ticket status sync to GitHub labels"
```

---

### Task 7: Preflight checks

**Files:**
- Create: `scripts/preflight.py`
- Test: `tests/test_preflight.py`

**Interfaces:**
- Consumes: `fakes.FakeRun` (tests only).
- Produces: `Check = namedtuple("Check", "name ok fix")`, `REQUIRED_SKILLS: tuple[str, ...]`, `GITIGNORE_LINES`, `check_git(root, run) -> Check`, `check_clean(root, run) -> Check`, `check_gh(run) -> Check`, `skill_search_dirs(home, root) -> list[Path]`, `find_missing_skills(dirs) -> list[str]`, `check_skills(home, root) -> Check`, `ensure_gitignore(root) -> bool`, `run_all(root, home, run=subprocess.run) -> list[Check]`, `main(argv=None) -> int`. CLI: `python3 preflight.py [--root DIR] [--home DIR]`; prints `updated .gitignore` when it changed the file.

- [ ] **Step 1: Write the failing tests**

`tests/test_preflight.py`:
```python
import preflight as pf
from fakes import FakeRun


def test_check_git():
    assert pf.check_git(".", FakeRun({("git", "rev-parse"): (0, "true\n")})).ok
    assert not pf.check_git(".", FakeRun({("git", "rev-parse"): (128, "")})).ok


def test_check_clean_ignores_harness_files():
    harness_only = FakeRun({("git", "status"): (0, " M .harness/x\n?? .harness/\n")})
    assert pf.check_clean(".", harness_only).ok
    dirty = FakeRun({("git", "status"): (0, " M app.py\n")})
    result = pf.check_clean(".", dirty)
    assert not result.ok and "stash" in result.fix


def test_check_gh():
    assert pf.check_gh(FakeRun()).ok
    result = pf.check_gh(FakeRun(fail_on=["auth"]))
    assert not result.ok and "gh auth login" in result.fix


def make_skills(base, names):
    for name in names:
        d = base / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n")


def test_find_missing_skills(tmp_path):
    home, root = tmp_path / "home", tmp_path / "repo"
    names = [n for n in pf.REQUIRED_SKILLS if n not in ("retro", "tdd")]
    make_skills(home / ".claude" / "skills", names)
    make_skills(home / ".claude" / "plugins" / "cache" / "x" / "skills", ["tdd"])
    assert pf.find_missing_skills(pf.skill_search_dirs(home, root)) == ["retro"]


def test_check_skills_lists_missing(tmp_path):
    result = pf.check_skills(tmp_path / "home", tmp_path / "repo")
    assert not result.ok and "wayfinder" in result.fix and "mattpocock/skills" in result.fix


def test_ensure_gitignore(tmp_path):
    (tmp_path / ".gitignore").write_text("node_modules")
    assert pf.ensure_gitignore(tmp_path) is True
    assert (tmp_path / ".gitignore").read_text() == "node_modules\n.harness/*\n!.harness/retros/\n"
    assert pf.ensure_gitignore(tmp_path) is False


def test_ensure_gitignore_creates_file(tmp_path):
    assert pf.ensure_gitignore(tmp_path) is True
    assert (tmp_path / ".gitignore").read_text() == ".harness/*\n!.harness/retros/\n"


def test_run_all_order(tmp_path):
    run = FakeRun({("git", "rev-parse"): (0, "true\n"), ("git", "status"): (0, "")})
    names = [c.name for c in pf.run_all(tmp_path, tmp_path, run)]
    assert names == ["git repo", "clean tree", "gh auth", "matt pocock skills"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_preflight.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'preflight'`.

- [ ] **Step 3: Implement `scripts/preflight.py`**

```python
#!/usr/bin/env python3
"""Preflight checks before a dev-harness run. Prints a fix for each failure; exit 1 if any fail."""
from __future__ import annotations

import argparse
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

Check = namedtuple("Check", "name ok fix")

# Keep in sync with the skill table in skills/harness-orchestrator/SKILL.md
# (tests/test_plugin_layout.py enforces this).
REQUIRED_SKILLS = (
    "setup-matt-pocock-skills", "ask-matt", "wayfinder", "prototype", "research",
    "grill-with-docs", "to-spec", "to-tickets", "domain-modeling", "implement",
    "implement-spec", "tdd", "code-review", "pr", "retro",
)
SKILLS_FIX = (
    "Install Matt Pocock's skills from https://github.com/mattpocock/skills so each skill "
    "folder (with its SKILL.md) is under ~/.claude/skills/ or this repo's .claude/skills/, "
    "then rerun /ship."
)
GITIGNORE_LINES = (".harness/*", "!.harness/retros/")


def check_git(root, run) -> Check:
    result = run(["git", "rev-parse", "--is-inside-work-tree"], cwd=root, capture_output=True, text=True)
    ok = result.returncode == 0 and result.stdout.strip() == "true"
    return Check("git repo", ok, "Run /ship inside a git repository with at least one commit.")


def check_clean(root, run) -> Check:
    result = run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True)
    dirty = [line for line in result.stdout.splitlines() if line.strip() and ".harness/" not in line]
    ok = result.returncode == 0 and not dirty
    return Check("clean tree", ok, "Commit or stash your changes first: git stash -u")


def check_gh(run) -> Check:
    result = run(["gh", "auth", "status"], capture_output=True, text=True)
    return Check("gh auth", result.returncode == 0, "Log in to the GitHub CLI: gh auth login")


def skill_search_dirs(home, root) -> list[Path]:
    return [Path(home) / ".claude" / "skills", Path(root) / ".claude" / "skills",
            Path(home) / ".claude" / "plugins"]


def find_missing_skills(dirs) -> list[str]:
    found = set()
    for directory in dirs:
        if directory.is_dir():
            found.update(p.parent.name for p in directory.rglob("SKILL.md"))
    return [name for name in REQUIRED_SKILLS if name not in found]


def check_skills(home, root) -> Check:
    missing = find_missing_skills(skill_search_dirs(home, root))
    fix = f"Missing: {', '.join(missing)}. {SKILLS_FIX}" if missing else ""
    return Check("matt pocock skills", not missing, fix)


def ensure_gitignore(root) -> bool:
    path = Path(root) / ".gitignore"
    text = path.read_text() if path.exists() else ""
    existing = text.splitlines()
    missing = [line for line in GITIGNORE_LINES if line not in existing]
    if not missing:
        return False
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text + "\n".join(missing) + "\n")
    return True


def run_all(root, home, run=subprocess.run) -> list[Check]:
    return [check_git(root, run), check_clean(root, run), check_gh(run), check_skills(home, root)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="preflight.py")
    parser.add_argument("--root", default=".")
    parser.add_argument("--home", default=str(Path.home()))
    args = parser.parse_args(argv)
    checks = run_all(args.root, args.home)
    for check in checks:
        print(f"[{'ok' if check.ok else 'FAIL'}] {check.name}")
        if not check.ok:
            print(f"    fix: {check.fix}")
    if not all(check.ok for check in checks):
        return 1
    if ensure_gitignore(args.root):
        print("updated .gitignore")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/preflight.py tests/test_preflight.py
git commit -m "feat: preflight environment checks"
```

---

### Task 8: Plugin wiring — manifest, hooks, command, skills, README

**Files:**
- Create: `.claude-plugin/plugin.json`, `hooks/hooks.json`, `commands/ship.md`, `skills/harness-orchestrator/SKILL.md`, `skills/harness-triage/SKILL.md`, `README.md`
- Test: `tests/test_plugin_layout.py`

**Interfaces:**
- Consumes: `preflight.REQUIRED_SKILLS`; the CLI forms of `state.py`, `tickets_sync.py`, `preflight.py`; hook files from Tasks 4–5.
- Produces: the installable plugin. The skills are invoked as `dev-harness:harness-orchestrator` and `dev-harness:harness-triage`; the command is `/ship`.

- [ ] **Step 1: Write the failing test**

`tests/test_plugin_layout.py`:
```python
import json
import re
from pathlib import Path

import preflight

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text()


def test_manifest():
    manifest = json.loads(read(".claude-plugin/plugin.json"))
    assert manifest["name"] == "dev-harness"
    assert manifest["version"]


def test_hook_commands_point_at_existing_files():
    hooks = json.loads(read("hooks/hooks.json"))["hooks"]
    assert set(hooks) == {"Stop", "PreToolUse"}
    for groups in hooks.values():
        for group in groups:
            for hook in group["hooks"]:
                rel = re.search(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\"]+)", hook["command"]).group(1)
                assert (ROOT / rel).is_file(), rel


def test_skills_have_frontmatter():
    for name in ("harness-orchestrator", "harness-triage"):
        text = read(f"skills/{name}/SKILL.md")
        assert text.startswith(f"---\nname: {name}\ndescription: ")


def test_orchestrator_names_every_required_skill():
    text = read("skills/harness-orchestrator/SKILL.md")
    for skill in preflight.REQUIRED_SKILLS:
        assert f"`{skill}`" in text, skill


def test_ship_command_loads_orchestrator():
    text = read("commands/ship.md")
    assert "dev-harness:harness-orchestrator" in text and "$ARGUMENTS" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_plugin_layout.py -q`
Expected: FAIL with `FileNotFoundError` for `.claude-plugin/plugin.json`.

- [ ] **Step 3: Create the manifest and hook registration**

`.claude-plugin/plugin.json`:
```json
{
  "name": "dev-harness",
  "version": "0.1.0",
  "description": "Gated, resumable automation of the Matt Pocock skills workflow: /ship an idea through spec, tickets, parallel build, PR and retro."
}
```

`hooks/hooks.json`:
```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/build_loop.py\"" }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Write|Edit|MultiEdit|NotebookEdit|Bash",
        "hooks": [
          { "type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/guard.py\"" }
        ]
      }
    ]
  }
}
```

- [ ] **Step 4: Create the `/ship` command**

`commands/ship.md`:
```markdown
---
description: Ship an idea through the gated dev-harness pipeline (triage → spec → tickets → parallel build → PR → retro)
argument-hint: "<idea>" | --resume | --status | --abort
---

Invoke the `dev-harness:harness-orchestrator` skill and follow it exactly, with these arguments:

$ARGUMENTS
```

- [ ] **Step 5: Create the triage skill**

`skills/harness-triage/SKILL.md`:
```markdown
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
```

- [ ] **Step 6: Create the orchestrator skill**

`skills/harness-orchestrator/SKILL.md`:
````markdown
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

## Gate procedure

1. `state.py set-gate <gate>`
2. Ask the gate's question with AskUserQuestion.
3. On approval: `state.py approve-gate <gate>`. On rejection: do what that phase says, then ask again. Never approve a gate without the user's explicit answer in this session.

## Start

1. `python3 "$SCRIPTS/preflight.py" --root .` If it exits non-zero, show its output and stop.
2. If it printed `updated .gitignore`: `git add .gitignore && git commit -m "chore: ignore dev-harness state"`
3. If `.harness/setup-done` does not exist: invoke `setup-matt-pocock-skills` with GitHub as the issue tracker, then run `mkdir -p .harness && touch .harness/setup-done`.
4. Make a slug from the idea: lowercase, hyphen-separated, letters, digits and hyphens only, at most 48 characters.
5. `state.py init --slug <slug> --idea "<idea>"`
6. From the default branch: `git checkout -b harness/<slug>`
7. `state.py transition triage`, then continue with **Phase: triage**.

## Resume

1. `state.py resume-check`. This resets in-progress tickets whose worktrees have disappeared.
2. `state.py get --field slug`, `--field phase`, `--field gate_pending`. Then `git checkout harness/<slug>`.
3. If a gate is pending, ask that gate's question again (Gate procedure, step 2). Otherwise continue with the section for the current phase.

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
2. Register each ticket in dependency order: `state.py ticket-add --id T<n> --issue <number> --deps T<a>,T<b>` (leave out `--deps` when there are none).
3. `python3 "$SCRIPTS/tickets_sync.py" --ensure-labels`
4. `state.py transition build`

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
````

- [ ] **Step 7: Create the README**

`README.md`:
````markdown
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
````

- [ ] **Step 8: Run tests to verify they pass**

Run: `.venv/bin/pytest -q`
Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add .claude-plugin hooks/hooks.json commands skills README.md tests/test_plugin_layout.py
git commit -m "feat: plugin manifest, /ship command, orchestrator and triage skills"
```

---

### Task 9: End-to-end dry run

**Files:**
- Create: `docs/e2e-dry-run.md`

**Interfaces:**
- Consumes: the whole plugin.
- Produces: a recorded result for each checklist item. Any failure becomes a fix task before release.

This task is manual and has external side effects (a GitHub repo, issues, a PR). **Ask the user before Step 2.**

- [ ] **Step 1: Make sure prerequisites are installed**

```bash
command -v claude || curl -fsSL https://claude.ai/install.sh | bash
```
```bash
gh auth status
```
Install Matt Pocock's skills by following https://github.com/mattpocock/skills, then confirm:
```bash
python3 scripts/preflight.py --root .
```
Expected: `[ok] matt pocock skills` (the `clean tree` line can be ignored here).

- [ ] **Step 2: Create a sandbox repo (after the user approves)**

```bash
mkdir -p ~/harness-sandbox && cd ~/harness-sandbox && git init -b main
printf 'import sys\n\ndef main(argv=None):\n    print("hello")\n\nif __name__ == "__main__":\n    main(sys.argv[1:])\n' > hello.py
printf 'from hello import main\n\ndef test_main(capsys):\n    main([])\n    assert capsys.readouterr().out == "hello\\n"\n' > test_hello.py
git add . && git commit -m "init sandbox"
gh repo create harness-sandbox --private --source . --push
```

- [ ] **Step 3: Run the STANDARD route**

In `~/harness-sandbox`: `claude --plugin-dir <path-to-dev-harness>`, then `/ship "Add a --version flag that prints 0.1.0"`. At triage answer One session / Known / None.

- [ ] **Step 4: Run the FOGGY route**

After the first run finishes, start `/ship "Add pluggable output formats (json, yaml)"` and answer A few sessions / Unknown / Some. Abort with `/ship --abort` once the fog phase has written `map.md`.

- [ ] **Step 5: Record results in `docs/e2e-dry-run.md`**

```markdown
# E2E dry run — <date>

| # | Check | Expected | Result |
|---|---|---|---|
| 1 | Preflight on sandbox | all `[ok]`, `.gitignore` updated and committed | |
| 2 | Gate 1 asked | route question shown; `state.py get --field approvals` includes `route` after answering | |
| 3 | Grill interview | questions come to the user; GLOSSARY.md committed | |
| 4 | Gate 2 asked | spec at `docs/specs/<slug>/spec.md`; approval recorded | |
| 5 | Tickets | issues created with `harness:ticket` + `harness:ready`; `tickets_sync.py` exits 0 on fresh issues | |
| 6 | AFK build | the Stop hook continues without user input; `build_iterations` increases | |
| 7 | STOP file | `touch .harness/STOP` mid-build halts at the next stop; `halted` in log | |
| 8 | Guard | asking Claude to `git push origin main` is denied with a dev-harness message | |
| 9 | Guard | asking Claude to edit `.harness/state.json` directly is denied | |
| 10 | PR | PR opened from `harness/<slug>` with the structured body; Gate 3 asked; harness does not merge | |
| 11 | Resume | after merging manually, `/ship --resume` verifies MERGED and runs retro | |
| 12 | Retro | `.harness/retros/<slug>.md` exists; nothing applied automatically | |
| 13 | FOGGY route | triage → fog; `harness/<slug>-fog` deleted; `map.md` committed on `harness/<slug>` | |
| 14 | Skill-name collisions | note any Matt skill that conflicted with a built-in (e.g. `code-review`) and how it was resolved | |
```

Fill in the Result column. For each failed row, add a fix task.

- [ ] **Step 6: Commit**

```bash
git add docs/e2e-dry-run.md
git commit -m "docs: end-to-end dry run results"
```
