mod support;

use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_auth_api::{AuthPluginChain, AuthPrincipal};
use bcs_auth_local::StaticAuthPlugin;
use bcs_bot::{Bot, BotCore};
use bcs_http::{
    router::build_router,
    state::{ChainUserIdentityPort, HttpAppState},
};
use bcs_service_api::{
    ActorKind, ActorStatus, BotConnectCommand, BotConnectResult, BotDetailCommand, BotDetailResult,
    BotDynamicStatus, BotLeaveResult, BotListCommand, BotListEntry, BotListResult,
    BotManagementService, BotPagedListResult, BotQueryByIdsResult, BotQueryEntry, BotQueryService,
    BotRegistryCoreService, BotStatusUpdateCommand, BotStatusUpdateResult, BotUseCaseError,
    BotVisibilityCommand, BotVisibilityQueryResult, BotVisibilityResult, ConnectError,
    DynamicStatusResponse, ServiceError, Skill,
};
use bcs_services_container::Services;
use serde_json::Value;
use std::sync::Arc;
use support::bot_use_cases::{RecordingBotManagementService, RecordingBotQueryService};
use tempfile::TempDir;
use tower::ServiceExt;

fn static_auth_chain(staff_no: &str, nick_name: &str) -> Arc<AuthPluginChain> {
    let principal = AuthPrincipal {
        user_id: Some(staff_no.to_string()),
        user_name: Some(nick_name.to_string()),
        ..Default::default()
    };
    Arc::new(AuthPluginChain::new(vec![Box::new(
        StaticAuthPlugin::with_principal(principal),
    )]))
}

#[tokio::test]
async fn list_bots_route_builds_bot_list_command_and_returns_legacy_array() {
    let query = Arc::new(RecordingBotQueryService {
        list_result: Ok(BotListResult {
            bots: vec![BotListEntry {
                bot_uuid: "bot-alpha".to_string(),
                name: Some("Alpha".to_string()),
                summary: Some("Alpha bot".to_string()),
                capabilities: bcs_service_api::BotCapabilities {
                    name: Some("Alpha".to_string()),
                    summary: Some("Alpha bot".to_string()),
                    skills: vec![
                        Skill::with_description("review", "Review code"),
                        Skill::new("ops"),
                    ],
                    visibility: "public".to_string(),
                    ..Default::default()
                },
                status: ActorStatus::Online,
                visibility: "public".to_string(),
                owner_actor_id: Some("human_alice".to_string()),
                created_by: Some("alice".to_string()),
            }],
            offset: 5,
            limit: 10,
            total: 1,
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
                .uri("/bots?offset=5&limit=10&onboarded=true")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    let items = json.as_array().expect("/bots returns a legacy array");
    assert_eq!(items.len(), 1);
    assert_eq!(items[0]["bot_uuid"], "bot-alpha");
    assert_eq!(
        items[0]["capabilities"]["skills"],
        serde_json::json!(["review", "ops"])
    );
    assert_eq!(items[0]["created_by"], "alice");

    let commands = query.list_commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].caller_actor_id.as_deref(), Some("human_alice"));
    assert_eq!(commands[0].offset, 5);
    assert_eq!(commands[0].limit, 10);
    assert_eq!(commands[0].onboarded, Some(true));
}

#[tokio::test]
async fn list_bots_route_rejects_missing_caller() {
    let query = Arc::new(RecordingBotQueryService {
        list_result: Ok(BotListResult {
            bots: Vec::new(),
            offset: 0,
            limit: 10,
            total: 0,
        }),
        ..Default::default()
    });
    let services = Services::builder()
        .bot_query(query.clone())
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots?offset=0&limit=10")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert!(query.list_commands.lock().await.is_empty());
}

#[tokio::test]
async fn list_bots_paged_route_delegates_to_bot_query_service_and_preserves_legacy_object_shape() {
    let query = Arc::new(RecordingBotQueryService {
        paged_result: Ok(BotPagedListResult {
            items: vec![query_entry("agent:alice")],
            total: 1,
            offset: 0,
            limit: 5,
        }),
        ..Default::default()
    });
    let services = Services::builder()
        .bot_query(query.clone())
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/paged?user_id=alice&offset=0&limit=5")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["total"], 1);
    assert_eq!(json["offset"], 0);
    assert_eq!(json["limit"], 5);
    assert_eq!(json["items"].as_array().unwrap().len(), 1);
    assert_eq!(json["items"][0]["bot_uuid"], "agent:alice");

    let commands = query.paged_commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].user_id.as_deref(), Some("alice"));
    assert_eq!(commands[0].offset, 0);
    assert_eq!(commands[0].limit, 5);
}

#[tokio::test]
async fn list_my_bots_route_forwards_active_only_query() {
    let query = Arc::new(RecordingBotQueryService {
        my_bots_result: Ok(BotPagedListResult {
            items: vec![query_entry("agent:alice")],
            total: 1,
            offset: 2,
            limit: 3,
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
                .uri("/bots/my?offset=2&limit=3&active_only=true")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["total"], 1);
    assert_eq!(json["offset"], 2);
    assert_eq!(json["limit"], 3);
    assert_eq!(json["items"][0]["bot_uuid"], "agent:alice");
    assert_eq!(json["items"][0]["visibility"], "public");
    assert_eq!(json["items"][0]["capabilities"]["visibility"], "public");

    let commands = query.my_bots_commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].staff_no, "alice");
    assert_eq!(commands[0].offset, 2);
    assert_eq!(commands[0].limit, 3);
    assert!(commands[0].active_only);
}

#[tokio::test]
async fn get_bot_route_builds_bot_detail_command_and_returns_legacy_fields() {
    let query = Arc::new(RecordingBotQueryService {
        detail_result: Ok(BotDetailResult {
            bot_uuid: "bot-alpha".to_string(),
            capabilities: bcs_service_api::BotCapabilities {
                name: Some("Alpha".to_string()),
                summary: Some("Alpha bot".to_string()),
                visibility: "public".to_string(),
                ..Default::default()
            },
            status: ActorStatus::Hidden,
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
                .uri("/bots/bot-alpha")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["bot_uuid"], "bot-alpha");
    assert_eq!(json["capabilities"]["name"], "Alpha");
    assert_eq!(json["created_by"], "alice");
    assert_eq!(json["actor_kind"], "bot");
    assert_eq!(json["env"], "dev");
    assert_eq!(json["status"], "hidden");
    assert_eq!(json["dynamic_status"]["status"], "offline");

    let commands = query.detail_commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].caller_actor_id.as_deref(), Some("human_alice"));
    assert_eq!(commands[0].bot_id, "bot-alpha");
}

#[tokio::test]
async fn get_bot_route_rejects_missing_caller() {
    let query = Arc::new(RecordingBotQueryService {
        detail_result: Ok(BotDetailResult {
            bot_uuid: "bot-alpha".to_string(),
            capabilities: Default::default(),
            status: ActorStatus::Online,
            visibility: "public".to_string(),
            owner_actor_id: None,
            created_by: None,
            actor_kind: ActorKind::Bot,
            env: Some("dev".to_string()),
            dynamic_status: DynamicStatusResponse {
                status: "offline".to_string(),
            },
        }),
        ..Default::default()
    });
    let services = Services::builder()
        .bot_query(query.clone())
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/bot-alpha")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert!(query.detail_commands.lock().await.is_empty());
}

#[tokio::test]
async fn connect_bot_route_builds_bot_connect_command_and_returns_result() {
    let management = Arc::new(RecordingBotManagementService {
        connect_result: Ok(BotConnectResult {
            is_new: false,
            bot_uuid: "bot-alpha".to_string(),
            token: "session-token".to_string(),
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
                .method("POST")
                .uri("/bots/connect")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "token": "session-token",
                        "bot_id": "bot-alpha",
                        "protocol_version": 2
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
    assert_eq!(json["is_new"], false);
    assert_eq!(json["bot_uuid"], "bot-alpha");
    assert_eq!(json["token"], "session-token");

    let commands = management.connect_commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].caller_actor_id.as_deref(), Some("human_alice"));
    assert_eq!(commands[0].token.as_deref(), Some("session-token"));
    assert_eq!(commands[0].bot_id.as_deref(), Some("bot-alpha"));
    assert_eq!(commands[0].protocol_version, Some(2));
}

#[tokio::test]
async fn connect_bot_route_maps_use_case_conflict_errors() {
    let management = Arc::new(RecordingBotManagementService {
        connect_result: Err(BotUseCaseError::Connect(ConnectError::AlreadyRegistered(
            "bot-alpha".to_string(),
        ))),
        ..Default::default()
    });
    let services = Services::builder()
        .bot_management(management.clone())
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bots/connect")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "bot_id": "bot-alpha"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::CONFLICT);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["status"], StatusCode::CONFLICT.as_u16());
    let error_message = json["error"].as_str().unwrap();
    assert!(error_message.contains("already registered"));
}

#[tokio::test]
async fn status_route_builds_bot_status_command_with_token_caller() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("bot-alpha".to_string(), Default::default())
        .await
        .unwrap();
    registry
        .store_token_mapping("status-token".to_string(), "bot-alpha".to_string())
        .await;
    let management = Arc::new(RecordingBotManagementService {
        status_result: Ok(BotStatusUpdateResult {
            updated: true,
            bot_uuid: "bot-alpha".to_string(),
            status: BotDynamicStatus {
                status: "busy".to_string(),
                dynamic_summary: Some("running task".to_string()),
                load: Some(0.75),
                updated_at: Some(42),
            },
        }),
        ..Default::default()
    });
    let services = Services::builder()
        .registry(registry)
        .bot_management(management.clone())
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bots/status")
                .header("authorization", "Bearer status-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "bot_uuid": "bot-alpha",
                        "status": {
                            "status": "busy",
                            "dynamic_summary": "running task",
                            "load": 0.75,
                            "updated_at": 42
                        }
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
    assert_eq!(json["updated"], true);
    assert_eq!(json["bot_uuid"], "bot-alpha");
    assert_eq!(json["status"]["status"], "busy");
    assert_eq!(json["status"]["dynamic_summary"], "running task");

    let commands = management.status_commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].caller_actor_id.as_deref(), Some("bot-alpha"));
    assert_eq!(commands[0].bot_id, "bot-alpha");
    assert_eq!(commands[0].status.status, "busy");
    assert_eq!(
        commands[0].status.dynamic_summary.as_deref(),
        Some("running task")
    );
    assert_eq!(commands[0].status.load, Some(0.75));
    assert_eq!(commands[0].status.updated_at, Some(42));
}

#[tokio::test]
async fn status_route_uses_token_bot_when_body_bot_uuid_is_empty() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("bot-alpha".to_string(), Default::default())
        .await
        .unwrap();
    registry
        .store_token_mapping("status-token".to_string(), "bot-alpha".to_string())
        .await;
    let management = Arc::new(RecordingBotManagementService {
        status_result: Ok(BotStatusUpdateResult {
            updated: true,
            bot_uuid: "bot-alpha".to_string(),
            status: BotDynamicStatus {
                status: "busy".to_string(),
                dynamic_summary: Some("running task".to_string()),
                load: Some(0.75),
                updated_at: Some(42),
            },
        }),
        ..Default::default()
    });
    let services = Services::builder()
        .registry(registry)
        .bot_management(management.clone())
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bots/status")
                .header("authorization", "Bearer status-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "bot_uuid": "",
                        "status": {
                            "status": "busy",
                            "dynamic_summary": "running task",
                            "load": 0.75,
                            "updated_at": 42
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);

    let commands = management.status_commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].caller_actor_id.as_deref(), Some("bot-alpha"));
    assert_eq!(commands[0].bot_id, "bot-alpha");
    assert_eq!(commands[0].status.status, "busy");
}

#[tokio::test]
async fn status_route_rejects_body_bot_uuid_that_differs_from_token_bot() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("bot-alpha".to_string(), Default::default())
        .await
        .unwrap();
    registry
        .store_token_mapping("status-token".to_string(), "bot-alpha".to_string())
        .await;
    let management = Arc::new(RecordingBotManagementService {
        status_result: Ok(BotStatusUpdateResult {
            updated: true,
            bot_uuid: "bot-beta".to_string(),
            status: BotDynamicStatus {
                status: "busy".to_string(),
                ..Default::default()
            },
        }),
        ..Default::default()
    });
    let services = Services::builder()
        .registry(registry)
        .bot_management(management.clone())
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bots/status")
                .header("authorization", "Bearer status-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "bot_uuid": "bot-beta",
                        "status": {
                            "status": "busy"
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);

    let commands = management.status_commands.lock().await;
    assert!(commands.is_empty());
}

#[tokio::test]
async fn status_route_preserves_legacy_updated_false_for_missing_self_target() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .store_token_mapping("status-token".to_string(), "missing-bot".to_string())
        .await;
    let registry_service: Arc<dyn BotRegistryCoreService> = registry;
    let bot_use_cases = Arc::new(Bot::new(registry_service.clone()));
    let services = Services::builder()
        .registry(registry_service)
        .bot_management(bot_use_cases)
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bots/status")
                .header("authorization", "Bearer status-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "bot_uuid": "missing-bot",
                        "status": {
                            "status": "busy",
                            "dynamic_summary": "still booting"
                        }
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
    assert_eq!(json["updated"], false);
    assert_eq!(json["bot_uuid"], "missing-bot");
    assert_eq!(json["status"]["status"], "busy");
    assert_eq!(json["status"]["dynamic_summary"], "still booting");
}

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

fn assert_invalid_operation<T>(result: Result<T, BotUseCaseError>, expected_message: &str) {
    assert!(
        matches!(
            result,
            Err(BotUseCaseError::Service(ServiceError::InvalidOperation {
                message,
                request_id: None,
            })) if message == expected_message
        ),
        "expected InvalidOperation with message {expected_message}"
    );
}

fn query_entry(bot_uuid: &str) -> BotQueryEntry {
    BotQueryEntry {
        bot_uuid: bot_uuid.to_string(),
        capabilities: bcs_service_api::BotCapabilities {
            name: Some(bot_uuid.to_string()),
            summary: Some("Test bot".to_string()),
            visibility: "public".to_string(),
            ..Default::default()
        },
        visibility: "public".to_string(),
        status: ActorStatus::Online,
        actor_kind: ActorKind::Bot,
        env: Some("dev".to_string()),
        dynamic_status: DynamicStatusResponse {
            status: "active".to_string(),
        },
        created_by: Some("alice".to_string()),
    }
}
