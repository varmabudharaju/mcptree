"""One-line FastMCP mount: DecisionTrees(mcp, "trees/")."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import anyio
from fastmcp import FastMCP

from .engine import UnknownTreeError, envelope, start, submit
from .loader import load_trees_dir
from .sessions import JsonSessionStore, SessionStore


class DecisionTrees:
    def __init__(
        self,
        mcp: FastMCP,
        trees_dir: str | Path,
        *,
        sessions_dir: str | Path | None = None,
    ) -> None:
        self.trees = load_trees_dir(Path(trees_dir))  # fail fast on invalid trees
        root = Path(sessions_dir) if sessions_dir else Path.home() / ".mcptree" / "sessions"
        self.store: SessionStore = JsonSessionStore(root)
        self._lock = anyio.Lock()  # serializes submissions in-process (SPEC §5.7)
        self._register(mcp)

    def _register(self, mcp: FastMCP) -> None:
        trees = self.trees
        store = self.store
        lock = self._lock

        @mcp.tool
        async def tree_list() -> list[dict[str, str]]:
            """List published decision trees."""
            return [
                {"id": t.id, "title": t.title, "description": t.description}
                for t in trees.values()
            ]

        @mcp.tool
        async def tree_start(tree_id: str) -> dict[str, object]:
            """Start a session on a decision tree; returns the first step envelope."""
            if tree_id not in trees:
                raise UnknownTreeError(
                    f"unknown tree '{tree_id}'; known trees: {sorted(trees)}"
                )
            state = start(trees[tree_id])
            store.save(state)
            return envelope(state)

        @mcp.tool
        async def tree_answer(
            session_id: str,
            step: int,
            value: object,
            rationale: str | None = None,
            is_error: bool = False,
        ) -> dict[str, object]:
            """Submit what the current envelope asked for; returns the next envelope."""
            async with lock:
                state = store.load(session_id)
                env = submit(state, step, value, rationale=rationale, is_error=is_error)
                store.save(state)
            return env

        @mcp.tool
        async def tree_status(session_id: str) -> dict[str, object]:
            """Re-fetch the current step envelope (idempotent; use to resume)."""
            return envelope(store.load(session_id))

        @mcp.tool
        async def tree_trace(session_id: str) -> dict[str, object]:
            """Full audit trail for a session."""
            state = store.load(session_id)
            return {
                "session_id": state.session_id,
                "tree_id": state.tree.id,
                "tree_hash": state.tree_hash,
                "outcome": state.outcome,
                "trace": [asdict(t) for t in state.trace],
            }
