import unittest

from andie_adapters.execution_replay import replay_execution_record
from andie_adapters.audit_ledger import ExecutionAuditLedger
from andie_adapters.runtime_contract import (
    BlastRadiusEstimate,
    ExecutionEnvelope,
    ExecutionObservation,
    RollbackPlan,
    TelemetryReading,
    TelemetryRequirement,
)
from pathlib import Path
import tempfile


def _make_envelope(execution_id: str, adapter_id: str, capability_id: str) -> ExecutionEnvelope:
    return ExecutionEnvelope(
        execution_id=execution_id,
        adapter_id=adapter_id,
        capability_id=capability_id,
        policy_profile="prod",
        policy_snapshot={"profile": "prod", "rule": "deny-dangerous"},
        dry_run=False,
        approval_state="approved",
        capability_scope="restart",
        requested_action="supervised",
        telemetry_requirements=(
            TelemetryRequirement(
                signal="service_health",
                source="monitoring",
                required_for_verify=True,
                stabilization_window_seconds=120,
            ),
        ),
        rollback_plan=RollbackPlan(feasible=True, strategies=("restart_previous_state",)),
        blast_radius=BlastRadiusEstimate(scope="host", reversible=True, max_affected_units=2),
    )


def _make_observation(envelope: ExecutionEnvelope) -> ExecutionObservation:
    return ExecutionObservation(
        execution_id=envelope.execution_id,
        adapter_id=envelope.adapter_id,
        capability_id=envelope.capability_id,
        dry_run=envelope.dry_run,
        action=envelope.requested_action,
        policy_profile=envelope.policy_profile,
        policy_snapshot=envelope.policy_snapshot,
        telemetry_requirements=envelope.telemetry_requirements,
        rollback_plan=envelope.rollback_plan,
        blast_radius=envelope.blast_radius,
        verification_status="passed",
        telemetry=(
            TelemetryReading(
                signal="service_health",
                value="ok",
                source="monitoring",
                observed_at="2026-05-26T00:00:00+00:00",
                verification_status="passed",
            ),
        ),
    )


class ExecutionReplayTests(unittest.TestCase):
    def test_replay_payload_contains_core_timelines(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = ExecutionAuditLedger(Path(temp_dir) / "ledger.jsonl")
            envelope = _make_envelope("exec-replay-1", "docker", "docker.container.restart")
            observation = _make_observation(envelope)
            record = ledger.append_from_execution(
                envelope=envelope,
                observation=observation,
                operator="replay-user",
                confidence_score=0.82,
            )

            payload = replay_execution_record(record)
            self.assertEqual("exec-replay-1", payload["execution_id"])
            self.assertGreaterEqual(len(payload["lifecycle_timeline"]), 1)
            self.assertGreaterEqual(len(payload["confidence_timeline"]), 1)
            self.assertGreaterEqual(len(payload["telemetry_timeline"]), 1)
            self.assertIn("confidence_trend", payload)
            self.assertIn("governance_escalation", payload)

            trusted_payload = replay_execution_record(record, historical_reliability_score=0.2)
            self.assertEqual(0.2, trusted_payload["historical_reliability_score"])
            self.assertEqual("tighten", trusted_payload["governance_escalation"]["level"])


if __name__ == "__main__":
    unittest.main()
