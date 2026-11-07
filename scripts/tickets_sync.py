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
    except (st.StateError, ValueError, OSError) as exc:
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
