import subprocess
import sys
from pathlib import Path

import state as st

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "state.py"


def test_repo_root_from_subdirectory(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert st.repo_root(sub).resolve() == tmp_path.resolve()


def test_repo_root_falls_back_to_cwd(tmp_path):
    assert st.repo_root(tmp_path / "missing") == tmp_path / "missing"


def test_concurrent_cli_updates_are_not_lost(tmp_path):
    base = [sys.executable, str(SCRIPT), "--root", str(tmp_path)]
    subprocess.run([*base, "init", "--slug", "demo", "--idea", "x"], check=True, capture_output=True)
    state = st.load(tmp_path)
    state["phase"] = "build"
    for tid in ("A", "B"):
        st.add_ticket(state, tid, None, [])
    st.save(tmp_path, state)
    for i in range(20):
        procs = [
            subprocess.Popen([*base, "log", f"{tid}-{i}"], stderr=subprocess.PIPE)
            for tid in ("A", "B")
        ]
        for p in procs:
            assert p.wait() == 0
    events = [e["event"] for e in st.load(tmp_path)["log"]]
    for tid in ("A", "B"):
        for i in range(20):
            assert f"{tid}-{i}" in events
