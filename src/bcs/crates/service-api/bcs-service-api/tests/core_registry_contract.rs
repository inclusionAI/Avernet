use std::collections::HashSet;

use bcs_service_api::{
    ActorStatus, BotCapabilities, BotConnectParams, BotDynamicStatus, BotRegistryCoreService,
    ConnectError, ConnectionKind,
};
use bcs_test_support::NoopBotRegistryCoreService;

#[tokio::test]
async fn noop_registry_queries_and_default_helpers_are_empty() {
    let service = NoopBotRegistryCoreService::default();

    assert!(service.get("bot-1").await.is_none());
    assert!(service.get_agent_credentials("bot-1").await.is_none());
    assert!(
        service
            .get_by_ids(&[
                "bot-1".to_string(),
                "bot-1".to_string(),
                "bot-2".to_string(),
            ])
            .await
            .is_empty()
    );
    assert!(service.list_active().await.is_empty());
    assert!(service.list_all_bots().await.is_empty());
    assert!(service.list_bots_by_creator("staff001").await.is_empty());
    assert!(service.discover("helper").await.is_empty());
    assert!(service.find_by_skills(&["rust"]).await.is_empty());
    assert!(service.find_by_domains(&["backend"]).await.is_empty());
    assert!(service.find_by_scopes(&["chat"]).await.is_empty());
    assert!(service.find_by_name("helper").await.is_empty());

    let (page, total) = service
        .list_bots_by_name_and_cooperatable_with(
            "helper",
            "requester",
            true,
            &HashSet::new(),
            0,
            20,
        )
        .await;
    assert!(page.is_empty());
    assert_eq!(total, 0);

    assert!(service.load_from_storage("bot-1").await.is_none());
    assert!(service.load_token("bot-1").await.is_none());
    assert!(service.find_bot_by_token("token-1").await.is_none());
    assert!(
        service
            .find_bot_by_binding_channel("antding", "staff001")
            .await
            .is_none()
    );
    assert!(!service.has_been_onboarded("bot-1").await);
    assert!(!service.is_connected("bot-1").await);
    assert!(!service.is_effectively_online("bot-1").await);
    assert!(service.list_connected().await.is_empty());
    assert_eq!(service.get_protocol_version("bot-1").await, 1);
}

#[tokio::test]
#[allow(deprecated)]
async fn noop_registry_writes_and_transports_are_fail_closed() {
    let service = NoopBotRegistryCoreService::default();
    let capabilities = BotCapabilities::default();

    service
        .register("bot-1".to_string(), capabilities.clone())
        .await
        .unwrap();
    assert!(
        !service
            .update_status("bot-1", BotDynamicStatus::default())
            .await
    );
    assert!(!service.unregister("bot-1").await);
    service.cleanup_expired().await;
    service
        .save_to_storage("bot-1", &capabilities)
        .await
        .unwrap();
    service.update_visibility("bot-1", "public").await.unwrap();
    service.set_hidden("bot-1", true).await.unwrap();
    service
        .update_actor_status("bot-1", ActorStatus::Hidden)
        .await
        .unwrap();
    assert!(
        !service
            .ensure_human_actor("staff001", "Alice")
            .await
            .unwrap()
            .created
    );
    assert!(
        service
            .list_legacy_bots_for_owner("staff001", "dev")
            .await
            .unwrap()
            .is_empty()
    );
    service
        .update_human_name("staff001", "Alice")
        .await
        .unwrap();
    service
        .save_created_by("bot-1", "staff001", false)
        .await
        .unwrap();
    service.save_token("bot-1", "token-1").await.unwrap();

    assert_eq!(
        service
            .register_http_connection("bot-1".to_string(), "token-1".to_string())
            .await,
        "token-1"
    );
    assert!(
        service
            .register_streaming_connection("bot-1".to_string())
            .await
            .is_err()
    );
    assert!(
        service
            .reconnect_streaming("token-1".to_string())
            .await
            .is_err()
    );
    service.disconnect_streaming("bot-1").await;
    assert!(
        service
            .send_frame("bot-1", "frame".to_string())
            .await
            .is_err()
    );
    assert_eq!(
        service
            .send_request("bot-1", "ping", serde_json::json!({}), 100)
            .await
            .unwrap_err(),
        "send_request not implemented"
    );
    service
        .store_token_mapping("token-1".to_string(), "bot-1".to_string())
        .await;
    service.set_protocol_version("bot-1", 2).await;
}

#[tokio::test]
async fn noop_registry_connect_bot_fails_closed() {
    let service = NoopBotRegistryCoreService::default();

    let http_err = service
        .connect_bot(
            BotConnectParams {
                token: None,
                bot_id: Some("bot-http".to_string()),
                protocol_version: None,
                client_kind: None,
            },
            ConnectionKind::Http,
        )
        .await
        .unwrap_err();
    assert!(matches!(http_err, ConnectError::InternalError(_)));

    let streaming_err = service
        .connect_bot(
            BotConnectParams {
                token: None,
                bot_id: Some("bot-stream".to_string()),
                protocol_version: None,
                client_kind: None,
            },
            ConnectionKind::Streaming,
        )
        .await
        .unwrap_err();
    assert!(matches!(streaming_err, ConnectError::InternalError(_)));

    let invalid_err = service
        .connect_bot(
            BotConnectParams {
                token: None,
                bot_id: Some(String::new()),
                protocol_version: None,
                client_kind: None,
            },
            ConnectionKind::Http,
        )
        .await
        .unwrap_err();
    assert!(matches!(invalid_err, ConnectError::InternalError(_)));
}
