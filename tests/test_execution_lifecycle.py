import unittest

from andie_adapters.execution_lifecycle import (
    assert_valid_transition,
    build_default_lifecycle_timeline,
    is_valid_transition,
    validate_lifecycle_timeline,
)


class ExecutionLifecycleTests(unittest.TestCase):
    def test_transition_validation(self):
        self.assertTrue(is_valid_transition("requested", "approved"))
        self.assertTrue(is_valid_transition("telemetry_window", "verified"))
        self.assertFalse(is_valid_transition("requested", "verified"))

    def test_assert_invalid_transition_raises(self):
        with self.assertRaises(ValueError):
            assert_valid_transition("requested", "verified")

    def test_build_default_lifecycle_timeline_without_rollback(self):
        final_state, timeline = build_default_lifecycle_timeline(
            execution_id="exec-life-1",
            approval_state="approved",
            verification_result="passed",
            rollback_triggered=False,
            rollback_outcome="not_triggered",
        )

        self.assertEqual("completed", final_state)
        self.assertGreaterEqual(len(timeline), 5)
        self.assertEqual("requested", timeline[0].from_state)
        self.assertEqual("completed", timeline[-1].to_state)
        self.assertEqual([], validate_lifecycle_timeline(timeline, "exec-life-1"))

    def test_build_default_lifecycle_timeline_with_rollback(self):
        final_state, timeline = build_default_lifecycle_timeline(
            execution_id="exec-life-2",
            approval_state="approved",
            verification_result="failed",
            rollback_triggered=True,
            rollback_outcome="rollback_completed",
        )

        self.assertEqual("completed", final_state)
        states = [event.to_state for event in timeline]
        self.assertIn("rollback_triggered", states)
        self.assertIn("rollback_executing", states)
        self.assertIn("rollback_completed", states)
        self.assertEqual([], validate_lifecycle_timeline(timeline, "exec-life-2"))

    def test_build_default_lifecycle_timeline_with_rollback_failure_halts(self):
        final_state, timeline = build_default_lifecycle_timeline(
            execution_id="exec-life-3",
            approval_state="approved",
            verification_result="failed",
            rollback_triggered=True,
            rollback_outcome="rollback_failed",
        )

        self.assertEqual("halted", final_state)
        states = [event.to_state for event in timeline]
        self.assertIn("rollback_failed", states)
        self.assertIn("halted", states)
        self.assertEqual([], validate_lifecycle_timeline(timeline, "exec-life-3"))


if __name__ == "__main__":
    unittest.main()
