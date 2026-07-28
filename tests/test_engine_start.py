from mcptree.engine import MISSING, envelope, evaluate, resolve_path, start
from mcptree.models import Predicate, tree_from_dict

TREE = tree_from_dict(
    {
        "mcptree": "0.1",
        "id": "t",
        "title": "t",
        "entry": "route",
        "nodes": {
            "route": {
                "type": "condition",
                "on": "health.status",
                "branches": [{"when": {"eq": 200}, "then": "done_ok"}],
                "default": "probe",
            },
            "probe": {
                "type": "action",
                "tool": "http_get",
                "args": {"url": "http://x/health"},
                "result": {"capture": "health"},
                "next": "route",
            },
            "done_ok": {"type": "terminal", "outcome": "resolved", "summary": "all good"},
        },
    }
)


def test_resolve_path_and_missing() -> None:
    facts = {"health": {"status": 200}}
    assert resolve_path(facts, "health.status") == 200
    assert resolve_path(facts, "health.nope") is MISSING
    assert resolve_path({}, "a.b.c") is MISSING


def test_evaluate_predicates() -> None:
    assert evaluate(Predicate("eq", 5), 5)
    assert evaluate(Predicate("neq", 5), 6)
    assert evaluate(Predicate("gte", 500), 503)
    assert not evaluate(Predicate("lt", 10), 10)
    assert evaluate(Predicate("in", ["a", "b"]), "a")
    assert evaluate(Predicate("exists", True), 0)
    assert not evaluate(Predicate("exists", True), MISSING)
    assert not evaluate(Predicate("gte", 500), MISSING)


def test_start_auto_advances_to_first_external_node() -> None:
    state = start(TREE)
    # facts empty -> condition default -> action node awaits tool result
    assert state.current_node == "probe"
    assert state.done is False
    assert state.step == 1
    assert [t.node for t in state.trace] == ["route"]
    assert state.trace[0].resolved_branch == "probe"
    assert state.tree_hash == TREE.content_hash()
    assert state.session_id.startswith("ses_")


def test_envelope_shape_for_action() -> None:
    state = start(TREE)
    env = envelope(state)
    assert env["state"] == "awaiting_tool_result"
    assert env["node"] == "probe"
    assert env["expects"] == {
        "kind": "tool_result",
        "tool": "http_get",
        "args": {"url": "http://x/health"},
        "schema": None,
    }
    assert env["step"] == 1
    assert env["error"] is None and env["outcome"] is None
    assert "http_get" in str(env["instruction"])


def test_start_reaching_terminal_immediately() -> None:
    t = tree_from_dict(
        {
            "mcptree": "0.1",
            "id": "t2",
            "title": "t",
            "entry": "end",
            "nodes": {"end": {"type": "terminal", "outcome": "ok", "summary": "done"}},
        }
    )
    state = start(t)
    assert state.done and state.outcome == "ok"
    env = envelope(state)
    assert env["state"] == "done" and env["outcome"] == "ok"


def test_max_steps_aborts_runaway_loop() -> None:
    t = tree_from_dict(
        {
            "mcptree": "0.1",
            "id": "loop",
            "title": "t",
            "entry": "a",
            "max_steps": 5,
            "nodes": {
                "a": {
                    "type": "condition",
                    "on": "x",
                    "branches": [{"when": {"eq": 1}, "then": "end"}],
                    "default": "b",
                },
                "b": {
                    "type": "condition",
                    "on": "x",
                    "branches": [{"when": {"eq": 1}, "then": "end"}],
                    "default": "a",
                },
                "end": {"type": "terminal", "outcome": "ok", "summary": "s"},
            },
        }
    )
    state = start(t)
    assert state.done and state.outcome == "aborted:max_steps"
    assert len(state.trace) <= 6


def test_evaluate_composites() -> None:
    band = Predicate(op="all", value=(Predicate("gte", 1), Predicate("lte", 2)))
    assert evaluate(band, 2) and not evaluate(band, 3)
    either = Predicate(op="any", value=(Predicate("eq", 200), Predicate("eq", 204)))
    assert evaluate(either, 204) and not evaluate(either, 500)
    assert evaluate(Predicate(op="not", value=Predicate("eq", 1)), 2)


def test_not_on_missing_fact_is_true() -> None:
    """Documented consequence: leaves are false on MISSING, so not(leaf) is true."""
    assert evaluate(Predicate(op="not", value=Predicate("eq", 1)), MISSING)
    assert not evaluate(Predicate(op="all", value=(Predicate("eq", 1),)), MISSING)
