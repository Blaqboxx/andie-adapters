import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from andie_adapters.audit_ledger import ExecutionAuditLedger
from andie_adapters.governance_handshake import build_governance_decision_snapshot
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


class AuditLedgerCliTests(unittest.TestCase):
    def test_list_json_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "ledger.jsonl"
            ledger = ExecutionAuditLedger(ledger_path)

            envelope = _make_envelope("exec-cli-1", "docker", "docker.container.inspect")
            observation = _make_observation(envelope)
            decision = build_governance_decision_snapshot(
                profile="prod",
                resolved_policy={"default_action": "supervised"},
                evaluation={"blast_allowed": True},
                source="test-overlay",
                generated_at="2026-05-26T00:00:00+00:00",
            )
            ledger.append_from_execution(
                envelope=envelope,
                observation=observation,
                governance_decision=decision,
                operator="cli-user",
                confidence_score=0.88,
            )

            proc = subprocess.run(
                [
                    "python3",
                    "-m",
                    "andie_adapters.audit_ledger_cli",
                    "--ledger",
                    str(ledger_path),
                    "list",
                    "--execution-id",
                    "exec-cli-1",
                    "--json",
                ],
                cwd=str(Path(__file__).resolve().parent.parent),
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(0, proc.returncode, msg=proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(1, len(payload["records"]))
            self.assertEqual("exec-cli-1", payload["records"][0]["execution_id"])

    def test_summary_json_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "ledger.jsonl"
            ledger = ExecutionAuditLedger(ledger_path)

            env_one = _make_envelope("exec-cli-2", "docker", "docker.container.restart")
            env_two = _make_envelope("exec-cli-3", "redis", "redis.info")
            obs_one = _make_observation(env_one)
            obs_two = _make_observation(env_two)

            ledger.append_from_execution(
                envelope=env_one,
                observation=obs_one,
                operator="alice",
                confidence_score=0.9,
            )
            ledger.append_from_execution(
                envelope=env_two,
                observation=obs_two,
                operator="bob",
                confidence_score=0.85,
                rollback_triggered=True,
                rollback_outcome="rollback_completed",
            )

            proc = subprocess.run(
                [
                    "python3",
                    "-m",
                    "andie_adapters.audit_ledger_cli",
                    "--ledger",
                    str(ledger_path),
                    "summary",
                    "--json",
                ],
                cwd=str(Path(__file__).resolve().parent.parent),
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(0, proc.returncode, msg=proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(2, payload["total_records"])
            self.assertEqual(1, payload["rollback_triggered"])
            self.assertEqual(1, payload["by_adapter"]["docker"])
            self.assertEqual(1, payload["by_adapter"]["redis"])
            self.assertEqual(2, payload["by_lifecycle_state"]["completed"])
            self.assertIn("by_confidence_trend", payload)
            self.assertIn("confidence_bands", payload)
            self.assertIn("reliability_intelligence", payload)
            self.assertIn("risk_indicators", payload)
            self.assertIn("adapter_rankings", payload["reliability_intelligence"])
            self.assertEqual(2, len(payload["reliability_intelligence"]["adapter_rankings"]))
            self.assertIn("autonomy_readiness", payload["reliability_intelligence"])
            self.assertIn("rollback_frequency", payload["risk_indicators"])
            self.assertIn("confidence_decay_rate", payload["risk_indicators"])
            self.assertIn("telemetry_volatility_index", payload["risk_indicators"])
            self.assertIn("governance_suggestions", payload)
            self.assertIn("governance_suggestions_by_profile", payload)
            self.assertIn("suggestions", payload["governance_suggestions"])
            self.assertIn("watchlist_count", payload["governance_suggestions"])
            self.assertIn("requires_immediate_tightening", payload["governance_suggestions"])
            self.assertIn("profile", payload["governance_suggestions"])
            self.assertEqual("prod", payload["governance_suggestions"]["profile"])
            self.assertGreaterEqual(payload["governance_suggestions"]["watchlist_count"], 1)
            suggestion_ids = {item["id"] for item in payload["governance_suggestions"]["suggestions"]}
            self.assertIn("require_approval_on_rollback_instability", suggestion_ids)
            self.assertIn("reduce_autonomy_for_watchlist_adapters", suggestion_ids)
            self.assertTrue(payload["governance_suggestions"]["requires_immediate_tightening"])
            self.assertIn("prod", payload["governance_suggestions_by_profile"])
            self.assertIn("staging", payload["governance_suggestions_by_profile"])
            self.assertIn("dev", payload["governance_suggestions_by_profile"])
            prod_thresholds = payload["governance_suggestions_by_profile"]["prod"]["thresholds"]
            dev_thresholds = payload["governance_suggestions_by_profile"]["dev"]["thresholds"]
            self.assertLess(prod_thresholds["rollback_frequency"], dev_thresholds["rollback_frequency"])

    def test_replay_json_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "ledger.jsonl"
            ledger = ExecutionAuditLedger(ledger_path)

            envelope = _make_envelope("exec-cli-replay", "docker", "docker.container.inspect")
            observation = _make_observation(envelope)
            ledger.append_from_execution(
                envelope=envelope,
                observation=observation,
                operator="replay-operator",
                confidence_score=0.77,
            )

            proc = subprocess.run(
                [
                    "python3",
                    "-m",
                    "andie_adapters.audit_ledger_cli",
                    "--ledger",
                    str(ledger_path),
                    "replay",
                    "--execution-id",
                    "exec-cli-replay",
                    "--json",
                ],
                cwd=str(Path(__file__).resolve().parent.parent),
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(0, proc.returncode, msg=proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual("exec-cli-replay", payload["execution_id"])
            self.assertIn("lifecycle_timeline", payload)
            self.assertIn("confidence_timeline", payload)

    def test_learn_json_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "ledger.jsonl"
            ledger = ExecutionAuditLedger(ledger_path)

            envelope = _make_envelope("exec-cli-learn", "docker", "docker.container.inspect")
            observation = _make_observation(envelope)
            ledger.append_from_execution(
                envelope=envelope,
                observation=observation,
                operator="learning-user",
                confidence_score=0.84,
            )

            proc = subprocess.run(
                [
                    "python3",
                    "-m",
                    "andie_adapters.audit_ledger_cli",
                    "--ledger",
                    str(ledger_path),
                    "learn",
                    "--adapter-id",
                    "docker",
                    "--json",
                ],
                cwd=str(Path(__file__).resolve().parent.parent),
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(0, proc.returncode, msg=proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual("docker", payload["adapter_id"])
            self.assertIn("reliability_score", payload)
            self.assertIn("governance_effectiveness_score", payload)
            self.assertIn("recommendation", payload)

    def test_suggest_overlay_patch_json_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "ledger.jsonl"
            ledger = ExecutionAuditLedger(ledger_path)

            env_one = _make_envelope("exec-cli-patch-1", "docker", "docker.container.restart")
            env_two = _make_envelope("exec-cli-patch-2", "redis", "redis.info")
            obs_one = _make_observation(env_one)
            obs_two = _make_observation(env_two)

            ledger.append_from_execution(
                envelope=env_one,
                observation=obs_one,
                operator="patch-user-1",
                confidence_score=0.92,
            )
            ledger.append_from_execution(
                envelope=env_two,
                observation=obs_two,
                operator="patch-user-2",
                confidence_score=0.51,
                rollback_triggered=True,
                rollback_outcome="rollback_completed",
            )

            proc = subprocess.run(
                [
                    "python3",
                    "-m",
                    "andie_adapters.audit_ledger_cli",
                    "--ledger",
                    str(ledger_path),
                    "suggest-overlay-patch",
                    "--profile",
                    "prod",
                    "--json",
                ],
                cwd=str(Path(__file__).resolve().parent.parent),
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(0, proc.returncode, msg=proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual("prod", payload["profile"])
            self.assertIn("recommended_changes", payload)
            self.assertGreaterEqual(len(payload["recommended_changes"]), 1)
            actions = {item["action"] for item in payload["recommended_changes"]}
            self.assertIn("set_default_approval", actions)
            self.assertIn("set_adapter_override", actions)

    def test_workspace_snapshot_json_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "ledger.jsonl"
            ledger = ExecutionAuditLedger(ledger_path)

            env_one = _make_envelope("exec-cli-snapshot-1", "docker", "docker.container.restart")
            env_two = _make_envelope("exec-cli-snapshot-2", "redis", "redis.info")
            obs_one = _make_observation(env_one)
            obs_two = _make_observation(env_two)

            ledger.append_from_execution(
                envelope=env_one,
                observation=obs_one,
                operator="snapshot-user-1",
                confidence_score=0.91,
            )
            ledger.append_from_execution(
                envelope=env_two,
                observation=obs_two,
                operator="snapshot-user-2",
                confidence_score=0.52,
                rollback_triggered=True,
                rollback_outcome="rollback_completed",
            )

            proc = subprocess.run(
                [
                    "python3",
                    "-m",
                    "andie_adapters.audit_ledger_cli",
                    "--ledger",
                    str(ledger_path),
                    "workspace-snapshot",
                    "--limit",
                    "1",
                    "--json",
                ],
                cwd=str(Path(__file__).resolve().parent.parent),
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(0, proc.returncode, msg=proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertIn("status_bar", payload)
            self.assertIn("runs", payload)
            self.assertIn("telemetry_center", payload)
            self.assertIn("governance_center", payload)
            self.assertIn("trust_center", payload)
            self.assertIn("live_event_stream", payload)
            self.assertEqual(1, len(payload["runs"]))
            self.assertEqual("exec-cli-snapshot-2", payload["runs"][0]["execution_id"])
            self.assertIn("overlay_patch_candidate", payload["governance_center"])


if __name__ == "__main__":
    unittest.main()
