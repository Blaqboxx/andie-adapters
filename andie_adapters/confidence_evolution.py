from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .execution_lifecycle import LifecycleEvent
from .telemetry_evaluation import assess_telemetry_curve

ConfidenceTrend = Literal["rising", "stable", "falling"]


@dataclass(frozen=True, slots=True)
class ConfidencePoint:
    execution_id: str
    state: str
    score: float
    timestamp: str
    reason: str


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def _delta_for_state(state: str, telemetry_adjustment: float) -> float:
    base = {
        "approved": 0.05,
        "denied": -0.1,
        "executing": 0.0,
        "telemetry_window": 0.02 + telemetry_adjustment,
        "verified": 0.12,
        "failed": -0.2,
        "rollback_triggered": -0.1,
        "rollback_executing": -0.05,
        "rollback_completed": 0.06,
        "rollback_failed": -0.25,
        "halted": -0.1,
        "timed_out": -0.15,
        "completed": 0.02,
    }
    return base.get(state, 0.0)


def infer_confidence_trend(timeline: tuple[ConfidencePoint, ...]) -> ConfidenceTrend:
    if len(timeline) < 2:
        return "stable"

    deltas = [timeline[index].score - timeline[index - 1].score for index in range(1, len(timeline))]
    avg_delta = sum(deltas) / float(len(deltas))
    if avg_delta > 0.02:
        return "rising"
    if avg_delta < -0.02:
        return "falling"
    return "stable"


def validate_confidence_timeline(timeline: tuple[ConfidencePoint, ...], expected_execution_id: str) -> list[str]:
    errors: list[str] = []
    if not timeline:
        return ["confidence_timeline must be non-empty"]

    for index, point in enumerate(timeline):
        prefix = f"confidence_timeline[{index}]"
        if point.execution_id != expected_execution_id:
            errors.append(f"{prefix}.execution_id must match execution_id")
        if not (0.0 <= point.score <= 1.0):
            errors.append(f"{prefix}.score must be in range [0.0, 1.0]")
        if not point.state:
            errors.append(f"{prefix}.state must be non-empty")
        if not point.timestamp:
            errors.append(f"{prefix}.timestamp must be non-empty")
        if not point.reason:
            errors.append(f"{prefix}.reason must be non-empty")

    return errors


def build_confidence_timeline(
    execution_id: str,
    lifecycle_timeline: tuple[LifecycleEvent, ...],
    telemetry_scores: tuple[float, ...] = (),
    base_confidence: float = 0.5,
) -> tuple[tuple[ConfidencePoint, ...], float]:
    if not lifecycle_timeline:
        return (), _clamp(base_confidence)

    assessment = assess_telemetry_curve(telemetry_scores)
    telemetry_adjustment = assessment.threshold_adjustment * -0.8

    score = _clamp(base_confidence)
    points: list[ConfidencePoint] = []

    for event in lifecycle_timeline:
        delta = _delta_for_state(event.to_state, telemetry_adjustment if event.to_state == "telemetry_window" else 0.0)
        score = _clamp(score + delta)
        reason = f"state={event.to_state} delta={delta:.3f}"
        if event.to_state == "telemetry_window":
            reason = (
                f"state=telemetry_window trend={assessment.trend} volatility={assessment.volatility:.3f} "
                f"delta={delta:.3f}"
            )

        points.append(
            ConfidencePoint(
                execution_id=execution_id,
                state=event.to_state,
                score=score,
                timestamp=event.timestamp,
                reason=reason,
            )
        )

    return tuple(points), score
