from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any


def _to_canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def hash_governance_decision_snapshot(snapshot: dict[str, Any]) -> str:
    canonical = _to_canonical_json(snapshot)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_governance_decision_snapshot(
    profile: str,
    resolved_policy: dict[str, Any],
    evaluation: dict[str, Any] | None,
    source: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "profile": profile,
        "source": source,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "resolved_policy": resolved_policy,
        "evaluation": evaluation or {},
    }
