import unittest

from andie_adapters.transition_policy import (
    TransitionGateInput,
    evaluate_transition_gate,
    get_profile_transition_policy,
)


class TransitionPolicyTests(unittest.TestCase):
    def test_prod_requires_telemetry_ready_before_execute(self):
        policy = get_profile_transition_policy("prod")
        gate = TransitionGateInput(
            execution_id="exec-policy-1",
            from_state="approved",
            to_state="executing",
            profile="prod",
            telemetry_ready=False,
        )

        decision = evaluate_transition_gate(gate, policy)
        self.assertFalse(decision.allowed)
        self.assertIn("telemetry readiness", decision.reason)

    def test_verified_requires_stabilization_threshold(self):
        policy = get_profile_transition_policy("prod")
        gate = TransitionGateInput(
            execution_id="exec-policy-2",
            from_state="telemetry_window",
            to_state="verified",
            profile="prod",
            verification_result="passed",
            stabilization_score=0.72,
        )

        decision = evaluate_transition_gate(gate, policy)
        self.assertFalse(decision.allowed)
        self.assertIn("stabilization score", decision.reason)

    def test_telemetry_timeout_requires_timed_out_transition(self):
        policy = get_profile_transition_policy("staging")
        gate = TransitionGateInput(
            execution_id="exec-policy-3",
            from_state="telemetry_window",
            to_state="verified",
            profile="staging",
            verification_result="passed",
            stabilization_score=0.95,
            elapsed_in_state_seconds=999,
        )

        decision = evaluate_transition_gate(gate, policy)
        self.assertFalse(decision.allowed)
        self.assertIn("must transition to timed_out", decision.reason)

    def test_failed_to_rollback_triggered_requires_rollback_when_mandatory(self):
        policy = get_profile_transition_policy("prod")
        gate = TransitionGateInput(
            execution_id="exec-policy-4",
            from_state="failed",
            to_state="rollback_triggered",
            profile="prod",
            rollback_available=False,
        )

        decision = evaluate_transition_gate(gate, policy)
        self.assertFalse(decision.allowed)
        self.assertIn("rollback is mandatory", decision.reason)

    def test_rollback_failed_must_halt_in_prod(self):
        policy = get_profile_transition_policy("prod")
        complete_gate = TransitionGateInput(
            execution_id="exec-policy-5",
            from_state="rollback_failed",
            to_state="completed",
            profile="prod",
        )
        denied = evaluate_transition_gate(complete_gate, policy)
        self.assertFalse(denied.allowed)
        self.assertIn("operator intervention", denied.reason)

        halt_gate = TransitionGateInput(
            execution_id="exec-policy-5",
            from_state="rollback_failed",
            to_state="halted",
            profile="prod",
        )
        allowed = evaluate_transition_gate(halt_gate, policy)
        self.assertTrue(allowed.allowed)

    def test_improving_curve_can_relax_threshold(self):
        policy = get_profile_transition_policy("staging")
        gate = TransitionGateInput(
            execution_id="exec-policy-6",
            from_state="telemetry_window",
            to_state="verified",
            profile="staging",
            verification_result="passed",
            stabilization_score=0.78,
            telemetry_scores=(0.64, 0.7, 0.75, 0.79),
        )

        decision = evaluate_transition_gate(gate, policy)
        self.assertTrue(decision.allowed)

    def test_degrading_curve_blocks_verify_even_at_high_score(self):
        policy = get_profile_transition_policy("prod")
        gate = TransitionGateInput(
            execution_id="exec-policy-7",
            from_state="telemetry_window",
            to_state="verified",
            profile="prod",
            verification_result="passed",
            stabilization_score=0.95,
            telemetry_scores=(0.98, 0.96, 0.94, 0.91),
        )

        decision = evaluate_transition_gate(gate, policy)
        self.assertFalse(decision.allowed)
        self.assertIn("degrading", decision.reason)

    def test_oscillation_extends_window_for_non_verify_transition(self):
        policy = get_profile_transition_policy("prod")
        gate = TransitionGateInput(
            execution_id="exec-policy-8",
            from_state="telemetry_window",
            to_state="failed",
            profile="prod",
            verification_result="failed",
            stabilization_score=0.5,
            elapsed_in_state_seconds=360,
            telemetry_scores=(0.8, 0.6, 0.82, 0.58, 0.84),
        )

        decision = evaluate_transition_gate(gate, policy)
        self.assertTrue(decision.allowed)


if __name__ == "__main__":
    unittest.main()
