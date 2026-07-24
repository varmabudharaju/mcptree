# mcptree — Design Spec

**Date:** 2026-07-23
**Status:** Approved (design phase)
**Repo:** `~/mcptree` → github.com/varmabudharaju/mcptree (planned)
**PyPI:** `mcptree` (verified available 2026-07-23)

---

## 1. Positioning

**mcptree is an open protocol plus a FastMCP-style framework for declarative decision
logic in MCP.** Today MCP has tools, resources, prompts, and elicitation — but no
primitive that lets a server publish *branching logic* an agent follows. mcptree fills
that gap the way FastMCP filled the server-authoring gap: an open convention
(`SPEC.md`) backed by a framework so ergonomic it becomes the default way to do the
thing.

> Pitch line: *"FastMCP made tools easy. mcptree makes decisions declarative."*

The division of labor: **the agent does the acting, the tree does the deciding.**
Servers publish decision trees (runbooks, triage flows, checklists). The engine walks
them deterministically; the agent only supplies facts — tool results, user answers,
and (only where a node explicitly declares it) a semantic judgment. Every step leaves
an auditable trace.

**mcptree is fully standalone.** It has no relationship to, dependency on, or
integration with any other project. Session persistence is its own (pluggable backend,
JSON-on-disk default).

### Deliverables (v0.1)

1. **`SPEC.md`** — the language-agnostic wire protocol: tool surface, step envelope,
   node types, session semantics. The artifact other implementations conform to.
2. **The framework** — Python reference implementation with FastMCP-grade DX:
   - One-line mount onto any existing FastMCP server.
   - Zero-code CLI: `mcptree serve trees/`.
3. **Flagship demo** — an incident/debugging runbook exercising every node type.

### Non-goals for v0.1 (future leaf types / backends, spec written to accommodate)

- Routing trees (deterministic tool/server selection)
- Policy trees (allow/ask/deny gating of tool calls)
- Alternative persistence backends beyond the on-disk default
- Non-Python implementations (the spec enables them; we don't ship them)

---

## 2. Architecture

```
SPEC.md   (the protocol: tool names, step envelope, node types, session semantics)
   ▲ implements
mcptree   (framework, Python 3.11+, single runtime dep: fastmcp)
 ├─ schema/models  — typed tree format + validation (mypy --strict)
 ├─ engine         — pure Python traversal, zero MCP dependency
 ├─ mount          — DecisionTrees(mcp, "trees/") adds the protocol to any FastMCP server
 └─ CLI            — mcptree serve | validate | viz
```

### Framework surface

```python
# One line to add decision trees to ANY existing FastMCP server
from fastmcp import FastMCP
from mcptree import DecisionTrees

mcp = FastMCP("incident-bot")
DecisionTrees(mcp, "trees/")   # mounts tools, sessions, audit trail
```

```bash
mcptree serve trees/       # instant conforming server from a directory of YAML
mcptree validate trees/    # lint trees in CI (node-level errors)
mcptree viz incident.yaml  # render tree as a mermaid diagram
```

Trees are **data**: YAML files authored by humans, checked into repos.

---

## 3. Tree format

```yaml
mcptree: "0.1"                      # spec version
id: incident-triage
title: Production incident triage
entry: check_health

nodes:
  check_health:
    type: action                    # instruct the agent to call a tool it has
    tool: http_get
    args: { url: "https://api.example.com/health" }
    result: { capture: health }     # reported result stored into session facts
    next: branch_on_status

  branch_on_status:
    type: condition                 # pure data branch — engine-evaluated
    on: health.status               # fact path
    branches:
      - when: { eq: 200 }
        then: all_clear
      - when: { gte: 500 }
        then: inspect_logs
    default: ask_maintenance        # required unless branches are exhaustive

  inspect_logs:
    type: action
    tool: get_logs
    args: { service: api, lines: 200 }
    result: { capture: logs }
    next: classify_error

  classify_error:
    type: judgment                  # the ONLY place the model decides
    prompt: Classify the dominant error in the logs.
    evidence: [logs]                # facts surfaced to the model
    options:
      - { id: oom,     then: remediate_oom }
      - { id: db_conn, then: remediate_db }
      - { id: unknown, then: escalate }
    require_rationale: true         # rationale recorded in the trace

  ask_maintenance:
    type: ask                       # structured question to the human
    # reached on non-200/non-5xx statuses — could be planned downtime
    prompt: Is there a scheduled maintenance window right now?
    options:
      - { id: "yes", then: all_clear }
      - { id: "no",  then: escalate }
    capture: maintenance_window

  all_clear:
    type: terminal
    outcome: resolved
    summary: "Service healthy; no action required."

  remediate_oom:
    type: terminal
    outcome: remediate
    summary: "Restart with increased memory limit; see runbook §4."

  remediate_db:
    type: terminal
    outcome: remediate
    summary: "Recycle DB connection pool; verify with health check."

  escalate:
    type: terminal
    outcome: escalate
    summary: "Page on-call; attach the tree trace."
```

### Rules

- **Facts.** Each session accumulates a `facts` dict. `action` results and
  `ask`/`judgment` answers write into it; `condition` nodes read fact paths.
- **Five node types, one decision boundary.**
  - `condition` — engine-evaluated declarative predicates on fact paths
    (`eq / neq / gt / gte / lt / lte / in / exists`), branches evaluated in order,
    first match wins, `default` required unless exhaustive.
  - `ask` — structured question to the human (enum options or typed free answer),
    answer captured into facts.
  - `judgment` — model classifies among *declared* options only; `evidence` lists the
    facts shown; `require_rationale` records the model's reasoning in the trace.
  - `action` — instructs the agent to call a tool *it* has (MCP servers cannot call
    other servers' tools); the reported result is validated (optional JSON schema)
    and captured into facts.
  - `terminal` — outcome id + human-readable summary.
- **Validation at load** (`mcptree validate` and server startup): every `then`
  resolves to an existing node, all nodes reachable from `entry`, conditions
  exhaustive (or `default` present), result schemas well-formed.
- **Cycles allowed, runaways not.** Runbooks legitimately loop ("retry 3×"); the
  format permits cycles, and the engine enforces per-tree `max_steps` (default 50).

---

## 4. Runtime protocol (the heart of SPEC.md)

### Tool surface (every conforming server)

| Tool | Purpose |
|---|---|
| `tree_list()` | Catalog of published trees (id, title, description) |
| `tree_start(tree_id)` | Open a session; return the first step envelope |
| `tree_answer(session_id, step, value, rationale?)` | Submit what the envelope asked for; get the next envelope |
| `tree_status(session_id)` | Re-fetch the current envelope (idempotent) |
| `tree_trace(session_id)` | Full audit trail |

### Step envelope

One self-describing JSON shape returned by every call:

```json
{
  "session_id": "ses_9f2",
  "tree_id": "incident-triage",
  "node": "classify_error",
  "state": "awaiting_judgment",
  "instruction": "Classify the dominant error in the logs.",
  "expects": { "kind": "enum", "options": ["oom", "db_conn", "unknown"], "require_rationale": true },
  "evidence": { "logs": "…" },
  "step": 4,
  "error": null,
  "outcome": null
}
```

- `state` ∈ `awaiting_answer` (ask) | `awaiting_tool_result` (action) |
  `awaiting_judgment` (judgment) | `done` (terminal or abort).
- For `action` nodes, `expects` carries `{kind: "tool_result", tool, args, schema?}`
  and `instruction` tells the agent to call the tool and report back via `tree_answer`.
- `outcome` is populated only when `state == "done"`.

### Semantics

- **Engine auto-advances** through `condition` and other internally-resolvable nodes —
  deterministic hops never cost a round trip; the agent is consulted only when the
  outside world is needed.
- **Crash-proof by construction.** The envelope is self-describing: an agent that lost
  its entire context (compaction, new session, different client) calls `tree_status`
  and continues correctly. No prompt memory required.
- **Invalid submissions don't error the protocol.** Wrong enum value, schema-failing
  tool result, missing required rationale, answering a finished session → the same
  envelope returns with `error` set and state unchanged, so the model self-corrects.
  Only unknown `session_id`/`tree_id` is a hard tool error (with a recovery hint).
- **Idempotent submissions.** Submissions are serialized per session; `tree_answer`
  carries the envelope's `step`, and a step mismatch returns the current envelope
  instead of double-advancing.
- **The trace is evidence.** Append-only entries
  `{step, node, input, resolved_branch, rationale, timestamp}` plus the tree content
  hash. `tree_trace` is what you attach to an escalation page.

---

## 5. Sessions & persistence

- Sessions persist as JSON on disk (default backend); they survive server restarts.
- Backend is a small pluggable interface (`load / save / list`) so alternatives can be
  added later without touching the engine.
- **Tree pinning:** sessions snapshot the tree at `tree_start` (content hash recorded
  in the trace). Editing a tree file affects new sessions only; running sessions
  finish on the version they started with.
- Per-fact size cap (default 64KB): oversize tool-result reports are rejected
  in-envelope with "trim or summarize before reporting" — explicit rather than silent
  truncation, since facts feed judgment evidence.

---

## 6. Error handling summary

| Situation | Behavior |
|---|---|
| Invalid tree file | Server refuses to start; `mcptree validate` gives node-level errors |
| Invalid submission (enum/schema/rationale/finished session) | In-envelope `error`, state unchanged |
| Unknown session/tree id | Hard tool error with recovery hint |
| Stale/duplicate `tree_answer` | Step mismatch → current envelope returned, no double-advance |
| Agent's tool failed | Optional `on_error: <node>` branch on `action`; otherwise in-envelope error, agent may retry |
| Runaway loop | `max_steps` exceeded → `outcome: "aborted:max_steps"`, trace intact |
| Tree edited mid-session | Pinned snapshot; running sessions unaffected |
| Oversized fact | Rejected in-envelope (64KB default cap) |

---

## 7. Testing

- **Engine unit tests** (pure Python, no MCP): node semantics, predicate evaluation,
  auto-advance, step idempotency, `max_steps`, `on_error`, fact capture/caps.
- **Validator tests**: one per error class (unresolved `then`, unreachable node,
  non-exhaustive condition, bad schema, bad spec version).
- **Golden-trace tests**: YAML tree + scripted answers → exact expected trace JSON.
- **Server integration tests**: in-process FastMCP client walks the incident tree
  end-to-end, including resume-after-restart.
- `mypy --strict`, pytest, GitHub Actions CI.

---

## 8. Demo, docs, packaging

- **Demo:** `examples/incident_response.py` — real walk of the incident tree where
  each step is a separate OS process against the running server (proving crash-proof
  resume), with `capture` screenshots in `docs/screenshots/` as README evidence.
- **Docs:** `SPEC.md` (versioned protocol), README (pitch + two quickstarts:
  one-line mount and `mcptree serve`), `docs/use-case.md` walkthrough, `mcptree viz`
  mermaid output embedded.
- **Packaging:** Python 3.11+, single runtime dependency (`fastmcp`; engine itself
  dependency-free), MIT license, PyPI `mcptree`.

### Repo layout

```
mcptree/
├── SPEC.md
├── README.md
├── pyproject.toml
├── src/mcptree/
│   ├── __init__.py        # exports DecisionTrees
│   ├── models.py          # tree format, typed + validated
│   ├── engine.py          # pure traversal engine
│   ├── mount.py           # DecisionTrees(mcp, dir) — FastMCP integration
│   ├── sessions.py        # persistence backend interface + JSON default
│   └── cli.py             # serve / validate / viz
├── trees/incident.yaml    # flagship example
├── examples/incident_response.py
├── tests/
└── docs/
    ├── use-case.md
    ├── screenshots/
    └── superpowers/specs/2026-07-23-mcptree-design.md   # this file
```

---

## 9. Decision history

- **All four original ideas (workflow trees, routing, policy, durable state) unify
  under one tree format** — v0.1 ships workflow trees; the others are future leaf
  types/backends.
- **Server-walked with model-judgment nodes** over fully-deterministic or
  model-walked: deterministic where possible, model only where declared.
- **FastMCP-style framework + open spec** over embeddable-library or
  single-opinionated-server positioning.
- **Standalone project** — no ties to any other project.
- **Incident/debugging runbook** as the flagship demo (exercises every node type).
- **Name: `mcptree`** (PyPI-available, self-describing).
