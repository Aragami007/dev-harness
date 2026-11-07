import io
import json

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


@pytest.mark.parametrize("cmd", [
    "git push -fu origin harness/demo",
    "git push -uf origin harness/demo",
    "git -c k=v push -f origin harness/demo",
    "ls;git push -f origin harness/demo",
    "bash -c 'git push -f origin harness/demo'",
])
def test_blocks_force_push_variants(tmp_path, cmd):
    assert "force" in bash(tmp_path, cmd, run_state())


@pytest.mark.parametrize("cmd", [
    "git push origin x&&git push origin main",
    "git -C dir push origin main",
    "git --git-dir=.git push origin main",
    "git --no-pager push origin main",
    "sh -c \"git push origin main\"",
    "zsh -c 'cd x && git push origin main'",
    "bash -c \"bash -c 'git push origin main'\"",
    "git push --all",
    "git push --mirror origin",
])
def test_blocks_push_to_default_variants(tmp_path, cmd):
    assert bash(tmp_path, cmd, run_state())


def test_head_push_resolves_to_current_branch(tmp_path):
    assert bash(tmp_path, "git push origin HEAD", run_state(), current="main")
    assert bash(tmp_path, "git push origin HEAD", run_state(), current="harness/demo") is None


def test_git_global_opts_merge_on_default(tmp_path):
    assert bash(tmp_path, "git -C . merge x", run_state(), current="main")
    assert bash(tmp_path, "git -C . merge x", run_state(), current="harness/demo") is None


@pytest.mark.parametrize("cmd", [
    "gh -R o/r pr merge 1",
    "gh --repo o/r pr merge 1",
    "gh --repo=o/r pr merge 1",
    "true;gh pr merge 1",
    "bash -c 'gh pr merge 1'",
])
def test_blocks_gh_pr_merge_variants(tmp_path, cmd):
    assert bash(tmp_path, cmd, run_state())


def test_gh_pr_view_allowed(tmp_path):
    assert bash(tmp_path, "gh -R o/r pr view 1", run_state()) is None


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


def test_main_non_object_json_allows(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("[]"))
    assert guard.main() == 0


def test_main_bad_cwd_allows(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(
        json.dumps({"tool_name": "Bash", "cwd": 5, "tool_input": {"command": "ls"}})))
    assert guard.main() == 0


def test_main_denies_state_write(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"tool_name": "Write", "cwd": str(tmp_path), "tool_input": {"file_path": ".harness/state.json"}})))
    assert guard.main() == 2
    assert "dev-harness guard" in capsys.readouterr().err


def test_check_non_dict_tool_input(tmp_path):
    assert guard.check({"tool_name": "Bash", "cwd": str(tmp_path), "tool_input": "x"}, None, "main", "main") is None


def test_build_edit_allowance_uses_given_root(tmp_path):
    root = tmp_path / "repo"
    sub = root / "sub"
    sub.mkdir(parents=True)
    s = run_state("build")
    p = payload("Write", sub, file_path=str(root / "top.txt"))
    assert guard.check(p, s, "main", "x") is not None
    assert guard.check(p, s, "main", "x", root=root) is None


@pytest.mark.parametrize(
    "cmd,current",
    [
        ("git status\ngit push origin main", "harness/demo"),
        ("git status\ngit push -f origin x", "harness/demo"),
        ("/usr/bin/git push -f origin x", "harness/demo"),
        ('bash -lc "git push -f origin x"', "harness/demo"),
        ("bash -ec 'git push origin main'", "harness/demo"),
        ("git push origin @", "main"),
        ("git log && git status && git push origin main", "harness/demo"),
    ],
)
def test_guard_denies_extra_shapes(tmp_path, cmd, current):
    assert bash(tmp_path, cmd, run_state("build"), current=current)


def test_guard_allows_feature_push_still(tmp_path):
    assert bash(tmp_path, "git push -u origin harness/demo", run_state("build")) is None


def test_relative_ticket_worktree_resolves_against_root(tmp_path):
    root = tmp_path / "repo"
    sub = root / "sub"
    sub.mkdir(parents=True)
    ticket = {"id": "T1", "issue": None, "deps": [], "status": "in-progress",
              "attempts": 0, "worktree": "wt/T1", "block_reason": None}
    s = run_state("build", [ticket])
    p = payload("Edit", sub, file_path=str(root / "wt" / "T1" / "a.py"))
    assert guard.check(p, s, "main", "x", root=root) is None
