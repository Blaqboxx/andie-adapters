import tempfile
import unittest
from pathlib import Path

from andie_adapters.audit_ledger import ExecutionAuditLedger
from andie_adapters.governance_escalation import evaluate_governance_escalation
from tests.test_audit_ledger import _make_envelope, _make_observation


class GovernanceEscalationTests(unittest.TestCase):
    def _record(self, execution_id: str, adapter_id: str, capability_id: str, confidence_score: float, *, rollback_triggered: bool = False, rollback_outcome: str = "not_triggered"):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = ExecutionAuditLedger(Path(temp_dir) / "ledger.jsonl")
            envelope = _make_envelope(execution_id, adapter_id, capability_id)
            observation = _make_observation(envelope)
            return ledger.append_from_execution(
                envelope=envelope,
                observation=observation,
                operator="tester",
                confidence_score=confidence_score,
                rollback_triggered=rollback_triggered,
                rollback_outcome=rollback_outcome,
            )

    def test_watch_for_stable_high_confidence_success(self):
        record = self._record("exec-gov-1", "docker", "docker.container.inspect", 0.9)
        escalation = evaluate_governance_escalation(record)
        self.assertEqual("watch", escalation.level)
        self.assertIn("stable recovery", escalation.reasons[0])

    def test_low_historical_reliability_tightens_clean_execution(self):
        record = self._record("exec-gov-1b", "docker", "docker.container.inspect", 0.91)
        escalation = evaluate_governance_escalation(record, historical_reliability_score=0.2)
        self.assertEqual("tighten", escalation.level)
        self.assertIn("historical reliability is low", escalation.reasons)
        self.assertEqual("trust_constrained", escalation.governance_changes["autonomy"])

    def test_tighten_on_rollback_and_falling_confidence(self):
        record = self._record(
            "exec-gov-2",
            "redis",
            "redis.info",
            0.48,
            rollback_triggered=True,
            rollback_outcome="rollback_completed",
        )
        escalation = evaluate_governance_escalation(record)
        self.assertEqual("tighten", escalation.level)
        self.assertIn("rollback occurred", escalation.reasons)
        self.assertIn("tighten verification thresholds", escalation.recommended_actions)

    def test_escalate_on_critical_confidence_collapse(self):
        record = self._record("exec-gov-3", "ssh", "ssh.command.readonly", 0.22)
        escalation = evaluate_governance_escalation(record)
        self.assertEqual("escalate", escalation.level)
        self.assertIn("confidence collapsed", escalation.reasons[0])

    def test_halt_on_rollback_failure(self):
        record = self._record(
            "exec-gov-4",
            "systemd",
            "systemd.unit.restart",
            0.41,
            rollback_triggered=True,
            rollback_outcome="rollback_failed",
        )
        escalation = evaluate_governance_escalation(record)
        self.assertEqual("halt", escalation.level)
        self.assertIn("require human operator intervention", escalation.recommended_actions)


if __name__ == "__main__":
    unittest.main()
