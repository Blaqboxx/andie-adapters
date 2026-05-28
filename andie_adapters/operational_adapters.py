from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from shutil import which
import subprocess
from typing import Any, Callable

from .audit_ledger import ExecutionAuditLedger, ExecutionAuditRecord
from .runtime_contract import (
    AdapterRuntimeInterface,
    BlastRadiusEstimate,
    CapabilityContract,
    ExecutionEnvelope,
    ExecutionObservation,
    RollbackPlan,
    TelemetryReading,
    TelemetryRequirement,
    validate_execution_envelope,
)


CommandBuilder = Callable[[ExecutionEnvelope], tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class CapabilityExecutionSpec:
    contract: CapabilityContract
    command_builder: CommandBuilder
    rollback_builder: CommandBuilder | None
    blast_radius: BlastRadiusEstimate


@dataclass(frozen=True, slots=True)
class StabilizationResult:
    execution_id: str
    stable: bool
    missing_signals: tuple[str, ...]
    failed_signals: tuple[str, ...]
    telemetry: tuple[TelemetryReading, ...]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OperationalCommandAdapter(AdapterRuntimeInterface):
    """Command-backed governed adapter runtime.

    This adapter is intentionally conservative:
    - it executes only known capability ids
    - it requires valid governed envelopes
    - it records command-level telemetry for stabilization checks
    """

    def __init__(self, adapter_id: str, specs: tuple[CapabilityExecutionSpec, ...]):
        self.adapter_id = adapter_id
        self._specs = {item.contract.capability_id: item for item in specs}
        self._execution_history: dict[str, dict[str, Any]] = {}

    def describe_capabilities(self) -> tuple[CapabilityContract, ...]:
        return tuple(item.contract for item in self._specs.values())

    def estimate_blast_radius(self, capability_id: str) -> BlastRadiusEstimate:
        return self._require_spec(capability_id).blast_radius

    def dry_run(self, envelope: ExecutionEnvelope) -> ExecutionObservation:
        spec = self._require_spec(envelope.capability_id)
        self._validate(envelope)
        command = spec.command_builder(envelope)
        notes = (
            "dry-run only; no command executed",
            f"planned_command={' '.join(command)}",
        )
        telemetry = (
            TelemetryReading(
                signal="dry_run_planned",
                value=True,
                source=self.adapter_id,
                observed_at=_utc_now(),
                verification_status="passed",
            ),
        )
        return self._observation(envelope, verification_status="pending", telemetry=telemetry, notes=notes)

    def execute(self, envelope: ExecutionEnvelope) -> ExecutionObservation:
        spec = self._require_spec(envelope.capability_id)
        self._validate(envelope)
        command = spec.command_builder(envelope)
        result = self._run_command(command)
        telemetry = self._command_telemetry(result)
        status = "passed" if result["returncode"] == 0 else "failed"
        notes = (
            f"command={' '.join(command)}",
            f"returncode={result['returncode']}",
        )
        self._execution_history[envelope.execution_id] = {
            "capability_id": envelope.capability_id,
            "command": command,
            "rollback_executed": False,
            "result": result,
            "telemetry": telemetry,
        }
        return self._observation(envelope, verification_status=status, telemetry=telemetry, notes=notes)

    def rollback(self, envelope: ExecutionEnvelope) -> ExecutionObservation:
        spec = self._require_spec(envelope.capability_id)
        self._validate(envelope)
        rollback_builder = spec.rollback_builder
        if rollback_builder is None:
            notes = ("rollback not configured for capability",)
            telemetry = (
                TelemetryReading(
                    signal="rollback_available",
                    value=False,
                    source=self.adapter_id,
                    observed_at=_utc_now(),
                    verification_status="failed",
                ),
            )
            return self._observation(envelope, verification_status="failed", telemetry=telemetry, notes=notes)

        command = rollback_builder(envelope)
        result = self._run_command(command)
        telemetry = self._command_telemetry(result, prefix="rollback")
        status = "passed" if result["returncode"] == 0 else "failed"
        notes = (
            f"rollback_command={' '.join(command)}",
            f"rollback_returncode={result['returncode']}",
        )
        history = self._execution_history.get(envelope.execution_id)
        if history is not None:
            history["rollback_executed"] = True
            history["rollback_result"] = result
            history["telemetry"] = history["telemetry"] + telemetry
        else:
            self._execution_history[envelope.execution_id] = {
                "capability_id": envelope.capability_id,
                "command": command,
                "rollback_executed": True,
                "rollback_result": result,
                "telemetry": telemetry,
            }

        return self._observation(envelope, verification_status=status, telemetry=telemetry, notes=notes)

    def collect_telemetry(self, execution_id: str) -> tuple[TelemetryReading, ...]:
        history = self._execution_history.get(execution_id)
        if history is None:
            return (
                TelemetryReading(
                    signal="execution_history_present",
                    value=False,
                    source=self.adapter_id,
                    observed_at=_utc_now(),
                    verification_status="failed",
                ),
            )
        return tuple(history.get("telemetry", ()))

    def verify_stabilization(
        self,
        execution_id: str,
        requirements: tuple[TelemetryRequirement, ...],
    ) -> StabilizationResult:
        telemetry = self.collect_telemetry(execution_id)
        missing: list[str] = []
        failed: list[str] = []

        for requirement in requirements:
            if not requirement.required_for_verify:
                continue
            matches = [
                item
                for item in telemetry
                if item.signal == requirement.signal and item.source == requirement.source
            ]
            if not matches:
                missing.append(requirement.signal)
                continue
            if not any(item.verification_status == "passed" for item in matches):
                failed.append(requirement.signal)

        return StabilizationResult(
            execution_id=execution_id,
            stable=not missing and not failed,
            missing_signals=tuple(missing),
            failed_signals=tuple(failed),
            telemetry=telemetry,
        )

    def _validate(self, envelope: ExecutionEnvelope) -> None:
        errors = validate_execution_envelope(envelope)
        if errors:
            raise ValueError("; ".join(errors))
        if envelope.adapter_id != self.adapter_id:
            raise ValueError(f"envelope.adapter_id={envelope.adapter_id} does not match adapter={self.adapter_id}")

    def _require_spec(self, capability_id: str) -> CapabilityExecutionSpec:
        spec = self._specs.get(capability_id)
        if spec is None:
            raise KeyError(f"unsupported capability_id: {capability_id}")
        return spec

    def _run_command(self, command: tuple[str, ...]) -> dict[str, Any]:
        binary = command[0] if command else ""
        if not binary:
            return {"returncode": 127, "stdout": "", "stderr": "empty command"}
        if which(binary) is None:
            return {
                "returncode": 127,
                "stdout": "",
                "stderr": f"command not found: {binary}",
            }

        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        return {
            "returncode": int(completed.returncode),
            "stdout": completed.stdout or "",
            "stderr": completed.stderr or "",
        }

    def _command_telemetry(self, result: dict[str, Any], prefix: str = "command") -> tuple[TelemetryReading, ...]:
        code = int(result.get("returncode", 127))
        stdout = str(result.get("stdout", ""))
        stderr = str(result.get("stderr", ""))

        stderr_status = "passed" if not stderr else "failed"
        if code != 0:
            stderr_status = "failed"

        return (
            TelemetryReading(
                signal=f"{prefix}.return_code",
                value=code,
                source=self.adapter_id,
                observed_at=_utc_now(),
                verification_status="passed" if code == 0 else "failed",
            ),
            TelemetryReading(
                signal=f"{prefix}.stdout_present",
                value=bool(stdout.strip()),
                source=self.adapter_id,
                observed_at=_utc_now(),
                verification_status="passed" if stdout.strip() else "pending",
            ),
            TelemetryReading(
                signal=f"{prefix}.stderr_present",
                value=bool(stderr.strip()),
                source=self.adapter_id,
                observed_at=_utc_now(),
                verification_status=stderr_status,
            ),
        )

    def _observation(
        self,
        envelope: ExecutionEnvelope,
        *,
        verification_status: str,
        telemetry: tuple[TelemetryReading, ...],
        notes: tuple[str, ...],
    ) -> ExecutionObservation:
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
            telemetry=telemetry,
            notes=notes,
        )


def execute_with_audit(
    adapter: OperationalCommandAdapter,
    envelope: ExecutionEnvelope,
    ledger: ExecutionAuditLedger,
    operator: str,
    governance_decision: dict[str, Any] | None = None,
) -> ExecutionAuditRecord:
    observation = adapter.execute(envelope)
    rollback_triggered = observation.verification_status == "failed" and envelope.rollback_plan is not None and envelope.rollback_plan.feasible
    rollback_outcome = "not_triggered"

    if rollback_triggered:
        rollback_observation = adapter.rollback(envelope)
        rollback_outcome = "rollback_completed" if rollback_observation.verification_status == "passed" else "rollback_failed"
        observation = ExecutionObservation(
            execution_id=observation.execution_id,
            adapter_id=observation.adapter_id,
            capability_id=observation.capability_id,
            dry_run=observation.dry_run,
            action=observation.action,
            policy_profile=observation.policy_profile,
            policy_snapshot=observation.policy_snapshot,
            telemetry_requirements=observation.telemetry_requirements,
            rollback_plan=observation.rollback_plan,
            blast_radius=observation.blast_radius,
            verification_status=observation.verification_status,
            telemetry=observation.telemetry + rollback_observation.telemetry,
            notes=observation.notes + rollback_observation.notes,
        )

    return ledger.append_from_execution(
        envelope=envelope,
        observation=observation,
        operator=operator,
        governance_decision=governance_decision,
        rollback_triggered=rollback_triggered,
        rollback_outcome=rollback_outcome,
    )


def _docker_target(envelope: ExecutionEnvelope) -> str:
    target = str(envelope.metadata.get("target", "")).strip()
    if not target:
        raise ValueError("docker capability requires metadata.target")
    return target


def _systemd_unit(envelope: ExecutionEnvelope) -> str:
    unit = str(envelope.metadata.get("unit", "")).strip()
    if not unit:
        raise ValueError("systemd capability requires metadata.unit")
    return unit


def _ssh_target(envelope: ExecutionEnvelope) -> str:
    target = str(envelope.metadata.get("target", "")).strip()
    if not target:
        raise ValueError("ssh capability requires metadata.target")
    return target


def _ssh_remote_command(envelope: ExecutionEnvelope) -> str:
    remote_command = str(envelope.metadata.get("remote_command", "")).strip()
    if not remote_command:
        raise ValueError("ssh capability requires metadata.remote_command")
    return remote_command


def _redis_signal(envelope: ExecutionEnvelope) -> str:
    host = str(envelope.metadata.get("host", "")).strip()
    port = str(envelope.metadata.get("port", "")).strip()
    if host and port:
        return f"{host}:{port}"
    if host:
        return host
    return "default"


def make_docker_adapter() -> OperationalCommandAdapter:
    specs = (
        CapabilityExecutionSpec(
            contract=CapabilityContract(
                capability_id="docker.container.inspect",
                operation="inspect",
                scope="container",
                risk_tier="low",
                dangerous=False,
                production_default="supervised",
                requires_approval=False,
                audit_classification="read",
            ),
            command_builder=lambda envelope: ("docker", "inspect", _docker_target(envelope)),
            rollback_builder=None,
            blast_radius=BlastRadiusEstimate(scope="container", reversible=True, max_affected_units=1),
        ),
        CapabilityExecutionSpec(
            contract=CapabilityContract(
                capability_id="docker.container.restart",
                operation="restart",
                scope="container",
                risk_tier="medium",
                dangerous=True,
                production_default="supervised",
                requires_approval=True,
                audit_classification="mutation",
            ),
            command_builder=lambda envelope: ("docker", "restart", _docker_target(envelope)),
            rollback_builder=lambda envelope: ("docker", "restart", _docker_target(envelope)),
            blast_radius=BlastRadiusEstimate(scope="container", reversible=True, max_affected_units=3),
        ),
    )
    return OperationalCommandAdapter("docker", specs)


def make_systemd_adapter() -> OperationalCommandAdapter:
    specs = (
        CapabilityExecutionSpec(
            contract=CapabilityContract(
                capability_id="systemd.unit.status",
                operation="status",
                scope="unit",
                risk_tier="low",
                dangerous=False,
                production_default="supervised",
                requires_approval=False,
                audit_classification="read",
            ),
            command_builder=lambda envelope: ("systemctl", "status", _systemd_unit(envelope), "--no-pager"),
            rollback_builder=None,
            blast_radius=BlastRadiusEstimate(scope="service", reversible=True, max_affected_units=1),
        ),
        CapabilityExecutionSpec(
            contract=CapabilityContract(
                capability_id="systemd.unit.restart",
                operation="restart",
                scope="unit",
                risk_tier="high",
                dangerous=True,
                production_default="supervised",
                requires_approval=True,
                audit_classification="mutation",
            ),
            command_builder=lambda envelope: ("systemctl", "restart", _systemd_unit(envelope)),
            rollback_builder=lambda envelope: ("systemctl", "restart", _systemd_unit(envelope)),
            blast_radius=BlastRadiusEstimate(scope="service", reversible=True, max_affected_units=5),
        ),
    )
    return OperationalCommandAdapter("systemd", specs)


def make_ssh_adapter() -> OperationalCommandAdapter:
    specs = (
        CapabilityExecutionSpec(
            contract=CapabilityContract(
                capability_id="ssh.command.readonly",
                operation="exec",
                scope="remote_host",
                risk_tier="medium",
                dangerous=False,
                production_default="supervised",
                requires_approval=True,
                audit_classification="read",
            ),
            command_builder=lambda envelope: (
                "ssh",
                _ssh_target(envelope),
                _ssh_remote_command(envelope),
            ),
            rollback_builder=None,
            blast_radius=BlastRadiusEstimate(scope="remote_host", reversible=False, max_affected_units=1),
        ),
    )
    return OperationalCommandAdapter("ssh", specs)


def make_redis_adapter() -> OperationalCommandAdapter:
    specs = (
        CapabilityExecutionSpec(
            contract=CapabilityContract(
                capability_id="redis.info",
                operation="info",
                scope="redis",
                risk_tier="low",
                dangerous=False,
                production_default="supervised",
                requires_approval=False,
                audit_classification="read",
            ),
            command_builder=lambda envelope: ("redis-cli", "INFO"),
            rollback_builder=None,
            blast_radius=BlastRadiusEstimate(scope="redis", reversible=True, max_affected_units=1),
        ),
        CapabilityExecutionSpec(
            contract=CapabilityContract(
                capability_id="redis.ping",
                operation="ping",
                scope="redis",
                risk_tier="low",
                dangerous=False,
                production_default="allow",
                requires_approval=False,
                audit_classification="read",
            ),
            command_builder=lambda envelope: ("redis-cli", "PING"),
            rollback_builder=None,
            blast_radius=BlastRadiusEstimate(scope="redis", reversible=True, max_affected_units=1),
        ),
    )
    return OperationalCommandAdapter("redis", specs)
