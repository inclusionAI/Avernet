use super::*;

// ── Public Group Visibility Tests ────────────────────────────────────────────

#[tokio::test]
async fn create_public_group_with_all_public_bots_succeeds() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_bot("bot_b", "Bot B", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    let mut cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
        participant("bot_b", Some("consultant")),
    ]);
    cmd.visibility = Some("public".to_string());

    let result = service.create_group(cmd).await;
    assert!(result.is_ok(), "error: {:?}", result.unwrap_err());
    let detail = result.unwrap();
    assert_eq!(detail.visibility, "public");
}

#[tokio::test]
async fn create_public_group_rejects_non_public_bot() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_bot("bot_private", "Private Bot", "protected", None)
        .with_friendship("bot_a", "bot_private");
    let service = fixture.service_with_limits(5, 10, 10);

    let mut cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
        participant("bot_private", Some("consultant")),
    ]);
    cmd.visibility = Some("public".to_string());

    let result = service.create_group(cmd).await;
    assert!(result.is_err());
    let err = result.unwrap_err().to_string();
    assert!(err.contains("Group contains non-public bots"), "error: {}", err);
}

#[tokio::test]
async fn create_group_rejects_invalid_visibility() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    let mut cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
    ]);
    cmd.visibility = Some("invalid".to_string());

    let result = service.create_group(cmd).await;
    assert!(result.is_err());
    assert!(
        result.unwrap_err().to_string().contains("Invalid visibility value"),
        "expected error about invalid visibility"
    );
}

#[tokio::test]
async fn update_visibility_to_public_with_all_public_bots() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_bot("bot_b", "Bot B", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    // Create private group first
    let cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
        participant("bot_b", Some("consultant")),
    ]);
    service.create_group(cmd).await.unwrap();

    // Update to public
    let result = service.update_visibility(GroupUpdateVisibilityCommand {
        caller_actor_id: "bot_a".to_string(),
        group_id: "group-under-test".to_string(),
        visibility: "public".to_string(),
    }).await;
    assert!(result.is_ok(), "error: {:?}", result.unwrap_err());
    assert_eq!(result.unwrap().visibility, "public");
}

#[tokio::test]
async fn update_visibility_to_public_rejects_non_public_bot() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_bot("bot_private", "Private Bot", "protected", None)
        .with_friendship("bot_a", "bot_private");
    let service = fixture.service_with_limits(5, 10, 10);

    let cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
        participant("bot_private", Some("consultant")),
    ]);
    service.create_group(cmd).await.unwrap();

    let result = service.update_visibility(GroupUpdateVisibilityCommand {
        caller_actor_id: "bot_a".to_string(),
        group_id: "group-under-test".to_string(),
        visibility: "public".to_string(),
    }).await;
    assert!(result.is_err());
    match result.unwrap_err() {
        GroupUseCaseError::Service(ServiceError::ExistNonPublicBots { bots }) => {
            assert_eq!(bots.len(), 1);
            assert_eq!(bots[0].0, "bot_private");
            assert_eq!(bots[0].1.as_deref(), Some("Private Bot"));
        }
        other => panic!("expected ExistNonPublicBots, got {:?}", other),
    }
}

#[tokio::test]
async fn update_visibility_to_private_always_succeeds() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_bot("bot_b", "Bot B", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    let mut cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
        participant("bot_b", Some("consultant")),
    ]);
    cmd.visibility = Some("public".to_string());
    service.create_group(cmd).await.unwrap();

    let result = service.update_visibility(GroupUpdateVisibilityCommand {
        caller_actor_id: "bot_a".to_string(),
        group_id: "group-under-test".to_string(),
        visibility: "private".to_string(),
    }).await;
    assert!(result.is_ok(), "error: {:?}", result.unwrap_err());
    assert_eq!(result.unwrap().visibility, "private");
}

#[tokio::test]
async fn update_visibility_rejects_non_coordinator() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_bot("bot_b", "Bot B", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    let cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
        participant("bot_b", Some("consultant")),
    ]);
    service.create_group(cmd).await.unwrap();

    let result = service.update_visibility(GroupUpdateVisibilityCommand {
        caller_actor_id: "bot_b".to_string(),
        group_id: "group-under-test".to_string(),
        visibility: "public".to_string(),
    }).await;
    assert!(result.is_err());
    assert!(result.unwrap_err().to_string().contains("coordinator"));
}

#[tokio::test]
async fn add_non_public_bot_to_public_group_rejected() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_bot("bot_b", "Bot B", "public", None)
        .with_bot("bot_private", "Private Bot", "protected", None)
        .with_friendship("bot_a", "bot_private");
    let service = fixture.service_with_limits(5, 10, 10);

    let mut cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
        participant("bot_b", Some("consultant")),
    ]);
    cmd.visibility = Some("public".to_string());
    service.create_group(cmd).await.unwrap();

    let result = service.add_member(GroupAddMemberCommand {
        caller_actor_id: Some("bot_a".to_string()),
        human_actor_id: None,
        group_id: "group-under-test".to_string(),
        bot_id: "bot_private".to_string(),
        message_view_scope: None,
    }).await;
    assert!(result.is_err());
    assert!(result.unwrap_err().to_string().contains("Cannot add non-public bot"));
}

#[tokio::test]
async fn add_human_to_public_group_succeeds() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_human("human_123", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);

    let mut cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
    ]);
    cmd.visibility = Some("public".to_string());
    service.create_group(cmd).await.unwrap();

    let result = service.add_member(GroupAddMemberCommand {
        caller_actor_id: Some("bot_a".to_string()),
        human_actor_id: None,
        group_id: "group-under-test".to_string(),
        bot_id: "human_123".to_string(),
        message_view_scope: None,
    }).await;
    assert!(result.is_ok(), "error: {:?}", result.unwrap_err());
}

#[tokio::test]
async fn list_groups_filters_by_visibility() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_bot("bot_b", "Bot B", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    // Create a private group
    let cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
    ]);
    service.create_group(cmd).await.unwrap();

    // Create a public group
    let cmd2 = GroupCreateCommand {
        human_mention_notify_mode: Default::default(),
        create_initial_session: true,
        group_id: Some("public-group".to_string()),
        caller_actor_id: Some("bot_b".to_string()),
        driver_bot_id: "bot_b".to_string(),
        originator: None,
        label: Some("Translation".to_string()),
        topic: None,
        context: None,
        opening_message: None,
        routing_policy: None,
        member_bot_ids: Vec::new(),
        participants: vec![participant("bot_b", Some("driver"))],
        group_kind: None,
        group_strategy: None,
        visibility: Some("public".to_string()),
        provisioning: false,
    };
    service.create_group(cmd2).await.unwrap();

    // Filter by visibility=public
    let result = service.list_groups(GroupListCommand {
        group_kind: None,
        offset: 0,
        limit: 10,
        visibility: Some("public".to_string()),
        label: None,
    }).await.unwrap();
    assert_eq!(result.total, 1);
    assert_eq!(result.items[0].visibility, "public");

    // Filter by label
    let result = service.list_groups(GroupListCommand {
        group_kind: None,
        offset: 0,
        limit: 10,
        visibility: Some("public".to_string()),
        label: Some("translation".to_string()),
    }).await.unwrap();
    assert_eq!(result.total, 1);
}
