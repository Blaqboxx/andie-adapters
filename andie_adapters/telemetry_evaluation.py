from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TelemetryTrend = Literal["improving", "stable", "degrading", "oscillating"]


@dataclass(frozen=True, slots=True)
class AdaptiveTelemetryAssessment:
    trend: TelemetryTrend
    confidence: float
    threshold_adjustment: float
    timeout_extension_seconds: int
    volatility: float


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _average(values: tuple[float, ...]) -> float:
    return sum(values) / float(len(values)) if values else 0.0


def _volatility(scores: tuple[float, ...]) -> float:
    if len(scores) < 2:
        return 0.0
    deltas = [abs(scores[index] - scores[index - 1]) for index in range(1, len(scores))]
    return _average(tuple(deltas))


def _trend_from_scores(scores: tuple[float, ...]) -> TelemetryTrend:
    if len(scores) < 3:
        return "stable"

    deltas = [scores[index] - scores[index - 1] for index in range(1, len(scores))]

    sign_changes = 0
    previous_sign = 0
    for delta in deltas:
        sign = 1 if delta > 0 else (-1 if delta < 0 else 0)
        if sign == 0:
            continue
        if previous_sign != 0 and sign != previous_sign:
            sign_changes += 1
        previous_sign = sign

    if sign_changes >= 2:
        return "oscillating"

    avg_delta = _average(tuple(deltas))
    if avg_delta >= 0.02:
        return "improving"
    if avg_delta <= -0.02:
        return "degrading"
    return "stable"


def assess_telemetry_curve(scores: tuple[float, ...]) -> AdaptiveTelemetryAssessment:
    normalized = tuple(_clamp(score, 0.0, 1.0) for score in scores)
    if not normalized:
        return AdaptiveTelemetryAssessment(
            trend="stable",
            confidence=0.5,
            threshold_adjustment=0.0,
            timeout_extension_seconds=0,
            volatility=0.0,
        )

    trend = _trend_from_scores(normalized)
    volatility = _volatility(normalized)
    latest_score = normalized[-1]

    # Confidence rewards higher score and penalizes volatility.
    confidence = _clamp((latest_score * 0.8) + ((1.0 - volatility) * 0.2), 0.0, 1.0)

    if trend == "improving":
        return AdaptiveTelemetryAssessment(
            trend=trend,
            confidence=confidence,
            threshold_adjustment=-0.05,
            timeout_extension_seconds=0,
            volatility=volatility,
        )

    if trend == "degrading":
        return AdaptiveTelemetryAssessment(
            trend=trend,
            confidence=confidence,
            threshold_adjustment=0.05,
            timeout_extension_seconds=0,
            volatility=volatility,
        )

    if trend == "oscillating":
        return AdaptiveTelemetryAssessment(
            trend=trend,
            confidence=confidence,
            threshold_adjustment=0.1,
            timeout_extension_seconds=120,
            volatility=volatility,
        )

    return AdaptiveTelemetryAssessment(
        trend=trend,
        confidence=confidence,
        threshold_adjustment=0.0,
        timeout_extension_seconds=0,
        volatility=volatility,
    )
