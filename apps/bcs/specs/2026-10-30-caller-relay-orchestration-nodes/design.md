# Owner→caller relay for orchestration (SM/Workflow) nodes — design

Date: 2026-10-30 · Scope: `bcs-collaboration-runtime` seam · Status: proposed

## Problem

`H → bot-A(owner) → caller-node-B` inside StateMachine/Workflow groups.
Today the §8.5 bridge is complete — instance association → root lineage →
per-node §8.5 reservation → Execution assignee's material ledger — but the
bridge is only entered by Human-direct submissions: the HTTP adapter builds
`OrchestrationInstanceAssociation` exclusively from the authenticated-human
ingress chain and "Bot-only submissions keep None and the run executes
caller-closed" (`bcs-http/src/routes/collaboration_runs.rs:150-158`). A
workflow submitted (or auto-started) by an Owner bot therefore carries no
association and every Execution-policy caller node refuses closed at the
protected wire (`orchestration.rs` → `Error::LineageUnavailable`).

## Non-goal

No new per-node identity mechanism. The §8.5 pipeline stays untouched: one
relay redeem per SUBMISSION, the existing bridge runs verbatim.

## Design

### Reuse (zero new work)

- **Capture**: the deployment-wide `CallerRelayLedger` already captures the
  session's current human ingress on any web_send/group_chat speech with an
  allowlisted `from_bot` participant. SM/Workflow groups get it for free.
- **Bridge**: `register_instance` → lineage → `dispatch_orchestration_node`
  → Execution material — all unchanged.
- **Term sealing**: the ledger is already wiped in `CallerTermState::seal_for_new_epoch`.

### New seam 1 — port (service-api, `port/orchestration_caller.rs`)

```rust
pub trait CallerRelayRedeemerPort: Send + Sync {
    /// One relay redeem for a bot-submitted workflow: the deployment
    /// allowlist must name the submitting bot (`from_bot`) AND the
    /// submission's Execution assignees must all fall inside that
    /// from_bot's `to_bot` side; the session must hold a live capture.
    /// None = refused fail-closed (no capture / expired / unallowed).
    async fn redeem_submission(
        &self, environment: &str, group_id: &str, session_id: &str,
        submitted_by_bot: &str, execution_assignees: &[String], now_ms: i64,
    ) -> Option<CallerRelayCapture>;
}
pub struct CallerRelayCapture {
    pub ingress: AuthenticatedHumanCallerIngress,
    /// min(capture window, instance ledger horizon) — a run-budget fact.
    pub expires_at_ms: i64,
}
```

The `execution_assignees` argument fronts the to-side gate at SUBMISSION
time: every Execution-policy assignee of the parsed definition must be
allowlisted, or the whole submission is refused. This keeps the trust
decision static and leaves `orchestration.rs` and `RootLineage` untouched.

### New seam 2 — runtime submission branch (`runtime.rs`, at the
`cmd.caller_association` else-arm, ~:3248)

`cmd.caller_association.is_none()` + a relay redeemer attached +
`cmd.caller_bot_id` is a deployment-allowlisted submitter:

1. `redeem_submission(env, group, session, cmd.caller_bot_id, execution_assignees, now)`.
2. `None` + the definition carries Execution nodes → **refuse the whole
   submission** (`InvalidOperation`: "no captured session identity / caller
   nodes not allowlisted for relay") — fail-fast beats a caller-closed run
   failing node-by-node. Relay-disabled compositions keep today's accept-
   caller-closed behavior byte-for-byte.
3. `Some(capture)` → build the association exactly like the trusted adapter
   does (stamped by the runtime: run id, group, session, mega-turn,
   `expires_at_ms = capture.expires_at_ms`) and `register_instance` along
   the existing path.

Auto-start (`auto_start_on_service_invocation`) routes through the same
branch with the triggering bot as `submitted_by_bot`.

### Wiring (`message_delivery_wiring.rs`)

A `CallerRelayRedeemer` adapter over `CallerRelayState` (allowlist judgement
+ ledger redeem) handed to the collaboration runtime assembly next to the
existing `OrchestrationInstanceRegistrar` injection. Absent relay state ⇒
no redeemer ⇒ behavior unchanged.

## Invariants (all carried from the ManagerWorker relay)

- Identity material travels BCS→BCS only; the middle bot receives nothing.
- Policy decides: exact (from=submitter, to⊇Execution assignees) match.
- B5: expired/unbudgeted captures answer None forever; refusal surfaces at
  submission as an explicit error, never a silent Owner fallback.
- §8.5 budget/depth machinery is authoritative as-is.

## Known limitation (accepted, matches existing behavior)

`history_cutoff_seq = 0` — identical to today's Human-direct HTTP
submission (the adapter also stamps 0). §15.41 tightening is a separate
work item for BOTH paths, deliberately not folded in here.

## Tests

1. Runtime conformance (fake redeemer + the existing
   `RecordingInstanceRegistrar` in `runtime_progression/caller_orchestration.rs`):
   bot-submitted run with an Execution caller node + redeem Some →
   association registered → node dispatch reaches the protected wire.
2. Refusal matrix (RED first): relay-disabled accept-caller-closed; absent
   capture → submission refused; assignee outside the to-side → submission
   refused; expired capture → refused.
3. Regression: Human-direct submission behavior unchanged; delegation-only
   middle-hop stays closed when relay state is absent.

## Effort

Port + runtime branch + adapter + wiring + conformance ≈ 1 focused day.
No migration, no schema, no cross-crate refactors.
