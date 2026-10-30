//! Owner→caller identity relay state hand-instantiated with the composition
//! (`caller_relay_ledger` owns the capture semantics). DEFAULT-ON with a
//! single opt-out: no per-edge policy exists — the target side is governed
//! by the bot's own caller-enabled configuration (the §15.4 anti-downgrade
//! guard refuses everything else) and the bounds are TTL, per-turn budget
//! and epoch seals. Absent state (the wiring's only disabled shape, an
//! explicit `caller_identity.relay.enabled = false`) keeps every session's
//! today-behavior fail-closed.

use std::sync::Arc;

use crate::caller_relay_ledger::CallerRelayLedger;

/// Capture window default: twice the default caller JWT TTL — same scale as
/// the handoff's published reaping horizon, so a captured identity lives no
/// longer than the credential it would mint.
pub const DEFAULT_RELAY_CAPTURE_TTL_MS: i64 = 600_000;

pub struct CallerRelayState {
    pub ledger: Arc<CallerRelayLedger>,
    pub capture_ttl_ms: i64,
}

impl CallerRelayState {
    pub fn new(capture_ttl_ms: i64) -> Self {
        assert!(capture_ttl_ms > 0, "relay capture ttl must be positive");
        Self {
            // Same order of magnitude as the handoff slot map (256): one
            // live capture per active human session.
            ledger: Arc::new(CallerRelayLedger::new(256)),
            capture_ttl_ms,
        }
    }

    /// Per-edge relay decision. No per-edge policy exists (module doc):
    /// the middle hop never touches identity, the target side is already
    /// fenced by the §15.4 caller-enabled anti-downgrade guard, and the
    /// deployment's only opt-out is not carrying this state at all
    /// (`caller_identity.relay.enabled = false`). Every (middle, target)
    /// edge reaching an assembled relay is therefore allowed.
    pub fn allows_relay_edge(&self, _middle_bot: &str, _target_bot: &str) -> bool {
        true
    }
}

/// The production `CallerRelayRedeemerPort`: the owner→caller relay's
/// orchestration-submission adapter (design 2026-10-30). Zero policy here —
/// the run's Execution-policy assignees are caller-enabled BY construction
/// (that is what made them Execution), and the session ledger owns the
/// capture with its own TTL/budget bounds. Identity material never leaves
/// BCS: the submitting bot carries only the plain command.
pub struct CallerRelaySubmissionRedeemer {
    pub ledger: Arc<CallerRelayLedger>,
}

impl CallerRelaySubmissionRedeemer {
    pub fn new(ledger: Arc<CallerRelayLedger>) -> Self {
        Self { ledger }
    }
}

#[async_trait::async_trait]
impl bcs_service_api::port::CallerRelayRedeemerPort for CallerRelaySubmissionRedeemer {
    async fn redeem_submission(
        &self,
        environment: &str,
        _group_id: &str,
        session_id: &str,
        _submitted_by_bot: &str,
        _execution_assignees: &[String],
        now_ms: i64,
    ) -> Option<bcs_service_api::port::CallerRelayCapture> {
        let (_turn, ingress, expires_at_ms) =
            self.ledger.redeem_capture(environment, session_id, now_ms)?;
        Some(bcs_service_api::port::CallerRelayCapture { ingress, expires_at_ms })
    }
}
