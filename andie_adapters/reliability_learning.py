from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from .audit_ledger import ExecutionAuditRecord


@dataclass(frozen=True, slots=True)
class ReliabilityScore:
    adapter_id: str
    capability_id: str | None
    sample_count: int
    success_rate: float
    rollback_rate: float
    halt_rate: float
    average_confidence: float
    reliability_score: float
    governance_effectiveness_score: float
    recommendation: str
    reasons: tuple[str, ...]


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def _mean(values: list[float]) -> float:
    return sum(values) / float(len(values)) if values else 0.0


def _success(record: ExecutionAuditRecord) -> bool:
    return record.verification_result == "passed" and not record.rollback_triggered and record.lifecycle_state == "completed"


def _governance_effective(record: ExecutionAuditRecord) -> bool:
    return record.governance_decision_hash and record.approval_state in {"approved", "not_required"}


def evaluate_reliability(records: tuple[ExecutionAuditRecord, ...], adapter_id: str, capability_id: str | None = None) -> ReliabilityScore:
    filtered = [
        record
        for record in records
        if record.adapter_id == adapter_id and (capability_id is None or record.capability_id == capability_id)
    ]

    sample_count = len(filtered)
    if sample_count == 0:
        return ReliabilityScore(
            adapter_id=adapter_id,
            capability_id=capability_id,
            sample_count=0,
            success_rate=0.0,
            rollback_rate=0.0,
            halt_rate=0.0,
            average_confidence=0.0,
            reliability_score=0.0,
            governance_effectiveness_score=0.0,
            recommendation="insufficient history",
            reasons=("no matching executions found",),
        )

    successes = sum(1 for record in filtered if _success(record))
    rollbacks = sum(1 for record in filtered if record.rollback_triggered)
    halts = sum(1 for record in filtered if record.lifecycle_state == "halted")
    average_confidence = _mean([record.confidence_score for record in filtered])
    governance_effective_count = sum(1 for record in filtered if _governance_effective(record) and record.verification_result in {"passed", "failed"})

    success_rate = successes / float(sample_count)
    rollback_rate = rollbacks / float(sample_count)
    halt_rate = halts / float(sample_count)
    governance_effectiveness_score = governance_effective_count / float(sample_count)

    reliability_score = _clamp((success_rate * 0.55) + (average_confidence * 0.3) + ((1.0 - rollback_rate) * 0.1) + ((1.0 - halt_rate) * 0.05))

    reasons: list[str] = []
    if success_rate >= 0.8:
        reasons.append("high remediation success rate")
    elif success_rate >= 0.5:
        reasons.append("moderate remediation success rate")
    else:
        reasons.append("low remediation success rate")

    if rollback_rate > 0.25:
        reasons.append("frequent rollback usage")
    if halt_rate > 0.1:
        reasons.append("halt events observed")
    if average_confidence >= 0.8:
        reasons.append("strong confidence trajectory")
    elif average_confidence < 0.55:
        reasons.append("weak confidence trajectory")

    if reliability_score >= 0.8 and governance_effectiveness_score >= 0.8:
        recommendation = "safe to consider broader supervised use"
    elif reliability_score >= 0.6:
        recommendation = "retain supervised use and continue monitoring"
    elif reliability_score >= 0.4:
        recommendation = "tighten approval and telemetry requirements"
    else:
        recommendation = "restrict capability scope and require human approval"

    return ReliabilityScore(
        adapter_id=adapter_id,
        capability_id=capability_id,
        sample_count=sample_count,
        success_rate=success_rate,
        rollback_rate=rollback_rate,
        halt_rate=halt_rate,
        average_confidence=average_confidence,
        reliability_score=reliability_score,
        governance_effectiveness_score=governance_effectiveness_score,
        recommendation=recommendation,
        reasons=tuple(reasons),
    )


def reliability_score_to_dict(score: ReliabilityScore) -> dict[str, Any]:
    return asdict(score)
