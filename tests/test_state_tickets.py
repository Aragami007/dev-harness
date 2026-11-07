import pytest

import state as st


def ticket_state(*specs):
    s = st.new_state("demo", "x")
    s["phase"] = "tickets"
    for tid, deps in specs:
        st.add_ticket(s, tid, 40 + len(s["tickets"]), deps)
    return s


def status(s, tid):
    return st.find_ticket(s, tid)["status"]


def test_add_ticket_defaults():
    s = ticket_state(("T1", []))
    assert st.find_ticket(s, "T1") == {
        "id": "T1", "issue": 40, "deps": [], "status": "ready",
        "attempts": 0, "worktree": None, "block_reason": None,
    }


def test_add_ticket_validates():
    s = ticket_state(("T1", []))
    with pytest.raises(st.StateError):
        st.add_ticket(s, "T1", 99, [])            # duplicate
    with pytest.raises(st.StateError):
        st.add_ticket(s, "T2", 99, ["T9"])        # unknown dep
    with pytest.raises(st.StateError):
        st.add_ticket(s, "bad id\n", 99, [])      # unsafe id
    s["phase"] = "align"
    with pytest.raises(st.StateError):
        st.add_ticket(s, "T3", 99, [])            # wrong phase


def test_runnable_respects_deps():
    s = ticket_state(("T1", []), ("T2", ["T1"]))
    assert [t["id"] for t in st.runnable_tickets(s)] == ["T1"]
    st.update_ticket(s, "T1", "done")
    assert [t["id"] for t in st.runnable_tickets(s)] == ["T2"]


def test_in_progress_records_worktree_with_spaces():
    s = ticket_state(("T1", []))
    st.update_ticket(s, "T1", "in-progress", worktree="/home/u/PvP Coach/.wt/T1")
    t = st.find_ticket(s, "T1")
    assert t["status"] == "in-progress" and t["worktree"] == "/home/u/PvP Coach/.wt/T1"
    with pytest.raises(st.StateError):
        st.update_ticket(s, "T1", "in-progress", worktree="/a\n/b")


def test_failed_retries_then_blocks_and_cascades():
    s = ticket_state(("T1", []), ("T2", ["T1"]), ("T3", ["T2"]), ("T4", []))
    st.update_ticket(s, "T1", "failed")
    st.update_ticket(s, "T1", "failed")
    assert status(s, "T1") == "ready" and st.find_ticket(s, "T1")["attempts"] == 2
    st.update_ticket(s, "T1", "failed")
    assert status(s, "T1") == "blocked"
    assert st.find_ticket(s, "T1")["block_reason"] == "failed 3 attempts"
    assert status(s, "T2") == "blocked" and st.find_ticket(s, "T2")["block_reason"] == "dependency T1"
    assert status(s, "T3") == "blocked" and st.find_ticket(s, "T3")["block_reason"] == "dependency T2"
    assert status(s, "T4") == "ready"


def test_settled_ticket_cannot_change():
    s = ticket_state(("T1", []))
    st.update_ticket(s, "T1", "done")
    with pytest.raises(st.StateError):
        st.update_ticket(s, "T1", "ready")


def test_update_rejects_unknown_status():
    with pytest.raises(st.StateError):
        st.update_ticket(ticket_state(("T1", [])), "T1", "blocked")


def test_all_settled():
    s = ticket_state(("T1", []), ("T2", []))
    assert not st.all_settled(s)
    st.update_ticket(s, "T1", "done")
    for _ in range(3):
        st.update_ticket(s, "T2", "failed")
    assert st.all_settled(s)


def test_reset_missing_worktrees(tmp_path):
    s = ticket_state(("T1", []), ("T2", []))
    existing = tmp_path / "wt-T2"
    existing.mkdir()
    st.update_ticket(s, "T1", "in-progress", worktree=str(tmp_path / "gone"))
    st.update_ticket(s, "T2", "in-progress", worktree=str(existing))
    assert st.reset_missing_worktrees(s) == ["T1"]
    assert status(s, "T1") == "ready" and st.find_ticket(s, "T1")["worktree"] is None
    assert status(s, "T2") == "in-progress"
