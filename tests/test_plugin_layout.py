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


def test_orchestrator_resume_and_start_ordering():
    text = read("skills/harness-orchestrator/SKILL.md")
    start = text.split("## Start")[1].split("## Resume")[0]
    assert start.index("git checkout -b harness/<slug>") < start.index("git add .gitignore")
    assert start.index("git checkout -b harness/<slug>") < start.index("state.py init")
    resume = text.split("## Resume")[1].split("## Phase: triage")[0]
    assert "gh pr view <url> --json state -q .state" in resume
    assert "do not transition to build" in text
