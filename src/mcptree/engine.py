"""Pure traversal engine. Stdlib only — no yaml, no fastmcp."""

from __future__ import annotations

import json as _json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

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
    return datetime.now(UTC).isoformat()


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
        base["evidence"] = {name: state.facts.get(name) for name in node.evidence}
    return base


def check_schema(value: object, schema: dict[str, object]) -> str | None:
    types: dict[str, type | tuple[type, ...]] = {
        "object": dict, "array": list, "string": str,
        "integer": int, "number": (int, float), "boolean": bool,
    }
    t = schema.get("type")
    if isinstance(t, str) and t in types:
        if t in ("integer", "number") and isinstance(value, bool):
            return f"expected {t}, got boolean"
        if not isinstance(value, types[t]):
            return f"expected {t}, got {type(value).__name__}"
    if t == "object" and isinstance(value, dict):
        required = schema.get("required", [])
        if isinstance(required, list):
            for key in required:
                if key not in value:
                    return f"missing required key '{key}'"
    return None


def submit(
    state: SessionState,
    step: int,
    value: object,
    rationale: str | None = None,
    is_error: bool = False,
) -> dict[str, object]:
    if state.done:
        return envelope(state, error="finished_session: this session has ended")
    if step != state.step:
        return envelope(
            state, error=f"step_mismatch: expected step {state.step}, got {step}"
        )
    node = state.tree.nodes[state.current_node]

    if isinstance(node, (AskNode, JudgmentNode)):
        options = [o.id for o in node.options]
        if options and value not in options:
            return envelope(state, error=f"invalid_option: expected one of {options}")
        if isinstance(node, JudgmentNode) and node.require_rationale and not rationale:
            return envelope(state, error="rationale_required: include a rationale for this judgment")
        if isinstance(node, AskNode) and not node.options:
            encoded = _json.dumps(value, default=str)
            if len(encoded.encode()) > MAX_FACT_BYTES:
                return envelope(
                    state,
                    error=f"fact_too_large: answer exceeds {MAX_FACT_BYTES} bytes; "
                    "trim or summarize before answering",
                )
            assert node.next is not None
            target = node.next
        else:
            target = next(o.then for o in node.options if o.id == value)
        if isinstance(node, AskNode) and node.capture:
            state.facts[node.capture] = value
        _append(state, node.id, value, target, rationale)
    elif isinstance(node, ActionNode):
        if is_error:
            if node.on_error is None:
                return envelope(
                    state,
                    error="tool_error_unhandled: tool failed and no on_error branch; retry or abort",
                )
            target = node.on_error
            _append(state, node.id, {"error": value}, target)
        else:
            encoded = _json.dumps(value, default=str)
            if len(encoded.encode()) > MAX_FACT_BYTES:
                return envelope(
                    state,
                    error=f"fact_too_large: result exceeds {MAX_FACT_BYTES} bytes; "
                    "trim or summarize before reporting",
                )
            if node.schema is not None:
                problem = check_schema(value, node.schema)
                if problem is not None:
                    return envelope(state, error=f"schema_mismatch: {problem}")
            if node.capture:
                state.facts[node.capture] = value
            target = node.next
            _append(state, node.id, value, target)
    else:  # pragma: no cover - condition/terminal never await input
        raise TypeError(f"node {node.id} cannot accept submissions")

    state.current_node = target
    state.step += 1
    _advance(state)
    return envelope(state)
