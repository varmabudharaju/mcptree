"""Session persistence. Stdlib only."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol, cast

from .engine import SessionState, TraceEntry, UnknownSessionError
from .models import tree_from_dict


def state_to_dict(state: SessionState) -> dict[str, object]:
    return {
        "session_id": state.session_id,
        "tree_raw": state.tree.raw,
        "tree_hash": state.tree_hash,
        "current_node": state.current_node,
        "step": state.step,
        "facts": state.facts,
        "trace": [asdict(t) for t in state.trace],
        "done": state.done,
        "outcome": state.outcome,
    }


def state_from_dict(d: dict[str, object]) -> SessionState:
    tree = tree_from_dict(d["tree_raw"])  # type: ignore[arg-type]
    trace_list = cast(list[Any], d["trace"])
    trace = [TraceEntry(**t) for t in trace_list]
    facts_obj = cast(dict[str, object], d["facts"])
    step_obj = cast(int, d["step"])
    return SessionState(
        session_id=str(d["session_id"]),
        tree=tree,
        tree_hash=str(d["tree_hash"]),
        current_node=str(d["current_node"]),
        step=step_obj,
        facts=facts_obj,
        trace=trace,
        done=bool(d["done"]),
        outcome=d["outcome"] if d["outcome"] is None else str(d["outcome"]),
    )


class SessionStore(Protocol):
    def save(self, state: SessionState) -> None: ...
    def load(self, session_id: str) -> SessionState: ...
    def list_ids(self) -> list[str]: ...


class JsonSessionStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        safe = "".join(c for c in session_id if c.isalnum() or c == "_")
        return self.root / f"{safe}.json"

    def save(self, state: SessionState) -> None:
        self._path(state.session_id).write_text(json.dumps(state_to_dict(state)))

    def load(self, session_id: str) -> SessionState:
        p = self._path(session_id)
        if not p.exists():
            raise UnknownSessionError(f"unknown session '{session_id}'")
        return state_from_dict(json.loads(p.read_text()))

    def list_ids(self) -> list[str]:
        return sorted(p.stem for p in self.root.glob("ses_*.json"))
