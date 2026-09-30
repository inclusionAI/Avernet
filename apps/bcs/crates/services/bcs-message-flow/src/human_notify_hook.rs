//! Human mention notification hook: resolves @-mention sources to human
//! participants and spawns fire-and-forget notifications.

use std::sync::Arc;

use bcs_domain::routing::RouteParticipantOverlay;
use bcs_domain::{ActorKind, ActorStatus};
use bcs_service_api::port::{HumanMentionNotifyPort, MentionNotification, MentionedHuman};
use bcs_service_api::{GroupCoreService, SessionManagementService};

/// Context needed to assemble a [`MentionNotification`].
pub(crate) struct MentionNotifyContext {
    pub session_id: String,
    pub group_id: String,
    pub group_name: Option<String>,
    pub sender_actor_id: String,
    pub sender_label: String,
    pub message_text: String,
    pub timestamp_ms: u64,
}

/// Resolve a mention source (text-parsed or explicit actor ids) to the human
/// participants it refers to. Excludes the sender and Hidden humans, and
/// drops ids that are not participants.
pub(crate) fn build_mention_trigger(
    mention_actor_ids: &[String],
    overlay: &[RouteParticipantOverlay],
    sender_actor_id: &str,
) -> Option<Vec<MentionedHuman>> {
    let mut humans: Vec<MentionedHuman> = Vec::new();
    for actor_id in mention_actor_ids {
        if actor_id == sender_actor_id {
            continue;
        }
        let Some(entry) = overlay.iter().find(|entry| entry.bot_uuid == *actor_id) else {
            continue;
        };
        if entry.actor_kind != ActorKind::Human {
            continue;
        }
        if entry.status == ActorStatus::Hidden {
            continue;
        }
        if humans.iter().any(|human| human.actor_id == *actor_id) {
            continue;
        }
        let display_name = entry
            .bot_name
            .clone()
            .unwrap_or_else(|| actor_id.clone());
        humans.push(MentionedHuman {
            actor_id: actor_id.clone(),
            display_name,
        });
    }
    if humans.is_empty() {
        None
    } else {
        Some(humans)
    }
}

/// Spawn a fire-and-forget notification when the trigger resolves to at least
/// one human, the port is available, and the Group's current authoritative
/// notify policy allows the sender. The policy read is a dedicated uncached
/// `GroupCoreService::read_human_notify_policy` call: it happens only after
/// the in-memory eligibility checks, it never trusts a request-time copy of
/// mode/driver, and it is fail-closed (missing Group or read error logs a
/// warning and skips the external notification without failing the message
/// flow). Errors are logged by the port adapter.
pub(crate) async fn spawn_human_mention_notify(
    groups: &dyn GroupCoreService,
    port: &Option<Arc<dyn HumanMentionNotifyPort>>,
    sessions: &Option<Arc<dyn SessionManagementService>>,
    mention_actor_ids: Option<&[String]>,
    overlay: &[RouteParticipantOverlay],
    context: MentionNotifyContext,
) {
    let Some(port) = port.as_ref().filter(|port| port.is_available()) else {
        return;
    };
    let Some(mention_actor_ids) = mention_actor_ids else {
        return;
    };
    let Some(humans) = build_mention_trigger(mention_actor_ids, overlay, &context.sender_actor_id)
    else {
        return;
    };
    let policy = match groups.read_human_notify_policy(&context.group_id).await {
        Ok(Some(policy)) => policy,
        Ok(None) | Err(_) => {
            tracing::warn!(
                group_id = %context.group_id,
                sender_actor_id = %context.sender_actor_id,
                "current human-notify policy unavailable; external notification skipped"
            );
            return;
        }
    };
    if !policy
        .mode
        .allows_external_notify(&context.sender_actor_id, &policy.driver_bot_id)
    {
        tracing::debug!(
            group_id = %context.group_id,
            sender_actor_id = %context.sender_actor_id,
            mode = ?policy.mode,
            "human mention external notification suppressed by group policy"
        );
        return;
    }
    let mut notification = MentionNotification {
        session_id: context.session_id,
        group_id: context.group_id,
        group_name: context.group_name,
        session_name: None,
        sender_actor_id: context.sender_actor_id,
        sender_label: context.sender_label,
        mentioned: humans,
        message_text: context.message_text,
        timestamp_ms: context.timestamp_ms,
    };
    let port = port.clone();
    let sessions = sessions.clone();
    tokio::spawn(async move {
        // Resolve optional display metadata off the message's critical path and
        // only for an actual notification. Never borrow a title from another group.
        if let Some(sessions) = sessions.filter(|_| !notification.session_id.is_empty()) {
            match sessions.get(&notification.session_id).await {
                Ok(Some(session)) if session.group_id == notification.group_id => {
                    notification.session_name = session.session_title;
                }
                Ok(_) => {}
                Err(error) => {
                    tracing::warn!(
                        session_id = %notification.session_id,
                        %error,
                        "failed to load session title for human mention notification"
                    );
                }
            }
        }
        let _ = port.notify_mentioned_humans(notification).await;
    });
}

#[cfg(test)]
#[path = "human_notify_hook_tests.rs"]
mod tests;
