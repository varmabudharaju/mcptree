import pytest

from mcptree.models import SPEC_VERSION, Tree, TreeParseError, tree_from_dict

MINIMAL = {
    "mcptree": "0.1",
    "id": "t1",
    "title": "T1",
    "entry": "go",
    "nodes": {
        "go": {
            "type": "condition",
            "on": "x",
            "branches": [{"when": {"eq": 1}, "then": "yes"}],
            "default": "no",
        },
        "yes": {"type": "terminal", "outcome": "ok", "summary": "yes"},
        "no": {"type": "terminal", "outcome": "ok", "summary": "no"},
    },
}


def test_parse_minimal_tree() -> None:
    tree = tree_from_dict(MINIMAL)
    assert isinstance(tree, Tree)
    assert tree.id == "t1"
    assert tree.spec_version == SPEC_VERSION
    assert tree.entry == "go"
    assert tree.max_steps == 50
    cond = tree.nodes["go"]
    assert cond.type == "condition"
    assert cond.branches[0].when.op == "eq"
    assert cond.branches[0].when.value == 1
    assert cond.branches[0].then == "yes"
    assert tree.nodes["yes"].outcome == "ok"


def test_unknown_node_type_raises() -> None:
    bad = {**MINIMAL, "nodes": {**MINIMAL["nodes"], "go": {"type": "wat"}}}
    with pytest.raises(TreeParseError, match="unknown node type"):
        tree_from_dict(bad)


def test_missing_required_field_raises() -> None:
    bad = dict(MINIMAL)
    del bad["entry"]
    with pytest.raises(TreeParseError, match="entry"):
        tree_from_dict(bad)


def test_unsupported_spec_version_raises() -> None:
    with pytest.raises(TreeParseError, match="spec version"):
        tree_from_dict({**MINIMAL, "mcptree": "9.9"})


def test_content_hash_stable_and_key_order_insensitive() -> None:
    a = tree_from_dict(MINIMAL)
    reordered = dict(reversed(list(MINIMAL.items())))
    b = tree_from_dict(reordered)
    assert a.content_hash() == b.content_hash()
    changed = {**MINIMAL, "title": "different"}
    assert tree_from_dict(changed).content_hash() != a.content_hash()


def test_action_and_judgment_and_ask_parse() -> None:
    d = {
        "mcptree": "0.1",
        "id": "t2",
        "title": "T2",
        "entry": "a",
        "max_steps": 7,
        "nodes": {
            "a": {
                "type": "action",
                "tool": "get_logs",
                "args": {"n": 5},
                "result": {"capture": "logs", "schema": {"type": "object"}},
                "next": "j",
                "on_error": "end",
            },
            "j": {
                "type": "judgment",
                "prompt": "pick",
                "evidence": ["logs"],
                "options": [{"id": "x", "then": "q"}, {"id": "y", "then": "end"}],
                "require_rationale": True,
            },
            "q": {
                "type": "ask",
                "prompt": "sure?",
                "options": [{"id": "yes", "then": "end"}, {"id": "no", "then": "end"}],
                "capture": "sure",
            },
            "end": {"type": "terminal", "outcome": "done", "summary": "bye"},
        },
    }
    t = tree_from_dict(d)
    assert t.max_steps == 7
    a = t.nodes["a"]
    assert (a.tool, a.capture, a.next, a.on_error) == ("get_logs", "logs", "j", "end")
    assert a.schema == {"type": "object"}
    j = t.nodes["j"]
    assert j.require_rationale is True and j.evidence == ("logs",)
    q = t.nodes["q"]
    assert q.options[0].id == "yes" and q.capture == "sure" and q.next is None


def _mini(nodes: dict[str, object], version: str = "0.2") -> dict[str, object]:
    return {
        "mcptree": version,
        "id": "t",
        "title": "T",
        "entry": next(iter(nodes)),
        "nodes": nodes,
    }


COMPOSITE_COND: dict[str, object] = {
    "c": {
        "type": "condition",
        "on": "x",
        "branches": [{"when": {"all": [{"gte": 1}, {"lte": 2}]}, "then": "end"}],
        "default": "end",
    },
    "end": {"type": "terminal", "outcome": "done", "summary": "s"},
}


def test_composite_predicates_parse_in_02() -> None:
    tree = tree_from_dict(_mini(COMPOSITE_COND))
    pred = tree.nodes["c"].branches[0].when
    assert pred.op == "all"
    assert all(p.op in ("gte", "lte") for p in pred.value)


def test_composite_predicates_rejected_in_01() -> None:
    with pytest.raises(TreeParseError, match="requires mcptree 0.2"):
        tree_from_dict(_mini(COMPOSITE_COND, version="0.1"))


def test_not_takes_single_predicate() -> None:
    nodes = dict(COMPOSITE_COND)
    nodes["c"] = {
        **COMPOSITE_COND["c"],
        "branches": [{"when": {"not": {"eq": 200}}, "then": "end"}],
    }
    tree = tree_from_dict(_mini(nodes))
    assert tree.nodes["c"].branches[0].when.op == "not"


def test_all_requires_nonempty_list() -> None:
    nodes = dict(COMPOSITE_COND)
    nodes["c"] = {**COMPOSITE_COND["c"], "branches": [{"when": {"all": []}, "then": "end"}]}
    with pytest.raises(TreeParseError, match="non-empty list"):
        tree_from_dict(_mini(nodes))


def test_version_03_still_rejected() -> None:
    with pytest.raises(TreeParseError, match="unsupported spec version"):
        tree_from_dict(_mini(COMPOSITE_COND, version="0.3"))
