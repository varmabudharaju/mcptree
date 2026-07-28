from pathlib import Path

import pytest

from mcptree.cli import main, render_mermaid
from mcptree.loader import load_tree_file

TREE_PATH = Path(__file__).parent.parent / "trees" / "incident.yaml"


def test_render_mermaid_shapes_and_edges() -> None:
    m = render_mermaid(load_tree_file(TREE_PATH))
    assert m.startswith("flowchart TD")
    assert "branch_on_status{" in m          # condition -> diamond
    assert "all_clear([" in m                # terminal -> stadium
    assert 'check_health["' in m             # action -> rect
    assert "-- eq 200 -->" in m
    assert "-- default -->" in m
    assert "-- oom -->" in m


def test_validate_command_ok(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["validate", str(TREE_PATH.parent)])
    assert code == 0
    assert "OK" in capsys.readouterr().out


def test_validate_command_bad(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = TREE_PATH.read_text().replace("then: all_clear", "then: ghost")
    (tmp_path / "bad.yaml").write_text(bad)
    code = main(["validate", str(tmp_path)])
    assert code == 1
    assert "ghost" in capsys.readouterr().out


def test_viz_command(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["viz", str(TREE_PATH)])
    assert code == 0
    assert "flowchart TD" in capsys.readouterr().out


def test_mermaid_escapes_quotes() -> None:
    from mcptree.models import tree_from_dict

    tree = tree_from_dict(
        {
            "mcptree": "0.1", "id": "t", "title": "T", "entry": "a",
            "nodes": {
                "a": {"type": "action", "tool": 'curl "quoted"', "next": "end"},
                "end": {"type": "terminal", "outcome": "done", "summary": "s"},
            },
        }
    )
    out = render_mermaid(tree)
    assert '"quoted"' not in out and "#quot;quoted#quot;" in out


def test_mermaid_composite_predicate_labels() -> None:
    tree = load_tree_file(Path(__file__).parent.parent / "trees" / "deploy.yaml")
    out = render_mermaid(tree)
    assert "all(gte 1, lte 2)" in out
    assert "Predicate(" not in out
