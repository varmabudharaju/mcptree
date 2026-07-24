# mcptree — test evidence

_Generated 2026-07-24T02:49:37Z._

<!-- shotlist:start -->
### Mcptree Validate

<img src="docs/screenshots/01-mcptree-validate.png" width="100%" alt="mcptree validate trees/ — lints every tree in the directory (node types, required fields, dangling refs) and fails fast on the first error; incident-triage passes."/>

mcptree validate trees/ — lints every tree in the directory (node types, required fields, dangling refs) and fails fast on the first error; incident-triage passes.

`mcptree validate trees/`

### Mcptree Viz

<img src="docs/screenshots/02-mcptree-viz.png" width="100%" alt="mcptree viz trees/incident.yaml — the incident-triage tree rendered as a Mermaid flowchart: all five node types (action, condition, judgment, ask, terminal), branch labels, and the on_error edge, generated straight from the YAML."/>

mcptree viz trees/incident.yaml — the incident-triage tree rendered as a Mermaid flowchart: all five node types (action, condition, judgment, ask, terminal), branch labels, and the on_error edge, generated straight from the YAML.

`mcptree viz trees/incident.yaml`

### Demo Phase1 Start

<img src="docs/screenshots/03-demo-phase1-start.png" width="100%" alt="Process 1 (phase1) — a fresh OS process: tree_start opens a session on incident-triage, tree_answer reports a 503 health check and the engine auto-advances past the pure-data condition node straight to inspect_logs, then the process exits. The session lives only on disk from here."/>

Process 1 (phase1) — a fresh OS process: tree_start opens a session on incident-triage, tree_answer reports a 503 health check and the engine auto-advances past the pure-data condition node straight to inspect_logs, then the process exits. The session lives only on disk from here.

`rm -rf examples/.demo_sessions; python3 examples/incident_response.py phase1 2>/dev/null | tee /tmp/mcptree_demo_phase1.txt`

### Demo Phase2 Resume

<img src="docs/screenshots/04-demo-phase2-resume.png" width="100%" alt="Process 2 (phase2) — a brand-new OS process, sharing nothing with phase1 but the sessions directory: tree_status resumes the same session_id at inspect_logs, tree_answer reports OOM logs then the judgment classification, landing on outcome: remediate. tree_trace prints the full audit path from check_health to remediate_oom — real crash-proof resume, not a mock."/>

Process 2 (phase2) — a brand-new OS process, sharing nothing with phase1 but the sessions directory: tree_status resumes the same session_id at inspect_logs, tree_answer reports OOM logs then the judgment classification, landing on outcome: remediate. tree_trace prints the full audit path from check_health to remediate_oom — real crash-proof resume, not a mock.

`python3 examples/incident_response.py phase2 $(grep -o 'ses_[a-f0-9]*' /tmp/mcptree_demo_phase1.txt | head -1) 2>/dev/null`

<!-- shotlist:end -->
