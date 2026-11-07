#!/usr/bin/env python3
"""Preflight checks before a dev-harness run. Prints a fix for each failure; exit 1 if any fail."""
from __future__ import annotations

import argparse
import json
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
GH_INSTALL_FIX = "Install the GitHub CLI (https://cli.github.com) and run: gh auth login"
GITIGNORE_LINES = (".harness/*", "!.harness/retros/")


def _run(run, cmd, **kwargs):
    """Call run(); a missing binary becomes a failed CompletedProcess (127, shell convention)."""
    try:
        return run(cmd, **kwargs)
    except OSError:
        return subprocess.CompletedProcess(cmd, 127, "", "")


def check_git(root, run) -> Check:
    result = _run(run, ["git", "rev-parse", "--is-inside-work-tree"], cwd=root,
                  capture_output=True, text=True)
    ok = result.returncode == 0 and result.stdout.strip() == "true"
    return Check("git repo", ok, "Run /ship inside a git repository with at least one commit.")


def check_clean(root, run) -> Check:
    result = _run(run, ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True)
    dirty = [line for line in result.stdout.splitlines() if line.strip() and ".harness/" not in line]
    ok = result.returncode == 0 and not dirty
    return Check("clean tree", ok, "Commit or stash your changes first: git stash -u")


def check_gh(run) -> Check:
    result = _run(run, ["gh", "auth", "status"], capture_output=True, text=True)
    if result.returncode == 127:
        fix = GH_INSTALL_FIX
    else:
        fix = "Log in to the GitHub CLI: gh auth login"
    return Check("gh auth", result.returncode == 0, fix)


def skill_search_dirs(home, root) -> list[Path]:
    return [Path(home) / ".claude" / "skills", Path(root) / ".claude" / "skills",
            Path(home) / ".claude" / "plugins"]


def _preference(path: Path) -> tuple:
    # Prefer Matt Pocock's pack over same-named skills from other plugins,
    # and an installed copy (plugins/cache) over a marketplace checkout.
    text = path.as_posix()
    return ("mattpocock" not in text, "/cache/" not in text, text)


def find_skill_paths(dirs) -> dict[str, Path]:
    """Map each required skill name to the SKILL.md file to follow."""
    candidates: dict[str, list[Path]] = {}
    for directory in dirs:
        if directory.is_dir():
            for skill_md in directory.rglob("SKILL.md"):
                if skill_md.parent.name in REQUIRED_SKILLS:
                    candidates.setdefault(skill_md.parent.name, []).append(skill_md)
    return {name: min(paths, key=_preference) for name, paths in candidates.items()}


def find_missing_skills(dirs) -> list[str]:
    found = find_skill_paths(dirs)
    return [name for name in REQUIRED_SKILLS if name not in found]


def write_skill_map(root, paths: dict[str, Path]) -> Path:
    """Write .harness/skills.json so the orchestrator can follow skills that
    cannot be invoked through the Skill tool (disable-model-invocation)."""
    target = Path(root) / ".harness" / "skills.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({name: str(path) for name, path in sorted(paths.items())}, indent=2) + "\n")
    return target


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
    skill_map = write_skill_map(args.root, find_skill_paths(skill_search_dirs(args.home, args.root)))
    print(f"wrote {skill_map}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
