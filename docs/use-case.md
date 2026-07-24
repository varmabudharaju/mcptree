# Use case: incident triage, end to end

This is a captioned walkthrough of `trees/incident.yaml` — a production-incident
triage runbook that exercises all five mcptree node types (`action`, `condition`,
`judgment`, `ask`, `terminal`). Every envelope below is unedited output from a real
run against the actual code in this repository (`mcptree.engine.start`/`submit`
loading `trees/incident.yaml` via `mcptree.loader.load_tree_file`) — not hand-written
JSON. `session_id` values and `timestamp`s are whatever that run produced; everything
else is deterministic given the same inputs.

See [`SPEC.md`](../SPEC.md) for the normative definition of every field and error
string used here, and [`trees/incident.yaml`](../trees/incident.yaml) for the full
tree source.

## The scenario

An on-call agent gets paged. Rather than improvising, it starts the
`incident-triage` tree and lets it drive the investigation: check health, decide
whether logs need inspecting, classify the error if so, and land on a remediation
or an escalation — with every hop recorded.

Two paths are walked below:

1. **The OOM path** — health check comes back unhealthy, logs show an
   `OutOfMemoryError`, the tree lands on `remediate_oom`.
2. **The maintenance-window path** — health check comes back with an unexpected
   (but non-5xx) status, and a human confirms it's expected downtime.

## Path 1: `check_health` → `inspect_logs` → `classify_error` → `remediate_oom`

### Step 0 — `tree_start("incident-triage")`

The entry node, `check_health`, is an `action` node. The engine immediately stops
and asks the caller to run the tool named in `expects.tool` with `expects.args`:

```json
{
  "session_id": "ses_23d02c087aca",
  "tree_id": "incident-triage",
  "node": "check_health",
  "step": 1,
  "error": null,
  "outcome": null,
  "evidence": {},
  "state": "awaiting_tool_result",
  "instruction": "Call tool `http_get` with the args in `expects.args`, then report its result via tree_answer (set is_error=true if the call failed).",
  "expects": {
    "kind": "tool_result",
    "tool": "http_get",
    "args": {
      "url": "https://api.example.com/health"
    },
    "schema": null
  }
}
```

The agent has `http_get` as one of its own tools (mcptree servers never call tools
belonging to other servers — the *agent* does the calling, per node `tool`). It
calls it, gets back a 503, and reports that as the value.

### Step 1 — `tree_answer(session_id, step=1, value={"status": 503})`

`check_health.result.capture: health` writes `{"status": 503}` into session facts
as `health`. The engine advances to `branch_on_status`, a `condition` node that
reads the fact path `health.status`. `503` matches the `{ gte: 500 }` branch, so
the engine hops straight to `inspect_logs` — **no round trip for that hop**; the
condition node never appears as its own envelope. `inspect_logs` is itself an
`action` node, so that's where the walk actually stops:

```json
{
  "session_id": "ses_23d02c087aca",
  "tree_id": "incident-triage",
  "node": "inspect_logs",
  "step": 2,
  "error": null,
  "outcome": null,
  "evidence": {},
  "state": "awaiting_tool_result",
  "instruction": "Call tool `get_logs` with the args in `expects.args`, then report its result via tree_answer (set is_error=true if the call failed).",
  "expects": {
    "kind": "tool_result",
    "tool": "get_logs",
    "args": {
      "service": "api",
      "lines": 200
    },
    "schema": null
  }
}
```

### Step 2 — `tree_answer(session_id, step=2, value={"lines": ["java.lang.OutOfMemoryError: heap"]})`

`inspect_logs.result.capture: logs` writes the reported lines into facts as `logs`.
The engine advances to `classify_error`, a `judgment` node — the **only** node type
where the model has open-ended discretion, and even there it's bounded to the three
declared `options`. The envelope surfaces exactly the facts the node lists under
`evidence: [logs]`:

```json
{
  "session_id": "ses_23d02c087aca",
  "tree_id": "incident-triage",
  "node": "classify_error",
  "step": 3,
  "error": null,
  "outcome": null,
  "evidence": {
    "logs": {
      "lines": [
        "java.lang.OutOfMemoryError: heap"
      ]
    }
  },
  "state": "awaiting_judgment",
  "instruction": "Classify the dominant error in the logs.",
  "expects": {
    "kind": "enum",
    "options": [
      "oom",
      "db_conn",
      "unknown"
    ],
    "require_rationale": true
  }
}
```

`require_rationale: true` means the next `tree_answer` MUST include a non-empty
`rationale`, or the call is rejected in-envelope with `rationale_required` (state
unchanged, so the model gets another chance — see "What happens when you get it
wrong" below).

### Step 3 — `tree_answer(session_id, step=3, value="oom", rationale="OutOfMemoryError in captured logs")`

`"oom"` is a valid option, so it resolves to `remediate_oom` — a `terminal` node.
The engine auto-advances into it and finishes the session:

```json
{
  "session_id": "ses_23d02c087aca",
  "tree_id": "incident-triage",
  "node": "remediate_oom",
  "step": 4,
  "error": null,
  "outcome": "remediate",
  "evidence": {},
  "state": "done",
  "instruction": "Restart with increased memory limit; see runbook §4.",
  "expects": null
}
```

`state: "done"`, `outcome: "remediate"`, `expects: null` — the session is over.
`instruction` now carries the terminal node's `summary` text instead of a prompt.

### The trace — `tree_trace(session_id)`

Every node the walk touched — including `branch_on_status`, which never got its
own envelope — appears in the trace, in order, with the pinned tree's content
hash:

```json
{
  "session_id": "ses_23d02c087aca",
  "tree_id": "incident-triage",
  "tree_hash": "fd29bd6787ac927e99016e90ba98b83748025314d336aeaf11ba1908ee4cf866",
  "outcome": "remediate",
  "trace": [
    {
      "seq": 1,
      "node": "check_health",
      "input": {
        "status": 503
      },
      "resolved_branch": "branch_on_status",
      "rationale": null,
      "timestamp": "2026-07-24T02:34:16.460285+00:00"
    },
    {
      "seq": 2,
      "node": "branch_on_status",
      "input": null,
      "resolved_branch": "inspect_logs",
      "rationale": null,
      "timestamp": "2026-07-24T02:34:16.460448+00:00"
    },
    {
      "seq": 3,
      "node": "inspect_logs",
      "input": {
        "lines": [
          "java.lang.OutOfMemoryError: heap"
        ]
      },
      "resolved_branch": "classify_error",
      "rationale": null,
      "timestamp": "2026-07-24T02:34:16.460475+00:00"
    },
    {
      "seq": 4,
      "node": "classify_error",
      "input": "oom",
      "resolved_branch": "remediate_oom",
      "rationale": "OutOfMemoryError in captured logs",
      "timestamp": "2026-07-24T02:34:16.460497+00:00"
    },
    {
      "seq": 5,
      "node": "remediate_oom",
      "input": null,
      "resolved_branch": null,
      "rationale": null,
      "timestamp": "2026-07-24T02:34:16.460499+00:00"
    }
  ]
}
```

This is what gets attached to an escalation page: not a guess at "what the agent
probably did," but the actual sequence of nodes, inputs, and — for the one
judgment call — the model's stated rationale.

## What happens when you get it wrong

mcptree's rule is that invalid submissions don't blow up the protocol — they come
back as the *same* envelope with `error` set, so a model can read the error and
retry. Two examples, both taken at the same `classify_error` node as above (fresh
sessions, so `session_id` and `step` differ from Path 1):

**Answering with an option that wasn't declared** (`value="not_a_real_option"`)
produces `invalid_option`, and the node stays put:

```json
{
  "session_id": "ses_17c9220b4f68",
  "tree_id": "incident-triage",
  "node": "classify_error",
  "step": 3,
  "error": "invalid_option: expected one of ['oom', 'db_conn', 'unknown']",
  "outcome": null,
  "evidence": {
    "logs": {
      "lines": [
        "oom"
      ]
    }
  },
  "state": "awaiting_judgment",
  "instruction": "Classify the dominant error in the logs.",
  "expects": {
    "kind": "enum",
    "options": [
      "oom",
      "db_conn",
      "unknown"
    ],
    "require_rationale": true
  }
}
```

**Answering `"oom"` with no `rationale`** (the node requires one) produces
`rationale_required`, again with the node unchanged:

```json
{
  "session_id": "ses_172e6db4d96f",
  "tree_id": "incident-triage",
  "node": "classify_error",
  "step": 3,
  "error": "rationale_required: include a rationale for this judgment",
  "outcome": null,
  "evidence": {
    "logs": {
      "lines": [
        "oom"
      ]
    }
  },
  "state": "awaiting_judgment",
  "instruction": "Classify the dominant error in the logs.",
  "expects": {
    "kind": "enum",
    "options": [
      "oom",
      "db_conn",
      "unknown"
    ],
    "require_rationale": true
  }
}
```

The full list of in-envelope error prefixes — `finished_session`,
`step_mismatch`, `invalid_option`, `rationale_required`, `fact_too_large`,
`schema_mismatch:`, `tool_error_unhandled` — is normative in
[`SPEC.md` §5.3](../SPEC.md#53-in-envelope-errors). Only an unknown `tree_id` or
`session_id` is a hard tool error; everything else self-corrects in place.

## Path 2: the maintenance-window branch

Not every non-200 health check is an incident. If `branch_on_status` sees a status
that's neither `200` nor `>= 500` (here, `302`), it falls through to its
`default: ask_maintenance` — an `ask` node that puts a structured question to a
human instead of guessing.

### `tree_answer(session_id, step=1, value={"status": 302})`

```json
{
  "session_id": "ses_6ccd5b0ab25b",
  "tree_id": "incident-triage",
  "node": "ask_maintenance",
  "step": 2,
  "error": null,
  "outcome": null,
  "evidence": {},
  "state": "awaiting_answer",
  "instruction": "Is there a scheduled maintenance window right now?",
  "expects": {
    "kind": "enum",
    "options": [
      "yes",
      "no"
    ]
  }
}
```

### `tree_answer(session_id, step=2, value="yes")`

A human confirms it's expected downtime. `ask_maintenance.capture:
maintenance_window` records the answer as a fact, and `"yes"` resolves to the
`all_clear` terminal:

```json
{
  "session_id": "ses_6ccd5b0ab25b",
  "tree_id": "incident-triage",
  "node": "all_clear",
  "step": 3,
  "error": null,
  "outcome": "resolved",
  "evidence": {},
  "state": "done",
  "instruction": "Service healthy; no action required.",
  "expects": null
}
```

Had the human answered `"no"` instead, the same node would have resolved to
`escalate` (`outcome: "escalate"`) rather than `all_clear` — the branch is
declared entirely in the tree, not in agent-side logic.

## Resuming after context loss

Because every field needed to act next lives in the envelope itself, an agent
(or a completely different process) that has lost all memory of this
conversation can recover by calling `tree_status(session_id)` — it returns
exactly the envelope shown for whatever step the session is currently on, with
no dependency on prompt history. This is what makes mcptree sessions
crash-proof by construction: restart the server, switch clients, compact the
context — `tree_status` is always the source of truth.
