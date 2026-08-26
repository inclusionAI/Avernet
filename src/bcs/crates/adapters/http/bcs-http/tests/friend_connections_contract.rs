use std::sync::Arc;

use async_trait::async_trait;
use axum::{
    body::{to_bytes, Body},
    http::{Request, StatusCode},
};
use bcs_auth_api::AuthError;
use bcs_bot::BotCore;
use bcs_domain::{actor::ActorKind, edge_permission::{FriendListEntry, PermissionRequest, RequestKind, RequestStatus}};
use bcs_http::{
    router::build_router,
    state::{HttpAppState, UserIdentityPort},
};
use bcs_service_api::{
    application::{
        connect::{ConnectResult, ConnectService, ConnectStatus, RequestDirection, RequestsPage},
        BotListEntry, BotQueryService, BotVisibilityQueryCommand, BotVisibilityQueryResult,
    },
    ActorStatus, BotCapabilities, BotRegistryCoreService, ServiceError, ServiceResult,
};
use bcs_services_container::Services;
use serde_json::Value;
use tempfile::TempDir;
use tokio::sync::Mutex;
use tower::ServiceExt;

struct RecordingConnectService {
    create_commands: Mutex<Vec<(String, String, Option<String>)>>,
    approve_commands: Mutex<Vec<(String, String)>>,
    reject_commands: Mutex<Vec<(String, String, Option<String>)>>,
    cancel_commands: Mutex<Vec<String>>,
    request_lookup: Mutex<std::collections::HashMap<String, PermissionRequest>>,
    revoke_commands: Mutex<Vec<(String, String)>>,
    list_friends_commands: Mutex<Vec<String>>,
    list_requests_commands: Mutex<Vec<(String, RequestDirection, Option<RequestStatus>, u32, u32)>>,
    create_result: ConnectResult,
    list_friends_result: Vec<FriendListEntry>,
    list_requests_result: RequestsPage,
}

impl Default for RecordingConnectService {
    fn default() -> Self {
        Self {
            create_commands: Mutex::new(Vec::new()),
            approve_commands: Mutex::new(Vec::new()),
            reject_commands: Mutex::new(Vec::new()),
            cancel_commands: Mutex::new(Vec::new()),
            request_lookup: Mutex::new(std::collections::HashMap::new()),
            revoke_commands: Mutex::new(Vec::new()),
            list_friends_commands: Mutex::new(Vec::new()),
            list_requests_commands: Mutex::new(Vec::new()),
            create_result: ConnectResult {
                request_ids: vec!["1".to_string()],
                edge_ids: vec![11],
                status: ConnectStatus::Pending,
                auto_accepted: false,
            },
            list_friends_result: vec![FriendListEntry {
                actor_id: "friend-bot".to_string(),
                name: Some("Friend Bot".to_string()),
                summary: Some("friend summary".to_string()),
                is_online: true,
                kind: ActorKind::Bot,
            }],
            list_requests_result: RequestsPage {
                items: vec![],
                total: 0,
                page: 1,
                page_size: 20,
            },
        }
    }
}

#[async_trait]
impl ConnectService for RecordingConnectService {
    async fn create_connect(
        &self,
        caller: &str,
        to_bot: &str,
        message: Option<String>,
        _: Option<bcs_service_api::RequestAuthHeaders>,
    ) -> ServiceResult<ConnectResult> {
        self.create_commands
            .lock()
            .await
            .push((caller.to_string(), to_bot.to_string(), message));
        Ok(self.create_result.clone())
    }

    async fn approve(&self, request_id: &str, decider: &str) -> ServiceResult<Vec<u64>> {
        self.approve_commands
            .lock()
            .await
            .push((request_id.to_string(), decider.to_string()));
        Ok(vec![211])
    }

    async fn reject(
        &self,
        request_id: &str,
        decider: &str,
        reason: Option<String>,
    ) -> ServiceResult<()> {
        self.reject_commands
            .lock()
            .await
            .push((request_id.to_string(), decider.to_string(), reason));
        Ok(())
    }

    async fn cancel(&self, request_id: &str) -> ServiceResult<()> {
        self.cancel_commands.lock().await.push(request_id.to_string());
        Ok(())
    }

    async fn get_request(&self, request_id: &str) -> ServiceResult<PermissionRequest> {
        self.request_lookup
            .lock()
            .await
            .get(request_id)
            .cloned()
            .ok_or_else(|| {
                ServiceError::FriendRequestNotFound(request_id.to_string())
            })
    }

    async fn revoke_friend(&self, caller: &str, target: &str) -> ServiceResult<Vec<u64>> {
        self.revoke_commands
            .lock()
            .await
            .push((caller.to_string(), target.to_string()));
        Ok(vec![311])
    }

    async fn list_friends(&self, actor: &str) -> ServiceResult<Vec<FriendListEntry>> {
        self.list_friends_commands.lock().await.push(actor.to_string());
        Ok(self.list_friends_result.clone())
    }

    async fn list_requests(
        &self,
        actor: &str,
        direction: RequestDirection,
        status: Option<RequestStatus>,
        page: u32,
        page_size: u32,
    ) -> ServiceResult<RequestsPage> {
        self.list_requests_commands
            .lock()
            .await
            .push((actor.to_string(), direction, status, page, page_size));
        Ok(self.list_requests_result.clone())
    }

}

struct RecordingBotQueryService {
    list_bots_by_creator_calls: Mutex<Vec<String>>,
    list_bots_by_creator_result: Option<Vec<BotListEntry>>,
}

impl Default for RecordingBotQueryService {
    fn default() -> Self {
        Self {
            list_bots_by_creator_calls: Mutex::new(Vec::new()),
            list_bots_by_creator_result: Some(vec![]),
        }
    }
}

impl RecordingBotQueryService {
    fn with_owned_bot(bot_uuid: &str, created_by: &str) -> Self {
        Self {
            list_bots_by_creator_calls: Mutex::new(Vec::new()),
            list_bots_by_creator_result: Some(vec![BotListEntry {
                bot_uuid: bot_uuid.to_string(),
                name: Some("Owned Bot".to_string()),
                summary: Some("owned summary".to_string()),
                capabilities: BotCapabilities::default(),
                status: ActorStatus::Online,
                visibility: "protected".to_string(),
                owner_actor_id: Some(format!("human_{created_by}")),
                created_by: Some(created_by.to_string()),
            }]),
        }
    }
}

#[async_trait]
impl BotQueryService for RecordingBotQueryService {
    async fn list_bots(
        &self,
        _command: bcs_service_api::application::BotListCommand,
    ) -> Result<bcs_service_api::application::BotListResult, bcs_service_api::application::BotUseCaseError>
    {
        Err(not_configured("list_bots"))
    }

    async fn get_bot(
        &self,
        _command: bcs_service_api::application::BotDetailCommand,
    ) -> Result<bcs_service_api::application::BotDetailResult, bcs_service_api::application::BotUseCaseError>
    {
        Err(not_configured("get_bot"))
    }

    async fn get_visibility(
        &self,
        _command: BotVisibilityQueryCommand,
    ) -> Result<BotVisibilityQueryResult, bcs_service_api::application::BotUseCaseError> {
        Err(not_configured("get_visibility"))
    }

    async fn list_bots_by_creator(
        &self,
        staff_no: &str,
    ) -> Result<Vec<BotListEntry>, bcs_service_api::application::BotUseCaseError> {
        self.list_bots_by_creator_calls
            .lock()
            .await
            .push(staff_no.to_string());
        match &self.list_bots_by_creator_result {
            Some(value) => Ok(value.clone()),
            None => Err(not_configured("list_bots_by_creator")),
        }
    }
}

#[derive(Clone)]
struct StaticUserIdentityPort {
    staff_no: String,
}

impl StaticUserIdentityPort {
    fn human(staff_no: &str) -> Self {
        Self {
            staff_no: staff_no.to_string(),
        }
    }
}

#[async_trait]
impl UserIdentityPort for StaticUserIdentityPort {
    async fn extract(
        &self,
        _headers: &axum::http::HeaderMap,
        _uri: &axum::http::Uri,
    ) -> Option<bcs_http::state::HttpUserIdentity> {
        Some(bcs_http::state::HttpUserIdentity {
            staff_no: Some(self.staff_no.clone()),
            nick_name: Some("Human".to_string()),
        })
    }

    async fn ensure_identity(
        &self,
        _auth_source: &str,
        _external_user_id: &str,
        _external_user_name: Option<&str>,
        _avatar: Option<&str>,
        _env: &str,
    ) -> Result<String, AuthError> {
        Err(AuthError::LookupFailed("not configured".to_string()))
    }

    async fn get_identity_by_token(
        &self,
        _token: &str,
    ) -> Result<Option<bcs_auth_api::UserIdentityInfo>, AuthError> {
        Ok(None)
    }

    async fn get_identity_by_user_id(
        &self,
        _user_id: &str,
    ) -> Result<Option<bcs_auth_api::UserIdentityInfo>, AuthError> {
        Ok(None)
    }

    async fn update_token(
        &self,
        _user_id: &str,
        _token: &str,
        _expire_at: u64,
    ) -> Result<(), AuthError> {
        Ok(())
    }
}

fn not_configured(name: &str) -> bcs_service_api::application::BotUseCaseError {
    bcs_service_api::application::BotUseCaseError::Service(ServiceError::InvalidOperation {
        message: format!("{name} is not configured"),
        request_id: None,
    })
}

async fn build_app(
    temp_dir: &TempDir,
    connect: Arc<dyn ConnectService>,
    bot_query: Arc<dyn BotQueryService>,
    user_identity: Option<Arc<dyn UserIdentityPort>>,
    token_map: Option<(&str, &str)>,
) -> axum::Router {
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    if let Some((token, bot_id)) = token_map {
        registry
            .store_token_mapping(token.to_string(), bot_id.to_string())
            .await;
    }
    let services = Services::builder()
        .registry(registry)
        .bot_query(bot_query)
        .build_for_test();
    let state = if let Some(user_identity) = user_identity {
        HttpAppState::new(services)
            .with_connect(connect)
            .with_user_identity(user_identity)
    } else {
        HttpAppState::new(services).with_connect(connect)
    };
    build_router(state)
}

#[tokio::test]
async fn friend_connection_request_requires_bearer_even_with_from_actor() {
    let temp_dir = TempDir::new().unwrap();
    let app = build_app(
        &temp_dir,
        Arc::new(RecordingConnectService::default()),
        Arc::new(RecordingBotQueryService::default()),
        None,
        None,
    )
    .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/collaboration/friend-connections/requests")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "from_actor": "bot-owned",
                        "actor_kind": "bot",
                        "to_bot": "peer-bot"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn friend_connection_request_allows_human_to_act_as_owned_bot() {
    let connect = Arc::new(RecordingConnectService::default());
    let bot_query = Arc::new(RecordingBotQueryService::with_owned_bot(
        "bot-owned",
        "10001",
    ));
    let temp_dir = TempDir::new().unwrap();
    let app = build_app(
        &temp_dir,
        connect.clone(),
        bot_query.clone(),
        Some(Arc::new(StaticUserIdentityPort::human("10001"))),
        None,
    )
    .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/collaboration/friend-connections/requests")
                .header("authorization", "Bearer human-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "from_actor": "bot-owned",
                        "actor_kind": "bot",
                        "to_bot": "peer-bot",
                        "message": "hello"
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
    assert_eq!(json["data"]["status"], "pending");
    assert_eq!(json["data"]["request_ids"], serde_json::json!(["1"]));

    let calls = connect.create_commands.lock().await;
    assert_eq!(
        calls.as_slice(),
        &[("bot-owned".to_string(), "peer-bot".to_string(), Some("hello".to_string()))]
    );
    let owner_calls = bot_query.list_bots_by_creator_calls.lock().await;
    assert_eq!(owner_calls.as_slice(), &["10001".to_string()]);
}

#[tokio::test]
async fn friend_connection_request_rejects_unowned_bot_impersonation() {
    let connect = Arc::new(RecordingConnectService::default());
    let bot_query = Arc::new(RecordingBotQueryService::default());
    let temp_dir = TempDir::new().unwrap();
    let app = build_app(
        &temp_dir,
        connect.clone(),
        bot_query.clone(),
        Some(Arc::new(StaticUserIdentityPort::human("10001"))),
        None,
    )
    .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/collaboration/friend-connections/requests")
                .header("authorization", "Bearer human-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "from_actor": "bot-owned",
                        "actor_kind": "bot",
                        "to_bot": "peer-bot"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert!(connect.create_commands.lock().await.is_empty());
}

#[tokio::test]
async fn friend_connection_request_rejects_bot_behalf_of_other_bot() {
    let connect = Arc::new(RecordingConnectService::default());
    let bot_query = Arc::new(RecordingBotQueryService::default());
    let temp_dir = TempDir::new().unwrap();
    let app = build_app(
        &temp_dir,
        connect.clone(),
        bot_query,
        None,
        Some(("caller-token", "caller-bot")),
    )
    .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/collaboration/friend-connections/requests")
                .header("authorization", "Bearer caller-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "from_actor": "other-bot",
                        "actor_kind": "bot",
                        "to_bot": "peer-bot"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert!(connect.create_commands.lock().await.is_empty());
}

#[tokio::test]
async fn friend_connection_list_by_actor_uses_requested_actor() {
    let connect = Arc::new(RecordingConnectService::default());
    let temp_dir = TempDir::new().unwrap();
    let app = build_app(
        &temp_dir,
        connect.clone(),
        Arc::new(RecordingBotQueryService::default()),
        None,
        Some(("caller-token", "caller-bot")),
    )
    .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/collaboration/friend-connections?actor=target-bot&actor_kind=bot")
                .header("authorization", "Bearer caller-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["success"], true);
    assert_eq!(json["data"]["total"], 1);
    assert_eq!(json["data"]["items"][0]["actor_id"], "friend-bot");

    let calls = connect.list_friends_commands.lock().await;
    assert_eq!(calls.as_slice(), &["target-bot".to_string()]);
}

#[tokio::test]
async fn friend_connection_list_by_actor_translates_human_actor_kind() {
    let connect = Arc::new(RecordingConnectService::default());
    let temp_dir = TempDir::new().unwrap();
    let app = build_app(
        &temp_dir,
        connect.clone(),
        Arc::new(RecordingBotQueryService::default()),
        None,
        Some(("caller-token", "caller-bot")),
    )
    .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/collaboration/friend-connections?actor=10001&actor_kind=human")
                .header("authorization", "Bearer caller-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let calls = connect.list_friends_commands.lock().await;
    assert_eq!(calls.as_slice(), &["human_10001".to_string()]);
}

#[tokio::test]
async fn friend_connection_request_maps_connect_status_variants_and_accepted_alias() {
    let connect_approved = Arc::new(RecordingConnectService {
        create_result: ConnectResult {
            request_ids: vec!["101".to_string()],
            edge_ids: vec![111],
            status: ConnectStatus::Approved,
            auto_accepted: true,
        },
        ..RecordingConnectService::default()
    });
    let temp_dir = TempDir::new().unwrap();
    let app = build_app(
        &temp_dir,
        connect_approved.clone(),
        Arc::new(RecordingBotQueryService::default()),
        None,
        Some(("caller-token", "caller-bot")),
    )
    .await;

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/collaboration/friend-connections/requests")
                .header("authorization", "Bearer caller-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "to_bot": "peer-bot"
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
    assert_eq!(json["data"]["status"], "approved");

    let connect_public = Arc::new(RecordingConnectService {
        create_result: ConnectResult {
            request_ids: vec!["102".to_string()],
            edge_ids: vec![112],
            status: ConnectStatus::PublicNoEdge,
            auto_accepted: true,
        },
        ..RecordingConnectService::default()
    });
    let app = build_app(
        &temp_dir,
        connect_public.clone(),
        Arc::new(RecordingBotQueryService::default()),
        None,
        Some(("caller-token", "caller-bot")),
    )
    .await;

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/collaboration/friend-connections/requests")
                .header("authorization", "Bearer caller-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "to_bot": "peer-bot"
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
    assert_eq!(json["data"]["status"], "public_no_edge");

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/collaboration/friend-connections/requests?direction=all&status=accepted&page=2&page_size=10")
                .header("authorization", "Bearer caller-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let calls = connect_public.list_requests_commands.lock().await;
    assert_eq!(calls.len(), 1);
    let (_, direction, status, page, page_size) = &calls[0];
    assert!(matches!(direction, RequestDirection::All));
    assert!(matches!(status, Some(RequestStatus::Approved)));
    assert_eq!((*page, *page_size), (2, 10));
}


#[tokio::test]
async fn friend_connection_request_accepts_via_recorded_service() {
    let connect = Arc::new(RecordingConnectService::default());
    let temp_dir = TempDir::new().unwrap();
    let app = build_app(
        &temp_dir,
        connect.clone(),
        Arc::new(RecordingBotQueryService::default()),
        None,
        Some(("caller-token", "caller-bot")),
    )
    .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/collaboration/friend-connections/requests/77/accept")
                .header("authorization", "Bearer caller-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["success"], true);
    assert_eq!(json["data"]["edge_ids"], serde_json::json!([211]));

    let calls = connect.approve_commands.lock().await;
    assert_eq!(calls.as_slice(), &[("77".to_string(), "caller-bot".to_string())]);
}

#[tokio::test]
async fn friend_connection_request_rejects_with_reason() {
    let connect = Arc::new(RecordingConnectService::default());
    let temp_dir = TempDir::new().unwrap();
    let app = build_app(
        &temp_dir,
        connect.clone(),
        Arc::new(RecordingBotQueryService::default()),
        None,
        Some(("caller-token", "caller-bot")),
    )
    .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/collaboration/friend-connections/requests/88/reject")
                .header("authorization", "Bearer caller-token")
                .header("content-type", "application/json")
                .body(Body::from(serde_json::json!({ "reason": "nope" }).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["success"], true);
    assert_eq!(json["data"]["status"], "rejected");

    let calls = connect.reject_commands.lock().await;
    assert_eq!(
        calls.as_slice(),
        &[("88".to_string(), "caller-bot".to_string(), Some("nope".to_string()))]
    );
}

#[tokio::test]
async fn friend_connection_request_cancel_and_revoke_delegate_to_service() {
    let connect = Arc::new(RecordingConnectService::default());
    connect.request_lookup.lock().await.insert(
        "99".to_string(),
        PermissionRequest {
            request_id: "99".to_string(),
            edge_id: None,
            env: "dev".to_string(),
            from_id: "caller-bot".to_string(),
            to_id: "peer-bot".to_string(),
            request_kind: RequestKind::Connect,
            requested_ref_id: None,
            requested_rules: None,
            message: None,
            status: RequestStatus::Pending,
            decision_reason: None,
            created_by: "caller-bot".to_string(),
            decided_by: None,
            decided_at: None,
        },
    );
    let temp_dir = TempDir::new().unwrap();
    let app = build_app(
        &temp_dir,
        connect.clone(),
        Arc::new(RecordingBotQueryService::default()),
        None,
        Some(("caller-token", "caller-bot")),
    )
    .await;

    let cancel_response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/collaboration/friend-connections/requests/99/cancel")
                .header("authorization", "Bearer caller-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(cancel_response.status(), StatusCode::OK);
    let cancel_body = to_bytes(cancel_response.into_body(), usize::MAX).await.unwrap();
    let cancel_json: Value = serde_json::from_slice(&cancel_body).unwrap();
    assert_eq!(cancel_json["data"]["status"], "cancelled");

    let revoke_response = app
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri("/collaboration/friend-connections/peer-bot")
                .header("authorization", "Bearer caller-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(revoke_response.status(), StatusCode::OK);
    let revoke_body = to_bytes(revoke_response.into_body(), usize::MAX).await.unwrap();
    let revoke_json: Value = serde_json::from_slice(&revoke_body).unwrap();
    assert_eq!(revoke_json["data"]["revoked_edges"], serde_json::json!([311]));

    let cancel_calls = connect.cancel_commands.lock().await;
    assert_eq!(cancel_calls.as_slice(), &["99".to_string()]);
    let revoke_calls = connect.revoke_commands.lock().await;
    assert_eq!(revoke_calls.as_slice(), &[("caller-bot".to_string(), "peer-bot".to_string())]);
}


#[tokio::test]
async fn friend_connection_request_cancel_rejects_other_caller() {
    let connect = Arc::new(RecordingConnectService::default());
    connect.request_lookup.lock().await.insert(
        "100".to_string(),
        PermissionRequest {
            request_id: "100".to_string(),
            edge_id: None,
            env: "dev".to_string(),
            from_id: "other-bot".to_string(),
            to_id: "peer-bot".to_string(),
            request_kind: RequestKind::Connect,
            requested_ref_id: None,
            requested_rules: None,
            message: None,
            status: RequestStatus::Pending,
            decision_reason: None,
            created_by: "other-bot".to_string(),
            decided_by: None,
            decided_at: None,
        },
    );
    let temp_dir = TempDir::new().unwrap();
    let app = build_app(
        &temp_dir,
        connect.clone(),
        Arc::new(RecordingBotQueryService::default()),
        None,
        Some(("caller-token", "caller-bot")),
    )
    .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/collaboration/friend-connections/requests/100/cancel")
                .header("authorization", "Bearer caller-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert!(connect.cancel_commands.lock().await.is_empty());
}

#[tokio::test]
async fn friend_connection_requests_list_routes_through_service() {
    let connect = Arc::new(RecordingConnectService::default());
    let temp_dir = TempDir::new().unwrap();
    let app = build_app(
        &temp_dir,
        connect.clone(),
        Arc::new(RecordingBotQueryService::default()),
        None,
        Some(("caller-token", "caller-bot")),
    )
    .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/collaboration/friend-connections/requests?direction=sent&status=approved&page=2&page_size=10")
                .header("authorization", "Bearer caller-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["data"]["page"], 1);
    assert_eq!(json["data"]["page_size"], 20);

    let calls = connect.list_requests_commands.lock().await;
    assert_eq!(calls.len(), 1);
    let (actor, direction, status, page, page_size) = &calls[0];
    assert_eq!(actor, "caller-bot");
    assert!(matches!(direction, RequestDirection::Sent));
    assert!(matches!(status, Some(RequestStatus::Approved)));
    assert_eq!((*page, *page_size), (2, 10));
}

#[tokio::test]
async fn friend_connection_list_by_actor_defaults_to_raw_actor_when_kind_is_missing() {
    let connect = Arc::new(RecordingConnectService::default());
    let temp_dir = TempDir::new().unwrap();
    let app = build_app(
        &temp_dir,
        connect.clone(),
        Arc::new(RecordingBotQueryService::default()),
        None,
        Some(("caller-token", "caller-bot")),
    )
    .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/collaboration/friend-connections?actor=bot-raw")
                .header("authorization", "Bearer caller-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let calls = connect.list_friends_commands.lock().await;
    assert_eq!(calls.as_slice(), &["bot-raw".to_string()]);
}

#[tokio::test]
async fn friend_connection_requests_route_maps_status_filters_and_default_direction() {
    let connect = Arc::new(RecordingConnectService::default());
    let temp_dir = TempDir::new().unwrap();
    let app = build_app(
        &temp_dir,
        connect.clone(),
        Arc::new(RecordingBotQueryService::default()),
        None,
        Some(("caller-token", "caller-bot")),
    )
    .await;

    let cases = [
        ("", None),
        ("?status=pending", Some(RequestStatus::Pending)),
        ("?status=approved", Some(RequestStatus::Approved)),
        ("?status=rejected", Some(RequestStatus::Rejected)),
        ("?status=cancelled", Some(RequestStatus::Cancelled)),
        ("?status=accepted", Some(RequestStatus::Approved)),
        ("?status=unknown", None),
    ];

    for (suffix, expected_status) in cases {
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("GET")
                    .uri(format!("/collaboration/friend-connections/requests{suffix}"))
                    .header("authorization", "Bearer caller-token")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);

        let calls = connect.list_requests_commands.lock().await;
        let (_, direction, status, _, _) = calls.last().expect("list request command");
        if suffix.is_empty() {
            assert!(matches!(direction, RequestDirection::Received));
        }
        assert_eq!(status, &expected_status);
    }
}
