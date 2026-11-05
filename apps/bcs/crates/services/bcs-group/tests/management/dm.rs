use super::*;

#[tokio::test]
async fn create_dm_creates_and_reuses_human_bot_pair() {
    let fixture = Fixture::new().with_human("human_alice", "Alice").with_bot(
        "assistant",
        "Assistant",
        "public",
        Some("alice"),
    );
    let service = fixture.service_with_limits(5, 10, 10);

    let created = service
        .create_dm(DmCreateCommand {
            group_id: Some("dm-under-test".to_string()),
            caller_actor_id: Some("human_alice".to_string()),
            driver_bot: None,
            target_actor_id: "assistant".to_string(),
            label: None,
            topic: Some("help".to_string()),
            context: Some("dm context".to_string()),
            provisioning: false,
        })
        .await
        .expect("owner human should create Human-Bot DM");

    assert!(created.created);
    assert_eq!(created.group.group_id, "dm-under-test");
    assert_eq!(created.group.group_kind, GroupKind::Dm);
    assert_eq!(created.group.driver_bot_id, "assistant");
    assert_eq!(created.group.context.as_deref(), Some("dm context"));

    let stored = fixture.group.get("dm-under-test").await.unwrap();
    assert_eq!(stored.originator.as_deref(), Some("human_alice"));
    assert_eq!(
        stored.dm_pair_key.as_deref(),
        Some(Group::compute_dm_pair_key("human_alice", "assistant").as_str())
    );
    let human = stored
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == "human_alice")
        .expect("human participant");
    assert_eq!(human.actor_kind, ActorKind::Human);
    assert_eq!(human.mode, Some(bcs_service_api::ParticipantMode::Present));
    assert!(human.tags.is_empty());
    let bot = stored
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == "assistant")
        .expect("bot participant");
    assert_eq!(bot.actor_kind, ActorKind::Bot);
    assert_eq!(bot.role, bcs_service_api::ParticipantRole::Driver);
    assert_eq!(bot.mode, Some(bcs_service_api::ParticipantMode::Auto));

    let reused = service
        .create_dm(DmCreateCommand {
            group_id: Some("dm-ignored".to_string()),
            caller_actor_id: Some("human_alice".to_string()),
            driver_bot: None,
            target_actor_id: "assistant".to_string(),
            label: Some("new label should not overwrite".to_string()),
            topic: None,
            context: Some("new context should not overwrite".to_string()),
            provisioning: false,
        })
        .await
        .expect("second create should reuse canonical pair");
    assert!(!reused.created);
    assert_eq!(reused.group.group_id, "dm-under-test");
    assert_eq!(reused.group.context.as_deref(), Some("dm context"));
}

#[tokio::test]
async fn create_dm_without_explicit_id_uses_dm_namespace() {
    let fixture = Fixture::new().with_human("human_alice", "Alice").with_bot(
        "assistant",
        "Assistant",
        "public",
        Some("alice"),
    );
    let service = fixture.service_with_limits(5, 10, 10);

    let created = service
        .create_dm(DmCreateCommand {
            group_id: None,
            caller_actor_id: Some("human_alice".to_string()),
            driver_bot: None,
            target_actor_id: "assistant".to_string(),
            label: None,
            topic: None,
            context: None,
            provisioning: false,
        })
        .await
        .expect("owner human should create generated Human-Bot DM");

    assert!(created.group.group_id.starts_with("bcs_grp_dm_"));
    assert_eq!(created.group.group_id.chars().count(), 43);
}

#[tokio::test]
async fn create_dm_enforces_human_to_bot_reachability() {
    let protected = Fixture::new()
        .with_human("human_bob", "Bob")
        .with_bot("protected", "Protected", "protected", Some("owner"));
    let protected_service = protected.service_with_limits(5, 10, 10);
    let protected_err = protected_service
        .create_dm(DmCreateCommand {
            group_id: None,
            caller_actor_id: Some("human_bob".to_string()),
            driver_bot: None,
            target_actor_id: "protected".to_string(),
            label: None,
            topic: None,
            context: None,
            provisioning: false,
        })
        .await
        .expect_err("unrelated human cannot reach protected bot");
    assert!(matches!(protected_err, GroupUseCaseError::Forbidden(_)));

    let private = Fixture::new()
        .with_human("human_bob", "Bob")
        .with_bot("private", "Private", "private", Some("owner"));
    let private_service = private.service_with_limits(5, 10, 10);
    let private_err = private_service
        .create_dm(DmCreateCommand {
            group_id: None,
            caller_actor_id: Some("human_bob".to_string()),
            driver_bot: None,
            target_actor_id: "private".to_string(),
            label: None,
            topic: None,
            context: None,
            provisioning: false,
        })
        .await
        .expect_err("unrelated human sees private bot as unreachable");
    assert!(matches!(
        private_err,
        GroupUseCaseError::Service(ServiceError::BotNotFound(bot_id)) if bot_id == "private"
    ));

    let related = Fixture::new()
        .with_human("human_bob", "Bob")
        .with_bot("private", "Private", "private", Some("owner"))
        .with_friendship("human_bob", "private");
    let related_service = related.service_with_limits(5, 10, 10);
    let allowed = related_service
        .create_dm(DmCreateCommand {
            group_id: Some("related-dm".to_string()),
            caller_actor_id: Some("human_bob".to_string()),
            driver_bot: None,
            target_actor_id: "private".to_string(),
            label: None,
            topic: None,
            context: None,
            provisioning: false,
        })
        .await
        .expect("friendship allows private DM");
    assert!(allowed.created);
}

#[tokio::test]
async fn create_dm_rejects_mismatched_driver_bot_for_human_caller() {
    let fixture = Fixture::new()
        .with_human("human_alice", "Alice")
        .with_bot("assistant", "Assistant", "public", Some("alice"))
        .with_bot("other", "Other", "public", Some("alice"));
    let service = fixture.service_with_limits(5, 10, 10);

    let err = service
        .create_dm(DmCreateCommand {
            group_id: Some("dm-under-test".to_string()),
            caller_actor_id: Some("human_alice".to_string()),
            driver_bot: Some("other".to_string()),
            target_actor_id: "assistant".to_string(),
            label: None,
            topic: None,
            context: None,
            provisioning: false,
        })
        .await
        .expect_err("Human-Bot DM driver_bot is only legacy advisory for target bot");

    assert!(matches!(
        err,
        GroupUseCaseError::InvalidProposal(message) if message.contains("driver_bot must match")
    ));
}
