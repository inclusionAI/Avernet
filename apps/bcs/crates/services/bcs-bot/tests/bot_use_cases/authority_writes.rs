use super::*;

#[tokio::test]
async fn get_visibility_applies_read_policy_and_normalizes_visibility() {
    let fixture = RegistryFixture::new();
    fixture.seed_owner("private-bot", "alice");
    fixture.seed_owner("protected-bot", "alice");
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "private-bot",
        caps(Some("Private"), Some("Private bot"), "internal"),
        Some("alice"),
    )
    .await;
    register_bot(
        &fixture.registry,
        "protected-bot",
        caps(Some("Protected"), Some("Protected bot"), "protected"),
        Some("alice"),
    )
    .await;

    let owner_view = service
        .get_visibility(BotVisibilityQueryCommand {
            caller_actor_id: Some("human_alice".to_string()),
            bot_id: "private-bot".to_string(),
        })
        .await
        .expect("owner can read visibility");
    assert_eq!(owner_view.visibility, "private");

    let bot_view = service
        .get_visibility(BotVisibilityQueryCommand {
            caller_actor_id: Some("caller-bot".to_string()),
            bot_id: "protected-bot".to_string(),
        })
        .await
        .expect("bot can read public/protected visibility");
    assert_eq!(bot_view.visibility, "protected");

    let private_for_bot = service
        .get_visibility(BotVisibilityQueryCommand {
            caller_actor_id: Some("caller-bot".to_string()),
            bot_id: "private-bot".to_string(),
        })
        .await;
    assert!(matches!(
        private_for_bot,
        Err(BotUseCaseError::Service(ServiceError::BotNotFound(id)))
            if id == "private-bot"
    ));

    let non_owner = service
        .get_visibility(BotVisibilityQueryCommand {
            caller_actor_id: Some("human_bob".to_string()),
            bot_id: "private-bot".to_string(),
        })
        .await;
    assert!(matches!(non_owner, Err(BotUseCaseError::Forbidden(_))));
}

#[tokio::test]
async fn business_writes_admit_the_current_manager() {
    // 删除/配置/状态 are §8 BUSINESS operations
    // ("Bot 配置、状态、删除 | 使用当前角色"): the
    // current manager holds the same rights as the
    // owner (spec §1 goal 1) — never the stale
    // created_by (spec §3.3).
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "managed-bot",
        caps(Some("Managed"), Some("Managed bot"), "protected"),
        Some("alice"),
    )
    .await;
    fixture.seed_owner("managed-bot", "alice");
    fixture.seed_manager("managed-bot", "morgan");

    let status_update = service
        .update_status(BotStatusUpdateCommand {
            caller_actor_id: Some("human_morgan".to_string()),
            bot_id: "managed-bot".to_string(),
            status: bcs_service_api::BotDynamicStatus {
                status: "busy".to_string(),
                ..Default::default()
            },
        })
        .await
        .expect("the current manager updates status");
    assert_eq!(status_update.bot_uuid, "managed-bot");

    let visibility = service
        .set_visibility(BotVisibilityCommand {
            caller_actor_id: Some("human_morgan".to_string()),
            bot_id: "managed-bot".to_string(),
            visibility: "public".to_string(),
        })
        .await
        .expect("the current manager sets visibility");
    assert_eq!(visibility.visibility, "public");

    let left = service
        .leave_bot(BotLeaveCommand {
            caller_actor_id: Some("human_morgan".to_string()),
            human_actor_id: Some("human_morgan".to_string()),
            bot_id: "managed-bot".to_string(),
        })
        .await
        .expect("the current manager deletes (§8 删除)");
    assert!(left.left);
    assert!(fixture.registry.get("managed-bot").await.is_none());

    // The stale creator — no CURRENT edge at all —
    // never regains the rights the edges used to
    // imply (spec §3.3).
    register_bot(
        &fixture.registry,
        "stale-creator-bot",
        caps(Some("Stale"), Some("Stale creator bot"), "protected"),
        Some("alice"),
    )
    .await;
    let stale = service
        .set_visibility(BotVisibilityCommand {
            caller_actor_id: Some("human_alice".into()),
            bot_id: "stale-creator-bot".to_string(),
            visibility: "public".to_string(),
        })
        .await;
    assert!(matches!(
        stale,
        Err(BotUseCaseError::Forbidden(message))
            if message.contains("owner or manager role required")
    ));
}

#[tokio::test]
async fn leave_bot_allows_owner_soft_delete_for_unmanaged_bot() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "leave-bot",
        caps(Some("Leave"), Some("Leaving bot"), "public"),
        Some("alice"),
    )
    .await;
    fixture.seed_owner("leave-bot", "alice");
    register_bot(
        &fixture.registry,
        "other-owner-bot",
        caps(Some("Other"), Some("Other owner bot"), "public"),
        Some("bob"),
    )
    .await;

    let non_owner = service
        .leave_bot(BotLeaveCommand {
            caller_actor_id: Some("human_bob".to_string()),
            human_actor_id: Some("human_bob".to_string()),
            bot_id: "leave-bot".to_string(),
        })
        .await;
    assert!(matches!(
        non_owner,
        Err(BotUseCaseError::Forbidden(message))
            if message.contains("owner or manager role required")
    ));
    assert!(fixture.registry.get("leave-bot").await.is_some());

    let left = service
        .leave_bot(BotLeaveCommand {
            caller_actor_id: Some("human_alice".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            bot_id: "leave-bot".to_string(),
        })
        .await
        .expect("owner can delete unmanaged bot");
    assert!(left.left);
    assert_eq!(left.bot_uuid, "leave-bot");
    assert!(fixture.registry.get("leave-bot").await.is_none());
    assert!(fixture.registry.get("other-owner-bot").await.is_some());
}

#[tokio::test]
async fn leave_bot_rejects_bot_token_provider_managed_and_tc_style_deletes() {
    let fixture = ProviderRegistryFixture::new();
    let service = fixture.service();
    let provider_bot_id = fixture.register_provider_bot("alice").await;
    fixture
        .authority
        .seed_owner(&provider_bot_id, "alice");

    fixture
        .authority
        .seed_owner("teamclaw-bot:alice", "alice");
    register_bot(
        &fixture.registry,
        "teamclaw-bot:alice",
        caps(Some("Teamclaw"), Some("TC bot"), "public"),
        Some("alice"),
    )
    .await;

    let bot_token_delete = service
        .leave_bot(BotLeaveCommand {
            caller_actor_id: Some("teamclaw-bot:alice".to_string()),
            human_actor_id: None,
            bot_id: "teamclaw-bot:alice".to_string(),
        })
        .await;
    assert!(matches!(
        bot_token_delete,
        Err(BotUseCaseError::Forbidden(message))
            if message.contains("bot delete")
    ));

    let provider_managed = service
        .leave_bot(BotLeaveCommand {
            caller_actor_id: Some("human_alice".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            bot_id: provider_bot_id.clone(),
        })
        .await;
    assert!(matches!(
        provider_managed,
        Err(BotUseCaseError::Forbidden(message))
            if message.contains("provider")
    ));
    assert!(fixture.registry.get(&provider_bot_id).await.is_some());

    let tc_style = service
        .leave_bot(BotLeaveCommand {
            human_actor_id: Some("human_alice".to_string()),
            caller_actor_id: Some("human_alice".to_string()),
            bot_id: "teamclaw-bot:alice".to_string(),
        })
        .await;
    assert!(matches!(
        tc_style,
        Err(BotUseCaseError::Forbidden(message))
            if message.contains("TC")
    ));
    assert!(fixture.registry.get("teamclaw-bot:alice").await.is_some());
}

#[tokio::test]
async fn set_visibility_rejects_invalid_values() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    fixture.seed_owner("visibility-bot", "alice");
    register_bot(
        &fixture.registry,
        "visibility-bot",
        caps(Some("Visibility"), Some("Visibility bot"), "protected"),
        Some("alice"),
    )
    .await;

    let result = service
        .set_visibility(BotVisibilityCommand {
            caller_actor_id: Some("alice".to_string()),
            bot_id: "visibility-bot".to_string(),
            visibility: "friends".to_string(),
        })
        .await;

    assert!(matches!(
        result,
        Err(BotUseCaseError::InvalidVisibility(value)) if value == "friends"
    ));
    let stored = fixture
        .registry
        .get("visibility-bot")
        .await
        .expect("stored bot");
    assert_eq!(stored.capabilities.visibility, "protected");
}

#[tokio::test]
async fn set_visibility_updates_registry_for_valid_value() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    fixture.seed_owner("visibility-bot", "alice");
    register_bot(
        &fixture.registry,
        "visibility-bot",
        caps(Some("Visibility"), Some("Visibility bot"), "protected"),
        Some("alice"),
    )
    .await;

    let result = service
        .set_visibility(BotVisibilityCommand {
            caller_actor_id: Some("human_alice".to_string()),
            bot_id: "visibility-bot".to_string(),
            visibility: "private".to_string(),
        })
        .await
        .expect("visibility update");

    assert_eq!(result.bot_uuid, "visibility-bot");
    assert_eq!(result.visibility, "private");

    let stored = fixture
        .registry
        .get("visibility-bot")
        .await
        .expect("stored bot");
    assert_eq!(stored.capabilities.visibility, "private");
}

#[tokio::test]
async fn set_visibility_rejects_non_owner_when_owner_is_known() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    fixture.seed_owner("visibility-bot", "alice");
    register_bot(
        &fixture.registry,
        "visibility-bot",
        caps(Some("Visibility"), Some("Visibility bot"), "protected"),
        Some("alice"),
    )
    .await;

    let result = service
        .set_visibility(BotVisibilityCommand {
            caller_actor_id: Some("human_bob".to_string()),
            bot_id: "visibility-bot".to_string(),
            visibility: "public".to_string(),
        })
        .await;

    assert!(matches!(
        result,
        Err(BotUseCaseError::Forbidden(message))
            if message.contains("owner or manager role required")
    ));
    let stored = fixture
        .registry
        .get("visibility-bot")
        .await
        .expect("stored bot");
    assert_eq!(stored.capabilities.visibility, "protected");
}

#[tokio::test]
async fn set_visibility_rejects_missing_caller() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    fixture.seed_owner("visibility-bot", "alice");
    register_bot(
        &fixture.registry,
        "visibility-bot",
        caps(Some("Visibility"), Some("Visibility bot"), "protected"),
        Some("alice"),
    )
    .await;

    let result = service
        .set_visibility(BotVisibilityCommand {
            caller_actor_id: None,
            bot_id: "visibility-bot".to_string(),
            visibility: "public".to_string(),
        })
        .await;

    assert!(matches!(
        result,
        Err(BotUseCaseError::Unauthorized(message))
            if message.contains("caller identity")
    ));
}

#[tokio::test]
async fn set_visibility_rejects_ownerless_bot_for_non_self_caller() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "ownerless-bot",
        caps(Some("Ownerless"), Some("No owner"), "protected"),
        None,
    )
    .await;

    let result = service
        .set_visibility(BotVisibilityCommand {
            caller_actor_id: Some("human_alice".to_string()),
            bot_id: "ownerless-bot".to_string(),
            visibility: "public".to_string(),
        })
        .await;

    assert!(matches!(
        result,
        Err(BotUseCaseError::Forbidden(message))
            if message.contains("owner or manager role required")
    ));
    let stored = fixture
        .registry
        .get("ownerless-bot")
        .await
        .expect("stored bot");
    assert_eq!(stored.capabilities.visibility, "protected");
}
