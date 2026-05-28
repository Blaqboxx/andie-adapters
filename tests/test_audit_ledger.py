import tempfile
import unittest
from pathlib import Path

from andie_adapters.audit_ledger import ExecutionAuditLedger, hash_policy_snapshot
from andie_adapters.runtime_contract import (
    BlastRadiusEstimate,
    ExecutionEnvelope,
    ExecutionObservation,
    RollbackPlan,
    TelemetryReading,
    TelemetryRequirement,
)


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


class ExecutionAuditLedgerTests(unittest.TestCase):
    def test_policy_hash_is_stable(self):
        left = {"a": 1, "nested": {"k": "v"}}
        right = {"nested": {"k": "v"}, "a": 1}
        self.assertEqual(hash_policy_snapshot(left), hash_policy_snapshot(right))

    def test_append_and_list_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "ledger.jsonl"
            ledger = ExecutionAuditLedger(ledger_path)

            envelope = _make_envelope("exec-100", "docker", "docker.container.restart")
            observation = _make_observation(envelope)

            ledger.append_from_execution(
                envelope=envelope,
                observation=observation,
                operator="operator@example.com",
                confidence_score=0.93,
            )

            rows = ledger.list_records(execution_id="exec-100")
            self.assertEqual(1, len(rows))
            row = rows[0]
            self.assertEqual("docker", row.adapter_id)
            self.assertEqual("passed", row.verification_result)
            self.assertEqual("completed", row.lifecycle_state)
            self.assertGreaterEqual(len(row.lifecycle_timeline), 1)
            self.assertEqual(False, row.rollback_triggered)
            self.assertEqual("not_triggered", row.rollback_outcome)
            self.assertEqual(1, len(row.telemetry_timeline))
            self.assertGreaterEqual(len(row.confidence_timeline), 1)
            self.assertEqual(row.confidence_score, row.confidence_timeline[-1].score)

    def test_adapter_filtering(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "ledger.jsonl"
            ledger = ExecutionAuditLedger(ledger_path)

            env_one = _make_envelope("exec-201", "docker", "docker.container.inspect")
            obs_one = _make_observation(env_one)
            ledger.append_from_execution(
                envelope=env_one,
                observation=obs_one,
                operator="alice",
                confidence_score=0.7,
            )

            env_two = _make_envelope("exec-202", "redis", "redis.info")
            obs_two = _make_observation(env_two)
            ledger.append_from_execution(
                envelope=env_two,
                observation=obs_two,
                operator="bob",
                confidence_score=0.8,
                rollback_triggered=True,
                rollback_outcome="rollback_completed",
            )

            docker_rows = ledger.list_records(adapter_id="docker")
            redis_rows = ledger.list_records(adapter_id="redis")

            self.assertEqual(1, len(docker_rows))
            self.assertEqual("exec-201", docker_rows[0].execution_id)
            self.assertEqual(1, len(redis_rows))
            self.assertEqual(True, redis_rows[0].rollback_triggered)
            self.assertEqual("rollback_completed", redis_rows[0].rollback_outcome)

            completed_rows = ledger.list_records(lifecycle_state="completed")
            self.assertEqual(2, len(completed_rows))

    def test_confidence_score_out_of_range_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "ledger.jsonl"
            ledger = ExecutionAuditLedger(ledger_path)

            envelope = _make_envelope("exec-301", "ssh", "ssh.command.readonly")
            observation = _make_observation(envelope)

            with self.assertRaises(ValueError):
                ledger.append_from_execution(
                    envelope=envelope,
                    observation=observation,
                    operator="operator",
                    confidence_score=1.2,
                )

    def test_confidence_score_can_be_derived_when_omitted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "ledger.jsonl"
            ledger = ExecutionAuditLedger(ledger_path)

            envelope = _make_envelope("exec-401", "docker", "docker.container.inspect")
            observation = _make_observation(envelope)

            row = ledger.append_from_execution(
                envelope=envelope,
                observation=observation,
                operator="derived-confidence",
            )

            self.assertTrue(0.0 <= row.confidence_score <= 1.0)
            self.assertEqual(row.confidence_score, row.confidence_timeline[-1].score)


if __name__ == "__main__":
    unittest.main()
