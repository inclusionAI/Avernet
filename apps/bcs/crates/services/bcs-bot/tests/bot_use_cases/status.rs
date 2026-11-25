use super::*;

#[tokio::test]
async fn update_status_echoes_payload_without_retaining_it() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "status-bot",
        caps(Some("Status"), Some("Status bot"), "protected"),
        Some("alice"),
    )
    .await;

    let status = bcs_service_api::BotDynamicStatus {
        status: "busy".to_string(),
        dynamic_summary: Some("Working on a task".to_string()),
        load: Some(0.8),
        updated_at: Some(123),
    };
    let result = service
        .update_status(BotStatusUpdateCommand {
            caller_actor_id: Some("status-bot".to_string()),
            bot_id: "status-bot".to_string(),
            status: status.clone(),
        })
        .await
        .expect("status update");

    assert!(result.updated);
    assert_eq!(result.bot_uuid, "status-bot");
    assert_eq!(result.status.status, "busy");
    assert_eq!(
        result.status.dynamic_summary.as_deref(),
        Some("Working on a task")
    );
    assert_eq!(result.status.load, status.load);
    assert_eq!(result.status.updated_at, status.updated_at);

    let stored = fixture
        .registry
        .get("status-bot")
        .await
        .expect("stored bot");
    assert!(serde_json::to_value(stored).unwrap().get("dynamic_status").is_none());
}

#[tokio::test]
async fn update_status_preserves_legacy_false_for_missing_self_target() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    let status = bcs_service_api::BotDynamicStatus {
        status: "busy".to_string(),
        dynamic_summary: Some("still booting".to_string()),
        ..Default::default()
    };
    let result = service
        .update_status(BotStatusUpdateCommand {
            caller_actor_id: Some("missing-bot".to_string()),
            bot_id: "missing-bot".to_string(),
            status: status.clone(),
        })
        .await
        .expect("missing self target should preserve legacy false result");

    assert!(!result.updated);
    assert_eq!(result.bot_uuid, "missing-bot");
    assert_eq!(result.status.status, "busy");
    assert_eq!(
        result.status.dynamic_summary.as_deref(),
        Some("still booting")
    );
}

#[tokio::test]
async fn update_status_rejects_missing_caller() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "status-bot",
        caps(Some("Status"), Some("Status bot"), "protected"),
        Some("alice"),
    )
    .await;

    let result = service
        .update_status(BotStatusUpdateCommand {
            caller_actor_id: None,
            bot_id: "status-bot".to_string(),
            status: bcs_service_api::BotDynamicStatus {
                status: "busy".to_string(),
                ..Default::default()
            },
        })
        .await;

    assert!(matches!(
        result,
        Err(BotUseCaseError::Unauthorized(message))
            if message.contains("caller identity")
    ));
}

#[tokio::test]
async fn update_status_rejects_caller_mismatch() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "status-bot",
        caps(Some("Status"), Some("Status bot"), "protected"),
        Some("alice"),
    )
    .await;
    fixture.seed_owner("status-bot", "alice");

    let result = service
        .update_status(BotStatusUpdateCommand {
            caller_actor_id: Some("human_bob".to_string()),
            bot_id: "status-bot".to_string(),
            status: bcs_service_api::BotDynamicStatus {
                status: "busy".to_string(),
                ..Default::default()
            },
        })
        .await;

    assert!(matches!(
        result,
        Err(BotUseCaseError::Forbidden(message))
            if message.contains("owner or manager role required")
    ));
}
