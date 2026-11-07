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
