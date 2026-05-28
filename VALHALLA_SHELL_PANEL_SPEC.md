# Valhalla Shell Panel Spec

## Goal
Shift the product from chat-first to operations-first UI using the unified workspace-snapshot payload as the primary contract.

## Backend Contract
Primary source:
- CLI command: python3 -m andie_adapters.audit_ledger_cli workspace-snapshot --limit 20 --json

Intended HTTP mirror:
- GET /api/workspace-snapshot

Expected payload sections:
- generated_at
- status_bar
- navigation
- runs
- replay_index
- telemetry_center
- governance_center
- trust_center
- live_event_stream

## Shell Layout

```
Top: Status Bar
Body: Left Nav | Runs Timeline (primary) | Active Detail Pane (tabs)
Bottom: Live Event Strip
Docked: Operational Copilot (Chat)
```

## Global UI State
- selectedRunId: string | null
- selectedRun: object | null (derived from runs by selectedRunId)
- activeRightTab: telemetry | governance | trust | chat
- snapshot: workspace-snapshot payload
- snapshotUpdatedAt: timestamp
- snapshotLoading: boolean
- snapshotError: string | null

## Data Fetching
- Poll /api/workspace-snapshot every 10 seconds.
- Use stale-while-refresh behavior:
  - keep previous snapshot rendered while fetching
  - only replace after successful parse
- On poll failure:
  - preserve last good snapshot
  - show non-blocking stale badge in status bar
- Default selectedRun rule:
  - if selectedRunId exists in new snapshot, keep it
  - otherwise select first run from snapshot.runs

## Panel Specs

### 1. Status Bar (Top Chrome)
Bind to: snapshot.status_bar

Required fields:
- active_profile
- requires_immediate_tightening
- watchlist_count
- total_records

UI behavior:
- Show active_profile badge (dev/staging/prod)
- Show tightening state indicator:
  - red when requires_immediate_tightening is true
  - neutral otherwise
- Show watchlist_count and total_records counters
- Show last update time from snapshot.generated_at

Interactions:
- Clicking profile badge switches Governance tab and scrolls to active suggestions
- Clicking tightening indicator filters runs to risky entries (rollback_triggered true OR verification_result failed)

### 2. Left Navigation
Bind to: snapshot.navigation.sections

Render order from payload, expected sections:
- chat
- runs
- replay
- telemetry
- governance
- trust
- adapters
- ide
- sentinel

UI behavior:
- Each section maps to right-pane tab or future workspace route
- Keep runs pane always visible as the central primary lane
- Chat nav item opens docked Operational Copilot panel

### 3. Runs Timeline (Primary Center Pane)
Bind to: snapshot.runs and snapshot.replay_index

Run card fields:
- execution_id
- timestamp
- adapter_id
- capability_id
- policy_profile
- lifecycle_state
- verification_result
- rollback_triggered
- rollback_outcome
- confidence_score
- confidence_trend

Visual rules:
- Color by lifecycle_state and verification_result
- Confidence score shown as a horizontal meter
- Confidence trend rendered as chip (rising/stable/falling/etc)
- Rollback card accent when rollback_triggered true

Interactions:
- Select run updates selectedRunId
- Selection drives all right-pane tabs and chat context
- Double-click opens deep replay view (future route) using replay_index execution_id

### 4. Right Pane Tab: Telemetry
Bind to: snapshot.telemetry_center

Fields:
- risk_indicators.rollback_frequency
- risk_indicators.confidence_decay_rate
- risk_indicators.telemetry_volatility_index
- by_confidence_trend
- confidence_bands

Widgets:
- Risk KPI row (3 KPIs)
- Confidence trend distribution chart
- Confidence band distribution chart

Selected run overlay:
- If selectedRun exists, show run-level confidence_score and confidence_trend above global metrics

### 5. Right Pane Tab: Governance
Bind to: snapshot.governance_center

Fields:
- active
- projections
- overlay_patch_candidate

Active panel:
- active.profile
- active.watchlist_count
- active.requires_immediate_tightening
- active.suggestions[]

Projection panel:
- projections.dev
- projections.staging
- projections.prod

Patch candidate panel:
- overlay_patch_candidate.profile
- overlay_patch_candidate.recommended_changes[]

Interactions:
- Click suggestion opens related patch candidate action row
- Click projection profile swaps patch candidate preview to that profile

### 6. Right Pane Tab: Trust
Bind to: snapshot.trust_center

Fields:
- adapter_rankings[]
- top_adapters[]
- watchlist_adapters[]
- average_reliability_score
- average_governance_effectiveness
- autonomy_readiness

Widgets:
- Trust summary KPIs
- Adapter ranking table
- Watchlist table
- Autonomy readiness donut or stacked bar

Interactions:
- Click adapter row filters runs pane by adapter_id
- Click watchlist adapter auto-switches Governance tab and highlights related suggestions

### 7. Live Event Strip (Bottom)
Bind to: snapshot.live_event_stream

Event fields:
- execution_id
- adapter_id
- timestamp
- from_state
- to_state
- reason

UI behavior:
- Continuous horizontal feed sorted by timestamp descending
- Severity coding:
  - critical: transitions to halted or rollback_failed reason
  - warning: rollback_triggered transitions
  - normal: standard lifecycle transitions

Interactions:
- Clicking event selects related run and opens replay context

### 8. Operational Copilot (Docked Chat)
Chat component: keep existing ChatSurface.jsx unchanged

Shell responsibilities:
- Pass selectedRun context into ChatSurface wrapper props
- Context contract to pass:
  - selectedRun.execution_id
  - selectedRun.adapter_id
  - selectedRun.capability_id
  - selectedRun.lifecycle_state
  - selectedRun.confidence_score
  - selectedRun.confidence_trend
  - selectedRun.rollback_triggered

Behavior:
- If selectedRun changes, chat context updates immediately
- Chat remains docked, not primary pane
- Default prompt helper text should be run-aware, for example:
  - Why did this run require rollback?
  - Explain confidence trend for this execution.

## Error and Empty States

No runs:
- Show centered empty state in runs pane with guidance to execute adapter_runner_cli

Stale snapshot:
- Status bar shows stale badge and last successful generated_at

Malformed payload:
- Render fallback error panel with field-level validation hints

## Minimal Frontend Wiring Sequence
1. Build shell layout and state container.
2. Implement polling + stale handling for /api/workspace-snapshot.
3. Bind status bar and runs pane.
4. Add right-pane tabs (Telemetry, Governance, Trust).
5. Add live event strip.
6. Dock ChatSurface and inject selectedRun context.

## Acceptance Criteria
- Entire shell renders from one snapshot payload.
- Selecting a run updates all dependent panels.
- Polling refreshes data without losing selected run when still present.
- Chat remains functional and receives selected run context.
- Governance and trust views are usable without opening chat.
