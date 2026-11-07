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


def test_corrupt_state_reports_cleanly(tmp_path, capsys):
    run(tmp_path, "init", "--slug", "demo", "--idea", "x")
    st.state_path(tmp_path).write_text("{not json")
    capsys.readouterr()
    assert run(tmp_path, "get") == 1
    assert "state.py:" in capsys.readouterr().err


def test_lock_creation_failure_reports_cleanly(tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    rc = st.main(["--root", str(blocker / "sub"), "init", "--slug", "demo", "--idea", "x"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "state.py:" in err and "Traceback" not in err


def test_get_without_state_leaves_no_harness_dir(tmp_path, capsys):
    assert run(tmp_path, "get") == 1
    assert "state.py: no state at" in capsys.readouterr().err
    assert not (tmp_path / ".harness").exists()
