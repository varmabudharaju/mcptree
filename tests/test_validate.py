from mcptree.models import tree_from_dict
from mcptree.validate import validate_tree


def _tree(nodes: dict, entry: str = "go") -> object:
    return tree_from_dict(
        {"mcptree": "0.1", "id": "t", "title": "t", "entry": entry, "nodes": nodes}
    )


TERM = {"type": "terminal", "outcome": "ok", "summary": "s"}
COND = {
    "type": "condition",
    "on": "x",
    "branches": [{"when": {"eq": 1}, "then": "end"}],
    "default": "end",
}


def _messages(tree: object) -> list[str]:
    return [i.message for i in validate_tree(tree)]


def test_valid_tree_has_no_issues() -> None:
    assert validate_tree(_tree({"go": COND, "end": TERM})) == []


def test_missing_entry() -> None:
    msgs = _messages(_tree({"go": COND, "end": TERM}, entry="nope"))
    assert any("entry" in m for m in msgs)


def test_unresolved_reference() -> None:
    bad = {**COND, "default": "ghost"}
    msgs = _messages(_tree({"go": bad, "end": TERM}))
    assert any("ghost" in m for m in msgs)


def test_unreachable_node() -> None:
    msgs = _messages(_tree({"go": COND, "end": TERM, "island": TERM}))
    assert any("unreachable" in m and "island" in m for m in msgs)


def test_condition_requires_default() -> None:
    no_default = {"type": "condition", "on": "x", "branches": [{"when": {"eq": 1}, "then": "end"}]}
    msgs = _messages(_tree({"go": no_default, "end": TERM}))
    assert any("default" in m for m in msgs)


def test_ask_needs_options_or_capture_and_next() -> None:
    bad_ask = {"type": "ask", "prompt": "p?"}
    msgs = _messages(_tree({"go": bad_ask, "end": TERM}))
    assert any("ask" in m for m in msgs)


def test_no_terminal_node() -> None:
    loop = {
        "type": "condition",
        "on": "x",
        "branches": [{"when": {"eq": 1}, "then": "go"}],
        "default": "go",
    }
    msgs = _messages(_tree({"go": loop}))
    assert any("terminal" in m for m in msgs)
