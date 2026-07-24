from pathlib import Path

from mcptree.engine import start, submit
from mcptree.loader import load_tree_file

TREE_PATH = Path(__file__).parent.parent / "trees" / "incident.yaml"


def test_incident_tree_loads() -> None:
    tree = load_tree_file(TREE_PATH)
    assert tree.id == "incident-triage"


def test_golden_trace_oom_path() -> None:
    """check_health(503) -> inspect_logs -> judgment oom -> remediate_oom."""
    state = start(load_tree_file(TREE_PATH))
    assert state.current_node == "check_health"

    env = submit(state, 1, {"status": 503})
    assert env["node"] == "inspect_logs" and env["state"] == "awaiting_tool_result"

    env = submit(state, 2, {"lines": ["java.lang.OutOfMemoryError: heap"]})
    assert env["node"] == "classify_error" and env["state"] == "awaiting_judgment"
    assert "lines" in str(env["evidence"])

    env = submit(state, 3, "oom", rationale="OutOfMemoryError in captured logs")
    assert env["state"] == "done" and env["outcome"] == "remediate"

    golden = [
        ("check_health", "branch_on_status"),
        ("branch_on_status", "inspect_logs"),
        ("inspect_logs", "classify_error"),
        ("classify_error", "remediate_oom"),
        ("remediate_oom", None),
    ]
    assert [(t.node, t.resolved_branch) for t in state.trace] == golden
    assert state.trace[3].rationale == "OutOfMemoryError in captured logs"


def test_golden_trace_maintenance_path() -> None:
    """check_health(302) -> ask_maintenance yes -> all_clear."""
    state = start(load_tree_file(TREE_PATH))
    env = submit(state, 1, {"status": 302})
    assert env["node"] == "ask_maintenance" and env["state"] == "awaiting_answer"
    env = submit(state, 2, "yes")
    assert env["outcome"] == "resolved"
