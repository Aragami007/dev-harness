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
        if not isinstance(payload, dict):
            return 0
        cwd = payload.get("cwd")
        root = Path(cwd if isinstance(cwd, str) and cwd else ".")
        if not st.state_path(root).exists():
            return 0
        state = st.load(root)
        result = decide(root, state)
        st.save(root, state)
    except Exception:
        # A hook must never break the session: any failure allows the stop.
        return 0
    if result:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
