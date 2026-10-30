//! Plan-A readiness gate (2026-10 P2): the pre-StartSend probe the delivery
//! runtime consults before SCHEDULING a caller dispatch. The admission
//! reserve()s a row's handoff slot before its queue commit and publish()es
//! the identity only after it, so a scheduler tick can legitimately observe
//! a still-Reserved slot for a committed row. The probe tells the runtime
//! WHICH side of that race the row is on so it parks instead of converging
//! the accepted task permanently failed.

use bcs_service_api::application::caller_handoff::HandoffIdentityReadiness;
use bcs_service_api::application::caller_handoff::HandoffScope;

use super::CallerDispatchPipeline;

/// The single claim-key construction shared by the dispatch claim
/// (`dispatch_once`), the delegation-only registration and the plan-A
/// readiness probe: env + session + group + canonical turn + target.
/// Admission builds the same key on its side; scope drift between the
/// sites is a silent permanent identity loss, so none of them may
/// re-derive this independently.
pub(crate) fn dispatch_handoff_scope(
    environment: &str,
    session_id: &str,
    group_id: Option<&str>,
    message_turn_id: &str,
    target_bot_id: &str,
) -> HandoffScope {
    HandoffScope {
        env: environment.to_string(),
        session_id: session_id.to_string(),
        group_id: group_id.map(str::to_string),
        source_message_id: message_turn_id.to_string(),
        target_bot_id: target_bot_id.to_string(),
    }
}

impl CallerDispatchPipeline {
    /// Plan-A readiness gate probe for the delivery runtime: BEFORE any
    /// StartSend, is this row's dispatch-scope identity already claimable?
    /// `Reserved` means a same-process admission sits between its queue
    /// commit and its publish — the scheduler parks the row this tick
    /// instead of converging it permanently failed. The probe reuses the
    /// exact claim-side scope construction so it can never disagree with
    /// the claim it gates. Costs one in-memory slot lookup; no DB, no IO.
    pub async fn dispatch_identity_readiness(
        &self,
        row: &bcs_domain::message_delivery::PersistedMessageDelivery,
    ) -> HandoffIdentityReadiness {
        self.handoff
            .identity_readiness(&dispatch_handoff_scope(
                &row.env,
                &row.session_id,
                Some(&row.group_id),
                &row.source_message_id,
                &row.target_bot_id,
            ))
            .await
    }
}