from __future__ import annotations

from datetime import datetime
from typing import Any

from .audit_ledger import ExecutionAuditRecord
from .confidence_evolution import infer_confidence_trend
from .governance_escalation import escalation_to_dict, evaluate_governance_escalation


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _seconds_between(previous: str, current: str) -> float | None:
    left = _parse_iso(previous)
    right = _parse_iso(current)
    if left is None or right is None:
        return None
    return (right - left).total_seconds()


def _lifecycle_replay(record: ExecutionAuditRecord) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    previous_ts: str | None = None

    for event in record.lifecycle_timeline:
        elapsed = _seconds_between(previous_ts, event.timestamp) if previous_ts else 0.0
        output.append(
            {
                "from_state": event.from_state,
                "to_state": event.to_state,
                "timestamp": event.timestamp,
                "elapsed_since_previous_seconds": elapsed,
                "reason": event.reason,
                "metadata": event.metadata,
            }
        )
        previous_ts = event.timestamp

    return output


def _confidence_replay(record: ExecutionAuditRecord) -> list[dict[str, Any]]:
    return [
        {
            "state": point.state,
            "score": point.score,
            "timestamp": point.timestamp,
            "reason": point.reason,
        }
        for point in record.confidence_timeline
    ]


def _telemetry_replay(record: ExecutionAuditRecord) -> list[dict[str, Any]]:
    return [
        {
            "signal": entry.signal,
            "value": entry.value,
            "source": entry.source,
            "observed_at": entry.observed_at,
            "verification_status": entry.verification_status,
        }
        for entry in record.telemetry_timeline
    ]


def replay_execution_record(record: ExecutionAuditRecord, *, historical_reliability_score: float | None = None) -> dict[str, Any]:
    lifecycle = _lifecycle_replay(record)
    confidence = _confidence_replay(record)
    telemetry = _telemetry_replay(record)
    escalation = evaluate_governance_escalation(record, historical_reliability_score=historical_reliability_score)

    return {
        "execution_id": record.execution_id,
        "adapter_id": record.adapter_id,
        "capability_id": record.capability_id,
        "policy_profile": record.policy_profile,
        "governance_decision_hash": record.governance_decision_hash,
        "policy_snapshot_hash": record.policy_snapshot_hash,
        "verification_result": record.verification_result,
        "rollback_triggered": record.rollback_triggered,
        "rollback_outcome": record.rollback_outcome,
        "final_lifecycle_state": record.lifecycle_state,
        "final_confidence_score": record.confidence_score,
        "confidence_trend": infer_confidence_trend(record.confidence_timeline),
        "historical_reliability_score": historical_reliability_score,
        "governance_escalation": escalation_to_dict(escalation),
        "lifecycle_timeline": lifecycle,
        "confidence_timeline": confidence,
        "telemetry_timeline": telemetry,
        "governance_decision": record.governance_decision,
    }
