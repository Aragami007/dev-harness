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


def test_missing_binaries_fail_cleanly():
    def missing(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    assert not pf.check_git(".", missing).ok
    assert not pf.check_clean(".", missing).ok
    result = pf.check_gh(missing)
    assert not result.ok and "cli.github.com" in result.fix


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


def test_find_skill_paths_prefers_matt_pocock_cache(tmp_path):
    plugins = tmp_path / "home" / ".claude" / "plugins"
    make_skills(plugins / "marketplaces" / "mattpocock" / "skills" / "engineering", ["to-spec"])
    make_skills(plugins / "cache" / "mattpocock" / "mattpocock-skills" / "1.3.2" / "skills", ["to-spec"])
    make_skills(plugins / "cache" / "other" / "x" / "skills", ["tdd"])
    make_skills(plugins / "cache" / "mattpocock" / "mattpocock-skills" / "1.3.2" / "skills" / "e", ["tdd"])
    paths = pf.find_skill_paths(pf.skill_search_dirs(tmp_path / "home", tmp_path / "repo"))
    assert "/cache/mattpocock/" in str(paths["to-spec"])
    assert "/cache/mattpocock/" in str(paths["tdd"])
    assert paths["to-spec"].name == "SKILL.md"
    assert "retro" not in paths


def test_write_skill_map(tmp_path):
    import json
    skill = tmp_path / "s" / "to-spec" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("x")
    target = pf.write_skill_map(tmp_path, {"to-spec": skill})
    assert target == tmp_path / ".harness" / "skills.json"
    assert json.loads(target.read_text()) == {"to-spec": str(skill)}
