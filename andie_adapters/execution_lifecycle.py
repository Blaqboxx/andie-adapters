from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Literal

ExecutionState = Literal[
    "requested",
    "approved",
    "denied",
    "executing",
    "telemetry_window",
    "verified",
    "failed",
    "rollback_triggered",
    "rollback_executing",
    "rollback_completed",
    "rollback_failed",
    "halted",
    "completed",
    "timed_out",
]

_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "requested": {"approved", "denied", "timed_out"},
    "approved": {"executing", "timed_out"},
    "denied": {"completed"},
    "executing": {"telemetry_window", "failed", "timed_out"},
    "telemetry_window": {"verified", "failed", "timed_out"},
    "verified": {"rollback_triggered", "completed"},
    "failed": {"rollback_triggered", "completed"},
    "rollback_triggered": {"rollback_executing", "rollback_failed"},
    "rollback_executing": {"rollback_completed", "rollback_failed"},
    "rollback_completed": {"completed"},
    "rollback_failed": {"halted", "completed"},
    "halted": set(),
    "completed": set(),
    "timed_out": {"rollback_triggered", "completed"},
}


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    execution_id: str
    from_state: str
    to_state: str
    timestamp: str
    reason: str
    metadata: dict[str, Any]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_valid_transition(from_state: str, to_state: str) -> bool:
    return to_state in _ALLOWED_TRANSITIONS.get(from_state, set())


def assert_valid_transition(from_state: str, to_state: str) -> None:
    if not is_valid_transition(from_state, to_state):
        raise ValueError(f"invalid lifecycle transition: {from_state} -> {to_state}")


def make_event(
    execution_id: str,
    from_state: str,
    to_state: str,
    reason: str,
    metadata: dict[str, Any] | None = None,
    timestamp: str | None = None,
) -> LifecycleEvent:
    assert_valid_transition(from_state, to_state)
    return LifecycleEvent(
        execution_id=execution_id,
        from_state=from_state,
        to_state=to_state,
        timestamp=timestamp or _now_iso(),
        reason=reason,
        metadata=metadata or {},
    )


def serialize_lifecycle_timeline(timeline: tuple[LifecycleEvent, ...]) -> list[dict[str, Any]]:
    return [asdict(event) for event in timeline]


def deserialize_lifecycle_timeline(payload: list[dict[str, Any]]) -> tuple[LifecycleEvent, ...]:
    return tuple(LifecycleEvent(**item) for item in payload)


def validate_lifecycle_timeline(timeline: tuple[LifecycleEvent, ...], expected_execution_id: str) -> list[str]:
    errors: list[str] = []
    if not timeline:
        return ["lifecycle_timeline must be non-empty"]

    previous_state = "requested"
    for index, event in enumerate(timeline):
        prefix = f"lifecycle_timeline[{index}]"
        if event.execution_id != expected_execution_id:
            errors.append(f"{prefix}.execution_id must match execution_id")
        if event.from_state != previous_state:
            errors.append(f"{prefix}.from_state expected {previous_state} but found {event.from_state}")
        if not is_valid_transition(event.from_state, event.to_state):
            errors.append(f"{prefix} invalid transition: {event.from_state} -> {event.to_state}")
        if not event.timestamp:
            errors.append(f"{prefix}.timestamp must be non-empty")
        if not event.reason:
            errors.append(f"{prefix}.reason must be non-empty")
        previous_state = event.to_state

    return errors


def build_default_lifecycle_timeline(
    execution_id: str,
    approval_state: str,
    verification_result: str,
    rollback_triggered: bool,
    rollback_outcome: str,
) -> tuple[str, tuple[LifecycleEvent, ...]]:
    events: list[LifecycleEvent] = []
    current = "requested"

    if approval_state == "denied":
        events.append(make_event(execution_id, current, "denied", "approval denied by governance"))
        current = "denied"
        events.append(make_event(execution_id, current, "completed", "execution terminated after denial"))
        return "completed", tuple(events)

    events.append(make_event(execution_id, current, "approved", "approval granted or not required"))
    current = "approved"

    events.append(make_event(execution_id, current, "executing", "adapter execution started"))
    current = "executing"

    events.append(make_event(execution_id, current, "telemetry_window", "stabilization telemetry window opened"))
    current = "telemetry_window"

    if verification_result == "passed":
        events.append(make_event(execution_id, current, "verified", "verification passed"))
        current = "verified"
    else:
        events.append(make_event(execution_id, current, "failed", "verification failed"))
        current = "failed"

    if rollback_triggered:
        events.append(make_event(execution_id, current, "rollback_triggered", "rollback requested"))
        current = "rollback_triggered"
        events.append(make_event(execution_id, current, "rollback_executing", "rollback execution started"))
        current = "rollback_executing"

        if rollback_outcome == "rollback_completed":
            events.append(make_event(execution_id, current, "rollback_completed", "rollback completed"))
            current = "rollback_completed"
            events.append(make_event(execution_id, current, "completed", "execution lifecycle closed"))
            current = "completed"
        else:
            events.append(make_event(execution_id, current, "rollback_failed", "rollback failed"))
            current = "rollback_failed"
            events.append(make_event(execution_id, current, "halted", "rollback failure requires operator intervention"))
            current = "halted"

    if current not in {"completed", "halted"}:
        events.append(make_event(execution_id, current, "completed", "execution lifecycle closed"))
        current = "completed"

    return current, tuple(events)
