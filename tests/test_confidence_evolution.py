import unittest

from andie_adapters.confidence_evolution import (
    build_confidence_timeline,
    infer_confidence_trend,
    validate_confidence_timeline,
)
from andie_adapters.execution_lifecycle import build_default_lifecycle_timeline


class ConfidenceEvolutionTests(unittest.TestCase):
    def test_confidence_timeline_builds_for_verified_path(self):
        _, lifecycle = build_default_lifecycle_timeline(
            execution_id="exec-conf-1",
            approval_state="approved",
            verification_result="passed",
            rollback_triggered=False,
            rollback_outcome="not_triggered",
        )

        timeline, final_score = build_confidence_timeline(
            execution_id="exec-conf-1",
            lifecycle_timeline=lifecycle,
            telemetry_scores=(0.62, 0.7, 0.78, 0.84),
        )

        self.assertGreater(len(timeline), 0)
        self.assertTrue(0.0 <= final_score <= 1.0)
        self.assertEqual([], validate_confidence_timeline(timeline, "exec-conf-1"))
        self.assertIn(infer_confidence_trend(timeline), {"rising", "stable", "falling"})

    def test_rollback_failure_collapses_confidence(self):
        _, lifecycle = build_default_lifecycle_timeline(
            execution_id="exec-conf-2",
            approval_state="approved",
            verification_result="failed",
            rollback_triggered=True,
            rollback_outcome="rollback_failed",
        )

        timeline, final_score = build_confidence_timeline(
            execution_id="exec-conf-2",
            lifecycle_timeline=lifecycle,
            telemetry_scores=(0.5, 0.48, 0.45),
        )

        self.assertLess(final_score, 0.45)
        self.assertEqual("falling", infer_confidence_trend(timeline))


if __name__ == "__main__":
    unittest.main()
