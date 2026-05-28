from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .audit_ledger import ExecutionAuditLedger
from .confidence_evolution import infer_confidence_trend
from .execution_replay import replay_execution_record
from .reliability_learning import evaluate_reliability, reliability_score_to_dict


def _default_ledger_path() -> Path:
    return Path("audit") / "execution_ledger.jsonl"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Query and export ANDIE adapter execution audit ledger.")
    parser.add_argument("--ledger", default=str(_default_ledger_path()), help="Path to ledger JSONL file")

    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List records with optional filters")
    list_parser.add_argument("--execution-id", help="Filter by execution id")
    list_parser.add_argument("--adapter-id", help="Filter by adapter id")
    list_parser.add_argument("--lifecycle-state", help="Filter by lifecycle terminal state")
    list_parser.add_argument("--json", action="store_true", dest="json_output", help="Emit JSON output")

    summary_parser = subparsers.add_parser("summary", help="Show summary counts")
    summary_parser.add_argument("--json", action="store_true", dest="json_output", help="Emit JSON output")

    replay_parser = subparsers.add_parser("replay", help="Replay a single execution timeline")
    replay_parser.add_argument("--execution-id", required=True, help="Execution id to replay")
    replay_parser.add_argument("--json", action="store_true", dest="json_output", help="Emit JSON output")

    learn_parser = subparsers.add_parser("learn", help="Derive reliability and governance learning scores")
    learn_parser.add_argument("--adapter-id", help="Restrict learning to a single adapter")
    learn_parser.add_argument("--capability-id", help="Restrict learning to a single capability")
    learn_parser.add_argument("--json", action="store_true", dest="json_output", help="Emit JSON output")

    patch_parser = subparsers.add_parser("suggest-overlay-patch", help="Generate governance overlay patch candidates")
    patch_parser.add_argument("--profile", choices=("dev", "staging", "prod"), help="Target profile for patch candidates")
    patch_parser.add_argument("--json", action="store_true", dest="json_output", help="Emit JSON output")

    workspace_parser = subparsers.add_parser("workspace-snapshot", help="Emit command-center snapshot payload")
    workspace_parser.add_argument("--limit", type=int, default=20, help="Maximum recent executions to include")
    workspace_parser.add_argument("--json", action="store_true", dest="json_output", help="Emit JSON output")

    return parser


def _records_to_dict(rows: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [ExecutionAuditLedger.record_to_dict(item) for item in rows]


def _reliability_summary(rows: tuple[Any, ...]) -> dict[str, Any]:
    adapter_ids = sorted({row.adapter_id for row in rows})
    rankings: list[dict[str, Any]] = []
    readiness = {
        "ready": 0,
        "supervised": 0,
        "constrained": 0,
    }

    for adapter_id in adapter_ids:
        score = evaluate_reliability(rows, adapter_id=adapter_id)
        score_dict = reliability_score_to_dict(score)
        score_value = score_dict["reliability_score"]
        if score_value >= 0.8:
            readiness["ready"] += 1
        elif score_value >= 0.6:
            readiness["supervised"] += 1
        else:
            readiness["constrained"] += 1
        rankings.append(score_dict)

    rankings.sort(
        key=lambda item: (item["reliability_score"], item["governance_effectiveness_score"]),
        reverse=True,
    )

    if not rankings:
        return {
            "adapter_rankings": [],
            "top_adapters": [],
            "watchlist_adapters": [],
            "average_reliability_score": 0.0,
            "average_governance_effectiveness": 0.0,
            "autonomy_readiness": readiness,
        }

    avg_reliability = sum(item["reliability_score"] for item in rankings) / float(len(rankings))
    avg_effectiveness = sum(item["governance_effectiveness_score"] for item in rankings) / float(len(rankings))

    return {
        "adapter_rankings": rankings,
        "top_adapters": rankings[:3],
        "watchlist_adapters": [item for item in rankings if item["reliability_score"] < 0.6],
        "average_reliability_score": avg_reliability,
        "average_governance_effectiveness": avg_effectiveness,
        "autonomy_readiness": readiness,
    }


def _profile_thresholds(profile: str) -> dict[str, float]:
    if profile == "prod":
        return {
            "rollback_frequency": 0.2,
            "telemetry_volatility_index": 0.25,
            "confidence_decay_rate": 0.2,
            "watchlist_reliability": 0.7,
            "relax_average_reliability": 0.9,
            "relax_risk": 0.08,
        }
    if profile == "staging":
        return {
            "rollback_frequency": 0.3,
            "telemetry_volatility_index": 0.35,
            "confidence_decay_rate": 0.3,
            "watchlist_reliability": 0.6,
            "relax_average_reliability": 0.85,
            "relax_risk": 0.12,
        }
    # dev is intentionally more permissive for controlled experimentation.
    return {
        "rollback_frequency": 0.45,
        "telemetry_volatility_index": 0.5,
        "confidence_decay_rate": 0.45,
        "watchlist_reliability": 0.5,
        "relax_average_reliability": 0.8,
        "relax_risk": 0.2,
    }


def _governance_suggestions(reliability: dict[str, Any], risk: dict[str, Any], profile: str) -> dict[str, Any]:
    suggestions: list[dict[str, Any]] = []
    thresholds = _profile_thresholds(profile)
    watchlist = [
        item
        for item in reliability["adapter_rankings"]
        if float(item["reliability_score"]) < thresholds["watchlist_reliability"]
    ]

    rollback_frequency = float(risk["rollback_frequency"])
    confidence_decay_rate = float(risk["confidence_decay_rate"])
    telemetry_volatility_index = float(risk["telemetry_volatility_index"])

    if rollback_frequency >= thresholds["rollback_frequency"]:
        suggestions.append(
            {
                "id": "require_approval_on_rollback_instability",
                "severity": "high" if rollback_frequency < 0.5 else "critical",
                "signal": "repeated rollback failures",
                "recommended_change": {
                    "approval_state": "supervised_required",
                    "autonomy": "restricted",
                },
                "reason": "rollback frequency indicates unstable remediation outcomes",
                "profile": profile,
            }
        )

    if telemetry_volatility_index >= thresholds["telemetry_volatility_index"]:
        suggestions.append(
            {
                "id": "extend_telemetry_window_for_volatility",
                "severity": "high",
                "signal": "high telemetry volatility",
                "recommended_change": {
                    "telemetry_window": "extend",
                    "verification_depth": "increase",
                },
                "reason": "volatile telemetry patterns suggest verification windows are too short",
                "profile": profile,
            }
        )

    if confidence_decay_rate >= thresholds["confidence_decay_rate"]:
        suggestions.append(
            {
                "id": "tighten_scope_on_confidence_decay",
                "severity": "high",
                "signal": "severe confidence decay",
                "recommended_change": {
                    "capability_scope": "tighten",
                    "approval_state": "supervised_required",
                },
                "reason": "confidence trend indicates trust deterioration during execution lifecycle",
                "profile": profile,
            }
        )

    if watchlist:
        watchlist_ids = [item["adapter_id"] for item in watchlist]
        suggestions.append(
            {
                "id": "reduce_autonomy_for_watchlist_adapters",
                "severity": "high",
                "signal": "unstable recovery history",
                "adapters": watchlist_ids,
                "recommended_change": {
                    "autonomy": "trust_constrained",
                    "approval_state": "supervised_required",
                },
                "reason": "watchlist adapters have low reliability and require tighter governance",
                "profile": profile,
            }
        )

    if (
        reliability["average_reliability_score"] >= thresholds["relax_average_reliability"]
        and rollback_frequency < thresholds["relax_risk"]
        and confidence_decay_rate < thresholds["relax_risk"]
        and telemetry_volatility_index < thresholds["relax_risk"]
    ):
        suggestions.append(
            {
                "id": "cautiously_relax_pacing_for_stable_history",
                "severity": "advisory",
                "signal": "high trust and stable history",
                "recommended_change": {
                    "execution_pacing": "relax_cautiously",
                    "autonomy": "maintain_supervised",
                },
                "reason": "sustained stability permits careful efficiency gains without removing controls",
                "profile": profile,
            }
        )

    if not suggestions:
        suggestions.append(
            {
                "id": "retain_current_governance_posture",
                "severity": "none",
                "signal": "balanced trust profile",
                "recommended_change": {
                    "governance": "maintain",
                },
                "reason": "current risk indicators do not justify additional tightening or relaxation",
                "profile": profile,
            }
        )

    return {
        "profile": profile,
        "suggestions": suggestions,
        "watchlist_count": len(watchlist),
        "requires_immediate_tightening": any(item["severity"] in {"high", "critical"} for item in suggestions),
        "thresholds": thresholds,
    }


def _summary(rows: tuple[Any, ...]) -> dict[str, Any]:
    by_adapter: dict[str, int] = {}
    by_verification: dict[str, int] = {}
    by_lifecycle_state: dict[str, int] = {}
    by_confidence_trend: dict[str, int] = {}
    confidence_bands = {"low": 0, "moderate": 0, "high": 0}
    rollback_triggered = 0

    for row in rows:
        by_adapter[row.adapter_id] = by_adapter.get(row.adapter_id, 0) + 1
        by_verification[row.verification_result] = by_verification.get(row.verification_result, 0) + 1
        by_lifecycle_state[row.lifecycle_state] = by_lifecycle_state.get(row.lifecycle_state, 0) + 1
        trend = infer_confidence_trend(row.confidence_timeline)
        by_confidence_trend[trend] = by_confidence_trend.get(trend, 0) + 1
        if row.confidence_score < 0.4:
            confidence_bands["low"] += 1
        elif row.confidence_score < 0.75:
            confidence_bands["moderate"] += 1
        else:
            confidence_bands["high"] += 1
        if row.rollback_triggered:
            rollback_triggered += 1

    total = len(rows)
    falling = by_confidence_trend.get("falling", 0)
    oscillating = by_confidence_trend.get("oscillating", 0)
    degrading = by_confidence_trend.get("degrading", 0)
    reliability = _reliability_summary(rows)
    profile_counts: dict[str, int] = {}
    for row in rows:
        profile_counts[row.policy_profile] = profile_counts.get(row.policy_profile, 0) + 1
    active_profile = "prod"
    if profile_counts:
        active_profile = max(sorted(profile_counts.keys()), key=lambda item: profile_counts[item])

    risk = {
        "rollback_frequency": (rollback_triggered / float(total)) if total else 0.0,
        "confidence_decay_rate": (falling / float(total)) if total else 0.0,
        # Volatility proxy uses replayed confidence-trend dynamics.
        "telemetry_volatility_index": ((degrading + oscillating) / float(total)) if total else 0.0,
    }
    governance_suggestions = _governance_suggestions(reliability, risk, active_profile)
    profile_suggestions = {
        profile: _governance_suggestions(reliability, risk, profile)
        for profile in sorted({"dev", "staging", "prod"}.union(profile_counts.keys()))
    }

    return {
        "total_records": total,
        "rollback_triggered": rollback_triggered,
        "by_adapter": by_adapter,
        "by_verification_result": by_verification,
        "by_lifecycle_state": by_lifecycle_state,
        "by_confidence_trend": by_confidence_trend,
        "by_policy_profile": profile_counts,
        "confidence_bands": confidence_bands,
        "reliability_intelligence": reliability,
        "risk_indicators": risk,
        "governance_suggestions": governance_suggestions,
        "governance_suggestions_by_profile": profile_suggestions,
    }


def _overlay_patch_candidates(summary: dict[str, Any], profile: str) -> dict[str, Any]:
    by_profile = summary["governance_suggestions_by_profile"]
    guidance = by_profile[profile]
    candidates: list[dict[str, Any]] = []

    for item in guidance["suggestions"]:
        suggestion_id = item["id"]
        if suggestion_id == "require_approval_on_rollback_instability":
            candidates.append(
                {
                    "action": "set_default_approval",
                    "target": "policy.defaults",
                    "patch": {
                        "approval_state": "supervised_required",
                        "autonomy": "restricted",
                    },
                    "reason": item["reason"],
                    "severity": item["severity"],
                }
            )
        elif suggestion_id == "extend_telemetry_window_for_volatility":
            candidates.append(
                {
                    "action": "extend_telemetry_requirements",
                    "target": "policy.telemetry",
                    "patch": {
                        "window_mode": "extended",
                        "verification_depth": "increase",
                    },
                    "reason": item["reason"],
                    "severity": item["severity"],
                }
            )
        elif suggestion_id == "tighten_scope_on_confidence_decay":
            candidates.append(
                {
                    "action": "tighten_capability_scope",
                    "target": "policy.capability_scope",
                    "patch": {
                        "scope_mode": "tighten",
                        "approval_state": "supervised_required",
                    },
                    "reason": item["reason"],
                    "severity": item["severity"],
                }
            )
        elif suggestion_id == "reduce_autonomy_for_watchlist_adapters":
            for adapter_id in item.get("adapters", []):
                candidates.append(
                    {
                        "action": "set_adapter_override",
                        "target": f"policy.adapters.{adapter_id}",
                        "adapter_id": adapter_id,
                        "patch": {
                            "approval_state": "supervised_required",
                            "autonomy": "trust_constrained",
                        },
                        "reason": item["reason"],
                        "severity": item["severity"],
                    }
                )
        elif suggestion_id == "cautiously_relax_pacing_for_stable_history":
            candidates.append(
                {
                    "action": "relax_execution_pacing",
                    "target": "policy.execution",
                    "patch": {
                        "execution_pacing": "relax_cautiously",
                        "autonomy": "maintain_supervised",
                    },
                    "reason": item["reason"],
                    "severity": item["severity"],
                }
            )

    if not candidates:
        candidates.append(
            {
                "action": "no_change",
                "target": "policy",
                "patch": {},
                "reason": "no actionable suggestion required for current trust posture",
                "severity": "none",
            }
        )

    return {
        "profile": profile,
        "patch_format_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "risk_indicators": summary["risk_indicators"],
            "average_reliability_score": summary["reliability_intelligence"]["average_reliability_score"],
            "watchlist_count": guidance["watchlist_count"],
            "requires_immediate_tightening": guidance["requires_immediate_tightening"],
        },
        "recommended_changes": candidates,
    }


def _workspace_snapshot(rows: tuple[Any, ...], limit: int) -> dict[str, Any]:
    summary = _summary(rows)
    bounded_limit = max(1, int(limit))
    recent_rows = list(rows[-bounded_limit:])[::-1]

    runs: list[dict[str, Any]] = []
    live_events: list[dict[str, Any]] = []

    for row in recent_rows:
        trend = infer_confidence_trend(row.confidence_timeline)
        runs.append(
            {
                "execution_id": row.execution_id,
                "timestamp": row.timestamp,
                "adapter_id": row.adapter_id,
                "capability_id": row.capability_id,
                "policy_profile": row.policy_profile,
                "lifecycle_state": row.lifecycle_state,
                "verification_result": row.verification_result,
                "rollback_triggered": row.rollback_triggered,
                "rollback_outcome": row.rollback_outcome,
                "confidence_score": row.confidence_score,
                "confidence_trend": trend,
            }
        )
        for event in row.lifecycle_timeline:
            live_events.append(
                {
                    "execution_id": row.execution_id,
                    "adapter_id": row.adapter_id,
                    "timestamp": event.timestamp,
                    "from_state": event.from_state,
                    "to_state": event.to_state,
                    "reason": event.reason,
                }
            )

    live_events.sort(key=lambda item: item["timestamp"], reverse=True)
    live_events = live_events[: max(10, bounded_limit * 5)]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status_bar": {
            "active_profile": summary["governance_suggestions"]["profile"],
            "requires_immediate_tightening": summary["governance_suggestions"]["requires_immediate_tightening"],
            "watchlist_count": summary["governance_suggestions"]["watchlist_count"],
            "total_records": summary["total_records"],
        },
        "navigation": {
            "sections": [
                "chat",
                "runs",
                "replay",
                "telemetry",
                "governance",
                "trust",
                "adapters",
                "ide",
                "sentinel",
            ]
        },
        "runs": runs,
        "replay_index": [
            {
                "execution_id": item["execution_id"],
                "adapter_id": item["adapter_id"],
                "capability_id": item["capability_id"],
                "lifecycle_state": item["lifecycle_state"],
                "confidence_score": item["confidence_score"],
            }
            for item in runs
        ],
        "telemetry_center": {
            "risk_indicators": summary["risk_indicators"],
            "by_confidence_trend": summary["by_confidence_trend"],
            "confidence_bands": summary["confidence_bands"],
        },
        "governance_center": {
            "active": summary["governance_suggestions"],
            "projections": summary["governance_suggestions_by_profile"],
            "overlay_patch_candidate": _overlay_patch_candidates(
                summary,
                summary["governance_suggestions"]["profile"],
            ),
        },
        "trust_center": summary["reliability_intelligence"],
        "live_event_stream": live_events,
    }


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    ledger = ExecutionAuditLedger(args.ledger)
    all_rows = ledger.list_records()
    rows = ledger.list_records(
        execution_id=getattr(args, "execution_id", None),
        adapter_id=getattr(args, "adapter_id", None),
        lifecycle_state=getattr(args, "lifecycle_state", None),
    )

    if args.command == "list":
        output = _records_to_dict(rows)
        if args.json_output:
            print(json.dumps({"records": output}, indent=2))
            return 0

        if not output:
            print("No records found.")
            return 0

        for item in output:
            print(
                f"{item['timestamp']} execution_id={item['execution_id']} adapter={item['adapter_id']} "
                f"capability={item['capability_id']} verification={item['verification_result']} "
                f"lifecycle={item['lifecycle_state']} rollback_triggered={item['rollback_triggered']}"
            )
        return 0

    if args.command == "replay":
        if not rows:
            print(f"No records found for execution_id={args.execution_id}")
            return 1
        record = rows[-1]
        historical_reliability = evaluate_reliability(all_rows, adapter_id=record.adapter_id, capability_id=record.capability_id)
        replay_payload = replay_execution_record(record, historical_reliability_score=historical_reliability.reliability_score)
        if args.json_output:
            print(json.dumps(replay_payload, indent=2))
            return 0

        print(
            f"execution_id={replay_payload['execution_id']} adapter={replay_payload['adapter_id']} "
            f"capability={replay_payload['capability_id']} final_state={replay_payload['final_lifecycle_state']} "
            f"confidence={replay_payload['final_confidence_score']:.3f} trend={replay_payload['confidence_trend']} "
            f"historical_reliability={replay_payload['historical_reliability_score']:.3f}"
        )
        print("lifecycle_timeline:")
        for step in replay_payload["lifecycle_timeline"]:
            print(
                f"  {step['timestamp']} {step['from_state']} -> {step['to_state']} "
                f"elapsed={step['elapsed_since_previous_seconds']} reason={step['reason']}"
            )
        return 0

    if args.command == "learn":
        if args.adapter_id:
            score = evaluate_reliability(rows, adapter_id=args.adapter_id, capability_id=args.capability_id)
        elif rows:
            merged_records = tuple(rows)
            score = evaluate_reliability(merged_records, adapter_id=merged_records[0].adapter_id, capability_id=args.capability_id)
        else:
            score = evaluate_reliability(rows, adapter_id="*", capability_id=args.capability_id)

        output = reliability_score_to_dict(score)
        if args.json_output:
            print(json.dumps(output, indent=2))
            return 0

        print(
            f"adapter={output['adapter_id']} capability={output['capability_id']} samples={output['sample_count']} "
            f"reliability={output['reliability_score']:.3f} governance_effectiveness={output['governance_effectiveness_score']:.3f}"
        )
        print(f"success_rate={output['success_rate']:.3f} rollback_rate={output['rollback_rate']:.3f} halt_rate={output['halt_rate']:.3f}")
        print(f"recommendation={output['recommendation']}")
        for reason in output["reasons"]:
            print(f"  - {reason}")
        return 0

    if args.command == "suggest-overlay-patch":
        summary_for_patch = _summary(rows)
        target_profile = args.profile or summary_for_patch["governance_suggestions"]["profile"]
        patch_payload = _overlay_patch_candidates(summary_for_patch, target_profile)

        if args.json_output:
            print(json.dumps(patch_payload, indent=2))
            return 0

        print(f"profile={patch_payload['profile']} generated_at={patch_payload['generated_at']}")
        print(f"requires_immediate_tightening={patch_payload['source']['requires_immediate_tightening']}")
        print("recommended_changes:")
        for item in patch_payload["recommended_changes"]:
            print(f"  - action={item['action']} target={item['target']} severity={item['severity']}")
        return 0

    if args.command == "workspace-snapshot":
        snapshot = _workspace_snapshot(rows, args.limit)
        if args.json_output:
            print(json.dumps(snapshot, indent=2))
            return 0

        print(f"generated_at={snapshot['generated_at']}")
        print(
            "status="
            f"profile={snapshot['status_bar']['active_profile']} "
            f"tightening={snapshot['status_bar']['requires_immediate_tightening']} "
            f"watchlist={snapshot['status_bar']['watchlist_count']}"
        )
        print(f"runs={len(snapshot['runs'])} live_events={len(snapshot['live_event_stream'])}")
        return 0

    summary = _summary(rows)
    if args.json_output:
        print(json.dumps(summary, indent=2))
        return 0

    print(f"total_records={summary['total_records']}")
    print(f"rollback_triggered={summary['rollback_triggered']}")
    print("by_adapter:")
    for key, value in sorted(summary["by_adapter"].items()):
        print(f"  {key}: {value}")
    print("by_verification_result:")
    for key, value in sorted(summary["by_verification_result"].items()):
        print(f"  {key}: {value}")
    print("by_lifecycle_state:")
    for key, value in sorted(summary["by_lifecycle_state"].items()):
        print(f"  {key}: {value}")
    print("by_confidence_trend:")
    for key, value in sorted(summary["by_confidence_trend"].items()):
        print(f"  {key}: {value}")
    print("by_policy_profile:")
    for key, value in sorted(summary["by_policy_profile"].items()):
        print(f"  {key}: {value}")
    print("confidence_bands:")
    for key, value in sorted(summary["confidence_bands"].items()):
        print(f"  {key}: {value}")
    print("reliability_intelligence:")
    print(f"  average_reliability_score: {summary['reliability_intelligence']['average_reliability_score']:.3f}")
    print(f"  average_governance_effectiveness: {summary['reliability_intelligence']['average_governance_effectiveness']:.3f}")
    print("  autonomy_readiness:")
    for key, value in sorted(summary["reliability_intelligence"]["autonomy_readiness"].items()):
        print(f"    {key}: {value}")
    print("  top_adapters:")
    for item in summary["reliability_intelligence"]["top_adapters"]:
        print(
            f"    {item['adapter_id']}: reliability={item['reliability_score']:.3f} "
            f"governance_effectiveness={item['governance_effectiveness_score']:.3f}"
        )
    print("risk_indicators:")
    print(f"  rollback_frequency: {summary['risk_indicators']['rollback_frequency']:.3f}")
    print(f"  confidence_decay_rate: {summary['risk_indicators']['confidence_decay_rate']:.3f}")
    print(f"  telemetry_volatility_index: {summary['risk_indicators']['telemetry_volatility_index']:.3f}")
    print("governance_suggestions:")
    print(f"  profile: {summary['governance_suggestions']['profile']}")
    print(f"  watchlist_count: {summary['governance_suggestions']['watchlist_count']}")
    print(f"  requires_immediate_tightening: {summary['governance_suggestions']['requires_immediate_tightening']}")
    for item in summary["governance_suggestions"]["suggestions"]:
        print(f"  - {item['id']} severity={item['severity']} signal={item['signal']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
