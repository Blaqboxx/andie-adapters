from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .audit_ledger import ExecutionAuditLedger
from .execution_replay import replay_execution_record
from .operational_adapters import (
    OperationalCommandAdapter,
    make_docker_adapter,
    make_redis_adapter,
    make_ssh_adapter,
    make_systemd_adapter,
)
from .runtime_contract import (
    BlastRadiusEstimate,
    ExecutionEnvelope,
    ExecutionObservation,
    RollbackPlan,
    TelemetryReading,
    TelemetryRequirement,
    validate_execution_envelope,
)
from .reliability_learning import evaluate_reliability, reliability_score_to_dict


def _default_ledger_path() -> Path:
    return Path("audit") / "execution_ledger.jsonl"


def _adapter_factory(adapter_id: str) -> OperationalCommandAdapter:
    factories = {
        "docker": make_docker_adapter,
        "systemd": make_systemd_adapter,
        "ssh": make_ssh_adapter,
        "redis": make_redis_adapter,
    }
    if adapter_id not in factories:
        raise ValueError(f"unsupported adapter_id: {adapter_id}")
    return factories[adapter_id]()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a governed execution envelope through operational adapters.")
    parser.add_argument("--envelope", required=True, help="Path to envelope JSON")
    parser.add_argument("--ledger", default=str(_default_ledger_path()), help="Path to audit ledger JSONL")
    parser.add_argument("--operator", required=True, help="Operator identity for audit records")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Emit JSON output")
    return parser


def _parse_telemetry_requirements(payload: list[dict[str, Any]]) -> tuple[TelemetryRequirement, ...]:
    return tuple(TelemetryRequirement(**item) for item in payload)


def _parse_rollback_plan(payload: dict[str, Any]) -> RollbackPlan:
    return RollbackPlan(
        feasible=bool(payload.get("feasible", False)),
        strategies=tuple(payload.get("strategies", ())),
    )


def _parse_blast_radius(payload: dict[str, Any]) -> BlastRadiusEstimate:
    return BlastRadiusEstimate(
        scope=str(payload.get("scope", "unknown")),
        reversible=bool(payload.get("reversible", False)),
        max_affected_units=int(payload.get("max_affected_units", 1)),
    )


def load_envelope(file_path: str | Path) -> ExecutionEnvelope:
    with Path(file_path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    telemetry_requirements = _parse_telemetry_requirements(payload.get("telemetry_requirements", []))
    rollback_plan = _parse_rollback_plan(payload.get("rollback_plan", {}))
    blast_radius = _parse_blast_radius(payload.get("blast_radius", {}))

    return ExecutionEnvelope(
        execution_id=str(payload["execution_id"]),
        adapter_id=str(payload["adapter_id"]),
        capability_id=str(payload["capability_id"]),
        policy_profile=str(payload["policy_profile"]),
        policy_snapshot=dict(payload.get("policy_snapshot", {})),
        dry_run=bool(payload.get("dry_run", False)),
        approval_state=str(payload.get("approval_state", "approved")),
        capability_scope=str(payload.get("capability_scope", "runtime")),
        requested_action=str(payload.get("requested_action", "supervised")),
        telemetry_requirements=telemetry_requirements,
        rollback_plan=rollback_plan,
        blast_radius=blast_radius,
        metadata=dict(payload.get("metadata", {})),
    )


def _build_final_observation(
    envelope: ExecutionEnvelope,
    execute_observation: ExecutionObservation,
    stabilization_telemetry: tuple[TelemetryReading, ...],
    stable: bool,
    rollback_observation: ExecutionObservation | None,
) -> ExecutionObservation:
    combined_telemetry = execute_observation.telemetry + stabilization_telemetry
    combined_notes = list(execute_observation.notes)
    combined_notes.append(f"stabilization_stable={stable}")

    if rollback_observation is not None:
        combined_telemetry = combined_telemetry + rollback_observation.telemetry
        combined_notes.extend(rollback_observation.notes)

    verification_status = execute_observation.verification_status
    if verification_status == "passed" and not stable:
        verification_status = "failed"

    return ExecutionObservation(
        execution_id=envelope.execution_id,
        adapter_id=envelope.adapter_id,
        capability_id=envelope.capability_id,
        dry_run=envelope.dry_run,
        action=envelope.requested_action,
        policy_profile=envelope.policy_profile,
        policy_snapshot=envelope.policy_snapshot,
        telemetry_requirements=envelope.telemetry_requirements,
        rollback_plan=envelope.rollback_plan or RollbackPlan(feasible=False, strategies=("missing",)),
        blast_radius=envelope.blast_radius or BlastRadiusEstimate(scope="unknown", reversible=False, max_affected_units=1),
        verification_status=verification_status,
        telemetry=combined_telemetry,
        notes=tuple(combined_notes),
    )


def run_envelope(
    envelope: ExecutionEnvelope,
    ledger: ExecutionAuditLedger,
    operator: str,
) -> dict[str, Any]:
    envelope_errors = validate_execution_envelope(envelope)
    if envelope_errors:
        raise ValueError("; ".join(envelope_errors))

    adapter = _adapter_factory(envelope.adapter_id)
    blast_radius = adapter.estimate_blast_radius(envelope.capability_id)
    dry_run_observation = adapter.dry_run(envelope)

    execute_observation = adapter.execute(envelope)
    stabilization = adapter.verify_stabilization(envelope.execution_id, envelope.telemetry_requirements)

    rollback_triggered = execute_observation.verification_status == "failed" or not stabilization.stable
    rollback_outcome = "not_triggered"
    rollback_observation: ExecutionObservation | None = None

    if rollback_triggered and envelope.rollback_plan and envelope.rollback_plan.feasible:
        rollback_observation = adapter.rollback(envelope)
        rollback_outcome = "rollback_completed" if rollback_observation.verification_status == "passed" else "rollback_failed"

    stabilization_telemetry = (
        TelemetryReading(
            signal="stabilization.stable",
            value=stabilization.stable,
            source=envelope.adapter_id,
            observed_at=execute_observation.telemetry[-1].observed_at if execute_observation.telemetry else dry_run_observation.telemetry[0].observed_at,
            verification_status="passed" if stabilization.stable else "failed",
        ),
    )

    final_observation = _build_final_observation(
        envelope,
        execute_observation,
        stabilization_telemetry,
        stabilization.stable,
        rollback_observation,
    )

    record = ledger.append_from_execution(
        envelope=envelope,
        observation=final_observation,
        operator=operator,
        rollback_triggered=rollback_triggered and rollback_observation is not None,
        rollback_outcome=rollback_outcome,
    )

    rows = ledger.list_records(adapter_id=envelope.adapter_id)
    reliability = evaluate_reliability(rows, adapter_id=envelope.adapter_id, capability_id=envelope.capability_id)
    replay = replay_execution_record(record, historical_reliability_score=reliability.reliability_score)

    return {
        "execution_id": envelope.execution_id,
        "adapter_id": envelope.adapter_id,
        "capability_id": envelope.capability_id,
        "dry_run": {
            "verification_status": dry_run_observation.verification_status,
            "notes": list(dry_run_observation.notes),
        },
        "blast_radius": {
            "scope": blast_radius.scope,
            "reversible": blast_radius.reversible,
            "max_affected_units": blast_radius.max_affected_units,
        },
        "execute": {
            "verification_status": execute_observation.verification_status,
            "notes": list(execute_observation.notes),
        },
        "stabilization": {
            "stable": stabilization.stable,
            "missing_signals": list(stabilization.missing_signals),
            "failed_signals": list(stabilization.failed_signals),
        },
        "rollback": {
            "triggered": rollback_triggered and rollback_observation is not None,
            "outcome": rollback_outcome,
        },
        "record": ExecutionAuditLedger.record_to_dict(record),
        "replay": replay,
        "reliability": reliability_score_to_dict(reliability),
    }


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    envelope = load_envelope(args.envelope)
    ledger = ExecutionAuditLedger(args.ledger)
    payload = run_envelope(envelope, ledger=ledger, operator=args.operator)

    if args.json_output:
        print(json.dumps(payload, indent=2))
        return 0

    print(f"execution_id={payload['execution_id']} adapter={payload['adapter_id']} capability={payload['capability_id']}")
    print(f"dry_run_status={payload['dry_run']['verification_status']}")
    print(
        "blast_radius="
        f"{payload['blast_radius']['scope']} reversible={payload['blast_radius']['reversible']} "
        f"max_affected_units={payload['blast_radius']['max_affected_units']}"
    )
    print(f"execute_status={payload['execute']['verification_status']}")
    print(f"stabilization_stable={payload['stabilization']['stable']}")
    print(f"rollback_triggered={payload['rollback']['triggered']} rollback_outcome={payload['rollback']['outcome']}")
    print(f"reliability_score={payload['reliability']['reliability_score']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
