//! Producer output-facts freezing (bot-authority spec §8.2:
//! producer 可见性在准入时冻结，不追溯).
//!
//! A bot Run's output visibility (domain + producer role) must be
//! decided from the SAME admission-time facts that admitted the
//! operation — not re-resolved from current storage at the first
//! callback. This module seeds the tracker's per-run fact snapshot
//! BEFORE the run's first downstream event, so a membership or role
//! change landing between accept and first frame never reclassifies
//! already-admitted output (e.g. a removed Manager's chat stays
//! Public for the operation it was admitted under).

use super::*;

/// Load the Session snapshot ONCE for a whole send (one read per
/// send, never per run and never per frame). `None` when the send
/// is not session-anchored or the Session cannot be read (the
/// Group participant rows then decide, exactly like the event-time
/// resolver's fallback).
pub(super) async fn session_snapshot_for_freeze(
    flow: &BcsMessageFlow,
    session_id: Option<&str>,
) -> Option<bcs_service_api::Session> {
    match (session_id, flow.session_management.as_ref()) {
        (Some(session_id), Some(session_management)) => session_management
            .get(session_id)
            .await
            .ok()
            .flatten(),
        _ => None,
    }
}

/// Freeze the immutable producer facts of ONE admitted run into the
/// tracker's run-info cache, under the same key the event-time
/// resolver consults — the first callback therefore resolves from
/// THIS snapshot instead of re-reading current Group/Session rows.
///
/// Mirrors `resolve_bot_event_run_info` at admission time: domain
/// from the Group strategy, producer row from the Session when the
/// run's events will carry that Session (the same id the send
/// registered into its send context), else from the Group.
pub(super) async fn freeze_run_output_facts(
    flow: &BcsMessageFlow,
    group: &Group,
    session: Option<&bcs_service_api::Session>,
    run_id: &str,
    producer_actor_id: &str,
) {
    let visibility_domain = match group.group_strategy {
        GroupStrategy::Chat => MessageVisibilityDomain::Chat,
        GroupStrategy::ManagerWorker => MessageVisibilityDomain::ManagerWorker,
        GroupStrategy::StateMachine => MessageVisibilityDomain::StateMachine,
    };
    let mut participant = group.get_participant(producer_actor_id).cloned();
    if let Some(session) = session.filter(|session| session.group_id == group.id) {
        participant = session
            .participants
            .iter()
            .find(|candidate| candidate.bot_uuid == producer_actor_id)
            .cloned();
    }
    flow.message_tracker
        .cache_bot_event_run_info(
            run_id,
            crate::message_tracker::BotEventRunInfo {
                group_id: group.id.clone(),
                bot_id: producer_actor_id.to_string(),
                visibility_domain,
                participant,
            },
        )
        .await;
}
