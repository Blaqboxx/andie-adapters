import unittest

from andie_adapters.runtime_contract import (
    AdapterRuntimeInterface,
    BlastRadiusEstimate,
    CapabilityContract,
    ExecutionEnvelope,
    RollbackPlan,
    TelemetryRequirement,
    validate_execution_envelope,
)


class RuntimeContractTests(unittest.TestCase):
    def test_valid_execution_envelope(self):
        envelope = ExecutionEnvelope(
            execution_id="exec-001",
            adapter_id="docker",
            capability_id="docker.container.restart",
            policy_profile="prod",
            policy_snapshot={
                "profile": "prod",
                "resolved_policy": {"default_action": "supervised"},
                "evaluation": {"blast_allowed": True},
            },
            dry_run=True,
            approval_state="approved",
            capability_scope="restart",
            requested_action="supervised",
            telemetry_requirements=(
                TelemetryRequirement(
                    signal="container_health_status",
                    source="docker_events",
                    required_for_verify=True,
                    stabilization_window_seconds=300,
                ),
            ),
            rollback_plan=RollbackPlan(feasible=True, strategies=("restart_previous_container",)),
            blast_radius=BlastRadiusEstimate(scope="host", reversible=True, max_affected_units=3),
        )

        self.assertEqual([], validate_execution_envelope(envelope))

    def test_missing_rollback_plan_is_invalid(self):
        envelope = ExecutionEnvelope(
            execution_id="exec-002",
            adapter_id="redis",
            capability_id="redis.flushall",
            policy_profile="prod",
            policy_snapshot={"profile": "prod"},
            capability_scope="recover",
            requested_action="deny",
            telemetry_requirements=(
                TelemetryRequirement(
                    signal="keyspace_db0_keys",
                    source="redis_info",
                    required_for_verify=True,
                    stabilization_window_seconds=180,
                ),
            ),
            blast_radius=BlastRadiusEstimate(scope="service", reversible=False, max_affected_units=1),
        )

        errors = validate_execution_envelope(envelope)
        self.assertTrue(any("rollback_plan must be provided" in error for error in errors))

    def test_deny_requests_still_require_rollback_feasibility(self):
        envelope = ExecutionEnvelope(
            execution_id="exec-003",
            adapter_id="ssh",
            capability_id="ssh.command.mutating",
            policy_profile="prod",
            policy_snapshot={"profile": "prod"},
            capability_scope="configure",
            requested_action="deny",
            rollback_plan=RollbackPlan(feasible=False, strategies=("revert_last_known_good_command",)),
            telemetry_requirements=(
                TelemetryRequirement(
                    signal="remote_exit_code",
                    source="ssh_session",
                    required_for_verify=True,
                    stabilization_window_seconds=30,
                ),
            ),
            blast_radius=BlastRadiusEstimate(scope="host", reversible=True, max_affected_units=1),
        )

        errors = validate_execution_envelope(envelope)
        self.assertTrue(any("rollback feasibility" in error for error in errors))

    def test_interface_shape_is_protocol_based(self):
        class DummyAdapter:
            def describe_capabilities(self):
                return (
                    CapabilityContract(
                        capability_id="systemd.unit.status",
                        operation="inspect unit status",
                        scope="observe",
                        risk_tier="low",
                        dangerous=False,
                        production_default="allow",
                        requires_approval=False,
                        audit_classification="read",
                    ),
                )

            def estimate_blast_radius(self, capability_id: str):
                return BlastRadiusEstimate(scope="host", reversible=True, max_affected_units=1)

            def dry_run(self, envelope: ExecutionEnvelope):
                return None

            def execute(self, envelope: ExecutionEnvelope):
                return None

            def rollback(self, envelope: ExecutionEnvelope):
                return None

            def collect_telemetry(self, execution_id: str):
                return ()

        self.assertIsInstance(DummyAdapter(), AdapterRuntimeInterface)


if __name__ == "__main__":
    unittest.main()
