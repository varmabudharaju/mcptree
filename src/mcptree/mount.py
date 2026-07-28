"""One-line FastMCP mount: DecisionTrees(mcp, "trees/")."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import cast

import anyio
from fastmcp import Context, FastMCP
from fastmcp.server.elicitation import AcceptedElicitation

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
        elicit: bool = True,
    ) -> None:
        self.trees = load_trees_dir(Path(trees_dir))  # fail fast on invalid trees
        root = Path(sessions_dir) if sessions_dir else Path.home() / ".mcptree" / "sessions"
        self.store: SessionStore = JsonSessionStore(root)
        self.elicit = elicit
        self._lock = anyio.Lock()  # serializes submissions in-process (SPEC §5.7)
        self._register(mcp)

    async def _elicit_loop(
        self, ctx: Context | None, env: dict[str, object]
    ) -> dict[str, object]:
        """While the envelope is an ask and the client can elicit, ask the human directly."""
        while (
            self.elicit
            and ctx is not None
            and env.get("state") == "awaiting_answer"
            and env.get("error") is None
        ):
            expects = cast("dict[str, object]", env["expects"])
            message = cast(str, env["instruction"])
            try:
                # fastmcp's elicit overloads accept list[str] (choice) and type
                # (scalar) at runtime, but its stubs don't narrow them under strict.
                if expects["kind"] == "enum":
                    result = await ctx.elicit(
                        message,
                        response_type=list(cast("list[str]", expects["options"])),  # type: ignore[arg-type]
                    )
                else:
                    result = await ctx.elicit(message, response_type=str)  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001 — any client-side elicit failure means fall back
                return env  # client lacks elicitation; the agent relays instead
            if not isinstance(result, AcceptedElicitation):
                return env  # declined/cancelled: fall back to the agent path
            async with self._lock:
                state = self.store.load(cast(str, env["session_id"]))
                env = submit(
                    state, cast(int, env["step"]), result.data, source="elicitation"
                )
                self.store.save(state)
        return env

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
        async def tree_start(
            tree_id: str, ctx: Context | None = None
        ) -> dict[str, object]:
            """Start a session on a decision tree; returns the first step envelope."""
            if tree_id not in trees:
                raise UnknownTreeError(
                    f"unknown tree '{tree_id}'; known trees: {sorted(trees)}"
                )
            state = start(trees[tree_id])
            store.save(state)
            return await self._elicit_loop(ctx, envelope(state))

        @mcp.tool
        async def tree_answer(
            session_id: str,
            step: int,
            value: object,
            rationale: str | None = None,
            is_error: bool = False,
            ctx: Context | None = None,
        ) -> dict[str, object]:
            """Submit what the current envelope asked for; returns the next envelope."""
            async with lock:
                state = store.load(session_id)
                env = submit(state, step, value, rationale=rationale, is_error=is_error)
                store.save(state)
            return await self._elicit_loop(ctx, env)

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
