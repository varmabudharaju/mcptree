from pathlib import Path

import pytest

from mcptree.loader import TreeLoadError, load_tree_file, load_trees_dir

VALID = """
mcptree: "0.1"
id: mini
title: Mini
entry: go
nodes:
  go:
    type: condition
    on: x
    branches:
      - when: { eq: 1 }
        then: end
    default: end
  end: { type: terminal, outcome: ok, summary: fine }
"""

INVALID = VALID.replace("default: end", "default: ghost")


def test_load_valid_file(tmp_path: Path) -> None:
    p = tmp_path / "mini.yaml"
    p.write_text(VALID)
    tree = load_tree_file(p)
    assert tree.id == "mini"


def test_load_invalid_file_lists_issues(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text(INVALID)
    with pytest.raises(TreeLoadError, match="ghost"):
        load_tree_file(p)


def test_load_dir(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text(VALID)
    (tmp_path / "b.yml").write_text(VALID.replace("id: mini", "id: mini2"))
    trees = load_trees_dir(tmp_path)
    assert set(trees) == {"mini", "mini2"}


def test_load_dir_duplicate_id(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text(VALID)
    (tmp_path / "b.yaml").write_text(VALID)
    with pytest.raises(TreeLoadError, match="duplicate"):
        load_trees_dir(tmp_path)


def test_load_dir_empty(tmp_path: Path) -> None:
    with pytest.raises(TreeLoadError, match="no trees"):
        load_trees_dir(tmp_path)


def test_load_yaml11_unquoted_on_key(tmp_path: Path) -> None:
    """Regression: YAML-1.1 parses bare 'on' as boolean True key. Loader must normalize."""
    p = tmp_path / "on_key.yaml"
    p.write_text(VALID)
    tree = load_tree_file(p)
    cond = tree.nodes["go"]
    assert cond.type == "condition"
    assert cond.on == "x"


def test_load_missing_file(tmp_path: Path) -> None:
    """File I/O failure: missing file raises TreeLoadError with 'cannot read'."""
    p = tmp_path / "nope.yaml"
    with pytest.raises(TreeLoadError, match="cannot read"):
        load_tree_file(p)


def test_load_invalid_yaml_syntax(tmp_path: Path) -> None:
    """YAML syntax error: unclosed bracket raises TreeLoadError with 'invalid YAML'."""
    p = tmp_path / "bad_syntax.yaml"
    p.write_text("a: [unclosed")
    with pytest.raises(TreeLoadError, match="invalid YAML"):
        load_tree_file(p)


def test_load_non_dict_top_level(tmp_path: Path) -> None:
    """Non-dict top level: YAML list raises TreeLoadError with 'top level'."""
    p = tmp_path / "list.yaml"
    p.write_text("- 1\n- 2")
    with pytest.raises(TreeLoadError, match="top level"):
        load_tree_file(p)


def test_load_unquoted_date_rejected(tmp_path: Path) -> None:
    """Regression: yaml.safe_load turns an unquoted date into datetime.date,
    which passes validation but is not JSON-serializable and would later
    crash content_hash()/session serialization. Must be rejected at load."""
    text = """
mcptree: "0.1"
id: mini
title: Mini
entry: go
nodes:
  go:
    type: action
    tool: t
    args: { since: 2026-01-01 }
    result: { capture: r }
    next: end
  end: { type: terminal, outcome: ok, summary: fine }
"""
    p = tmp_path / "date.yaml"
    p.write_text(text)
    with pytest.raises(TreeLoadError, match="non-JSON"):
        load_tree_file(p)


def test_load_nested_boolean_key_rejected(tmp_path: Path) -> None:
    """Regression: the YAML-1.1 'on' normalization only rewrites a node's
    top-level 'on' key. A NESTED bare 'on:' (e.g. inside 'args') still
    parses as the bool key True, which json.dumps(sort_keys=True) cannot
    sort alongside string keys -- the same call Tree.content_hash() makes.
    Must be rejected at load time, not crash later at tree_start."""
    text = """
mcptree: "0.1"
id: mini
title: Mini
entry: go
nodes:
  go:
    type: action
    tool: t
    args: { on: x, other: y }
    result: { capture: r }
    next: end
  end: { type: terminal, outcome: ok, summary: fine }
"""
    p = tmp_path / "nested_bool_key.yaml"
    p.write_text(text)
    with pytest.raises(TreeLoadError, match="non-JSON"):
        load_tree_file(p)
