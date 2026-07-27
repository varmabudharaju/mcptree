import json

from mcptree.engine import MAX_FACT_BYTES, start, submit
from mcptree.models import tree_from_dict


def incident_tree() -> object:
    return tree_from_dict(
        {
            "mcptree": "0.1",
            "id": "inc",
            "title": "Incident",
            "entry": "check",
            "nodes": {
                "check": {
                    "type": "action",
                    "tool": "http_get",
                    "args": {"url": "http://x/health"},
                    "result": {"capture": "health", "schema": {"type": "object", "required": ["status"]}},
                    "next": "route",
                    "on_error": "escalate",
                },
                "route": {
                    "type": "condition",
                    "on": "health.status",
                    "branches": [
                        {"when": {"eq": 200}, "then": "ok"},
                        {"when": {"gte": 500}, "then": "classify"},
                    ],
                    "default": "ask_maint",
                },
                "classify": {
                    "type": "judgment",
                    "prompt": "Classify the error.",
                    "evidence": ["health"],
                    "options": [{"id": "oom", "then": "fix"}, {"id": "unknown", "then": "escalate"}],
                    "require_rationale": True,
                },
                "ask_maint": {
                    "type": "ask",
                    "prompt": "Maintenance window?",
                    "options": [{"id": "yes", "then": "ok"}, {"id": "no", "then": "escalate"}],
                    "capture": "maintenance",
                },
                "ok": {"type": "terminal", "outcome": "resolved", "summary": "fine"},
                "fix": {"type": "terminal", "outcome": "remediate", "summary": "restart"},
                "escalate": {"type": "terminal", "outcome": "escalate", "summary": "page"},
            },
        }
    )


def test_happy_path_action_condition_judgment() -> None:
    state = start(incident_tree())
    assert state.current_node == "check"
    env = submit(state, 1, {"status": 503})
    assert env["error"] is None
    assert env["state"] == "awaiting_judgment" and env["node"] == "classify"
    assert env["step"] == 2
    assert env["evidence"] == {"health": {"status": 503}}
    env = submit(state, 2, "oom", rationale="OOM lines in log")
    assert env["state"] == "done" and env["outcome"] == "remediate"
    picked = next(t for t in state.trace if t.node == "classify")
    assert picked.rationale == "OOM lines in log" and picked.input == "oom"


def test_ask_path_and_fact_capture() -> None:
    state = start(incident_tree())
    submit(state, 1, {"status": 302})  # default -> ask_maint
    env = submit(state, 2, "yes")
    assert env["outcome"] == "resolved"
    assert state.facts["maintenance"] == "yes"


def test_invalid_enum_value_keeps_state() -> None:
    state = start(incident_tree())
    submit(state, 1, {"status": 503})
    env = submit(state, 2, "not_an_option")
    assert env["error"].startswith("invalid_option")
    assert env["node"] == "classify" and env["step"] == 2


def test_missing_rationale_rejected() -> None:
    state = start(incident_tree())
    submit(state, 1, {"status": 503})
    env = submit(state, 2, "oom")
    assert env["error"].startswith("rationale_required")


def test_step_mismatch_returns_current_envelope() -> None:
    state = start(incident_tree())
    env = submit(state, 99, {"status": 200})
    assert env["error"].startswith("step_mismatch")
    assert env["node"] == "check" and env["step"] == 1


def test_finished_session_rejected() -> None:
    state = start(incident_tree())
    submit(state, 1, {"status": 200})
    env = submit(state, 2, "anything")
    assert env["error"].startswith("finished_session")


def test_schema_mismatch_rejected() -> None:
    state = start(incident_tree())
    env = submit(state, 1, {"nostatus": True})
    assert env["error"].startswith("schema_mismatch")
    assert env["node"] == "check"


def test_tool_error_takes_on_error_branch() -> None:
    state = start(incident_tree())
    env = submit(state, 1, "connection refused", is_error=True)
    assert env["outcome"] == "escalate"


def test_tool_error_without_on_error_is_in_envelope() -> None:
    t = incident_tree()
    raw = dict(t.raw)  # rebuild without on_error
    raw["nodes"] = json.loads(json.dumps(raw["nodes"]))
    del raw["nodes"]["check"]["on_error"]
    state = start(tree_from_dict(raw))
    env = submit(state, 1, "boom", is_error=True)
    assert env["error"].startswith("tool_error_unhandled")
    assert env["node"] == "check"


def test_fact_too_large_rejected() -> None:
    state = start(incident_tree())
    huge = {"status": 503, "blob": "x" * (MAX_FACT_BYTES + 10)}
    env = submit(state, 1, huge)
    assert env["error"].startswith("fact_too_large")


def free_text_ask_tree() -> object:
    return tree_from_dict(
        {
            "mcptree": "0.1",
            "id": "notes",
            "title": "Free Text Notes",
            "entry": "ask_notes",
            "nodes": {
                "ask_notes": {
                    "type": "ask",
                    "prompt": "What are your notes?",
                    "capture": "notes",
                    "next": "done",
                },
                "done": {"type": "terminal", "outcome": "collected", "summary": "notes saved"},
            },
        }
    )


def test_free_text_ask_small_answer_captured() -> None:
    state = start(free_text_ask_tree())
    assert state.current_node == "ask_notes"
    env = submit(state, 1, "hello world")
    assert env["error"] is None
    assert env["state"] == "done" and env["outcome"] == "collected"
    assert state.facts["notes"] == "hello world"


def test_free_text_ask_too_large_rejected() -> None:
    state = start(free_text_ask_tree())
    huge_text = "x" * (MAX_FACT_BYTES + 10)
    env = submit(state, 1, huge_text)
    assert env["error"].startswith("fact_too_large")
    assert env["node"] == "ask_notes" and env["step"] == 1
    assert "notes" not in state.facts
