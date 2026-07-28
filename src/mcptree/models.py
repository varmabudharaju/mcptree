"""Tree data model and dict parsing. Stdlib only — no yaml, no fastmcp."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

SPEC_VERSION = "0.1"

SUPPORTED_VERSIONS = frozenset({"0.1", "0.2"})

PREDICATE_OPS = frozenset({"eq", "neq", "gt", "gte", "lt", "lte", "in", "exists"})

COMPOSITE_OPS = frozenset({"all", "any", "not"})

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_]\w*(?:\.\w+)*)\s*\}\}")


class TreeParseError(Exception):
    """The tree dict is structurally invalid."""


@dataclass(frozen=True)
class Predicate:
    op: str
    value: object = None


@dataclass(frozen=True)
class Branch:
    when: Predicate
    then: str


@dataclass(frozen=True)
class ConditionNode:
    id: str
    on: str
    branches: tuple[Branch, ...]
    default: str | None
    type: str = "condition"


@dataclass(frozen=True)
class AskOption:
    id: str
    then: str


@dataclass(frozen=True)
class AskNode:
    id: str
    prompt: str
    options: tuple[AskOption, ...]
    capture: str | None
    next: str | None
    type: str = "ask"


@dataclass(frozen=True)
class JudgmentOption:
    id: str
    then: str


@dataclass(frozen=True)
class JudgmentNode:
    id: str
    prompt: str
    evidence: tuple[str, ...]
    options: tuple[JudgmentOption, ...]
    require_rationale: bool
    capture: str | None = None
    type: str = "judgment"


@dataclass(frozen=True)
class ActionNode:
    id: str
    tool: str
    args: dict[str, object]
    capture: str | None
    schema: dict[str, object] | None
    next: str
    on_error: str | None
    type: str = "action"


@dataclass(frozen=True)
class TerminalNode:
    id: str
    outcome: str
    summary: str
    type: str = "terminal"


Node = ConditionNode | AskNode | JudgmentNode | ActionNode | TerminalNode


@dataclass
class Tree:
    spec_version: str
    id: str
    title: str
    description: str
    entry: str
    max_steps: int
    nodes: dict[str, Node]
    raw: dict[str, object] = field(repr=False)

    def content_hash(self) -> str:
        canon = json.dumps(self.raw, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canon.encode()).hexdigest()


def _req(d: dict[str, object], key: str, ctx: str) -> object:
    if key not in d:
        raise TreeParseError(f"{ctx}: missing required field '{key}'")
    return d[key]


def _str(d: dict[str, object], key: str, ctx: str) -> str:
    v = _req(d, key, ctx)
    if not isinstance(v, str):
        raise TreeParseError(f"{ctx}: field '{key}' must be a string")
    return v


def _opt_str(d: dict[str, object], key: str, ctx: str) -> str | None:
    v = d.get(key)
    if v is not None and not isinstance(v, str):
        raise TreeParseError(f"{ctx}: field '{key}' must be a string")
    return v


def _parse_predicate(d: object, ctx: str, version: str) -> Predicate:
    if not isinstance(d, dict) or len(d) != 1:
        raise TreeParseError(f"{ctx}: 'when' must be a single-key mapping like {{eq: 200}}")
    op, value = next(iter(d.items()))
    if op in COMPOSITE_OPS:
        if version == "0.1":
            raise TreeParseError(f"{ctx}: predicate '{op}' requires mcptree 0.2")
        if op == "not":
            return Predicate(op="not", value=_parse_predicate(value, f"{ctx}.not", version))
        if not isinstance(value, list) or not value:
            raise TreeParseError(f"{ctx}: '{op}' must be a non-empty list of predicates")
        return Predicate(
            op=str(op),
            value=tuple(
                _parse_predicate(p, f"{ctx}.{op}[{i}]", version) for i, p in enumerate(value)
            ),
        )
    if op not in PREDICATE_OPS:
        raise TreeParseError(f"{ctx}: unknown predicate op '{op}'")
    return Predicate(op=str(op), value=value)


def _parse_options(raw: object, ctx: str) -> list[tuple[str, str]]:
    if not isinstance(raw, list) or not raw:
        raise TreeParseError(f"{ctx}: 'options' must be a non-empty list")
    out: list[tuple[str, str]] = []
    for i, o in enumerate(raw):
        if not isinstance(o, dict):
            raise TreeParseError(f"{ctx}: options[{i}] must be a mapping")
        out.append((_str(o, "id", f"{ctx}.options[{i}]"), _str(o, "then", f"{ctx}.options[{i}]")))
    return out


def _parse_node(node_id: str, d: object, version: str) -> Node:
    ctx = f"node '{node_id}'"
    if not isinstance(d, dict):
        raise TreeParseError(f"{ctx}: must be a mapping")
    ntype = _str(d, "type", ctx)
    if ntype == "condition":
        raw_branches = _req(d, "branches", ctx)
        if not isinstance(raw_branches, list) or not raw_branches:
            raise TreeParseError(f"{ctx}: 'branches' must be a non-empty list")
        branches = tuple(
            Branch(
                when=_parse_predicate(
                    _req(b, "when", f"{ctx}.branches[{i}]"), f"{ctx}.branches[{i}]", version
                ),
                then=_str(b, "then", f"{ctx}.branches[{i}]"),
            )
            for i, b in enumerate(raw_branches)
            if isinstance(b, dict) or _fail(f"{ctx}.branches[{i}] must be a mapping")
        )
        return ConditionNode(
            id=node_id, on=_str(d, "on", ctx), branches=branches, default=_opt_str(d, "default", ctx)
        )
    if ntype == "ask":
        options: tuple[AskOption, ...] = ()
        if "options" in d:
            options = tuple(AskOption(id=i, then=t) for i, t in _parse_options(d["options"], ctx))
        return AskNode(
            id=node_id,
            prompt=_str(d, "prompt", ctx),
            options=options,
            capture=_opt_str(d, "capture", ctx),
            next=_opt_str(d, "next", ctx),
        )
    if ntype == "judgment":
        raw_ev = d.get("evidence", [])
        if not isinstance(raw_ev, list) or not all(isinstance(e, str) for e in raw_ev):
            raise TreeParseError(f"{ctx}: 'evidence' must be a list of fact names")
        opts = tuple(JudgmentOption(id=i, then=t) for i, t in _parse_options(_req(d, "options", ctx), ctx))
        rr = d.get("require_rationale", False)
        if not isinstance(rr, bool):
            raise TreeParseError(f"{ctx}: 'require_rationale' must be a boolean")
        capture = _opt_str(d, "capture", ctx)
        if capture is not None and version == "0.1":
            raise TreeParseError(f"{ctx}: 'capture' on judgment requires mcptree 0.2")
        return JudgmentNode(
            id=node_id, prompt=_str(d, "prompt", ctx), evidence=tuple(raw_ev), options=opts,
            require_rationale=rr, capture=capture,
        )
    if ntype == "action":
        result = d.get("result", {})
        if not isinstance(result, dict):
            raise TreeParseError(f"{ctx}: 'result' must be a mapping")
        args = d.get("args", {})
        if not isinstance(args, dict):
            raise TreeParseError(f"{ctx}: 'args' must be a mapping")
        schema = result.get("schema")
        if schema is not None and not isinstance(schema, dict):
            raise TreeParseError(f"{ctx}: 'result.schema' must be a mapping")
        return ActionNode(
            id=node_id,
            tool=_str(d, "tool", ctx),
            args=dict(args),
            capture=_opt_str(result, "capture", f"{ctx}.result"),
            schema=schema,
            next=_str(d, "next", ctx),
            on_error=_opt_str(d, "on_error", ctx),
        )
    if ntype == "terminal":
        return TerminalNode(id=node_id, outcome=_str(d, "outcome", ctx), summary=_str(d, "summary", ctx))
    raise TreeParseError(f"{ctx}: unknown node type '{ntype}'")


def _fail(msg: str) -> bool:
    raise TreeParseError(msg)


def tree_from_dict(d: dict[str, object]) -> Tree:
    version = _str(d, "mcptree", "tree")
    if version not in SUPPORTED_VERSIONS:
        raise TreeParseError(
            f"unsupported spec version '{version}' (this build supports {sorted(SUPPORTED_VERSIONS)})"
        )
    raw_nodes = _req(d, "nodes", "tree")
    if not isinstance(raw_nodes, dict) or not raw_nodes:
        raise TreeParseError("tree: 'nodes' must be a non-empty mapping")
    max_steps = d.get("max_steps", 50)
    if not isinstance(max_steps, int) or isinstance(max_steps, bool) or max_steps < 1:
        raise TreeParseError("tree: 'max_steps' must be a positive integer")
    nodes = {str(nid): _parse_node(str(nid), nd, version) for nid, nd in raw_nodes.items()}
    return Tree(
        spec_version=version,
        id=_str(d, "id", "tree"),
        title=_str(d, "title", "tree"),
        description=str(d.get("description", "")),
        entry=_str(d, "entry", "tree"),
        max_steps=max_steps,
        nodes=nodes,
        raw=d,
    )
