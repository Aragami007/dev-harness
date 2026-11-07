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
STATE_MSG = "write .harness/state.json only through scripts/state.py"


SHELLS = {"bash", "sh", "zsh"}
MAX_DEPTH = 3
GIT_OPTS_WITH_ARG = {"-C", "-c", "--git-dir", "--work-tree"}
GH_OPTS_WITH_ARG = {"-R", "--repo"}


def _tokens(cmd: str) -> list[str]:
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        return list(lex)
    except ValueError:
        return cmd.split()


def _is_separator(tok: str) -> bool:
    return bool(tok) and set(tok) <= set(";&|")


def _shell_string(simple: list[str]) -> str | None:
    """The script of a `bash|sh|zsh ... -c <script>` simple command, if any."""
    for i, tok in enumerate(simple):
        if Path(tok).name in SHELLS:
            for j in range(i + 1, len(simple) - 1):
                if simple[j] == "-c":
                    return simple[j + 1]
            return None
    return None


def _commands(cmd: str, depth: int = 0) -> list[list[str]]:
    """Split a command line into simple commands, expanding `<shell> -c` strings."""
    simples: list[list[str]] = [[]]
    for tok in _tokens(cmd):
        if _is_separator(tok):
            simples.append([])
        else:
            simples[-1].append(tok)
    out = []
    for simple in simples:
        if not simple:
            continue
        out.append(simple)
        script = _shell_string(simple)
        if script is not None and depth < MAX_DEPTH:
            out.extend(_commands(script, depth + 1))
    return out


def _subcommand_index(simple: list[str], tool: str, opts_with_arg: set[str]) -> int | None:
    """Index of the subcommand of `tool` in `simple`, skipping global options."""
    for i, tok in enumerate(simple):
        if tok != tool:
            continue
        j = i + 1
        while j < len(simple) and simple[j].startswith("-"):
            j += 2 if simple[j] in opts_with_arg else 1
        return j if j < len(simple) else None
    return None


def _resolve(cwd: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    return (path if path.is_absolute() else cwd / path).resolve()


def _is_state_file(path: Path) -> bool:
    return path.name == "state.json" and path.parent.name == ".harness"


def _is_force_flag(tok: str) -> bool:
    if tok.startswith("--"):
        return tok.startswith("--force")
    return tok.startswith("-") and "f" in tok[1:]


def _push_problem(args: list[str], default_branch: str, current_branch: str) -> str | None:
    positional = []
    for tok in args:
        if _is_force_flag(tok):
            return "force-push is blocked by dev-harness"
        if tok in ("--all", "--mirror"):
            return f"{tok} would push {default_branch}; blocked by dev-harness"
        if not tok.startswith("-"):
            positional.append(tok)
    refspecs = positional[1:]
    if not refspecs and current_branch == default_branch:
        return f"pushing {default_branch} is blocked by dev-harness"
    for ref in refspecs:
        if ref.startswith("+"):
            return "force-push is blocked by dev-harness"
        dest = ref.split(":")[-1].removeprefix("refs/heads/")
        if dest == "HEAD":
            dest = current_branch
        if dest == default_branch:
            return f"pushing to {default_branch} is blocked by dev-harness; the human merges the PR"
    return None


def _simple_problem(simple: list[str], default_branch: str, current_branch: str) -> str | None:
    gi = _subcommand_index(simple, "git", GIT_OPTS_WITH_ARG)
    if gi is not None:
        if simple[gi] == "push":
            return _push_problem(simple[gi + 1:], default_branch, current_branch)
        if simple[gi] == "merge" and current_branch == default_branch:
            return f"merging into {default_branch} is blocked by dev-harness"
    hi = _subcommand_index(simple, "gh", GH_OPTS_WITH_ARG)
    if hi is not None and simple[hi] == "pr" and simple[hi + 1:hi + 2] == ["merge"]:
        return "merging PRs is the human's job (Gate 3)"
    return None


def _bash_problem(cmd: str, active: bool, default_branch: str, current_branch: str) -> str | None:
    # Best-effort: guards against accidental direct writes, not adversarial bypass.
    if ".harness/state.json" in cmd and "state.py" not in cmd:
        return STATE_MSG
    if not active:
        return None
    for simple in _commands(cmd):
        problem = _simple_problem(simple, default_branch, current_branch)
        if problem:
            return problem
    return None


def check(payload: dict, state: dict | None, default_branch: str, current_branch: str) -> str | None:
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
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
    if not isinstance(payload, dict):
        return 0
    try:
        cwd = payload.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            cwd = "."
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
    except Exception:
        return 0
    if reason:
        print(f"dev-harness guard: {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
