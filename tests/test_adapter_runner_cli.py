import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from andie_adapters.adapter_runner_cli import load_envelope, run_envelope
from andie_adapters.audit_ledger import ExecutionAuditLedger


def _envelope_payload(execution_id: str, capability_id: str = "docker.container.inspect") -> dict:
    return {
        "execution_id": execution_id,
        "adapter_id": "docker",
        "capability_id": capability_id,
        "policy_profile": "prod",
        "policy_snapshot": {"profile": "prod", "default_action": "supervised"},
        "dry_run": False,
        "approval_state": "approved",
        "capability_scope": "container",
        "requested_action": "supervised",
        "telemetry_requirements": [
            {
                "signal": "command.return_code",
                "source": "docker",
                "required_for_verify": True,
                "stabilization_window_seconds": 20,
            }
        ],
        "rollback_plan": {
            "feasible": True,
            "strategies": ["restart"],
        },
        "blast_radius": {
            "scope": "container",
            "reversible": True,
            "max_affected_units": 1,
        },
        "metadata": {"target": "api"},
    }


class AdapterRunnerCliTests(unittest.TestCase):
    def test_load_envelope_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            envelope_path = Path(temp_dir) / "envelope.json"
            payload = _envelope_payload("exec-runner-1")
            envelope_path.write_text(json.dumps(payload), encoding="utf-8")

            envelope = load_envelope(envelope_path)

            self.assertEqual("exec-runner-1", envelope.execution_id)
            self.assertEqual("docker", envelope.adapter_id)
            self.assertEqual("docker.container.inspect", envelope.capability_id)

    @patch("andie_adapters.operational_adapters.which", return_value="/usr/bin/docker")
    @patch("andie_adapters.operational_adapters.subprocess.run")
    def test_run_envelope_success_path(self, mock_run, _mock_which):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "inspect", "api"],
            returncode=0,
            stdout="ok",
            stderr="",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            envelope_path = Path(temp_dir) / "envelope.json"
            ledger_path = Path(temp_dir) / "ledger.jsonl"
            envelope_path.write_text(json.dumps(_envelope_payload("exec-runner-2")), encoding="utf-8")

            envelope = load_envelope(envelope_path)
            ledger = ExecutionAuditLedger(ledger_path)
            payload = run_envelope(envelope, ledger, operator="runner-user")

            self.assertEqual("exec-runner-2", payload["execution_id"])
            self.assertEqual("pending", payload["dry_run"]["verification_status"])
            self.assertEqual("passed", payload["execute"]["verification_status"])
            self.assertTrue(payload["stabilization"]["stable"])
            self.assertFalse(payload["rollback"]["triggered"])
            self.assertIn("replay", payload)
            self.assertIn("reliability", payload)

    @patch("andie_adapters.operational_adapters.which", return_value="/usr/bin/docker")
    @patch("andie_adapters.operational_adapters.subprocess.run")
    def test_run_envelope_triggers_rollback_on_failure(self, mock_run, _mock_which):
        mock_run.side_effect = [
            subprocess.CompletedProcess(
                args=["docker", "inspect", "api"],
                returncode=1,
                stdout="",
                stderr="failed",
            ),
            subprocess.CompletedProcess(
                args=["docker", "inspect", "api"],
                returncode=0,
                stdout="recovered",
                stderr="",
            ),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            envelope_path = Path(temp_dir) / "envelope.json"
            ledger_path = Path(temp_dir) / "ledger.jsonl"
            envelope_path.write_text(
                json.dumps(_envelope_payload("exec-runner-3", capability_id="docker.container.restart")),
                encoding="utf-8",
            )

            envelope = load_envelope(envelope_path)
            ledger = ExecutionAuditLedger(ledger_path)
            payload = run_envelope(envelope, ledger, operator="runner-user")

            self.assertTrue(payload["rollback"]["triggered"])
            self.assertEqual("rollback_completed", payload["rollback"]["outcome"])
            self.assertEqual("rollback_completed", payload["record"]["rollback_outcome"])


if __name__ == "__main__":
    unittest.main()
