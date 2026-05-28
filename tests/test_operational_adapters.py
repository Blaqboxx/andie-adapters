import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from andie_adapters.audit_ledger import ExecutionAuditLedger
from andie_adapters.operational_adapters import execute_with_audit, make_docker_adapter
from andie_adapters.runtime_contract import (
    BlastRadiusEstimate,
    ExecutionEnvelope,
    RollbackPlan,
    TelemetryRequirement,
)


def _docker_envelope(execution_id: str, capability_id: str, *, dry_run: bool = False) -> ExecutionEnvelope:
    return ExecutionEnvelope(
        execution_id=execution_id,
        adapter_id="docker",
        capability_id=capability_id,
        policy_profile="prod",
        policy_snapshot={"profile": "prod", "default": "supervised"},
        dry_run=dry_run,
        approval_state="approved",
        capability_scope="container",
        requested_action="supervised",
        telemetry_requirements=(
            TelemetryRequirement(
                signal="command.return_code",
                source="docker",
                required_for_verify=True,
                stabilization_window_seconds=30,
            ),
        ),
        rollback_plan=RollbackPlan(feasible=True, strategies=("restart",)),
        blast_radius=BlastRadiusEstimate(scope="container", reversible=True, max_affected_units=1),
        metadata={"target": "api"},
    )


class OperationalAdaptersTests(unittest.TestCase):
    def test_dry_run_returns_planned_command(self):
        adapter = make_docker_adapter()
        envelope = _docker_envelope("exec-op-1", "docker.container.inspect", dry_run=True)

        observation = adapter.dry_run(envelope)

        self.assertEqual("pending", observation.verification_status)
        self.assertIn("planned_command=docker inspect api", observation.notes)

    @patch("andie_adapters.operational_adapters.which", return_value=None)
    def test_execute_handles_missing_binary(self, _mock_which):
        adapter = make_docker_adapter()
        envelope = _docker_envelope("exec-op-2", "docker.container.inspect")

        observation = adapter.execute(envelope)

        self.assertEqual("failed", observation.verification_status)
        telemetry = {item.signal: item for item in observation.telemetry}
        self.assertEqual(127, telemetry["command.return_code"].value)

    @patch("andie_adapters.operational_adapters.which", return_value="/usr/bin/docker")
    @patch("andie_adapters.operational_adapters.subprocess.run")
    def test_execute_success_collects_telemetry(self, mock_run, _mock_which):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "inspect", "api"],
            returncode=0,
            stdout="{\"status\":\"running\"}",
            stderr="",
        )
        adapter = make_docker_adapter()
        envelope = _docker_envelope("exec-op-3", "docker.container.inspect")

        observation = adapter.execute(envelope)
        telemetry = adapter.collect_telemetry("exec-op-3")

        self.assertEqual("passed", observation.verification_status)
        self.assertGreaterEqual(len(telemetry), 3)

    @patch("andie_adapters.operational_adapters.which", return_value="/usr/bin/docker")
    @patch("andie_adapters.operational_adapters.subprocess.run")
    def test_verify_stabilization_uses_required_signals(self, mock_run, _mock_which):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "inspect", "api"],
            returncode=0,
            stdout="ok",
            stderr="",
        )
        adapter = make_docker_adapter()
        envelope = _docker_envelope("exec-op-4", "docker.container.inspect")

        adapter.execute(envelope)
        result = adapter.verify_stabilization("exec-op-4", envelope.telemetry_requirements)

        self.assertTrue(result.stable)
        self.assertEqual(0, len(result.missing_signals))
        self.assertEqual(0, len(result.failed_signals))

    @patch("andie_adapters.operational_adapters.which", return_value="/usr/bin/docker")
    @patch("andie_adapters.operational_adapters.subprocess.run")
    def test_execute_with_audit_records_failed_then_rollback(self, mock_run, _mock_which):
        mock_run.side_effect = [
            subprocess.CompletedProcess(
                args=["docker", "restart", "api"],
                returncode=1,
                stdout="",
                stderr="restart failed",
            ),
            subprocess.CompletedProcess(
                args=["docker", "restart", "api"],
                returncode=0,
                stdout="restarted",
                stderr="",
            ),
        ]
        adapter = make_docker_adapter()
        envelope = _docker_envelope("exec-op-5", "docker.container.restart")

        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = ExecutionAuditLedger(Path(temp_dir) / "ledger.jsonl")
            record = execute_with_audit(adapter, envelope, ledger, operator="ops-user")
            rows = ledger.list_records(execution_id="exec-op-5")

        self.assertEqual("rollback_completed", record.rollback_outcome)
        self.assertTrue(record.rollback_triggered)
        self.assertEqual(1, len(rows))


if __name__ == "__main__":
    unittest.main()
