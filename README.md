# andie-adapters

ANDIE Adapters is the governed execution layer for real infrastructure integrations.

## Scope

- Docker adapter
- Redis adapter
- SSH adapter
- systemd adapter
- Kubernetes adapter (future)
- telemetry hooks
- rollback semantics
- dry-run execution
- adapter manifests
- blast-radius metadata

## Adapter Requirements

Each adapter must provide:

- capability manifest
- blast-radius metadata
- telemetry requirements
- rollback feasibility declaration
- governance compatibility metadata
- audit classification

## Governance Rules

- Execute only approved and scoped capabilities.
- Enforce deny-by-default behavior for dangerous operations.
- Support dry-run and rollback-first semantics.
- Emit structured telemetry and audit logs for every operation.

## Manifest Validation

Adapter manifests live in `manifests/` and must be validated by governance tooling:

```bash
python3 ../andie-governance/tools/validate_adapter_manifest.py manifests/*.adapter.json
```

Validation enforces capability enums, production-safe defaults, telemetry verification requirements, and rollback feasibility declarations.

## Runtime Interface Contract

Adapter implementations should expose a governed runtime interface that accepts an execution envelope carrying policy context, telemetry requirements, blast-radius metadata, and rollback planning.

The canonical contract is defined in [andie_adapters/runtime_contract.py](andie_adapters/runtime_contract.py).

Required adapter responsibilities:

- describe capabilities
- estimate blast radius
- dry-run before execution when requested
- execute only through governed envelopes
- rollback using declared rollback plans
- collect telemetry for verification

Execution envelopes must be validated before any runtime action is taken.

## Operational Adapter Runtime

Command-backed operational adapters are now available in [andie_adapters/operational_adapters.py](andie_adapters/operational_adapters.py) for:

- Docker
- systemd
- SSH
- Redis

Each runtime adapter supports governed:

- `dry_run(...)`
- `execute(...)`
- `rollback(...)`
- `collect_telemetry(...)`
- `verify_stabilization(...)`
- `estimate_blast_radius(...)`

Use `execute_with_audit(...)` to execute and persist results directly to the execution audit ledger.

## Execution Audit Ledger

Execution history is persisted through an append-only ledger in [andie_adapters/audit_ledger.py](andie_adapters/audit_ledger.py).

Each record captures:

- execution id and timestamp
- adapter and capability identifiers
- policy profile and policy snapshot hash
- approval state
- telemetry timeline
- verification outcome
- rollback trigger and rollback outcome
- blast radius metadata
- operator identity
- confidence score

This supports provenance, replayability, governance forensics, and rollback accountability.

## Governance Handshake Snapshot

Execution records persist the resolved governance overlay decision payload and its hash.

Use [andie_adapters/governance_handshake.py](andie_adapters/governance_handshake.py) to build a standardized decision snapshot with:

- profile
- resolved policy
- evaluation payload
- source attribution
- generated timestamp

The ledger stores both `governance_decision` and `governance_decision_hash` to support tamper-evident replay.

## Ledger CLI

Query and export execution history:

```bash
python3 -m andie_adapters.audit_ledger_cli --ledger audit/execution_ledger.jsonl list --json
python3 -m andie_adapters.audit_ledger_cli --ledger audit/execution_ledger.jsonl summary --json
python3 -m andie_adapters.audit_ledger_cli --ledger audit/execution_ledger.jsonl replay --execution-id exec-123 --json
python3 -m andie_adapters.audit_ledger_cli --ledger audit/execution_ledger.jsonl learn --adapter-id docker --json
python3 -m andie_adapters.audit_ledger_cli --ledger audit/execution_ledger.jsonl suggest-overlay-patch --profile prod --json
python3 -m andie_adapters.audit_ledger_cli --ledger audit/execution_ledger.jsonl workspace-snapshot --limit 20 --json
python3 -m andie_adapters.adapter_runner_cli --envelope envelope.json --operator andie --json
```

The replay command reconstructs lifecycle, telemetry, governance, and confidence progression for a single execution id.

The summary command now includes reliability-aware operational intelligence:

- adapter trust rankings
- watchlist adapters with low reliability
- average governance effectiveness
- autonomy readiness buckets (`ready`, `supervised`, `constrained`)
- risk indicators (`rollback_frequency`, `confidence_decay_rate`, `telemetry_volatility_index`)

Summary output also includes proactive governance suggestions derived from trust posture, including:

- require approval when rollback instability is elevated
- extend telemetry windows for volatile adapters
- tighten scope when confidence decay is severe
- reduce autonomy for watchlist adapters
- cautiously relax pacing when trust is high and stable

Governance suggestions are profile-aware (`dev`, `staging`, `prod`) with environment-sensitive thresholds:

- `prod` applies the strictest containment and escalation thresholds
- `staging` applies supervised, observation-heavy thresholds
- `dev` allows more experimentation before tightening recommendations

Use `suggest-overlay-patch` to convert governance guidance into structured overlay patch candidates for a target profile.

Use `adapter_runner_cli` for end-to-end governed execution of a single envelope:

- load and validate envelope
- run dry-run planning
- execute adapter command
- verify stabilization from required telemetry signals
- trigger rollback when execution or stabilization fails
- persist lifecycle/audit record
- emit replay and reliability output

Use `workspace-snapshot` as the data source for an operational command-center UI. The payload includes status bar context, recent runs, replay index, telemetry center, governance center, trust center, and live event stream.

Detailed shell and panel wiring spec is available in [VALHALLA_SHELL_PANEL_SPEC.md](VALHALLA_SHELL_PANEL_SPEC.md).

## Reliability Learning

Historical executions are now converted into reliability and governance effectiveness scores by [andie_adapters/reliability_learning.py](andie_adapters/reliability_learning.py).

The `learn` CLI command derives:

- success rate
- rollback rate
- halt rate
- average confidence
- reliability score
- governance effectiveness score
- operational recommendation

This is the first experiential learning layer on top of replayable operational memory.

## Adaptive Governance Escalation

Replay output now includes a governance escalation decision derived from lifecycle progression, rollback outcome, and confidence trajectory.

Escalation levels:

- `none` - retain current posture
- `watch` - stable recovery, continue monitoring
- `tighten` - tighten verification and telemetry controls
- `escalate` - require supervisor approval and restrict autonomy
- `halt` - require human intervention and suspend autonomous remediation

Filter by lifecycle terminal state:

```bash
python3 -m andie_adapters.audit_ledger_cli --ledger audit/execution_ledger.jsonl list --lifecycle-state completed --json
```

## Execution Lifecycle State Tracking

Execution records include validated lifecycle timelines generated from [andie_adapters/execution_lifecycle.py](andie_adapters/execution_lifecycle.py).

Default progression:

- requested
- approved
- executing
- telemetry_window
- verified or failed
- rollback_triggered and rollback_executing (when rollback is required)
- rollback_completed or rollback_failed
- completed

Transition validation enforces temporal integrity before records are persisted.

## Lifecycle Transition Policy Engine

Profile-aware transition gating is defined in [andie_adapters/transition_policy.py](andie_adapters/transition_policy.py).

Policy controls include:

- telemetry readiness gate for approved -> executing
- stabilization threshold gate for telemetry_window -> verified
- telemetry window timeout semantics
- rollback mandate on failure for conservative profiles
- rollback failure escalation to halted operator-intervention state
- adaptive telemetry trend evaluation (improving, stable, degrading, oscillating)
- dynamic threshold adjustment and telemetry-window extension

Example usage:

```python
from andie_adapters.transition_policy import (
	TransitionGateInput,
	evaluate_transition_gate,
	get_profile_transition_policy,
)

policy = get_profile_transition_policy("prod")
decision = evaluate_transition_gate(
	TransitionGateInput(
		execution_id="exec-1",
		from_state="approved",
		to_state="executing",
		profile="prod",
		telemetry_ready=True,
	),
	policy,
)
```

## Adaptive Telemetry Evaluation

Telemetry trend analysis is implemented in [andie_adapters/telemetry_evaluation.py](andie_adapters/telemetry_evaluation.py).

Adaptive behavior includes:

- improving curves can relax verification thresholds
- degrading curves tighten verification and can block verify transitions
- oscillation detection can extend telemetry windows and prevent premature verification

## Confidence Evolution Engine

Temporal confidence modeling is implemented in [andie_adapters/confidence_evolution.py](andie_adapters/confidence_evolution.py).

Each execution record now persists:

- `confidence_score` as terminal confidence
- `confidence_timeline` as lifecycle-aligned confidence progression

Confidence evolves through state transitions and telemetry trend context, enabling:

- confidence decay during instability
- confidence growth during verified recovery
- confidence collapse on rollback failures
- replayable confidence trajectories for post-incident analysis
