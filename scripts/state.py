#!/usr/bin/env python3
"""dev-harness state: the only writer of .harness/state.json.

Used as a library by the hooks and as a CLI by the orchestrator skill.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import subprocess
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


def repo_root(cwd) -> Path:
    """The main repository root (worktree-safe); falls back to cwd. Never raises."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        )
        common = result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired, ValueError):
        common = ""
    return Path(common).parent if common else Path(cwd)


@contextlib.contextmanager
def locked(root):
    """Exclusive advisory lock serialising load-modify-save cycles on the state."""
    lock_path = Path(root) / ".harness" / "state.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


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
    with locked(root):
        return _run(args, root)


def _run(args, root: Path) -> int:
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
    except (StateError, ValueError, OSError) as exc:
        print(f"state.py: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
