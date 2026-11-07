import state as st
import tickets_sync as ts
from fakes import FakeRun


def make_state():
    s = st.new_state("demo", "x")
    s["phase"] = "tickets"
    st.add_ticket(s, "T1", 41, [])
    st.add_ticket(s, "T2", 42, ["T1"])
    st.add_ticket(s, "T3", None, [])
    st.update_ticket(s, "T1", "done")
    return s


def test_label_command():
    assert ts.label_command(41, "done") == [
        "gh", "issue", "edit", "41",
        "--add-label", "harness:ticket,harness:done",
        "--remove-label", "harness:ready,harness:in-progress,harness:blocked",
    ]


def test_sync_skips_tickets_without_issue():
    run = FakeRun()
    assert ts.sync(make_state(), run) == []
    assert [c[3] for c in run.calls] == ["41", "42"]
    assert "harness:ready" in run.calls[1][5]


def test_sync_reports_failures():
    assert ts.sync(make_state(), FakeRun(fail_on=["42"])) == [42]


def test_ensure_labels_creates_all_with_force():
    run = FakeRun()
    assert ts.ensure_labels(run) == []
    assert len(run.calls) == len(ts.LABEL_COLORS)
    assert all(c[:3] == ["gh", "label", "create"] and "--force" in c for c in run.calls)


def test_ensure_labels_reports_failures():
    assert ts.ensure_labels(FakeRun(fail_on=["harness:done"])) == ["harness:done"]


def test_main_corrupt_state_exits_1(tmp_path, capsys):
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "state.json").write_text("{bad")
    assert ts.main(["--root", str(tmp_path)]) == 1
    assert "tickets_sync:" in capsys.readouterr().err
