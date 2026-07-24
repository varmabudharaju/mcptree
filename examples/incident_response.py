"""Real two-process walk of the incident tree.

Run:  python3 examples/incident_response.py phase1   # start + report health
      python3 examples/incident_response.py phase2 <session_id>   # resume in a NEW process
Each phase spawns its own `mcptree serve` stdio subprocess; only the sessions
directory on disk is shared — the resume in phase2 is genuine durability.
"""

import asyncio
import json
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

SESSIONS = str(Path(__file__).parent / ".demo_sessions")
TREES = str(Path(__file__).parent.parent / "trees")


def make_client() -> Client:
    return Client(
        StdioTransport("mcptree", ["serve", TREES, "--sessions", SESSIONS])
    )


def payload(result):
    return result.data if getattr(result, "data", None) is not None else json.loads(
        result.content[0].text
    )


async def phase1() -> None:
    async with make_client() as c:
        env = payload(await c.call_tool("tree_start", {"tree_id": "incident-triage"}))
        print("started:", env["session_id"], "->", env["node"], env["state"])
        env = payload(
            await c.call_tool(
                "tree_answer",
                {"session_id": env["session_id"], "step": 1, "value": {"status": 503}},
            )
        )
        print("after health report ->", env["node"], env["state"])
        print("resume later with:", f"python3 examples/incident_response.py phase2 {env['session_id']}")


async def phase2(session_id: str) -> None:
    async with make_client() as c:
        env = payload(await c.call_tool("tree_status", {"session_id": session_id}))
        print("resumed:", env["node"], env["state"])
        env = payload(
            await c.call_tool(
                "tree_answer",
                {"session_id": session_id, "step": env["step"],
                 "value": {"lines": ["OutOfMemoryError: heap"]}},
            )
        )
        env = payload(
            await c.call_tool(
                "tree_answer",
                {"session_id": session_id, "step": env["step"], "value": "oom",
                 "rationale": "OutOfMemoryError in captured logs"},
            )
        )
        print("outcome:", env["outcome"], "-", env["instruction"])
        trace = payload(await c.call_tool("tree_trace", {"session_id": session_id}))
        print("trace:", " -> ".join(t["node"] for t in trace["trace"]))


if __name__ == "__main__":
    if sys.argv[1] == "phase1":
        asyncio.run(phase1())
    else:
        asyncio.run(phase2(sys.argv[2]))
