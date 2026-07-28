from pathlib import Path

import pytest

from mcptree.engine import UnknownSessionError, start, submit
from mcptree.models import tree_from_dict
from mcptree.sessions import JsonSessionStore, state_from_dict, state_to_dict

TREE_DICT = {
    "mcptree": "0.1",
    "id": "t",
    "title": "t",
    "entry": "a",
    "nodes": {
        "a": {
            "type": "action",
            "tool": "noop",
            "args": {},
            "result": {"capture": "r"},
            "next": "end",
        },
        "end": {"type": "terminal", "outcome": "ok", "summary": "s"},
    },
}


def test_state_round_trip() -> None:
    state = start(tree_from_dict(TREE_DICT))
    submit(state, 1, {"v": 1})
    restored = state_from_dict(state_to_dict(state))
    assert restored.session_id == state.session_id
    assert restored.done and restored.outcome == "ok"
    assert restored.facts == {"r": {"v": 1}}
    assert [t.node for t in restored.trace] == [t.node for t in state.trace]
    assert restored.tree_hash == state.tree_hash
    assert restored.tree.id == "t"


def test_state_round_trip_facts_isolation() -> None:
    """Regression: ensure facts is copied, not aliased."""
    state = start(tree_from_dict(TREE_DICT))
    submit(state, 1, {"v": 1})
    restored = state_from_dict(state_to_dict(state))
    # Verify facts is a separate object
    assert restored.facts is not state.facts
    # Mutate restored.facts and verify original is unchanged
    restored.facts["r"] = {"v": 999}
    assert state.facts == {"r": {"v": 1}}


def test_json_store_save_load_list(tmp_path: Path) -> None:
    store = JsonSessionStore(tmp_path / "sessions")
    state = start(tree_from_dict(TREE_DICT))
    store.save(state)
    loaded = store.load(state.session_id)
    assert loaded.session_id == state.session_id
    assert loaded.current_node == "a"
    assert store.list_ids() == [state.session_id]


def test_load_unknown_session_raises(tmp_path: Path) -> None:
    store = JsonSessionStore(tmp_path)
    with pytest.raises(UnknownSessionError):
        store.load("ses_nope")


def test_pinned_tree_survives_source_change(tmp_path: Path) -> None:
    store = JsonSessionStore(tmp_path)
    state = start(tree_from_dict(TREE_DICT))
    store.save(state)
    # Even with no access to the original Tree object, load reconstructs it:
    loaded = store.load(state.session_id)
    env = submit(loaded, 1, {"v": 2})
    assert env["outcome"] == "ok"


def test_malformed_session_id_rejected_not_collided(tmp_path: Path) -> None:
    """Regression: 'ses_a.b/c' used to sanitize to the same file as 'ses_abc' —
    a malformed id could silently load someone else's session. Must raise."""
    store = JsonSessionStore(tmp_path)
    (tmp_path / "ses_abc.json").write_text("{}")  # collision target the old code would read
    for bad in ("ses_a.b/c", "ses_ABC", "ses_../../etc", "notasession", "ses_abc"):
        with pytest.raises(UnknownSessionError):
            store.load(bad)


def test_save_with_malformed_id_rejected(tmp_path: Path) -> None:
    store = JsonSessionStore(tmp_path)
    state = start(tree_from_dict(TREE_DICT), session_id="ses_not-hex-here")
    with pytest.raises(UnknownSessionError):
        store.save(state)
