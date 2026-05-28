import tempfile
import unittest
from pathlib import Path

from andie_adapters.audit_ledger import ExecutionAuditLedger
from andie_adapters.reliability_learning import evaluate_reliability
from tests.test_audit_ledger import _make_envelope, _make_observation


class ReliabilityLearningTests(unittest.TestCase):
    def test_reliability_scoring_uses_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = ExecutionAuditLedger(Path(temp_dir) / "ledger.jsonl")

            docker_pass = ledger.append_from_execution(
                envelope=_make_envelope("exec-rel-1", "docker", "docker.container.inspect"),
                observation=_make_observation(_make_envelope("exec-rel-1", "docker", "docker.container.inspect")),
                operator="tester",
                confidence_score=0.9,
            )
            docker_fail = ledger.append_from_execution(
                envelope=_make_envelope("exec-rel-2", "docker", "docker.container.restart"),
                observation=_make_observation(_make_envelope("exec-rel-2", "docker", "docker.container.restart")),
                operator="tester",
                confidence_score=0.45,
                rollback_triggered=True,
                rollback_outcome="rollback_completed",
            )
            redis_pass = ledger.append_from_execution(
                envelope=_make_envelope("exec-rel-3", "redis", "redis.info"),
                observation=_make_observation(_make_envelope("exec-rel-3", "redis", "redis.info")),
                operator="tester",
                confidence_score=0.8,
            )

            records = ledger.list_records()
            docker_score = evaluate_reliability(records, adapter_id="docker")
            redis_score = evaluate_reliability(records, adapter_id="redis")

            self.assertEqual(2, docker_score.sample_count)
            self.assertEqual(1, redis_score.sample_count)
            self.assertGreater(docker_score.rollback_rate, 0.0)
            self.assertGreater(redis_score.reliability_score, 0.0)
            self.assertIn(docker_score.recommendation, {
                "retain supervised use and continue monitoring",
                "tighten approval and telemetry requirements",
                "restrict capability scope and require human approval",
                "safe to consider broader supervised use",
            })

    def test_missing_history_returns_insufficient_history(self):
        score = evaluate_reliability((), adapter_id="missing")
        self.assertEqual(0, score.sample_count)
        self.assertEqual("insufficient history", score.recommendation)


if __name__ == "__main__":
    unittest.main()
