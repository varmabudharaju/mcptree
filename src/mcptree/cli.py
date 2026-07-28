"""mcptree CLI: serve / validate / viz."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from typing import cast

from .loader import TreeLoadError, load_tree_file
from .models import (
    ActionNode,
    AskNode,
    ConditionNode,
    JudgmentNode,
    Predicate,
    TerminalNode,
    Tree,
)


def _esc(text: object) -> str:
    return str(text).replace('"', "#quot;")


def _pred_label(p: Predicate) -> str:
    if p.op == "exists":
        return "exists"
    if p.op == "not":
        return f"not({_pred_label(cast(Predicate, p.value))})"
    if p.op in ("all", "any"):
        inner = ", ".join(_pred_label(q) for q in cast("tuple[Predicate, ...]", p.value))
        return f"{p.op}({inner})"
    return f"{p.op} {_esc(p.value)}"


def render_mermaid(tree: Tree) -> str:
    lines = ["flowchart TD"]
    for nid, node in tree.nodes.items():
        if isinstance(node, ConditionNode):
            lines.append(f'  {nid}{{"{nid}<br/>on {_esc(node.on)}"}}')
        elif isinstance(node, TerminalNode):
            lines.append(f'  {nid}(["{nid}<br/>{_esc(node.outcome)}"])')
        elif isinstance(node, ActionNode):
            lines.append(f'  {nid}["{nid}<br/>action: {_esc(node.tool)}"]')
        elif isinstance(node, JudgmentNode):
            lines.append(f'  {nid}[["{nid}<br/>judgment"]]')
        elif isinstance(node, AskNode):
            lines.append(f'  {nid}["{nid}<br/>ask"]')
    for nid, node in tree.nodes.items():
        if isinstance(node, ConditionNode):
            for b in node.branches:
                lines.append(f"  {nid} -- {_pred_label(b.when)} --> {b.then}")
            if node.default:
                lines.append(f"  {nid} -- default --> {node.default}")
        elif isinstance(node, (AskNode, JudgmentNode)):
            for o in node.options:
                lines.append(f"  {nid} -- {_esc(o.id)} --> {o.then}")
            if isinstance(node, AskNode) and node.next:
                lines.append(f"  {nid} --> {node.next}")
        elif isinstance(node, ActionNode):
            lines.append(f"  {nid} --> {node.next}")
            if node.on_error:
                lines.append(f"  {nid} -- on_error --> {node.on_error}")
    return "\n".join(lines)


def _cmd_validate(trees_dir: Path) -> int:
    files = sorted(list(trees_dir.glob("*.yaml")) + list(trees_dir.glob("*.yml")))
    if not files:
        print(f"no trees found in {trees_dir}")
        return 1
    failed = False
    for f in files:
        try:
            tree = load_tree_file(f)
            print(f"OK   {f} (id={tree.id})")
        except TreeLoadError as exc:
            failed = True
            print(f"FAIL {exc}")
    return 1 if failed else 0


def _cmd_viz(path: Path) -> int:
    try:
        print(render_mermaid(load_tree_file(path)))
    except TreeLoadError as exc:
        print(f"FAIL {exc}")
        return 1
    return 0


def _cmd_serve(trees_dir: Path, sessions: Path | None, name: str, elicit: bool) -> int:
    from fastmcp import FastMCP

    from .mount import DecisionTrees

    mcp = FastMCP(name)
    DecisionTrees(mcp, trees_dir, sessions_dir=sessions, elicit=elicit)
    mcp.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mcptree", description="Declarative decision trees for MCP")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="Run an MCP server publishing a directory of trees")
    p_serve.add_argument("trees_dir", type=Path)
    p_serve.add_argument("--sessions", type=Path, default=None)
    p_serve.add_argument("--name", default="mcptree")
    p_serve.add_argument(
        "--no-elicit", action="store_true",
        help="Never use MCP elicitation for ask nodes (agent relays instead)",
    )

    p_val = sub.add_parser("validate", help="Validate all trees in a directory")
    p_val.add_argument("trees_dir", type=Path)

    p_viz = sub.add_parser("viz", help="Render a tree as a mermaid diagram")
    p_viz.add_argument("tree_file", type=Path)

    args = parser.parse_args(argv)
    if args.command == "serve":
        return _cmd_serve(args.trees_dir, args.sessions, args.name, not args.no_elicit)
    if args.command == "validate":
        return _cmd_validate(args.trees_dir)
    return _cmd_viz(args.tree_file)


if __name__ == "__main__":
    sys.exit(main())
