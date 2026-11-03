use super::*;

#[tokio::test]
async fn set_visibility_route_builds_bot_visibility_command_and_returns_wrapper() {
    let management = Arc::new(RecordingBotManagementService {
        visibility_result: Ok(BotVisibilityResult {
            bot_uuid: "bot-alpha".to_string(),
            visibility: "private".to_string(),
        }),
        ..Default::default()
    });
    let services = Services::builder()
        .bot_management(management.clone())
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/bots/bot-alpha/visibility")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "visibility": "private"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["success"], true);
    assert_eq!(json["data"]["bot_uuid"], "bot-alpha");
    assert_eq!(json["data"]["visibility"], "private");

    let commands = management.visibility_commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].caller_actor_id.as_deref(), Some("human_alice"));
    assert_eq!(commands[0].bot_id, "bot-alpha");
    assert_eq!(commands[0].visibility, "private");
}

#[tokio::test]
async fn get_visibility_route_builds_bot_visibility_query_command() {
    let query = Arc::new(RecordingBotQueryService {
        visibility_result: Ok(BotVisibilityQueryResult {
            bot_uuid: "bot-alpha".to_string(),
            visibility: "protected".to_string(),
        }),
        ..Default::default()
    });
    let services = Services::builder()
        .bot_query(query.clone())
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/bot-alpha/visibility")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["success"], true);
    assert_eq!(json["data"]["bot_uuid"], "bot-alpha");
    assert_eq!(json["data"]["visibility"], "protected");

    let commands = query.visibility_commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].caller_actor_id.as_deref(), Some("human_alice"));
    assert_eq!(commands[0].bot_id, "bot-alpha");
}

#[tokio::test]
async fn leave_bot_route_builds_human_owner_leave_command_and_returns_wrapper() {
    let management = Arc::new(RecordingBotManagementService {
        leave_result: Ok(BotLeaveResult {
            left: true,
            bot_uuid: "bot-alpha".to_string(),
        }),
        ..Default::default()
    });
    let services = Services::builder()
        .bot_management(management.clone())
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri("/bots/bot-alpha")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["left"], true);
    assert_eq!(json["bot_uuid"], "bot-alpha");

    let commands = management.leave_commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].caller_actor_id.as_deref(), Some("human_alice"));
    assert_eq!(commands[0].human_actor_id.as_deref(), Some("human_alice"));
    assert_eq!(commands[0].bot_id, "bot-alpha");
}

#[tokio::test]
async fn query_bots_route_delegates_to_bot_query_service_and_preserves_legacy_array() {
    let query = Arc::new(RecordingBotQueryService {
        query_by_ids_result: Ok(BotQueryByIdsResult {
            bots: vec![query_entry("bot-alpha")],
        }),
        ..Default::default()
    });
    let services = Services::builder()
        .bot_query(query.clone())
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bots/query")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "bot_uuids": ["bot-alpha", "bot-missing"]
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    let items = json.as_array().expect("/bots/query returns a legacy array");
    assert_eq!(items.len(), 1);
    assert_eq!(items[0]["bot_uuid"], "bot-alpha");
    assert_eq!(items[0]["visibility"], "public");
    assert_eq!(items[0]["status"], "online");
    assert_eq!(items[0]["dynamic_status"]["status"], "active");

    let commands = query.query_by_ids_commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(
        commands[0].bot_ids,
        vec!["bot-alpha".to_string(), "bot-missing".to_string()]
    );
}

#[tokio::test]
async fn query_bots_route_rejects_missing_caller() {
    let query = Arc::new(RecordingBotQueryService {
        query_by_ids_result: Ok(BotQueryByIdsResult { bots: Vec::new() }),
        ..Default::default()
    });
    let services = Services::builder()
        .bot_query(query.clone())
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bots/query")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "bot_uuids": ["bot-alpha"]
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert!(query.query_by_ids_commands.lock().await.is_empty());
}

#[tokio::test]
async fn recording_bot_query_service_captures_list_and_detail_commands() {
    let service = RecordingBotQueryService {
        list_result: Ok(BotListResult {
            bots: vec![BotListEntry {
                bot_uuid: "bot-1".to_string(),
                name: Some("Bot One".to_string()),
                summary: Some("List compatible".to_string()),
                capabilities: Default::default(),
                status: ActorStatus::Online,
                visibility: "public".to_string(),
                owner_actor_id: Some("human_alice".to_string()),
                created_by: Some("alice".to_string()),
            }],
            offset: 5,
            limit: 10,
            total: 1,
        }),
        detail_result: Ok(BotDetailResult {
            agent_card: None,
            bot_uuid: "bot-1".to_string(),
            capabilities: Default::default(),
            status: ActorStatus::Online,
            visibility: "public".to_string(),
            owner_actor_id: Some("human_alice".to_string()),
            created_by: Some("alice".to_string()),
            actor_kind: ActorKind::Bot,
            env: Some("dev".to_string()),
            dynamic_status: DynamicStatusResponse {
                status: "offline".to_string(),
            },
        }),
        ..Default::default()
    };

    let list = service
        .list_bots(BotListCommand {
            caller_actor_id: Some("human_alice".to_string()),
            offset: 5,
            limit: 10,
            onboarded: Some(true),
        })
        .await
        .expect("list result");
    let detail = service
        .get_bot(BotDetailCommand {
            caller_actor_id: Some("human_alice".to_string()),
            bot_id: "bot-1".to_string(),
        })
        .await
        .expect("detail result");

    assert_eq!(list.limit, 10);
    assert_eq!(list.bots[0].created_by.as_deref(), Some("alice"));
    assert_eq!(list.bots[0].summary.as_deref(), Some("List compatible"));
    assert_eq!(detail.bot_uuid, "bot-1");
    assert_eq!(detail.actor_kind, ActorKind::Bot);
    assert_eq!(detail.dynamic_status.status, "offline");

    let list_commands = service.list_commands.lock().await;
    assert_eq!(list_commands.len(), 1);
    assert_eq!(
        list_commands[0].caller_actor_id.as_deref(),
        Some("human_alice")
    );
    assert_eq!(list_commands[0].offset, 5);
    assert_eq!(list_commands[0].limit, 10);
    assert_eq!(list_commands[0].onboarded, Some(true));
    drop(list_commands);

    let detail_commands = service.detail_commands.lock().await;
    assert_eq!(detail_commands.len(), 1);
    assert_eq!(
        detail_commands[0].caller_actor_id.as_deref(),
        Some("human_alice")
    );
    assert_eq!(detail_commands[0].bot_id, "bot-1");
}

#[tokio::test]
async fn recording_bot_management_service_captures_mutation_commands() {
    let service = RecordingBotManagementService {
        connect_result: Ok(BotConnectResult {
            is_new: false,
            bot_uuid: "bot-1".to_string(),
            token: "session-token".to_string(),
        }),
        status_result: Ok(BotStatusUpdateResult {
            updated: true,
            bot_uuid: "bot-1".to_string(),
            status: BotDynamicStatus {
                status: "idle".to_string(),
                dynamic_summary: Some("ready".to_string()),
                load: Some(0.25),
                updated_at: Some(42),
            },
        }),
        visibility_result: Ok(BotVisibilityResult {
            bot_uuid: "bot-1".to_string(),
            visibility: "private".to_string(),
        }),
        ..Default::default()
    };

    let connected = service
        .connect_bot(BotConnectCommand {
            caller_actor_id: Some("human_alice".to_string()),
            token: Some("session-token".to_string()),
            bot_id: Some("bot-1".to_string()),
            protocol_version: Some(2),
        })
        .await
        .expect("connect result");
    let status = service
        .update_status(BotStatusUpdateCommand {
            caller_actor_id: Some("human_alice".to_string()),
            bot_id: "bot-1".to_string(),
            status: BotDynamicStatus {
                status: "idle".to_string(),
                dynamic_summary: Some("ready".to_string()),
                load: Some(0.25),
                updated_at: Some(42),
            },
        })
        .await
        .expect("status result");
    let visibility = service
        .set_visibility(BotVisibilityCommand {
            caller_actor_id: Some("human_alice".to_string()),
            bot_id: "bot-1".to_string(),
            visibility: "private".to_string(),
        })
        .await
        .expect("visibility result");

    assert_eq!(connected.bot_uuid, "bot-1");
    assert_eq!(status.status.status, "idle");
    assert_eq!(visibility.visibility, "private");

    let connect_commands = service.connect_commands.lock().await;
    assert_eq!(connect_commands.len(), 1);
    assert_eq!(
        connect_commands[0].caller_actor_id.as_deref(),
        Some("human_alice")
    );
    assert_eq!(connect_commands[0].token.as_deref(), Some("session-token"));
    assert_eq!(connect_commands[0].bot_id.as_deref(), Some("bot-1"));
    assert_eq!(connect_commands[0].protocol_version, Some(2));
    drop(connect_commands);

    let status_commands = service.status_commands.lock().await;
    assert_eq!(status_commands.len(), 1);
    assert_eq!(
        status_commands[0].caller_actor_id.as_deref(),
        Some("human_alice")
    );
    assert_eq!(status_commands[0].bot_id, "bot-1");
    assert_eq!(status_commands[0].status.status, "idle");
    drop(status_commands);

    let visibility_commands = service.visibility_commands.lock().await;
    assert_eq!(visibility_commands.len(), 1);
    assert_eq!(
        visibility_commands[0].caller_actor_id.as_deref(),
        Some("human_alice")
    );
    assert_eq!(visibility_commands[0].bot_id, "bot-1");
    assert_eq!(visibility_commands[0].visibility, "private");
}

#[tokio::test]
async fn recording_bot_services_return_configured_errors() {
    let query = RecordingBotQueryService {
        list_result: Err(BotUseCaseError::Forbidden("not visible".to_string())),
        detail_result: Err(BotUseCaseError::Unauthorized("missing actor".to_string())),
        ..Default::default()
    };
    let management = RecordingBotManagementService {
        connect_result: Err(BotUseCaseError::Connect(ConnectError::AlreadyConnected(
            "bot-1".to_string(),
        ))),
        status_result: Err(BotUseCaseError::Forbidden("not owner".to_string())),
        visibility_result: Err(BotUseCaseError::InvalidVisibility("friends".to_string())),
        ..Default::default()
    };

    let list = query.list_bots(BotListCommand::default()).await;
    assert!(matches!(list, Err(BotUseCaseError::Forbidden(message)) if message == "not visible"));

    let detail = query
        .get_bot(BotDetailCommand {
            caller_actor_id: None,
            bot_id: "bot-1".to_string(),
        })
        .await;
    assert!(
        matches!(detail, Err(BotUseCaseError::Unauthorized(message)) if message == "missing actor")
    );

    let connect = management
        .connect_bot(BotConnectCommand {
            caller_actor_id: None,
            token: None,
            bot_id: None,
            protocol_version: None,
        })
        .await;
    assert!(
        matches!(connect, Err(BotUseCaseError::Connect(ConnectError::AlreadyConnected(id))) if id == "bot-1")
    );

    let status = management
        .update_status(BotStatusUpdateCommand {
            caller_actor_id: None,
            bot_id: "bot-1".to_string(),
            status: BotDynamicStatus {
                status: "idle".to_string(),
                ..Default::default()
            },
        })
        .await;
    assert!(matches!(status, Err(BotUseCaseError::Forbidden(message)) if message == "not owner"));

    let visibility = management
        .set_visibility(BotVisibilityCommand {
            caller_actor_id: None,
            bot_id: "bot-1".to_string(),
            visibility: "friends".to_string(),
        })
        .await;
    assert!(
        matches!(visibility, Err(BotUseCaseError::InvalidVisibility(value)) if value == "friends")
    );
}

#[tokio::test]
async fn recording_bot_query_service_fails_closed_when_unconfigured() {
    let service = RecordingBotQueryService::default();

    let result = service
        .list_bots(BotListCommand {
            caller_actor_id: Some("human_alice".to_string()),
            offset: 0,
            limit: 10,
            onboarded: None,
        })
        .await;

    assert_invalid_operation(
        result,
        "RecordingBotQueryService::list_bots is not configured",
    );

    let commands = service.list_commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].caller_actor_id.as_deref(), Some("human_alice"));
}
