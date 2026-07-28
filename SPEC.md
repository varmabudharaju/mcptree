# mcptree Protocol Specification

**Version:** 0.1
**Status:** Draft

This document specifies the mcptree wire protocol: a set of MCP tools and a JSON
"step envelope" that let an MCP server publish declarative decision logic (runbooks,
triage flows, checklists) that an agent walks one step at a time.

mcptree is standalone. It has no dependency on, or relationship to, any other
project. It composes with any MCP server.

## 1. Overview & conformance

The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD",
"SHOULD NOT", "RECOMMENDED", "MAY", and "OPTIONAL" in this document are to be
interpreted as described in RFC 2119.

A **conforming server**:

- MUST expose the five tools defined in [§3](#3-tool-surface) —
  `tree_list`, `tree_start`, `tree_answer`, `tree_status`, `tree_trace` — with the
  parameter names and types specified there.
- MUST validate every tree document it publishes at load time ([§2](#2-tree-document-format))
  and MUST refuse to start if any tree is structurally invalid.
- MUST return the step envelope shape defined in [§4](#4-step-envelope) from
  `tree_start`, `tree_answer`, and `tree_status`.
- MUST implement the session semantics in [§5](#5-session-semantics): step
  idempotency, in-envelope errors for invalid submissions, tree pinning by content
  hash, and the per-fact size cap.
- MUST reject tree documents whose `mcptree` version it does not support
  ([§6](#6-versioning)).

A **conforming client (agent)**:

- MUST treat the envelope as the sole source of truth for what to do next — it MUST
  NOT rely on remembering prior turns, since the envelope is designed to be
  resumable after context loss.
- MUST echo back the `step` value from the envelope it is answering when calling
  `tree_answer`.
- SHOULD call `tree_status` to resume a session after losing context.

Everything below is normative for the wire behavior. Implementation details not
observable on the wire (e.g. internal data structures) are non-normative.

### 1.1 Trust model & determinism boundary

mcptree's determinism claim is precise and bounded: **given the same reported
inputs, the engine always takes the same path.** What it does not — and cannot —
claim:

- **`action` results are agent-reported and unverified.** The server never
  observes the agent's real tool calls; an agent can report a result it never
  obtained. The trace is an honest record of *what was reported through which
  channel*, not proof of what happened in the world.
- **`ask` answers reach the human only when the client supports elicitation**
  (§5.8). When elicitation is unavailable or declined, the agent relays the
  question, and the answer is only as trustworthy as the agent. The trace's
  `source` field (§5.5) records the channel — `"elicitation"` (the client's
  own human-facing prompt, which the model cannot fabricate) versus
  `"tree_answer"` (agent-submitted) — so an auditor can tell the difference.
- **Client conformance is cooperative.** Nothing server-side forces an agent to
  start a tree or to keep answering it. mcptree constrains *how* decisions are
  made once a tree is being walked; it does not compel the walk.

Deployments that need stronger guarantees should treat the trace as testimony,
not evidence: corroborate reported tool results out-of-band (e.g. server-side
logs of the real systems) before acting on high-stakes outcomes.

---

## 2. Tree document format

A tree document is a YAML (or equivalent JSON) mapping. Here is a complete, real
example — the flagship tree shipped in this repository at `trees/incident.yaml`:

```yaml
mcptree: "0.1"
id: incident-triage
title: Production incident triage
entry: check_health

nodes:
  check_health:
    type: action
    tool: http_get
    args: { url: "https://api.example.com/health" }
    result: { capture: health }
    next: branch_on_status

  branch_on_status:
    type: condition
    on: health.status
    branches:
      - when: { eq: 200 }
        then: all_clear
      - when: { gte: 500 }
        then: inspect_logs
    default: ask_maintenance

  inspect_logs:
    type: action
    tool: get_logs
    args: { service: api, lines: 200 }
    result: { capture: logs }
    next: classify_error

  classify_error:
    type: judgment
    prompt: Classify the dominant error in the logs.
    evidence: [logs]
    options:
      - { id: oom,     then: remediate_oom }
      - { id: db_conn, then: remediate_db }
      - { id: unknown, then: escalate }
    require_rationale: true

  ask_maintenance:
    type: ask
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

### 2.1 Top-level fields

| Field | Type | Required | Meaning |
|---|---|---|---|
| `mcptree` | string | yes | Spec version this document targets. See [§6](#6-versioning). |
| `id` | string | yes | Unique tree id, used by `tree_start`. |
| `title` | string | yes | Human-readable title. |
| `description` | string | no | Free text, defaults to `""`; surfaced by `tree_list`. |
| `entry` | string | yes | Id of the first node to enter. |
| `max_steps` | integer | no | Per-session step cap; defaults to `50`. MUST be a positive integer. |
| `nodes` | mapping | yes | Non-empty mapping of node id → node object. |

### 2.2 The YAML-1.1 "Norway problem" and `on:`

YAML 1.1 parses a bare scalar key `on` as the boolean `True` (alongside `yes`,
`no`, `off`, etc.). A conforming loader MUST normalize this so tree authors can
write the natural, unquoted form:

```yaml
branch_on_status:
  type: condition
  on: health.status   # parses as {True: "health.status"} in raw YAML — normalized to "on"
```

Concretely: if a node mapping has a boolean-`True` key and no string `"on"` key,
the loader rewrites the boolean key to the string `"on"` before validation. This
normalization applies only to the node-level `on:` field.

The inverse gotcha remains: **option/branch ids that look like YAML booleans
(`yes`, `no`, `on`, `off`, `true`, `false`) MUST be quoted** by the author (as in
`ask_maintenance` above: `{ id: "yes", then: all_clear }`), or YAML will parse them
as booleans rather than the strings the engine compares option ids against.

### 2.3 Node types

Every node has a `type` field selecting one of five node types. `then`/`next`
references MUST resolve to another node id in the same document.

| Type | Purpose | Who resolves it |
|---|---|---|
| `condition` | Declarative branch on a fact path. | Engine (no round trip). |
| `ask` | Structured question to the human. | Human, via `tree_answer`. |
| `judgment` | Model classifies among **declared** options only. | Model, via `tree_answer`. |
| `action` | Instructs the agent to call a tool *it* has. | Agent, via `tree_answer`. |
| `terminal` | Ends the session with an outcome. | — |

**`condition`**

| Field | Type | Required | Notes |
|---|---|---|---|
| `on` | string | yes | Dotted fact path evaluated against session facts, e.g. `health.status`. |
| `branches` | list | yes, non-empty | Each entry: `{ when: <predicate>, then: <node id> }`. Evaluated in order; first match wins. |
| `default` | string | **yes, always** (v0.1 tightening) | Node id to use when no branch matches. See §2.5. |

**`ask`**

| Field | Type | Required | Notes |
|---|---|---|---|
| `prompt` | string | yes | Shown to the human as `instruction`. |
| `options` | list | no | `[{ id: <string>, then: <node id> }, …]`. If present, the answer MUST be one of these ids (`expects.kind = "enum"`). |
| `capture` | string | no | Fact name the answer is written to. |
| `next` | string | conditionally | REQUIRED if `options` is absent (free-text ask); ignored if `options` is present (routing comes from the matched option's `then`). |

If `options` is omitted, `expects.kind = "text"` and the node MUST have both
`capture` and `next` (free-text answers still need somewhere to write the value
and somewhere to go next).

**`judgment`**

| Field | Type | Required | Notes |
|---|---|---|---|
| `prompt` | string | yes | The classification instruction. |
| `evidence` | list of strings | no, defaults `[]` | Fact names copied into the envelope's `evidence` object. |
| `options` | list | yes, non-empty | `[{ id: <string>, then: <node id> }, …]`. The model's answer MUST be one of these ids — this is the **only** place the model has open-ended discretion, and it is bounded to a declared enum. |
| `require_rationale` | boolean | no, defaults `false` | If true, `tree_answer` MUST include a non-empty `rationale`; the rationale is recorded in the trace. |

**`action`**

| Field | Type | Required | Notes |
|---|---|---|---|
| `tool` | string | yes | Name of a tool the *agent* is expected to already have (mcptree servers do not call other servers' tools). |
| `args` | mapping | no, defaults `{}` | Passed through verbatim in `expects.args`. |
| `result.capture` | string | no | Fact name the reported result is written to. |
| `result.schema` | mapping | no | Validated against the reported result. See §2.6 for the v0.1 subset. |
| `next` | string | yes | Node to advance to on success. |
| `on_error` | string | no | Node to advance to when the agent reports failure (`tree_answer(..., is_error=True)`). If absent, a failure report yields `tool_error_unhandled` in-envelope instead of advancing. |

**`terminal`**

| Field | Type | Required | Notes |
|---|---|---|---|
| `outcome` | string | yes | Machine-readable outcome id, e.g. `resolved`, `remediate`, `escalate`. Becomes the envelope's `outcome`. |
| `summary` | string | yes | Human-readable summary, becomes the envelope's `instruction` when `state == "done"`. |

### 2.4 Predicate operators

`condition.branches[].when` is a single-key mapping `{ <op>: <value> }`. The
supported operators, evaluated against the fact resolved by `on`:

| Op | Meaning | Notes |
|---|---|---|
| `eq` | `value == fact` | |
| `neq` | `value != fact` | |
| `gt` | `fact > value` | |
| `gte` | `fact >= value` | |
| `lt` | `fact < value` | |
| `lte` | `fact <= value` | |
| `in` | `fact in value` | `value` is typically a list. |
| `exists` | fact path resolves to something | The only op evaluated when the fact is missing; all others are `false` on a missing fact. |

Comparisons that raise `TypeError` (e.g. comparing a string to an int with `gt`)
evaluate to `false` rather than raising — a conforming engine MUST NOT crash a
session on a type-mismatched predicate.

### 2.5 v0.1 tightening: `condition.default` is always required

Earlier drafts allowed omitting `default` when branches were provably exhaustive.
**v0.1 requires `default` on every `condition` node, unconditionally.** A tree that
omits it MUST fail validation with `condition node requires a 'default' branch`,
even if the branches look exhaustive to a human reader. This keeps the validator
simple and the runtime total: every condition node always has somewhere to go.

### 2.6 v0.1 tightening: `result.schema` is a validated subset

`action.result.schema`, when present, MUST be a mapping. A conforming engine
validates only this subset of JSON Schema:

- **`type`** (optional): one of `object`, `array`, `string`, `integer`, `number`,
  `boolean`. The reported result MUST be an instance of the corresponding Python
  type. `integer`/`number` explicitly **exclude** booleans (Python's `bool` is a
  subtype of `int`, but `true`/`false` do not satisfy `type: integer` or
  `type: number` here).
- **`required`** (optional, only meaningful when `type: object`): a list of key
  names that MUST be present in the reported object.

Any other JSON Schema keyword (`properties`, `minimum`, `pattern`, `items`, …) is
**not validated** in v0.1 — a conforming engine MUST NOT error on their presence,
but MUST NOT enforce them either. A schema mismatch produces the in-envelope error
`schema_mismatch: <reason>` (see §5.3).

### 2.7 `max_steps` and cycles

Trees MAY contain cycles (e.g. a retry loop). The engine enforces a per-session
step budget: `max_steps` (default `50`). When the number of recorded trace entries
exceeds `max_steps`, the session ends with `outcome: "aborted:max_steps"` and
`state: "done"`; the trace up to that point is preserved and retrievable via
`tree_trace`.

### 2.8 Load-time validation

A conforming loader/validator MUST reject a tree document (server refuses to
start; `mcptree validate` reports node-level errors) when any of the following
hold:

- `entry` does not name a node in `nodes`.
- Any `then`/`next`/`on_error`/`default` reference does not name a node in `nodes`.
- A `condition` node has no `default` (§2.5).
- An `ask` node has neither `options` nor (`capture` and `next`).
- The tree has no `terminal` node at all.
- Some node is unreachable from `entry` (computed by following all outgoing
  references, including `default`/`on_error`, from `entry`).
- `mcptree` names an unsupported spec version (§6).

---

## 3. Tool surface

Every conforming server exposes exactly these five tools (Python reference
signatures shown; other languages MUST preserve parameter names and semantics):

| Tool | Signature | Purpose |
|---|---|---|
| `tree_list` | `tree_list() -> list[{id, title, description}]` | Catalog of published trees. |
| `tree_start` | `tree_start(tree_id: str) -> Envelope` | Open a session; returns the first step envelope. |
| `tree_answer` | `tree_answer(session_id: str, step: int, value: object, rationale: str \| None = None, is_error: bool = False) -> Envelope` | Submit what the current envelope asked for; returns the next envelope. |
| `tree_status` | `tree_status(session_id: str) -> Envelope` | Re-fetch the current envelope. Idempotent; safe to call any number of times. |
| `tree_trace` | `tree_trace(session_id: str) -> Trace` | Full audit trail for a session. |

### 3.1 `tree_answer` parameters

| Parameter | Type | Required | Meaning |
|---|---|---|---|
| `session_id` | string | yes | Session to advance. |
| `step` | integer | yes | The `step` value from the envelope being answered. Used for idempotency (§5.2). |
| `value` | any JSON value | yes | The answer/result: an option id (`ask`/`judgment`), or a tool result (`action`). |
| `rationale` | string \| null | no | Model's reasoning. REQUIRED (non-empty) when the current `judgment` node has `require_rationale: true`; ignored otherwise. |
| `is_error` | boolean | no, defaults `false` | For `action` nodes only: set `true` to report that the tool call failed, taking the node's `on_error` branch if one exists. |

### 3.2 Unknown ids are hard tool errors

`tree_start` with an unknown `tree_id`, or any of `tree_answer`/`tree_status`/
`tree_trace` with an unknown `session_id`, MUST raise a tool-level error (not an
in-envelope one) — these indicate the caller is not even talking about a session
or tree that exists, so there is no envelope to return `error` inside. A
conforming server SHOULD include a recovery hint (e.g. the list of known tree ids)
in the error message.

### 3.3 `tree_trace` shape

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
      "input": { "status": 503 },
      "resolved_branch": "branch_on_status",
      "rationale": null,
      "timestamp": "2026-07-24T02:34:16.460285+00:00"
    }
  ]
}
```

Each trace entry MUST have the shape `{ seq, node, input, resolved_branch,
rationale, timestamp }` (§5.5). `seq` is a monotonically increasing integer across
the whole session and is **distinct from** the envelope's `step` (see §5.2).

---

## 4. Step envelope

Every one of `tree_start`, `tree_answer`, and `tree_status` returns the same JSON
shape:

| Field | Type | Present | Meaning |
|---|---|---|---|
| `session_id` | string | always | `ses_` + 12 hex characters. |
| `tree_id` | string | always | Id of the tree this session runs. |
| `node` | string | always | Current node id. |
| `state` | string | always | One of `awaiting_answer`, `awaiting_tool_result`, `awaiting_judgment`, `done`. |
| `instruction` | string | always | Human/model-readable instruction for the current node (the node's `prompt`/tool-call instruction, or the terminal `summary` when `done`). |
| `expects` | object \| null | always (key present) | Describes what `tree_answer` wants next; `null` when `state == "done"`. See §4.2. |
| `evidence` | object | always | Fact values surfaced for a `judgment` node (keyed by the names in `evidence:`); `{}` for every other state. |
| `step` | integer | always | Number of accepted submissions so far, plus 1. Echo this back to `tree_answer`. |
| `error` | string \| null | always (key present) | Set when the most recent submission was rejected in-envelope; `null` otherwise. See §5.3. |
| `outcome` | string \| null | always (key present) | The terminal node's `outcome`, or `"aborted:max_steps"`; `null` until `state == "done"`. |

### 4.1 `state` values

- `awaiting_answer` — current node is `ask`; call `tree_answer` with an option id
  (or free text) as `value`.
- `awaiting_tool_result` — current node is `action`; call the tool named in
  `expects.tool` with `expects.args`, then report via `tree_answer`.
- `awaiting_judgment` — current node is `judgment`; call `tree_answer` with one of
  `expects.options` as `value` (and `rationale` if required).
- `done` — session ended; `outcome` is set, `expects` is `null`.

### 4.2 `expects` variants

| `expects.kind` | Fields | Node type |
|---|---|---|
| `"enum"` | `options: string[]`, and for judgment nodes `require_rationale: bool` | `ask` (with options), `judgment` |
| `"text"` | *(no extra fields)* | `ask` (no options declared) |
| `"tool_result"` | `tool: string`, `args: object`, `schema: object \| null` | `action` |

### 4.3 A real envelope — from an actual `tree_status`-equivalent call

This is unedited output of `mcptree.engine.envelope()` mid-way through a walk of
`trees/incident.yaml`, at the `judgment` node (`tree_status` returns exactly this
shape):

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

See `docs/use-case.md` for the full step-by-step walk this was taken from,
including the `action` and `done` envelope shapes.

---

## 5. Session semantics

### 5.1 Auto-advance

The engine advances through internally-resolvable nodes — currently `condition`
and `terminal` — without a round trip to the caller. It stops (returns an
envelope) only at `ask`, `judgment`, and `action` nodes, where the outside world
(human, model, or agent's own tools) is genuinely needed. This means a single
`tree_answer` call can jump the session forward through several `condition` hops
before the next envelope is returned.

### 5.2 Step idempotency

`tree_answer` requires `step` to equal the session's current step counter. If it
does not match — a stale or duplicate submission — the call does **not** advance
the session: it returns the current (unchanged) envelope with
`error: "step_mismatch: expected step <n>, got <m>"`. This makes retries and
at-least-once delivery safe: replaying the same `(session_id, step, value)` after
a successful advance is a no-op that returns the *new* current envelope with a
`step_mismatch` error, never a double-advance.

`step` (envelope field, increments once per accepted submission and never
resets) is distinct from `seq` (trace entry field, strictly increasing for the
life of the session) — see §5.5.

### 5.3 In-envelope errors

Invalid submissions do not raise tool errors — they return the *same* envelope
(state unchanged) with `error` set to one of these prefixes, so the caller
(often a model) can self-correct:

| Prefix | Raised when |
|---|---|
| `finished_session` | `tree_answer` called on a session where `state` is already `done`. |
| `step_mismatch` | `step` does not match the session's current step (§5.2). |
| `invalid_option` | `value` is not one of the current node's declared option ids (`ask` with options, or `judgment`). |
| `rationale_required` | Current node is a `judgment` with `require_rationale: true` and `rationale` is missing/empty. |
| `fact_too_large` | The JSON-encoded `value` exceeds the 64KB cap (§5.4). Enforced on **both** free-text `ask` answers and `action` results. |
| `schema_mismatch:` | Current node is `action` with `result.schema`, and the reported result fails the subset check in §2.6. The rest of the string is a human-readable reason. |
| `tool_error_unhandled` | `tree_answer(..., is_error=True)` on an `action` node with no `on_error` branch. |

Only unknown `tree_id`/`session_id` are **hard** tool errors, not in-envelope
ones — see §3.2.

### 5.4 Per-fact size cap

Reported values that get written into session facts — `action` results and
free-text (`options`-less) `ask` answers — are capped at **65536 bytes** of their
`json.dumps` encoding (UTF-8). Oversize values are rejected in-envelope with
`fact_too_large` rather than silently truncated, because facts feed `judgment`
evidence and silent truncation there would be a correctness hazard, not just a
storage one. Enum-based `ask`/`judgment` answers are exempt (they're bounded to a
declared, already-small set of option ids).

### 5.5 The trace

Every node the session passes through — including auto-advanced `condition` and
the final `terminal` — appends one entry:

```
{ seq, node, input, resolved_branch, rationale, timestamp }
```

- `seq` — 1-based, strictly increasing for the life of the session.
- `node` — the node id the entry is for.
- `input` — what was submitted (`null` for auto-advanced `condition`/`terminal`
  nodes, which take no input).
- `resolved_branch` — the node id the walk advanced to (`null` at a `terminal`,
  which advances nowhere).
- `rationale` — the judgment rationale if one was given, else `null`.
- `timestamp` — ISO-8601 UTC.

`tree_trace` returns the full list plus `tree_hash` and the final `outcome`
(§3.3). This is what gets attached to an escalation.

### 5.6 Tree pinning

`tree_start` snapshots the tree document as it exists at that moment: the
session stores the tree's canonical raw form and a content hash — SHA-256 over
`json.dumps(raw, sort_keys=True, separators=(",", ":"))`. All subsequent steps of
that session run against the pinned snapshot, **not** the live file. Editing a
tree file on disk affects only sessions started *after* the edit; a session
already in flight finishes on the version it started with. The pinned hash is
reported in `tree_trace` as `tree_hash`.

### 5.7 Persistence

Sessions are persisted as one JSON file per session, named `<session_id>.json`,
under a session store directory (default `~/.mcptree/sessions`, overridable per
mount/CLI invocation). This makes sessions survive server restarts: an agent that
lost all context can call `tree_status(session_id)` on a freshly started server
process and get back the exact envelope it would have gotten before the restart.
Session ids have the form `ses_` followed by 12 lowercase hex characters.

Session saves are atomic per session file; no cross-session or cross-process
locking is guaranteed.

---

## 6. Versioning

Every tree document MUST declare `mcptree: "0.1"` at the top level. A conforming
loader MUST reject any document whose `mcptree` value it does not recognize —
this document defines behavior for `"0.1"` only. There is no forward- or
backward-compatibility guarantee across major protocol revisions; a `0.x` engine
MUST NOT attempt to load a tree declaring a version it doesn't implement.

This specification itself is versioned independently of the `mcptree` Python
package's own release version (`pyproject.toml`), though both currently track
`0.1`.
