from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .execution_lifecycle import is_valid_transition
from .telemetry_evaluation import assess_telemetry_curve

PolicyProfile = Literal["dev", "staging", "prod"]
DecisionSeverity = Literal["info", "warn", "error"]


@dataclass(frozen=True, slots=True)
class TransitionPolicy:
    profile: PolicyProfile
    require_telemetry_ready_for_execute: bool
    min_stabilization_score_for_verify: float
    max_telemetry_window_seconds: int
    auto_rollback_on_failure: bool
    halt_on_rollback_failure: bool


@dataclass(frozen=True, slots=True)
class TransitionGateInput:
    execution_id: str
    from_state: str
    to_state: str
    profile: PolicyProfile
    telemetry_ready: bool = True
    stabilization_score: float = 1.0
    verification_result: str = "passed"
    rollback_available: bool = True
    elapsed_in_state_seconds: int = 0
    telemetry_scores: tuple[float, ...] = ()


@dataclass(frozen=True, slots=True)
class TransitionDecision:
    allowed: bool
    reason: str
    severity: DecisionSeverity


def get_profile_transition_policy(profile: PolicyProfile) -> TransitionPolicy:
    if profile == "prod":
        return TransitionPolicy(
            profile="prod",
            require_telemetry_ready_for_execute=True,
            min_stabilization_score_for_verify=0.9,
            max_telemetry_window_seconds=300,
            auto_rollback_on_failure=True,
            halt_on_rollback_failure=True,
        )
    if profile == "staging":
        return TransitionPolicy(
            profile="staging",
            require_telemetry_ready_for_execute=True,
            min_stabilization_score_for_verify=0.8,
            max_telemetry_window_seconds=420,
            auto_rollback_on_failure=True,
            halt_on_rollback_failure=True,
        )
    return TransitionPolicy(
        profile="dev",
        require_telemetry_ready_for_execute=False,
        min_stabilization_score_for_verify=0.65,
        max_telemetry_window_seconds=600,
        auto_rollback_on_failure=False,
        halt_on_rollback_failure=False,
    )


def evaluate_transition_gate(gate: TransitionGateInput, policy: TransitionPolicy) -> TransitionDecision:
    if not is_valid_transition(gate.from_state, gate.to_state):
        return TransitionDecision(False, f"invalid lifecycle transition: {gate.from_state} -> {gate.to_state}", "error")

    if gate.profile != policy.profile:
        return TransitionDecision(False, "profile mismatch between gate input and policy", "error")

    if gate.from_state == "approved" and gate.to_state == "executing":
        if policy.require_telemetry_ready_for_execute and not gate.telemetry_ready:
            return TransitionDecision(False, "telemetry readiness required before executing", "error")

    if gate.from_state == "telemetry_window":
        assessment = assess_telemetry_curve(gate.telemetry_scores)
        max_window = policy.max_telemetry_window_seconds + assessment.timeout_extension_seconds
        effective_threshold = min(
            1.0,
            max(0.0, policy.min_stabilization_score_for_verify + assessment.threshold_adjustment),
        )

        if gate.elapsed_in_state_seconds > max_window and gate.to_state != "timed_out":
            return TransitionDecision(False, "telemetry window exceeded max duration; must transition to timed_out", "error")

        if gate.to_state == "verified":
            if gate.verification_result != "passed":
                return TransitionDecision(False, "verification_result must be passed to transition to verified", "error")
            if assessment.trend == "oscillating":
                return TransitionDecision(False, "oscillation detected; continue telemetry_window instead of verifying", "error")
            if assessment.trend == "degrading":
                return TransitionDecision(False, "telemetry trend is degrading; verification blocked", "error")
            if gate.stabilization_score < effective_threshold:
                return TransitionDecision(False, "stabilization score below policy threshold for verified", "error")

    if gate.from_state == "failed" and gate.to_state == "rollback_triggered":
        if policy.auto_rollback_on_failure and not gate.rollback_available:
            return TransitionDecision(False, "rollback is mandatory on failure for this profile but is unavailable", "error")

    if gate.from_state == "rollback_failed" and gate.to_state == "completed":
        if policy.halt_on_rollback_failure:
            return TransitionDecision(False, "rollback_failed must transition to halted for operator intervention", "error")

    if gate.from_state == "rollback_failed" and gate.to_state == "halted":
        if not policy.halt_on_rollback_failure:
            return TransitionDecision(True, "halted accepted although policy does not require it", "warn")

    return TransitionDecision(True, "transition allowed by policy", "info")
