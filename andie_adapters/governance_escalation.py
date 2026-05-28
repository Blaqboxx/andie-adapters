from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Literal

from .audit_ledger import ExecutionAuditRecord
from .confidence_evolution import infer_confidence_trend

GovernanceEscalationLevel = Literal["none", "watch", "tighten", "escalate", "halt"]


@dataclass(frozen=True, slots=True)
class GovernanceEscalation:
    execution_id: str
    level: GovernanceEscalationLevel
    reasons: tuple[str, ...]
    recommended_actions: tuple[str, ...]
    governance_changes: dict[str, Any]
    confidence_trend: str
    final_confidence_score: float
    historical_reliability_score: float | None
    rollback_triggered: bool
    rollback_outcome: str


def _build_level(
    execution_id: str,
    level: GovernanceEscalationLevel,
    reasons: list[str],
    recommended_actions: list[str],
    governance_changes: dict[str, Any],
    confidence_trend: str,
    final_confidence_score: float,
    historical_reliability_score: float | None,
    rollback_triggered: bool,
    rollback_outcome: str,
) -> GovernanceEscalation:
    return GovernanceEscalation(
        execution_id=execution_id,
        level=level,
        reasons=tuple(reasons),
        recommended_actions=tuple(recommended_actions),
        governance_changes=governance_changes,
        confidence_trend=confidence_trend,
        final_confidence_score=final_confidence_score,
        historical_reliability_score=historical_reliability_score,
        rollback_triggered=rollback_triggered,
        rollback_outcome=rollback_outcome,
    )


def evaluate_governance_escalation(record: ExecutionAuditRecord, *, historical_reliability_score: float | None = None) -> GovernanceEscalation:
    confidence_trend = infer_confidence_trend(record.confidence_timeline)
    confidence_score = record.confidence_score
    reasons: list[str] = []
    recommended_actions: list[str] = []
    governance_changes: dict[str, Any] = {
        "capability_scope": "unchanged",
        "approval_state": record.approval_state,
        "telemetry_window": "unchanged",
        "autonomy": "unchanged",
    }

    if record.lifecycle_state == "halted" or record.rollback_outcome == "rollback_failed":
        reasons.append("rollback failed or execution halted")
        recommended_actions.extend(
            [
                "require human operator intervention",
                "suspend autonomous remediation for this adapter capability",
                "tighten approval thresholds for similar capabilities",
            ]
        )
        governance_changes.update(
            {
                "capability_scope": "restrict",
                "approval_state": "human_required",
                "telemetry_window": "extend_and_lock",
                "autonomy": "suspended",
            }
        )
        return _build_level(
            record.execution_id,
            "halt",
            reasons,
            recommended_actions,
            governance_changes,
            confidence_trend,
            confidence_score,
            historical_reliability_score,
            record.rollback_triggered,
            record.rollback_outcome,
        )

    if historical_reliability_score is not None and historical_reliability_score < 0.4:
        reasons.append("historical reliability is low")
        recommended_actions.extend(
            [
                "require supervised approval for this adapter capability",
                "increase verification depth before execution",
                "treat historical trust as a gating signal",
            ]
        )
        governance_changes.update(
            {
                "capability_scope": "tighten",
                "approval_state": "supervised_required",
                "telemetry_window": "extend",
                "autonomy": "trust_constrained",
            }
        )
        return _build_level(
            record.execution_id,
            "tighten",
            reasons,
            recommended_actions,
            governance_changes,
            confidence_trend,
            confidence_score,
            historical_reliability_score,
            record.rollback_triggered,
            record.rollback_outcome,
        )

    if confidence_score < 0.3:
        reasons.append("confidence collapsed below critical threshold")
        recommended_actions.extend(
            [
                "escalate to supervisor approval",
                "restrict adapter capability scope",
                "freeze autonomous remediation for this execution class",
            ]
        )
        governance_changes.update(
            {
                "capability_scope": "restrict",
                "approval_state": "supervisor_required",
                "telemetry_window": "extend",
                "autonomy": "restricted",
            }
        )
        return _build_level(
            record.execution_id,
            "escalate",
            reasons,
            recommended_actions,
            governance_changes,
            confidence_trend,
            confidence_score,
            historical_reliability_score,
            record.rollback_triggered,
            record.rollback_outcome,
        )

    if record.rollback_triggered or confidence_trend == "falling" or confidence_score < 0.55:
        if record.rollback_triggered:
            reasons.append("rollback occurred")
        if confidence_trend == "falling":
            reasons.append("confidence trend is falling")
        if confidence_score < 0.55:
            reasons.append("confidence below stable operating threshold")
        recommended_actions.extend(
            [
                "tighten verification thresholds",
                "increase telemetry observation window",
                "prefer supervised remediation for next execution",
            ]
        )
        governance_changes.update(
            {
                "capability_scope": "tighten",
                "approval_state": "supervised_required",
                "telemetry_window": "extend",
                "autonomy": "tightened",
            }
        )
        return _build_level(
            record.execution_id,
            "tighten",
            reasons,
            recommended_actions,
            governance_changes,
            confidence_trend,
            confidence_score,
            historical_reliability_score,
            record.rollback_triggered,
            record.rollback_outcome,
        )

    if confidence_score >= 0.8 and confidence_trend == "rising" and not record.rollback_triggered:
        reasons.append("stable recovery with rising confidence")
        recommended_actions.extend(
            [
                "maintain current policy profile",
                "continue standard telemetry monitoring",
            ]
        )
        governance_changes.update(
            {
                "capability_scope": "maintain",
                "approval_state": "current",
                "telemetry_window": "maintain",
                "autonomy": "watch",
            }
        )
        return _build_level(
            record.execution_id,
            "watch",
            reasons,
            recommended_actions,
            governance_changes,
            confidence_trend,
            confidence_score,
            historical_reliability_score,
            record.rollback_triggered,
            record.rollback_outcome,
        )

    reasons.append("no adaptive governance escalation required")
    recommended_actions.append("retain current governance posture")
    return _build_level(
        record.execution_id,
        "none",
        reasons,
        recommended_actions,
        governance_changes,
        confidence_trend,
        confidence_score,
        historical_reliability_score,
        record.rollback_triggered,
        record.rollback_outcome,
    )


def escalation_to_dict(escalation: GovernanceEscalation) -> dict[str, Any]:
    return asdict(escalation)
