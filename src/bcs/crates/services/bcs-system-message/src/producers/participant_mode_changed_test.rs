//! Unit tests for `ParticipantModeChangedMessageProducer`.

use bcs_domain::{
    ActorKind, Group, Participant, ParticipantMode, ParticipantRole, SystemMessageEvent,
};
use bcs_service_api::SystemMessageProducerService;

use super::participant_mode_changed::ParticipantModeChangedMessageProducer;

fn make_group_with_bots_and_human() -> Group {
    let driver = Participant::bot("driver-id", ParticipantRole::Driver);
    let consultant = Participant::bot("consultant-id", ParticipantRole::Consultant);
    let human = Participant::human("human-id", ParticipantRole::Observer);
    Group::new("group-1", "driver-id", vec![driver, consultant, human])
}

#[tokio::test]
async fn human_joined_from_none_produces_message() {
    let group = make_group_with_bots_and_human();
    let producer = ParticipantModeChangedMessageProducer;
    let event = SystemMessageEvent::ParticipantModeChanged {
        group_id: "group-1".to_string(),
        actor_id: "human-id".to_string(),
        actor_name: "Alice".to_string(),
        actor_kind: ActorKind::Human,
        from: None,
        to: ParticipantMode::Present,
    };

    let (messages, user_message) = producer
        .produce(&event, &group, &bcs_test_support::NoopBotRegistryCoreService, &group.participants)
        .await;
    assert_eq!(messages.len(), 1);
    assert!(messages[0].message.contains("用户 Alice 已加入协作群"));
    assert!(messages[0].recipients.contains(&"driver-id".to_string()));
    assert!(messages[0].recipients.contains(&"consultant-id".to_string()));
    assert!(!messages[0].recipients.contains(&"human-id".to_string()));
    assert_eq!(messages[0].delivery_type, bcs_domain::DeliveryType::Inject);
    assert_eq!(user_message, Some(messages[0].message.clone()));
}

#[tokio::test]
async fn human_mode_change_produces_message() {
    let group = make_group_with_bots_and_human();
    let producer = ParticipantModeChangedMessageProducer;
    let event = SystemMessageEvent::ParticipantModeChanged {
        group_id: "group-1".to_string(),
        actor_id: "human-id".to_string(),
        actor_name: "Alice".to_string(),
        actor_kind: ActorKind::Human,
        from: Some(ParticipantMode::Present),
        to: ParticipantMode::Absent,
    };

    let (messages, user_message) = producer
        .produce(&event, &group, &bcs_test_support::NoopBotRegistryCoreService, &group.participants)
        .await;
    assert_eq!(messages.len(), 1);
    assert!(messages[0].message.contains("用户 Alice 已退出协作群"));
    assert!(messages[0].recipients.contains(&"driver-id".to_string()));
    assert!(messages[0].recipients.contains(&"consultant-id".to_string()));
    assert!(!messages[0].recipients.contains(&"human-id".to_string()));
    assert_eq!(messages[0].delivery_type, bcs_domain::DeliveryType::Inject);
    assert_eq!(user_message, Some(messages[0].message.clone()));
}

#[tokio::test]
async fn bot_joined_from_none_produces_empty() {
    let group = make_group_with_bots_and_human();
    let producer = ParticipantModeChangedMessageProducer;
    let event = SystemMessageEvent::ParticipantModeChanged {
        group_id: "group-1".to_string(),
        actor_id: "new-bot-id".to_string(),
        actor_name: "NewBot".to_string(),
        actor_kind: ActorKind::Bot,
        from: None,
        to: ParticipantMode::Auto,
    };

    let (messages, user_message) = producer
        .produce(&event, &group, &bcs_test_support::NoopBotRegistryCoreService, &group.participants)
        .await;
    assert!(messages.is_empty());
    assert_eq!(user_message, None, "anomalous from=None Bot yields no user_message");
}

#[tokio::test]
async fn bot_mode_change_produces_message() {
    let group = make_group_with_bots_and_human();
    let producer = ParticipantModeChangedMessageProducer;
    let event = SystemMessageEvent::ParticipantModeChanged {
        group_id: "group-1".to_string(),
        actor_id: "consultant-id".to_string(),
        actor_name: "Consultant".to_string(),
        actor_kind: ActorKind::Bot,
        from: Some(ParticipantMode::Auto),
        to: ParticipantMode::Muted,
    };

    let (messages, user_message) = producer
        .produce(&event, &group, &bcs_test_support::NoopBotRegistryCoreService, &group.participants)
        .await;
    assert_eq!(messages.len(), 1);
    assert!(messages[0].message.contains("Bot Consultant 已切换成禁言模式"));
    assert!(messages[0].recipients.contains(&"driver-id".to_string()));
    assert!(messages[0].recipients.contains(&"consultant-id".to_string()));
    assert!(!messages[0].recipients.contains(&"human-id".to_string()));
    assert_eq!(messages[0].delivery_type, bcs_domain::DeliveryType::Inject);
    assert_eq!(user_message, Some(messages[0].message.clone()));
}
