use super::*;

#[tokio::test]
async fn connect_bot_preserves_provided_bot_id_and_reconnect_token() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    let connected = service
        .connect_bot(bcs_service_api::BotConnectCommand {
            caller_actor_id: None,
            token: None,
            bot_id: Some("provided-bot".to_string()),
            protocol_version: Some(2),
        })
        .await
        .expect("connect new bot");

    assert!(connected.is_new);
    assert_eq!(connected.bot_uuid, "provided-bot");
    assert!(!connected.token.is_empty());

    let reconnected = service
        .connect_bot(bcs_service_api::BotConnectCommand {
            caller_actor_id: None,
            token: Some(connected.token.clone()),
            bot_id: None,
            protocol_version: None,
        })
        .await
        .expect("reconnect by token");

    assert!(!reconnected.is_new);
    assert_eq!(reconnected.bot_uuid, "provided-bot");
    assert_eq!(reconnected.token, connected.token);
}

#[tokio::test]
async fn connect_bot_rejects_unknown_reserved_human_bot_id() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    let result = service
        .connect_bot(bcs_service_api::BotConnectCommand {
            caller_actor_id: None,
            token: None,
            bot_id: Some("human_foo".to_string()),
            protocol_version: Some(2),
        })
        .await;

    assert!(matches!(
        result,
        Err(BotUseCaseError::InvalidBotId(message))
            if message.contains("human_")
    ));
}

#[tokio::test]
async fn connect_bot_allows_existing_human_actor_id_to_reach_registry() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();
    fixture
        .registry
        .ensure_human_actor("foo", "Foo")
        .await
        .expect("ensure human");

    let result = service
        .connect_bot(bcs_service_api::BotConnectCommand {
            caller_actor_id: None,
            token: None,
            bot_id: Some("human_foo".to_string()),
            protocol_version: Some(2),
        })
        .await;

    assert!(matches!(
        result,
        Err(BotUseCaseError::Connect(ConnectError::AlreadyRegistered(id)))
            if id == "human_foo"
    ));
}

#[tokio::test]
async fn connect_bot_preserves_registry_conflict_error_class() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "provided-bot",
        caps(Some("Provided"), Some("Existing bot"), "protected"),
        Some("alice"),
    )
    .await;

    let result = service
        .connect_bot(bcs_service_api::BotConnectCommand {
            caller_actor_id: None,
            token: None,
            bot_id: Some("provided-bot".to_string()),
            protocol_version: Some(2),
        })
        .await;

    assert!(matches!(
        result,
        Err(BotUseCaseError::Connect(ConnectError::AlreadyRegistered(id)))
            if id == "provided-bot"
    ));
}

#[tokio::test]
async fn bot_runtime_connection_service_manages_streaming_lifecycle() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    let connected = service
        .connect_streaming(BotRuntimeConnectCommand {
            caller_actor_id: None,
            token: None,
            bot_id: Some("runtime-bot".to_string()),
            protocol_version: Some(2),
            client_kind: None,
        })
        .await
        .expect("runtime connect");

    assert!(connected.is_new);
    assert_eq!(connected.bot_uuid, "runtime-bot");
    assert!(fixture.registry.is_connected("runtime-bot").await);

    let status = bcs_service_api::BotDynamicStatus {
        status: "busy".to_string(),
        dynamic_summary: Some("serving websocket".to_string()),
        load: Some(0.4),
        updated_at: Some(456),
    };
    let updated = service
        .update_runtime_status(BotRuntimeStatusCommand {
            caller_actor_id: Some("runtime-bot".to_string()),
            bot_id: "runtime-bot".to_string(),
            status: status.clone(),
        })
        .await
        .expect("runtime status");

    assert!(updated.updated);
    assert_eq!(updated.bot_uuid, "runtime-bot");
    assert_eq!(updated.status.status, "busy");
    let stored = fixture
        .registry
        .get("runtime-bot")
        .await
        .expect("stored bot");
    assert!(serde_json::to_value(stored).unwrap().get("dynamic_status").is_none());

    service
        .disconnect_streaming(BotRuntimeDisconnectCommand {
            bot_id: "runtime-bot".to_string(),
        })
        .await
        .expect("runtime disconnect");

    assert!(!fixture.registry.is_connected("runtime-bot").await);
}

#[tokio::test]
async fn streaming_client_kind_requires_server_profile_and_is_replaced_on_reconnect() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    let untrusted = service
        .connect_streaming(BotRuntimeConnectCommand {
            caller_actor_id: None,
            token: None,
            bot_id: Some("profile-bot".to_string()),
            protocol_version: Some(3),
            client_kind: Some("native_mcp".to_string()),
        })
        .await
        .expect("initial connect");
    assert_eq!(untrusted.negotiated_client_kind, None);
    assert_eq!(
        fixture.registry.get_bot_info("profile-bot", "client_kind").await,
        None
    );

    service
        .disconnect_streaming(BotRuntimeDisconnectCommand {
            bot_id: "profile-bot".to_string(),
        })
        .await
        .expect("disconnect untrusted connection");
    let service = fixture.service().with_uplink_config(bcs_config_api::UplinkConfig {
        allowed_profiles: vec![bcs_config_api::UplinkProfile::NativeMcp],
    });

    let trusted = service
        .connect_streaming(BotRuntimeConnectCommand {
            caller_actor_id: None,
            token: Some(untrusted.token.clone()),
            bot_id: Some("profile-bot".to_string()),
            protocol_version: Some(3),
            client_kind: Some("native_mcp".to_string()),
        })
        .await
        .expect("trusted reconnect");
    assert_eq!(trusted.negotiated_client_kind.as_deref(), Some("native_mcp"));
    assert_eq!(
        fixture.registry.get_bot_info("profile-bot", "client_kind").await.as_deref(),
        Some("native_mcp")
    );

    service
        .disconnect_streaming(BotRuntimeDisconnectCommand {
            bot_id: "profile-bot".to_string(),
        })
        .await
        .expect("disconnect trusted connection");
    assert_eq!(
        fixture.registry.get_bot_info("profile-bot", "client_kind").await,
        None
    );
    let omitted = service
        .connect_streaming(BotRuntimeConnectCommand {
            caller_actor_id: None,
            token: Some(trusted.token),
            bot_id: Some("profile-bot".to_string()),
            protocol_version: Some(3),
            client_kind: None,
        })
        .await
        .expect("reconnect without client profile");
    assert_eq!(omitted.negotiated_client_kind, None);
    assert_eq!(
        fixture.registry.get_bot_info("profile-bot", "client_kind").await,
        None
    );
}

#[tokio::test]
async fn authenticated_connect_cannot_switch_body_identity() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();
    let b = service.connect_bot(bcs_service_api::BotConnectCommand {
        caller_actor_id: None, token: None, bot_id: Some("b".into()), protocol_version: None,
    }).await.unwrap();
    for (id, token) in [(Some("c".into()), None), (None, Some(b.token))] {
        assert!(service.connect_bot(bcs_service_api::BotConnectCommand {
            caller_actor_id: Some("a".into()), token, bot_id: id, protocol_version: None,
        }).await.is_err());
    }
    assert!(fixture.registry.get("c").await.is_none());
}
