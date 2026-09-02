//! Human mention notification hook: resolves @-mention sources to human
//! participants and spawns fire-and-forget notifications.

use std::sync::Arc;

use bcs_domain::routing::RouteParticipantOverlay;
use bcs_domain::{ActorKind, ActorStatus};
use bcs_service_api::port::{HumanMentionNotifyPort, MentionNotification, MentionedHuman};

/// Context needed to assemble a [`MentionNotification`].
pub(crate) struct MentionNotifyContext {
    pub session_id: String,
    pub group_id: String,
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
/// one human and the port is available. Errors are logged by the port adapter.
pub(crate) fn spawn_human_mention_notify(
    port: &Option<Arc<dyn HumanMentionNotifyPort>>,
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
    let notification = MentionNotification {
        session_id: context.session_id,
        group_id: context.group_id,
        sender_actor_id: context.sender_actor_id,
        sender_label: context.sender_label,
        mentioned: humans,
        message_text: context.message_text,
        timestamp_ms: context.timestamp_ms,
    };
    let port = port.clone();
    tokio::spawn(async move {
        let _ = port.notify_mentioned_humans(notification).await;
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn overlay_entry(
        bot_uuid: &str,
        bot_name: Option<&str>,
        actor_kind: ActorKind,
        status: ActorStatus,
    ) -> RouteParticipantOverlay {
        RouteParticipantOverlay {
            bot_uuid: bot_uuid.to_string(),
            bot_name: bot_name.map(str::to_string),
            actor_kind,
            mode: None,
            status,
            is_driver: false,
        }
    }

    fn overlay() -> Vec<RouteParticipantOverlay> {
        vec![
            overlay_entry("bot-driver", Some("Driver"), ActorKind::Bot, ActorStatus::Online),
            overlay_entry("human_1", Some("Human One"), ActorKind::Human, ActorStatus::Online),
            overlay_entry("human_2", Some("Hidden Human"), ActorKind::Human, ActorStatus::Hidden),
        ]
    }

    #[test]
    fn trigger_resolves_human_mentions() {
        let ids = vec!["human_1".to_string(), "bot-driver".to_string()];
        let trigger = build_mention_trigger(&ids, &overlay(), "bot-driver").expect("trigger");
        assert_eq!(trigger.len(), 1);
        assert_eq!(trigger[0].actor_id, "human_1");
        assert_eq!(trigger[0].display_name, "Human One");
    }

    #[test]
    fn trigger_excludes_self_mention() {
        let ids = vec!["human_1".to_string()];
        assert!(build_mention_trigger(&ids, &overlay(), "human_1").is_none());
    }

    #[test]
    fn trigger_skips_hidden_humans() {
        let ids = vec!["human_2".to_string()];
        assert!(build_mention_trigger(&ids, &overlay(), "bot-driver").is_none());
    }

    #[test]
    fn trigger_none_for_bot_only_or_unknown_mentions() {
        let overlay = overlay();
        assert!(build_mention_trigger(&["bot-driver".to_string()], &overlay, "human_1").is_none());
        assert!(build_mention_trigger(&["unknown".to_string()], &overlay, "bot-driver").is_none());
        assert!(build_mention_trigger(&[], &overlay, "bot-driver").is_none());
    }

    #[test]
    fn trigger_deduplicates_repeated_mentions() {
        let ids = vec!["human_1".to_string(), "human_1".to_string()];
        let trigger = build_mention_trigger(&ids, &overlay(), "bot-driver").expect("trigger");
        assert_eq!(trigger.len(), 1);
    }
}
