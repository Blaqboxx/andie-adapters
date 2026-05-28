from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

ActionType = Literal["allow", "supervised", "deny"]
VerificationStatus = Literal["pending", "passed", "failed"]


@dataclass(frozen=True, slots=True)
class CapabilityContract:
    capability_id: str
    operation: str
    scope: str
    risk_tier: str
    dangerous: bool
    production_default: ActionType
    requires_approval: bool
    audit_classification: str


@dataclass(frozen=True, slots=True)
class TelemetryRequirement:
    signal: str
    source: str
    required_for_verify: bool
    stabilization_window_seconds: int


@dataclass(frozen=True, slots=True)
class RollbackPlan:
    feasible: bool
    strategies: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BlastRadiusEstimate:
    scope: str
    reversible: bool
    max_affected_units: int


@dataclass(frozen=True, slots=True)
class TelemetryReading:
    signal: str
    value: Any
    source: str
    observed_at: str
    verification_status: VerificationStatus = "pending"


@dataclass(frozen=True, slots=True)
class ExecutionObservation:
    execution_id: str
    adapter_id: str
    capability_id: str
    dry_run: bool
    action: ActionType
    policy_profile: str
    policy_snapshot: dict[str, Any]
    telemetry_requirements: tuple[TelemetryRequirement, ...]
    rollback_plan: RollbackPlan
    blast_radius: BlastRadiusEstimate
    verification_status: VerificationStatus = "pending"
    telemetry: tuple[TelemetryReading, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ExecutionEnvelope:
    execution_id: str
    adapter_id: str
    capability_id: str
    policy_profile: str
    policy_snapshot: dict[str, Any]
    dry_run: bool = True
    approval_state: str = "pending"
    capability_scope: str = ""
    requested_action: ActionType = "supervised"
    telemetry_requirements: tuple[TelemetryRequirement, ...] = field(default_factory=tuple)
    rollback_plan: RollbackPlan | None = None
    blast_radius: BlastRadiusEstimate | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class AdapterRuntimeInterface(Protocol):
    """Contract for governed adapter implementations.

    Core cognition must never execute infrastructure directly; it submits an
    envelope and the adapter is responsible for dry-run, execute, rollback, and
    telemetry collection under governance constraints.
    """

    def describe_capabilities(self) -> tuple[CapabilityContract, ...]:
        ...

    def estimate_blast_radius(self, capability_id: str) -> BlastRadiusEstimate:
        ...

    def dry_run(self, envelope: ExecutionEnvelope) -> ExecutionObservation:
        ...

    def execute(self, envelope: ExecutionEnvelope) -> ExecutionObservation:
        ...

    def rollback(self, envelope: ExecutionEnvelope) -> ExecutionObservation:
        ...

    def collect_telemetry(self, execution_id: str) -> tuple[TelemetryReading, ...]:
        ...


def _require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def validate_execution_envelope(envelope: ExecutionEnvelope) -> list[str]:
    errors: list[str] = []

    _require(bool(envelope.execution_id), "execution_id must be non-empty", errors)
    _require(bool(envelope.adapter_id), "adapter_id must be non-empty", errors)
    _require(bool(envelope.capability_id), "capability_id must be non-empty", errors)
    _require(bool(envelope.policy_profile), "policy_profile must be non-empty", errors)
    _require(isinstance(envelope.policy_snapshot, dict), "policy_snapshot must be an object", errors)
    _require(envelope.requested_action in ("allow", "supervised", "deny"), "requested_action must be allow/supervised/deny", errors)
    _require(envelope.approval_state in {"pending", "approved", "denied", "not_required"}, "approval_state is invalid", errors)
    _require(bool(envelope.capability_scope), "capability_scope must be non-empty", errors)

    if envelope.rollback_plan is None:
        errors.append("rollback_plan must be provided")
    else:
        _require(isinstance(envelope.rollback_plan.strategies, tuple), "rollback_plan.strategies must be a tuple", errors)
        _require(len(envelope.rollback_plan.strategies) >= 1, "rollback_plan.strategies must be non-empty", errors)
        _require(all(isinstance(strategy, str) and strategy for strategy in envelope.rollback_plan.strategies), "rollback_plan.strategies must contain non-empty strings", errors)
        if envelope.requested_action == "deny":
            _require(envelope.rollback_plan.feasible is True, "denied remediation requests must still declare rollback feasibility", errors)

    if envelope.blast_radius is None:
        errors.append("blast_radius must be provided")
    else:
        _require(bool(envelope.blast_radius.scope), "blast_radius.scope must be non-empty", errors)
        _require(isinstance(envelope.blast_radius.reversible, bool), "blast_radius.reversible must be boolean", errors)
        _require(isinstance(envelope.blast_radius.max_affected_units, int) and envelope.blast_radius.max_affected_units >= 1, "blast_radius.max_affected_units must be integer >= 1", errors)

    _require(isinstance(envelope.telemetry_requirements, tuple), "telemetry_requirements must be a tuple", errors)
    _require(len(envelope.telemetry_requirements) >= 1, "telemetry_requirements must be non-empty", errors)
    if isinstance(envelope.telemetry_requirements, tuple):
        for index, requirement in enumerate(envelope.telemetry_requirements):
            prefix = f"telemetry_requirements[{index}]"
            _require(isinstance(requirement, TelemetryRequirement), f"{prefix} must be TelemetryRequirement", errors)
            if isinstance(requirement, TelemetryRequirement):
                _require(bool(requirement.signal), f"{prefix}.signal must be non-empty", errors)
                _require(bool(requirement.source), f"{prefix}.source must be non-empty", errors)
                _require(isinstance(requirement.required_for_verify, bool), f"{prefix}.required_for_verify must be boolean", errors)
                _require(isinstance(requirement.stabilization_window_seconds, int) and requirement.stabilization_window_seconds >= 1, f"{prefix}.stabilization_window_seconds must be integer >= 1", errors)

    if envelope.metadata:
        _require(isinstance(envelope.metadata, dict), "metadata must be an object", errors)

    return errors
