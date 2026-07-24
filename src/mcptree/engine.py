"""Pure traversal engine. Stdlib only — no yaml, no fastmcp."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .models import (
    ActionNode,
    AskNode,
    ConditionNode,
    JudgmentNode,
    Node,
    Predicate,
    TerminalNode,
    Tree,
)

MAX_FACT_BYTES = 65536


class UnknownSessionError(Exception):
    """No session with that id."""


class UnknownTreeError(Exception):
    """No tree with that id."""


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING = _Missing()


@dataclass
class TraceEntry:
    seq: int
    node: str
    input: object
    resolved_branch: str | None
    rationale: str | None
    timestamp: str


@dataclass
class SessionState:
    session_id: str
    tree: Tree
    tree_hash: str
    current_node: str
    step: int
    facts: dict[str, object] = field(default_factory=dict)
    trace: list[TraceEntry] = field(default_factory=list)
    done: bool = False
    outcome: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_path(facts: dict[str, object], path: str) -> object:
    cur: object = facts
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return MISSING
        cur = cur[part]
    return cur


def evaluate(pred: Predicate, value: object) -> bool:
    if pred.op == "exists":
        return value is not MISSING
    if value is MISSING:
        return False
    try:
        if pred.op == "eq":
            return bool(value == pred.value)
        if pred.op == "neq":
            return bool(value != pred.value)
        if pred.op == "gt":
            return bool(value > pred.value)  # type: ignore[operator]
        if pred.op == "gte":
            return bool(value >= pred.value)  # type: ignore[operator]
        if pred.op == "lt":
            return bool(value < pred.value)  # type: ignore[operator]
        if pred.op == "lte":
            return bool(value <= pred.value)  # type: ignore[operator]
        if pred.op == "in":
            return bool(value in pred.value)  # type: ignore[operator]
    except TypeError:
        return False
    return False


def _append(state: SessionState, node: str, input_: object, branch: str | None,
            rationale: str | None = None) -> None:
    state.trace.append(
        TraceEntry(
            seq=len(state.trace) + 1,
            node=node,
            input=input_,
            resolved_branch=branch,
            rationale=rationale,
            timestamp=_now(),
        )
    )


def _finish(state: SessionState, outcome: str) -> None:
    state.done = True
    state.outcome = outcome


def _advance(state: SessionState) -> None:
    """Walk internally-resolvable nodes until external input is needed or done."""
    while not state.done:
        if len(state.trace) > state.tree.max_steps:
            _finish(state, "aborted:max_steps")
            return
        node = state.tree.nodes[state.current_node]
        if isinstance(node, TerminalNode):
            _append(state, node.id, None, None)
            _finish(state, node.outcome)
            return
        if isinstance(node, ConditionNode):
            value = resolve_path(state.facts, node.on)
            target = node.default
            for b in node.branches:
                if evaluate(b.when, value):
                    target = b.then
                    break
            assert target is not None  # validator guarantees default
            _append(state, node.id, None, target)
            state.current_node = target
            continue
        return  # ask / action / judgment: wait for input


def start(tree: Tree, session_id: str | None = None) -> SessionState:
    state = SessionState(
        session_id=session_id or f"ses_{uuid.uuid4().hex[:12]}",
        tree=tree,
        tree_hash=tree.content_hash(),
        current_node=tree.entry,
        step=1,
    )
    _advance(state)
    return state


def _expects(node: Node) -> tuple[str, str, dict[str, object]]:
    """Return (envelope_state, instruction, expects) for a waiting node."""
    if isinstance(node, AskNode):
        if node.options:
            expects: dict[str, object] = {"kind": "enum", "options": [o.id for o in node.options]}
        else:
            expects = {"kind": "text"}
        return "awaiting_answer", node.prompt, expects
    if isinstance(node, JudgmentNode):
        return (
            "awaiting_judgment",
            node.prompt,
            {
                "kind": "enum",
                "options": [o.id for o in node.options],
                "require_rationale": node.require_rationale,
            },
        )
    if isinstance(node, ActionNode):
        instruction = (
            f"Call tool `{node.tool}` with the args in `expects.args`, "
            "then report its result via tree_answer (set is_error=true if the call failed)."
        )
        return (
            "awaiting_tool_result",
            instruction,
            {"kind": "tool_result", "tool": node.tool, "args": node.args, "schema": node.schema},
        )
    raise AssertionError(f"node {node.id} does not wait for input")


def envelope(state: SessionState, error: str | None = None) -> dict[str, object]:
    base: dict[str, object] = {
        "session_id": state.session_id,
        "tree_id": state.tree.id,
        "node": state.current_node,
        "step": state.step,
        "error": error,
        "outcome": state.outcome,
        "evidence": {},
    }
    if state.done:
        node = state.tree.nodes[state.current_node]
        summary = node.summary if isinstance(node, TerminalNode) else ""
        base |= {"state": "done", "instruction": summary, "expects": None}
        return base
    node = state.tree.nodes[state.current_node]
    env_state, instruction, expects = _expects(node)
    base |= {"state": env_state, "instruction": instruction, "expects": expects}
    if isinstance(node, JudgmentNode):
        base["evidence"] = {name: _present(state.facts.get(name)) for name in node.evidence}
    return base


def _present(value: object) -> object:
    return value if value is not None else None
