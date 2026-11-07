import pytest

import state as st


def make(phase="preflight", **overrides):
    s = st.new_state("demo", "Demo idea")
    s["phase"] = phase
    s.update(overrides)
    return s


def test_new_state_shape():
    s = st.new_state("demo", "x")
    assert s["phase"] == "preflight"
    assert s["integration_branch"] == "harness/demo"
    assert s["tickets"] == [] and s["approvals"] == [] and s["gate_pending"] is None
    assert s["artifacts"] == {"map": None, "spec": None, "pr": None}


@pytest.mark.parametrize("slug", ["Bad Slug", "-lead", "a" * 49, "x\ny"])
def test_new_state_rejects_bad_slug(slug):
    with pytest.raises(st.StateError):
        st.new_state(slug, "x")


def test_save_load_roundtrip(tmp_path):
    s = st.new_state("demo", "x")
    st.save(tmp_path, s)
    assert st.load(tmp_path) == s
    assert (tmp_path / ".harness" / "state.json").is_file()


def test_load_missing_raises(tmp_path):
    with pytest.raises(st.StateError):
        st.load(tmp_path)


def gate(s, name):
    st.set_gate(s, name)
    st.approve_gate(s, name)


def test_full_standard_path():
    s = make("preflight")
    st.transition(s, "triage")
    st.set_route(s, "STANDARD")
    gate(s, "route")
    st.transition(s, "align")
    st.transition(s, "spec")
    gate(s, "spec")
    for phase in ("tickets", "build", "pr"):
        st.transition(s, phase)
    gate(s, "pr")
    st.transition(s, "retro")
    st.transition(s, "done")
    assert s["phase"] == "done"
    transitions = [e for e in s["log"] if e["event"] == "transition"]
    assert transitions[-1]["from"] == "retro" and transitions[-1]["to"] == "done"


def test_triage_requires_route_gate():
    s = make("triage", route="STANDARD")
    with pytest.raises(st.StateError, match="Gate 1"):
        st.transition(s, "align")


def test_route_decides_next_phase():
    s = make("triage", route="FOGGY", approvals=["route"])
    with pytest.raises(st.StateError):
        st.transition(s, "align")
    st.transition(s, "fog")
    assert s["phase"] == "fog"


def test_approve_route_requires_route_set():
    s = make("triage")
    st.set_gate(s, "route")
    with pytest.raises(st.StateError):
        st.approve_gate(s, "route")


def test_spec_requires_approval():
    s = make("spec")
    with pytest.raises(st.StateError, match="Gate 2"):
        st.transition(s, "tickets")


def test_pr_requires_approval():
    s = make("pr")
    with pytest.raises(st.StateError, match="Gate 3"):
        st.transition(s, "retro")


def test_pending_gate_blocks_transition():
    s = make("spec", approvals=["spec"])
    st.set_gate(s, "spec")
    with pytest.raises(st.StateError, match="pending"):
        st.transition(s, "tickets")


def test_illegal_skip():
    with pytest.raises(st.StateError, match="illegal"):
        st.transition(make("align"), "build")


def test_abort_from_active_phase_even_with_gate():
    s = make("spec")
    st.set_gate(s, "spec")
    st.transition(s, "aborted")
    assert s["phase"] == "aborted"
    with pytest.raises(st.StateError):
        st.transition(s, "aborted")


def test_approve_wrong_gate_raises():
    s = make("spec")
    st.set_gate(s, "spec")
    with pytest.raises(st.StateError):
        st.approve_gate(s, "pr")


def test_set_gate_twice_raises():
    s = make("spec")
    st.set_gate(s, "spec")
    with pytest.raises(st.StateError):
        st.set_gate(s, "spec")


def test_set_route_validates():
    with pytest.raises(st.StateError):
        st.set_route(make("triage"), "MAYBE")
    with pytest.raises(st.StateError):
        st.set_route(make("align"), "FOGGY")


def test_set_artifact_validates():
    s = make("spec")
    st.set_artifact(s, "spec", "docs/specs/demo/spec.md")
    st.set_artifact(s, "pr", "https://github.com/o/r/pull/3")
    assert s["artifacts"]["spec"] == "docs/specs/demo/spec.md"
    with pytest.raises(st.StateError):
        st.set_artifact(s, "spec", "docs/a\nIGNORE PREVIOUS")
    with pytest.raises(st.StateError):
        st.set_artifact(s, "notes", "x.md")
