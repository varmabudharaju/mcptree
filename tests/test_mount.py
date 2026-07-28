import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client, FastMCP

from mcptree import DecisionTrees

TREE_YAML = """
mcptree: "0.1"
id: mini
title: Mini triage
description: tiny test tree
entry: probe
nodes:
  probe:
    type: action
    tool: http_get
    args: { url: "http://x/health" }
    result: { capture: health }
    next: route
  route:
    type: condition
    on: health.status
    branches:
      - when: { eq: 200 }
        then: ok
    default: bad
  ok: { type: terminal, outcome: resolved, summary: fine }
  bad: { type: terminal, outcome: escalate, summary: page }
"""


def payload(result: Any) -> Any:
    data = getattr(result, "data", None)
    if data is not None:
        return data
    return json.loads(result.content[0].text)


@pytest.fixture()
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    trees = tmp_path / "trees"
    trees.mkdir()
    (trees / "mini.yaml").write_text(TREE_YAML)
    return trees, tmp_path / "sessions"


def make_server(dirs: tuple[Path, Path]) -> FastMCP:
    mcp = FastMCP("test-server")
    DecisionTrees(mcp, dirs[0], sessions_dir=dirs[1])
    return mcp


async def test_full_walk_through_tools(dirs: tuple[Path, Path]) -> None:
    mcp = make_server(dirs)
    async with Client(mcp) as client:
        listing = payload(await client.call_tool("tree_list", {}))
        assert listing == [{"id": "mini", "title": "Mini triage", "description": "tiny test tree"}]

        env = payload(await client.call_tool("tree_start", {"tree_id": "mini"}))
        assert env["state"] == "awaiting_tool_result" and env["node"] == "probe"
        sid = env["session_id"]

        env = payload(
            await client.call_tool(
                "tree_answer", {"session_id": sid, "step": 1, "value": {"status": 503}}
            )
        )
        assert env["state"] == "done" and env["outcome"] == "escalate"

        trace = payload(await client.call_tool("tree_trace", {"session_id": sid}))
        assert trace["outcome"] == "escalate"
        assert [t["node"] for t in trace["trace"]] == ["probe", "route", "bad"]


async def test_status_resumes_after_restart(dirs: tuple[Path, Path]) -> None:
    mcp1 = make_server(dirs)
    async with Client(mcp1) as client:
        env = payload(await client.call_tool("tree_start", {"tree_id": "mini"}))
        sid = env["session_id"]

    # Brand-new server instance, same dirs — simulates a restart.
    mcp2 = make_server(dirs)
    async with Client(mcp2) as client:
        env = payload(await client.call_tool("tree_status", {"session_id": sid}))
        assert env["state"] == "awaiting_tool_result" and env["node"] == "probe"
        env = payload(
            await client.call_tool(
                "tree_answer", {"session_id": sid, "step": 1, "value": {"status": 200}}
            )
        )
        assert env["outcome"] == "resolved"


async def test_unknown_tree_and_session_are_hard_errors(dirs: tuple[Path, Path]) -> None:
    mcp = make_server(dirs)
    async with Client(mcp) as client:
        with pytest.raises(Exception, match="mini"):  # hint lists known tree ids
            await client.call_tool("tree_start", {"tree_id": "ghost"})
        with pytest.raises(Exception, match="ses_nope"):
            await client.call_tool("tree_status", {"session_id": "ses_nope"})


def test_invalid_tree_dir_fails_fast(tmp_path: Path) -> None:
    trees = tmp_path / "trees"
    trees.mkdir()
    (trees / "bad.yaml").write_text(TREE_YAML.replace("default: bad", "default: ghost"))
    mcp = FastMCP("x")
    with pytest.raises(Exception, match="ghost"):
        DecisionTrees(mcp, trees, sessions_dir=tmp_path / "s")


async def test_concurrent_answers_advance_exactly_once(dirs: tuple[Path, Path]) -> None:
    """N racing answers for the same step: exactly one wins, the rest get step_mismatch."""
    mcp = make_server(dirs)
    async with Client(mcp) as client:
        env = payload(await client.call_tool("tree_start", {"tree_id": "mini"}))
        sid = env["session_id"]
        results = await asyncio.gather(
            *(
                client.call_tool(
                    "tree_answer",
                    {"session_id": sid, "step": 1, "value": {"status": 200}},
                )
                for _ in range(10)
            )
        )
        envs = [payload(r) for r in results]
        winners = [e for e in envs if e["error"] is None]
        # the winner drives this tree to done, so racers are rejected either as
        # step_mismatch (lost the race mid-flight) or finished_session (lost after done)
        losers = [
            e for e in envs
            if e["error"] and e["error"].split(":")[0] in ("step_mismatch", "finished_session")
        ]
        assert len(winners) == 1 and len(losers) == 9
        status = payload(await client.call_tool("tree_status", {"session_id": sid}))
        assert status["state"] == "done" and status["outcome"] == "resolved"


ASK_TREE_YAML = """
mcptree: "0.1"
id: askme
title: Ask tree
entry: gate
nodes:
  gate:
    type: ask
    prompt: Proceed?
    options:
      - { id: "go",   then: confirm }
      - { id: "stop", then: halted }
  confirm:
    type: ask
    prompt: Name the change ticket.
    capture: ticket
    next: done_ok
  done_ok: { type: terminal, outcome: proceeded, summary: fine }
  halted:  { type: terminal, outcome: halted, summary: stopped }
"""


@pytest.fixture()
def ask_dirs(tmp_path: Path) -> tuple[Path, Path]:
    trees = tmp_path / "trees"
    trees.mkdir()
    (trees / "askme.yaml").write_text(ASK_TREE_YAML)
    return trees, tmp_path / "sessions"


async def test_elicitation_answers_chained_asks(ask_dirs: tuple[Path, Path]) -> None:
    """Client supports elicitation: both asks are answered by the 'human' in one tree_start."""
    answers = iter(["go", "TICKET-42"])

    async def handler(message: str, response_type: Any, params: Any, context: Any) -> Any:
        return next(answers)

    mcp = FastMCP("t")
    DecisionTrees(mcp, ask_dirs[0], sessions_dir=ask_dirs[1])
    async with Client(mcp, elicitation_handler=handler) as client:
        env = payload(await client.call_tool("tree_start", {"tree_id": "askme"}))
        assert env["state"] == "done" and env["outcome"] == "proceeded"
        trace = payload(await client.call_tool("tree_trace", {"session_id": env["session_id"]}))
        sources = {t["node"]: t["source"] for t in trace["trace"]}
        assert sources["gate"] == "elicitation" and sources["confirm"] == "elicitation"


async def test_elicitation_decline_falls_back_to_envelope(ask_dirs: tuple[Path, Path]) -> None:
    from fastmcp.client.elicitation import ElicitResult

    async def handler(message: str, response_type: Any, params: Any, context: Any) -> Any:
        return ElicitResult(action="decline")

    mcp = FastMCP("t")
    DecisionTrees(mcp, ask_dirs[0], sessions_dir=ask_dirs[1])
    async with Client(mcp, elicitation_handler=handler) as client:
        env = payload(await client.call_tool("tree_start", {"tree_id": "askme"}))
        assert env["state"] == "awaiting_answer" and env["node"] == "gate"
        env = payload(await client.call_tool(
            "tree_answer", {"session_id": env["session_id"], "step": 1, "value": "stop"}))
        assert env["outcome"] == "halted"


async def test_no_elicitation_capability_falls_back(ask_dirs: tuple[Path, Path]) -> None:
    mcp = FastMCP("t")
    DecisionTrees(mcp, ask_dirs[0], sessions_dir=ask_dirs[1])
    async with Client(mcp) as client:  # no handler -> server's elicit fails -> fallback
        env = payload(await client.call_tool("tree_start", {"tree_id": "askme"}))
        assert env["state"] == "awaiting_answer" and env["node"] == "gate"


async def test_elicit_flag_off_never_elicits(ask_dirs: tuple[Path, Path]) -> None:
    async def handler(message: str, response_type: Any, params: Any, context: Any) -> Any:
        raise AssertionError("elicit called despite elicit=False")

    mcp = FastMCP("t")
    DecisionTrees(mcp, ask_dirs[0], sessions_dir=ask_dirs[1], elicit=False)
    async with Client(mcp, elicitation_handler=handler) as client:
        env = payload(await client.call_tool("tree_start", {"tree_id": "askme"}))
        assert env["state"] == "awaiting_answer"
