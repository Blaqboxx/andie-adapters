from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from .confidence_evolution import (
    ConfidencePoint,
    build_confidence_timeline,
    validate_confidence_timeline,
)
from .execution_lifecycle import (
    LifecycleEvent,
    build_default_lifecycle_timeline,
    deserialize_lifecycle_timeline,
    serialize_lifecycle_timeline,
    validate_lifecycle_timeline,
)
from .governance_handshake import build_governance_decision_snapshot, hash_governance_decision_snapshot
from .runtime_contract import BlastRadiusEstimate, ExecutionEnvelope, ExecutionObservation, TelemetryReading


@dataclass(frozen=True, slots=True)
class ExecutionAuditRecord:
    execution_id: str
    timestamp: str
    adapter_id: str
    capability_id: str
    policy_profile: str
    policy_snapshot_hash: str
    approval_state: str
    governance_decision_hash: str
    governance_decision: dict[str, Any]
    telemetry_timeline: tuple[TelemetryReading, ...]
    verification_result: str
    lifecycle_state: str
    lifecycle_timeline: tuple[LifecycleEvent, ...]
    rollback_triggered: bool
    rollback_outcome: str
    blast_radius: BlastRadiusEstimate
    operator: str
    confidence_score: float
    confidence_timeline: tuple[ConfidencePoint, ...]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def hash_policy_snapshot(policy_snapshot: dict[str, Any]) -> str:
    canonical = _to_canonical_json(policy_snapshot)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _serialize_telemetry(telemetry: tuple[TelemetryReading, ...]) -> list[dict[str, Any]]:
    return [asdict(item) for item in telemetry]


def _deserialize_telemetry(payload: list[dict[str, Any]]) -> tuple[TelemetryReading, ...]:
    return tuple(TelemetryReading(**item) for item in payload)


def _serialize_blast_radius(blast_radius: BlastRadiusEstimate) -> dict[str, Any]:
    return asdict(blast_radius)


def _deserialize_blast_radius(payload: dict[str, Any]) -> BlastRadiusEstimate:
    return BlastRadiusEstimate(**payload)


def _serialize_confidence_timeline(timeline: tuple[ConfidencePoint, ...]) -> list[dict[str, Any]]:
    return [asdict(point) for point in timeline]


def _deserialize_confidence_timeline(payload: list[dict[str, Any]]) -> tuple[ConfidencePoint, ...]:
    return tuple(ConfidencePoint(**item) for item in payload)


def _validate_record(record: ExecutionAuditRecord) -> list[str]:
    errors: list[str] = []

    if not record.execution_id:
        errors.append("execution_id must be non-empty")
    if not record.adapter_id:
        errors.append("adapter_id must be non-empty")
    if not record.capability_id:
        errors.append("capability_id must be non-empty")
    if not record.policy_profile:
        errors.append("policy_profile must be non-empty")
    if not record.policy_snapshot_hash:
        errors.append("policy_snapshot_hash must be non-empty")
    if len(record.policy_snapshot_hash) != 64:
        errors.append("policy_snapshot_hash must be a sha256 hex digest")
    if not record.approval_state:
        errors.append("approval_state must be non-empty")
    if not record.governance_decision_hash:
        errors.append("governance_decision_hash must be non-empty")
    if len(record.governance_decision_hash) != 64:
        errors.append("governance_decision_hash must be a sha256 hex digest")
    if not isinstance(record.governance_decision, dict):
        errors.append("governance_decision must be an object")
    else:
        actual_hash = hash_governance_decision_snapshot(record.governance_decision)
        if actual_hash != record.governance_decision_hash:
            errors.append("governance_decision_hash does not match governance_decision payload")
    if not record.operator:
        errors.append("operator must be non-empty")
    if not (0.0 <= record.confidence_score <= 1.0):
        errors.append("confidence_score must be in range [0.0, 1.0]")
    if not record.rollback_outcome:
        errors.append("rollback_outcome must be non-empty")
    if not record.timestamp:
        errors.append("timestamp must be non-empty")

    lifecycle_errors = validate_lifecycle_timeline(record.lifecycle_timeline, record.execution_id)
    errors.extend(lifecycle_errors)
    if record.lifecycle_timeline:
        final_state = record.lifecycle_timeline[-1].to_state
        if final_state != record.lifecycle_state:
            errors.append("lifecycle_state must match final lifecycle_timeline to_state")

    confidence_errors = validate_confidence_timeline(record.confidence_timeline, record.execution_id)
    errors.extend(confidence_errors)
    if record.confidence_timeline:
        final_confidence = record.confidence_timeline[-1].score
        if abs(final_confidence - record.confidence_score) > 1e-9:
            errors.append("confidence_score must match final confidence_timeline score")

    return errors


class ExecutionAuditLedger:
    """Append-only execution ledger for governed adapter actions."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def append(self, record: ExecutionAuditRecord) -> None:
        errors = _validate_record(record)
        if errors:
            raise ValueError("; ".join(errors))

        payload = {
            "execution_id": record.execution_id,
            "timestamp": record.timestamp,
            "adapter_id": record.adapter_id,
            "capability_id": record.capability_id,
            "policy_profile": record.policy_profile,
            "policy_snapshot_hash": record.policy_snapshot_hash,
            "approval_state": record.approval_state,
            "governance_decision_hash": record.governance_decision_hash,
            "governance_decision": record.governance_decision,
            "telemetry_timeline": _serialize_telemetry(record.telemetry_timeline),
            "verification_result": record.verification_result,
            "lifecycle_state": record.lifecycle_state,
            "lifecycle_timeline": serialize_lifecycle_timeline(record.lifecycle_timeline),
            "rollback_triggered": record.rollback_triggered,
            "rollback_outcome": record.rollback_outcome,
            "blast_radius": _serialize_blast_radius(record.blast_radius),
            "operator": record.operator,
            "confidence_score": record.confidence_score,
            "confidence_timeline": _serialize_confidence_timeline(record.confidence_timeline),
        }

        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(_to_canonical_json(payload) + "\n")

    def append_from_execution(
        self,
        envelope: ExecutionEnvelope,
        observation: ExecutionObservation,
        operator: str,
        confidence_score: float | None = None,
        governance_decision: dict[str, Any] | None = None,
        lifecycle_timeline: tuple[LifecycleEvent, ...] | None = None,
        rollback_triggered: bool = False,
        rollback_outcome: str = "not_triggered",
        timestamp: str | None = None,
    ) -> ExecutionAuditRecord:
        decision_payload = governance_decision or build_governance_decision_snapshot(
            profile=envelope.policy_profile,
            resolved_policy=envelope.policy_snapshot,
            evaluation=envelope.metadata.get("evaluation") if isinstance(envelope.metadata, dict) else None,
            source="envelope.policy_snapshot",
        )
        final_state, default_timeline = build_default_lifecycle_timeline(
            execution_id=envelope.execution_id,
            approval_state=envelope.approval_state,
            verification_result=observation.verification_status,
            rollback_triggered=rollback_triggered,
            rollback_outcome=rollback_outcome,
        )
        effective_timeline = lifecycle_timeline if lifecycle_timeline is not None else default_timeline
        effective_state = effective_timeline[-1].to_state if effective_timeline else final_state
        telemetry_scores = tuple(
            float(item.value)
            for item in observation.telemetry
            if isinstance(item.value, (int, float)) and 0.0 <= float(item.value) <= 1.0
        )
        confidence_timeline, derived_confidence = build_confidence_timeline(
            execution_id=envelope.execution_id,
            lifecycle_timeline=effective_timeline,
            telemetry_scores=telemetry_scores,
            base_confidence=0.5,
        )
        effective_confidence = derived_confidence if confidence_score is None else confidence_score
        if confidence_timeline:
            last_point = confidence_timeline[-1]
            if abs(last_point.score - effective_confidence) > 1e-9:
                confidence_timeline = confidence_timeline[:-1] + (
                    ConfidencePoint(
                        execution_id=last_point.execution_id,
                        state=last_point.state,
                        score=effective_confidence,
                        timestamp=last_point.timestamp,
                        reason="confidence override applied",
                    ),
                )
        record = ExecutionAuditRecord(
            execution_id=envelope.execution_id,
            timestamp=timestamp or _now_iso(),
            adapter_id=envelope.adapter_id,
            capability_id=envelope.capability_id,
            policy_profile=envelope.policy_profile,
            policy_snapshot_hash=hash_policy_snapshot(envelope.policy_snapshot),
            approval_state=envelope.approval_state,
            governance_decision_hash=hash_governance_decision_snapshot(decision_payload),
            governance_decision=decision_payload,
            telemetry_timeline=observation.telemetry,
            verification_result=observation.verification_status,
            lifecycle_state=effective_state,
            lifecycle_timeline=effective_timeline,
            rollback_triggered=rollback_triggered,
            rollback_outcome=rollback_outcome,
            blast_radius=observation.blast_radius,
            operator=operator,
            confidence_score=effective_confidence,
            confidence_timeline=confidence_timeline,
        )
        self.append(record)
        return record

    @staticmethod
    def record_to_dict(record: ExecutionAuditRecord) -> dict[str, Any]:
        return {
            "execution_id": record.execution_id,
            "timestamp": record.timestamp,
            "adapter_id": record.adapter_id,
            "capability_id": record.capability_id,
            "policy_profile": record.policy_profile,
            "policy_snapshot_hash": record.policy_snapshot_hash,
            "approval_state": record.approval_state,
            "governance_decision_hash": record.governance_decision_hash,
            "governance_decision": record.governance_decision,
            "telemetry_timeline": _serialize_telemetry(record.telemetry_timeline),
            "verification_result": record.verification_result,
            "lifecycle_state": record.lifecycle_state,
            "lifecycle_timeline": serialize_lifecycle_timeline(record.lifecycle_timeline),
            "rollback_triggered": record.rollback_triggered,
            "rollback_outcome": record.rollback_outcome,
            "blast_radius": _serialize_blast_radius(record.blast_radius),
            "operator": record.operator,
            "confidence_score": record.confidence_score,
            "confidence_timeline": _serialize_confidence_timeline(record.confidence_timeline),
        }

    def list_records(
        self,
        execution_id: str | None = None,
        adapter_id: str | None = None,
        lifecycle_state: str | None = None,
    ) -> tuple[ExecutionAuditRecord, ...]:
        output: list[ExecutionAuditRecord] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                record = ExecutionAuditRecord(
                    execution_id=payload["execution_id"],
                    timestamp=payload["timestamp"],
                    adapter_id=payload["adapter_id"],
                    capability_id=payload["capability_id"],
                    policy_profile=payload["policy_profile"],
                    policy_snapshot_hash=payload["policy_snapshot_hash"],
                    approval_state=payload["approval_state"],
                    governance_decision_hash=payload["governance_decision_hash"],
                    governance_decision=payload["governance_decision"],
                    telemetry_timeline=_deserialize_telemetry(payload.get("telemetry_timeline", [])),
                    verification_result=payload["verification_result"],
                    lifecycle_state=payload["lifecycle_state"],
                    lifecycle_timeline=deserialize_lifecycle_timeline(payload.get("lifecycle_timeline", [])),
                    rollback_triggered=bool(payload["rollback_triggered"]),
                    rollback_outcome=payload["rollback_outcome"],
                    blast_radius=_deserialize_blast_radius(payload["blast_radius"]),
                    operator=payload["operator"],
                    confidence_score=float(payload["confidence_score"]),
                    confidence_timeline=_deserialize_confidence_timeline(payload.get("confidence_timeline", [])),
                )

                if execution_id is not None and record.execution_id != execution_id:
                    continue
                if adapter_id is not None and record.adapter_id != adapter_id:
                    continue
                if lifecycle_state is not None and record.lifecycle_state != lifecycle_state:
                    continue

                output.append(record)

        return tuple(output)
