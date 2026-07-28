"""Graph-level tree validation. Stdlib only."""

from __future__ import annotations

from dataclasses import dataclass

from .models import ActionNode, AskNode, ConditionNode, JudgmentNode, Node, TerminalNode, Tree


@dataclass(frozen=True)
class ValidationIssue:
    node: str | None
    message: str


def _refs(node: Node) -> list[str]:
    if isinstance(node, ConditionNode):
        out = [b.then for b in node.branches]
        if node.default is not None:
            out.append(node.default)
        return out
    if isinstance(node, AskNode):
        out = [o.then for o in node.options]
        if node.next is not None and not node.options:
            out.append(node.next)
        return out
    if isinstance(node, JudgmentNode):
        return [o.then for o in node.options]
    if isinstance(node, ActionNode):
        return [node.next] + ([node.on_error] if node.on_error is not None else [])
    return []


def validate_tree(tree: Tree) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if tree.entry not in tree.nodes:
        issues.append(ValidationIssue(None, f"entry '{tree.entry}' is not a node"))

    for nid, node in tree.nodes.items():
        for ref in _refs(node):
            if ref not in tree.nodes:
                issues.append(ValidationIssue(nid, f"reference to unknown node '{ref}'"))
        if isinstance(node, ConditionNode) and node.default is None:
            issues.append(ValidationIssue(nid, "condition node requires a 'default' branch"))
        if isinstance(node, AskNode) and not node.options and not (node.capture and node.next):
            issues.append(
                ValidationIssue(nid, "ask node needs 'options', or 'capture' plus 'next'")
            )
        if isinstance(node, AskNode) and node.options and node.next is not None:
            issues.append(
                ValidationIssue(
                    nid,
                    "ask node with 'options' must not also have 'next' (it is ignored at runtime)",
                )
            )

    if not any(isinstance(n, TerminalNode) for n in tree.nodes.values()):
        issues.append(ValidationIssue(None, "tree has no terminal node"))

    if tree.entry in tree.nodes:
        seen: set[str] = set()
        stack = [tree.entry]
        while stack:
            nid = stack.pop()
            if nid in seen or nid not in tree.nodes:
                continue
            seen.add(nid)
            stack.extend(_refs(tree.nodes[nid]))
        for nid in tree.nodes:
            if nid not in seen:
                issues.append(ValidationIssue(nid, f"node '{nid}' is unreachable from entry"))

    return issues
