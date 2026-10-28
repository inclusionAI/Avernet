use super::*;
use bcs_service_api::{
    BotCapabilities, ChatEventRouting, GroupStrategy, HumanMentionNotifyMode, Participant,
    ParticipantRole, RegisteredBot, ResponseMode, RouteSelectorWire,
};
use bcs_test_support::NoopBotRegistryCoreService;

fn create_test_session() -> Group {
    Group {
        id: "test-session".to_string(),
        label: None,
        status: bcs_service_api::GroupStatus::Active,
        driver_bot: "driver".to_string(),
        originator: None, // Defaults to driver_bot
        routing_policy: None,
        human_mention_notify_mode: HumanMentionNotifyMode::default(),
        context: None,
        opening_message: None,
        participants: vec![
            Participant {
                bot_uuid: "driver".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                actor_kind: bcs_service_api::ActorKind::default(),
                tags: Vec::new(),
                message_view_scope: bcs_service_api::application::v1::MessageViewScope::Full,
                mode: None,
            },
            Participant {
                bot_uuid: "consultant".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                actor_kind: bcs_service_api::ActorKind::default(),
                tags: Vec::new(),
                message_view_scope: bcs_service_api::application::v1::MessageViewScope::Full,
                mode: None,
            },
        ],
        messages: Vec::new(),
        service_group_uuid: None,
        service_mode: None,
        created_at: 0,
        updated_at: 0,
        group_kind: bcs_service_api::GroupKind::default(),
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 1,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
    }
}

// =========================================================================
// I.6: route_with_overlay integration tests (Human Actor V1, X.3 + X.7)
//
// Covers:
//   - mode × status overlay matrix (auto/muted × online/hidden) for Bots
//   - @Human present vs absent behavior on the routing layer
//   - hidden/muted forced-Inject downgrade
//   - mentions list survives present-Human, drops absent-Human
//
// Notes:
//   - X.7 envelope `silent=true` is added by `WorkbenchChannel::dispatch`,
//     not by routing — these tests assert the routing-side contract only
//     (delivery_type downgrade for hidden actors). The dispatch-side
//     `silent` propagation is exercised in higher-level tests.
//   - Sender-side gating (X.1/X.2) is at WS ingress, not routing's job;
//     these tests therefore use sender_bot_id only to exclude the sender
//     from `targets`, not to validate auth.
// =========================================================================

/// Build a 4-bot session for overlay tests:
/// - "driver" (Bot, driver role)
/// - "bot_x"  (Bot, consultant)
/// - "human_alice" (Human)
/// - "sender" (Bot, originator of the message)
fn create_overlay_test_session() -> Group {
    Group {
        id: "ovr-001".to_string(),
        label: None,
        status: bcs_service_api::GroupStatus::Active,
        driver_bot: "driver".to_string(),
        originator: None,
        routing_policy: None,
        human_mention_notify_mode: HumanMentionNotifyMode::default(),
        context: None,
        opening_message: None,
        service_group_uuid: None,
        service_mode: None,
        participants: vec![
            Participant {
                bot_uuid: "driver".to_string(),
                bot_name: Some("Driver Bot".to_string()),
                kind: None,
                role: ParticipantRole::Driver,
                actor_kind: ActorKind::Bot,
                tags: Vec::new(),
                message_view_scope: bcs_service_api::application::v1::MessageViewScope::Full,
                mode: None,
            },
            Participant {
                bot_uuid: "bot_x".to_string(),
                bot_name: Some("Bot X".to_string()),
                kind: None,
                role: ParticipantRole::Consultant,
                actor_kind: ActorKind::Bot,
                tags: Vec::new(),
                message_view_scope: bcs_service_api::application::v1::MessageViewScope::Full,
                mode: None,
            },
            Participant {
                bot_uuid: "human_alice".to_string(),
                bot_name: Some("Alice".to_string()),
                kind: None,
                role: ParticipantRole::Consultant,
                actor_kind: ActorKind::Human,
                tags: Vec::new(),
                message_view_scope: bcs_service_api::application::v1::MessageViewScope::Full,
                mode: Some(ParticipantMode::Present),
            },
            Participant {
                bot_uuid: "sender".to_string(),
                bot_name: Some("Sender Bot".to_string()),
                kind: None,
                role: ParticipantRole::Consultant,
                actor_kind: ActorKind::Bot,
                tags: Vec::new(),
                message_view_scope: bcs_service_api::application::v1::MessageViewScope::Full,
                mode: None,
            },
        ],
        messages: Vec::new(),
        created_at: 0,
        updated_at: 0,
        group_kind: bcs_service_api::GroupKind::default(),
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 1,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
    }
}

/// Convenience: build an overlay row for a Bot. `uuid == "driver"` marks
/// the driver — keep this in sync with `create_overlay_test_session`'s
/// `driver_bot` field so overlay matches participant rows.
fn ov_bot(
    uuid: &str,
    mode: Option<ParticipantMode>,
    status: ActorStatus,
) -> RouteParticipantOverlay {
    RouteParticipantOverlay {
        bot_uuid: uuid.to_string(),
        bot_name: None,
        actor_kind: ActorKind::Bot,
        mode,
        status,
        is_driver: uuid == "driver",
    }
}

/// Convenience: build an overlay row for a Human (Humans are never driver).
fn ov_human(uuid: &str, mode: ParticipantMode, status: ActorStatus) -> RouteParticipantOverlay {
    RouteParticipantOverlay {
        bot_uuid: uuid.to_string(),
        bot_name: None,
        actor_kind: ActorKind::Human,
        mode: Some(mode),
        status,
        is_driver: false,
    }
}

fn target_for<'a>(decision: &'a RoutingDecision, uuid: &str) -> Option<&'a RoutingTarget> {
    decision.targets.iter().find(|t| t.bot_uuid == uuid)
}

fn create_human_bot_dm_session() -> Group {
    let mut session = create_overlay_test_session();
    session.group_kind = GroupKind::Dm;
    session.driver_bot = "bot_x".to_string();
    session.dm_pair_key = Some("bot_x|human_alice".to_string());
    session.participants = vec![
        Participant {
            bot_uuid: "human_alice".to_string(),
            bot_name: Some("Alice".to_string()),
            kind: None,
            role: ParticipantRole::Observer,
            actor_kind: ActorKind::Human,
            tags: Vec::new(),
            message_view_scope: bcs_service_api::application::v1::MessageViewScope::Full,
            mode: Some(ParticipantMode::Present),
        },
        Participant {
            bot_uuid: "bot_x".to_string(),
            bot_name: Some("Bot X".to_string()),
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: ActorKind::Bot,
            tags: Vec::new(),
            message_view_scope: bcs_service_api::application::v1::MessageViewScope::Full,
            mode: Some(ParticipantMode::Auto),
        },
    ];
    session
}

fn create_bot_bot_dm_session() -> Group {
    let mut session = create_overlay_test_session();
    session.group_kind = GroupKind::Dm;
    session.driver_bot = "bot_a".to_string();
    session.dm_pair_key = Some("bot_a|bot_b".to_string());
    session.participants = vec![
        Participant {
            bot_uuid: "bot_a".to_string(),
            bot_name: Some("Bot A".to_string()),
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: ActorKind::Bot,
            tags: Vec::new(),
            message_view_scope: bcs_service_api::application::v1::MessageViewScope::Full,
            mode: Some(ParticipantMode::Auto),
        },
        Participant {
            bot_uuid: "bot_b".to_string(),
            bot_name: Some("Bot B".to_string()),
            kind: None,
            role: ParticipantRole::Consultant,
            actor_kind: ActorKind::Bot,
            tags: Vec::new(),
            message_view_scope: bcs_service_api::application::v1::MessageViewScope::Full,
            mode: Some(ParticipantMode::Auto),
        },
    ];
    session
}


mod broadcast;
mod structured;
mod overlay;
