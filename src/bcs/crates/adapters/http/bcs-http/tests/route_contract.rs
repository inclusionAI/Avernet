use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_auth_api::{AuthPluginChain, AuthPrincipal};
use bcs_auth_local::StaticAuthPlugin;
use bcs_bot::{ActorDirectory, Bot, BotOnboarding, BotCore, HumanActor};
use bcs_friend::Friend;
use bcs_group::{GroupManagement, GroupStore};
use bcs_group_store::MemoryGroupRepo;
use bcs_session::SessionManagementServiceImpl;
use bcs_session_store::MemorySessionRepo;
use bcs_http::{
    router::build_router,
    state::{
        ChatRunCleanupPort, ChainUserIdentityPort, HttpAppState, VisibilitySyncPort,
        VisibilitySyncRequest,
    },
};
use bcs_service_api::{
    A2aChatCommand, A2aChatOutcome, A2aChatRunService, A2aChatService, A2aRunStatus,
    ActorDirectoryService, ActorStatus, AsyncA2aChatAccepted, AsyncA2aChatCommand,
    BlockingA2aChatCommand, BlockingA2aChatOutcome, BotCapabilities, BotOnboardingService,
    BotRegistryCoreService, CallerContext, ChatRunCancelCommand, ChatRunQueryCommand,
    FriendCoreService, FriendRequest, FriendRequestCoreService, FriendRequestDirection,
    FriendRequestStatus, FriendService, Group, GroupCoreService, GroupKind, GroupStatus,
    GroupStrategy, HumanActorService, Participant, ParticipantRole, RelationCoreService,
    ServiceResult, SessionRepoPort, Skill,
};
use bcs_services_container::{Services, ServicesBuilder};
use bcs_test_support::{NoopFriendCoreService, NoopRelationCoreService};
use serde_json::Value;
use std::sync::Arc;
use tempfile::TempDir;
use tokio::sync::{Mutex, Notify};
use tower::ServiceExt;

#[derive(Default)]
struct RecordingA2aChat {
    get_runs: Mutex<Vec<(String, String)>>,
    cancel_runs: Mutex<Vec<(String, String)>>,
    run_state: Mutex<Option<String>>,
}

impl RecordingA2aChat {
    async fn set_run_state(&self, state: &str) {
        *self.run_state.lock().await = Some(state.to_string());
    }
}

#[async_trait::async_trait]
impl A2aChatRunService for RecordingA2aChat {
    async fn run_blocking_chat(
        &self,
        _cmd: BlockingA2aChatCommand,
    ) -> ServiceResult<BlockingA2aChatOutcome> {
        unreachable!("run_blocking_chat is not used by route contract tests")
    }

    async fn start_async_chat(
        &self,
        _cmd: AsyncA2aChatCommand,
    ) -> ServiceResult<AsyncA2aChatAccepted> {
        unreachable!("start_async_chat is not used by route contract tests")
    }

    async fn get_run(&self, cmd: ChatRunQueryCommand) -> ServiceResult<A2aRunStatus> {
        A2aChatService::get_run(self, cmd.caller, &cmd.run_id).await
    }

    async fn cancel_run(&self, cmd: ChatRunCancelCommand) -> ServiceResult<A2aRunStatus> {
        A2aChatService::cancel_run(self, cmd.caller, &cmd.run_id).await
    }
}

#[async_trait::async_trait]
impl A2aChatService for RecordingA2aChat {
    async fn chat(&self, _cmd: A2aChatCommand) -> ServiceResult<A2aChatOutcome> {
        unreachable!("chat is not used by route contract tests")
    }

    async fn get_run(&self, caller: CallerContext, run_id: &str) -> ServiceResult<A2aRunStatus> {
        let caller_id = match caller {
            CallerContext::Bot(bot) => bot.bot_uuid,
            other => format!("{other:?}"),
        };
        self.get_runs
            .lock()
            .await
            .push((caller_id, run_id.to_string()));
        let state = self
            .run_state
            .lock()
            .await
            .clone()
            .unwrap_or_else(|| "completed".to_string());
        let is_terminal = state == "completed";
        Ok(A2aRunStatus {
            run_id: run_id.to_string(),
            status: state.clone(),
            response: Some(serde_json::json!({
                "state": state,
                "content": "done",
                "version": 7,
                "is_terminal": is_terminal
            })),
        })
    }

    async fn wait_run(
        &self,
        _caller: CallerContext,
        _run_id: &str,
        _since_version: u64,
        _wait_ms: u64,
    ) -> ServiceResult<A2aRunStatus> {
        unreachable!("wait_run is not used by this test")
    }

    async fn record_run_event(&self, _run_id: &str, _event_json: &str) -> ServiceResult<bool> {
        unreachable!("record_run_event is not used by route contract tests")
    }

    async fn fail_run_if_open(&self, _run_id: &str, _error: &str) -> ServiceResult<bool> {
        unreachable!("fail_run_if_open is not used by route contract tests")
    }

    async fn cancel_run(&self, caller: CallerContext, run_id: &str) -> ServiceResult<A2aRunStatus> {
        let caller_id = match caller {
            CallerContext::Bot(bot) => bot.bot_uuid,
            other => format!("{other:?}"),
        };
        self.cancel_runs
            .lock()
            .await
            .push((caller_id, run_id.to_string()));
        Ok(A2aRunStatus {
            run_id: run_id.to_string(),
            status: "cancelled".to_string(),
            response: Some(serde_json::json!({
                "state": "cancelled",
                "content": "partial",
                "cancelled": true,
                "version": 3
            })),
        })
    }

    async fn cleanup_expired(
        &self,
        _now_ms: u64,
        _retention_ms: u64,
    ) -> ServiceResult<(Vec<String>, Vec<String>)> {
        unreachable!("cleanup_expired is not used by route contract tests")
    }
}

#[derive(Default)]
struct RecordingChatRunCleanup {
    unregistered: Mutex<Vec<String>>,
}

#[async_trait::async_trait]
impl ChatRunCleanupPort for RecordingChatRunCleanup {
    async fn unregister(&self, run_id: &str) {
        self.unregistered.lock().await.push(run_id.to_string());
    }
}

fn static_auth_chain(staff_no: &str, nick_name: &str) -> Arc<AuthPluginChain> {
    let principal = AuthPrincipal {
        user_id: Some(staff_no.to_string()),
        user_name: Some(nick_name.to_string()),
        ..Default::default()
    };
    Arc::new(AuthPluginChain::new(vec![Box::new(StaticAuthPlugin::with_principal(principal))]))
}

#[derive(Default)]
struct RecordingVisibilitySyncPort {
    requests: Mutex<Vec<VisibilitySyncRequest>>,
    notify: Notify,
}

#[async_trait::async_trait]
impl VisibilitySyncPort for RecordingVisibilitySyncPort {
    async fn sync_visibility(&self, request: VisibilitySyncRequest) {
        self.requests.lock().await.push(request);
        self.notify.notify_waiters();
    }
}

struct SlowVisibilitySyncPort {
    requests: Mutex<Vec<VisibilitySyncRequest>>,
    notify: Notify,
    delay: std::time::Duration,
}

impl SlowVisibilitySyncPort {
    fn new(delay: std::time::Duration) -> Self {
        Self {
            requests: Mutex::new(Vec::new()),
            notify: Notify::new(),
            delay,
        }
    }
}

#[async_trait::async_trait]
impl VisibilitySyncPort for SlowVisibilitySyncPort {
    async fn sync_visibility(&self, request: VisibilitySyncRequest) {
        self.requests.lock().await.push(request);
        self.notify.notify_waiters();
        tokio::time::sleep(self.delay).await;
    }
}

#[derive(Default)]
struct StaticFriendCoreService {
    friends: Vec<(String, String)>,
}

impl StaticFriendCoreService {
    fn new(friends: Vec<(&str, &str)>) -> Self {
        Self {
            friends: friends
                .into_iter()
                .map(|(left, right)| (left.to_string(), right.to_string()))
                .collect(),
        }
    }
}

fn noop_relation() -> Arc<dyn RelationCoreService> {
    Arc::new(NoopRelationCoreService)
}

fn actor_directory_use_cases(
    registry: Arc<dyn BotRegistryCoreService>,
    friend: Arc<dyn FriendCoreService>,
) -> Arc<dyn ActorDirectoryService> {
    Arc::new(ActorDirectory::new(registry, friend, noop_relation()))
}

fn friend_use_cases(
    registry: Arc<dyn BotRegistryCoreService>,
    friend: Arc<dyn FriendCoreService>,
    friend_request: Arc<dyn FriendRequestCoreService>,
) -> Arc<dyn FriendService> {
    Arc::new(Friend::new(
        registry,
        friend,
        friend_request,
        noop_relation(),
    ))
}

fn human_actor_use_cases(registry: Arc<dyn BotRegistryCoreService>) -> Arc<dyn HumanActorService> {
    Arc::new(HumanActor::new(registry, noop_relation()))
}

fn bot_onboarding_use_cases(
    registry: Arc<dyn BotRegistryCoreService>,
) -> Arc<dyn BotOnboardingService> {
    Arc::new(BotOnboarding::new(registry, noop_relation(), true, None))
}

fn services_builder_with_bot_use_cases(registry: Arc<BotCore>) -> ServicesBuilder {
    services_builder_with_bot_use_cases_and_friend(registry, Arc::new(NoopFriendCoreService))
}

fn services_builder_with_bot_use_cases_and_friend(
    registry: Arc<BotCore>,
    friend: Arc<dyn FriendCoreService>,
) -> ServicesBuilder {
    let registry_service: Arc<dyn BotRegistryCoreService> = registry;
    let bot_use_cases = Arc::new(Bot::new_with_friend(
        registry_service.clone(),
        friend.clone(),
    ));
    Services::builder()
        .registry(registry_service)
        .friend(friend)
        .bot_query(bot_use_cases.clone())
        .bot_management(bot_use_cases.clone())
        .bot_discovery(bot_use_cases)
}

fn services_builder_with_group_use_cases(
    group: Arc<GroupStore>,
    registry: Arc<BotCore>,
    friend: Arc<dyn FriendCoreService>,
) -> ServicesBuilder {
    let group_service: Arc<dyn GroupCoreService> = group;
    let registry_service: Arc<dyn BotRegistryCoreService> = registry;
    let group_use_cases = Arc::new(GroupManagement::with_defaults(
        group_service.clone(),
        registry_service.clone(),
        friend.clone(),
    ));
    Services::builder()
        .registry(registry_service)
        .friend(friend)
        .group(group_service)
        .group_management(group_use_cases.clone())
        .group_query(group_use_cases)
}

struct RecordingFriendRequestCoreService {
    request: FriendRequest,
    created: Mutex<Vec<(String, String)>>,
    listed: Mutex<Vec<(String, FriendRequestDirection, Option<FriendRequestStatus>)>>,
    accepted: Mutex<Vec<String>>,
    rejected: Mutex<Vec<String>>,
}

impl RecordingFriendRequestCoreService {
    fn new(request: FriendRequest) -> Self {
        Self {
            request,
            created: Mutex::new(Vec::new()),
            listed: Mutex::new(Vec::new()),
            accepted: Mutex::new(Vec::new()),
            rejected: Mutex::new(Vec::new()),
        }
    }
}

#[async_trait::async_trait]
impl FriendRequestCoreService for RecordingFriendRequestCoreService {
    async fn create_request(&self, from_bot: &str, to_bot: &str) -> ServiceResult<FriendRequest> {
        self.created
            .lock()
            .await
            .push((from_bot.to_string(), to_bot.to_string()));
        let mut request = self.request.clone();
        request.from_bot = from_bot.to_string();
        request.to_bot = to_bot.to_string();
        Ok(request)
    }

    async fn accept_request(&self, request_id: &str) -> ServiceResult<()> {
        self.accepted.lock().await.push(request_id.to_string());
        Ok(())
    }

    async fn reject_request(&self, request_id: &str) -> ServiceResult<()> {
        self.rejected.lock().await.push(request_id.to_string());
        Ok(())
    }

    async fn get_request(&self, _request_id: &str) -> ServiceResult<FriendRequest> {
        Ok(self.request.clone())
    }

    async fn list_requests(
        &self,
        bot_id: &str,
        direction: FriendRequestDirection,
        status_filter: Option<FriendRequestStatus>,
    ) -> Vec<FriendRequest> {
        self.listed
            .lock()
            .await
            .push((bot_id.to_string(), direction, status_filter));
        vec![self.request.clone()]
    }

    async fn cancel_pending_requests(&self, _bot_id: &str) -> ServiceResult<usize> {
        unreachable!("cancel_pending_requests is not used by route contract tests")
    }
}

fn pending_friend_request(from_bot: &str, to_bot: &str) -> FriendRequest {
    FriendRequest {
        id: "request-1".to_string(),
        from_bot: from_bot.to_string(),
        to_bot: to_bot.to_string(),
        status: FriendRequestStatus::Pending,
        created_at: 11,
        updated_at: 12,
    }
}

#[async_trait::async_trait]
impl FriendCoreService for StaticFriendCoreService {
    async fn list_friends(&self, bot_id: &str) -> Vec<String> {
        self.friends
            .iter()
            .filter_map(|(left, right)| {
                if left == bot_id {
                    Some(right.clone())
                } else if right == bot_id {
                    Some(left.clone())
                } else {
                    None
                }
            })
            .collect()
    }

    async fn are_friends(&self, bot_a: &str, bot_b: &str) -> bool {
        self.friends.iter().any(|(left, right)| {
            (left == bot_a && right == bot_b) || (left == bot_b && right == bot_a)
        })
    }

    async fn are_all_friends(&self, bot_id: &str, others: &[String]) -> ServiceResult<()> {
        for other in others {
            if !self.are_friends(bot_id, other).await {
                return Err(bcs_service_api::ServiceError::NotFriends(vec![
                    other.clone(),
                ]));
            }
        }
        Ok(())
    }

    async fn add_friendship(&self, _bot_a: &str, _bot_b: &str) -> ServiceResult<()> {
        unreachable!("add_friendship is not used by route contract tests")
    }

    async fn remove_all_friendships(&self, _bot_id: &str) -> ServiceResult<usize> {
        unreachable!("remove_all_friendships is not used by route contract tests")
    }
}

#[tokio::test]
async fn health_route_is_served_by_http_adapter_router() {
    let app = build_router(HttpAppState::new(Services::noop()));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/health")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
}

#[tokio::test]
async fn chat_run_route_uses_a2a_service_and_bot_token_identity() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("caller-bot".to_string(), BotCapabilities::default())
        .await
        .unwrap();
    registry
        .store_token_mapping("token-1".to_string(), "caller-bot".to_string())
        .await;
    let a2a_chat = Arc::new(RecordingA2aChat::default());
    let services = Services::builder()
        .registry(registry)
        .a2a_chat(a2a_chat.clone())
        .a2a_chat_runs(a2a_chat.clone())
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/chat/runs/run-1")
                .header("authorization", "Bearer token-1")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["run_id"], "run-1");
    assert_eq!(json["state"], "completed");
    assert_eq!(json["response"]["content"], "done");

    let calls = a2a_chat.get_runs.lock().await;
    assert_eq!(
        calls.as_slice(),
        &[("caller-bot".to_string(), "run-1".to_string())]
    );
}

#[tokio::test]
async fn chat_run_route_downgrades_submitted_for_legacy_chat_version() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("caller-bot".to_string(), BotCapabilities::default())
        .await
        .unwrap();
    registry
        .store_token_mapping("token-1".to_string(), "caller-bot".to_string())
        .await;
    let a2a_chat = Arc::new(RecordingA2aChat::default());
    a2a_chat.set_run_state("submitted").await;
    let services = Services::builder()
        .registry(registry)
        .a2a_chat(a2a_chat.clone())
        .a2a_chat_runs(a2a_chat)
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/chat/runs/run-1")
                .header("authorization", "Bearer token-1")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["state"], "running");
    assert_eq!(json["is_terminal"], false);
}

#[tokio::test]
async fn chat_run_route_returns_submitted_for_chat_version_2() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("caller-bot".to_string(), BotCapabilities::default())
        .await
        .unwrap();
    registry
        .store_token_mapping("token-1".to_string(), "caller-bot".to_string())
        .await;
    let a2a_chat = Arc::new(RecordingA2aChat::default());
    a2a_chat.set_run_state("submitted").await;
    let services = Services::builder()
        .registry(registry)
        .a2a_chat(a2a_chat.clone())
        .a2a_chat_runs(a2a_chat)
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/chat/runs/run-1")
                .header("authorization", "Bearer token-1")
                .header("X-BCS-CHAT-VERSION", "2")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["state"], "submitted");
    assert_eq!(json["is_terminal"], false);
}

#[tokio::test]
async fn bots_route_lists_onboarded_bots_and_downgrades_skills() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "bot-alpha".to_string(),
            BotCapabilities {
                name: Some("Alpha".to_string()),
                summary: Some("Alpha bot".to_string()),
                skills: vec![
                    Skill::with_description("review", "Review code"),
                    Skill::new("ops"),
                ],
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .register("bot-unonboarded".to_string(), BotCapabilities::default())
        .await
        .unwrap();
    let services = services_builder_with_bot_use_cases(registry).build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots?offset=0&limit=10")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    let items = json.as_array().unwrap();
    assert_eq!(items.len(), 1);
    assert_eq!(items[0]["bot_uuid"], "bot-alpha");
    assert_eq!(
        items[0]["capabilities"]["skills"],
        serde_json::json!(["review", "ops"])
    );
}

#[tokio::test]
async fn bots_paged_route_filters_by_user_suffix() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for bot_id in ["agent:alice", "agent:bob"] {
        registry
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    summary: Some("Test bot".to_string()),
                    ..BotCapabilities::default()
                },
            )
            .await
            .unwrap();
    }
    let services = services_builder_with_bot_use_cases(registry).build_for_test();
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
    assert_eq!(json["items"].as_array().unwrap().len(), 1);
    assert_eq!(json["items"][0]["bot_uuid"], "agent:alice");
}

#[tokio::test]
async fn get_bot_route_returns_registration_and_effective_status() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "bot-alpha".to_string(),
            BotCapabilities {
                name: Some("Alpha".to_string()),
                summary: Some("Alpha bot".to_string()),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .register_streaming_connection("bot-alpha".to_string())
        .await
        .unwrap();
    let services = services_builder_with_bot_use_cases(registry).build_for_test();
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
    assert_eq!(json["actor_kind"], "bot");
    assert_eq!(json["status"], "online");
    assert_eq!(json["dynamic_status"]["status"], "active");
}

#[tokio::test]
async fn leave_bot_route_rejects_bot_token_without_human_identity() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("bot-leave".to_string(), BotCapabilities::default())
        .await
        .unwrap();
    registry
        .store_token_mapping("leave-token".to_string(), "bot-leave".to_string())
        .await;
    let services = services_builder_with_bot_use_cases(registry.clone()).build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri("/bots/bot-leave")
                .header("authorization", "Bearer leave-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert!(registry.get("bot-leave").await.is_some());
}

#[tokio::test]
async fn leave_bot_route_soft_deletes_owner_bot() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("bot-leave".to_string(), BotCapabilities::default())
        .await
        .unwrap();
    registry
        .save_created_by("bot-leave", "alice", true)
        .await
        .unwrap();
    let services = services_builder_with_bot_use_cases(registry.clone()).build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri("/bots/bot-leave")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["left"], true);
    assert_eq!(json["bot_uuid"], "bot-leave");
    assert!(registry.get("bot-leave").await.is_none());
}

#[tokio::test]
async fn leave_bot_route_rejects_non_owner_human_identity() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("bot-leave".to_string(), BotCapabilities::default())
        .await
        .unwrap();
    registry
        .save_created_by("bot-leave", "alice", true)
        .await
        .unwrap();
    let services = services_builder_with_bot_use_cases(registry.clone()).build_for_test();
    let chain = static_auth_chain("bob", "Bob");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri("/bots/bot-leave")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert!(registry.get("bot-leave").await.is_some());
}

#[tokio::test]
async fn get_visibility_route_returns_normalized_visibility() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "bot-visible".to_string(),
            BotCapabilities {
                visibility: "protected".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .store_token_mapping("visible-token".to_string(), "bot-visible".to_string())
        .await;
    let services = services_builder_with_bot_use_cases(registry).build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/bot-visible/visibility")
                .header("authorization", "Bearer visible-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["success"], true);
    assert_eq!(json["data"]["bot_uuid"], "bot-visible");
    assert_eq!(json["data"]["visibility"], "protected");
}

#[tokio::test]
async fn get_visibility_route_rejects_invalid_bot_token() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "bot-visible".to_string(),
            BotCapabilities {
                visibility: "protected".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    let services = services_builder_with_bot_use_cases(registry).build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/bot-visible/visibility")
                .header("authorization", "Bearer invalid-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["success"], false);
    assert_eq!(json["error"], "valid bot token is required");
}

#[tokio::test]
async fn set_visibility_route_updates_registry_and_triggers_sync_port() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "bot-visible".to_string(),
            BotCapabilities {
                name: Some("Visible".to_string()),
                summary: Some("Visibility test".to_string()),
                domains: vec!["ops".to_string()],
                skills: vec![Skill::new("monitor")],
                visibility: "private".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .store_token_mapping("visible-token".to_string(), "bot-visible".to_string())
        .await;
    let sync = Arc::new(RecordingVisibilitySyncPort::default());
    let services = services_builder_with_bot_use_cases(registry.clone()).build_for_test();
    let app = build_router(HttpAppState::new(services).with_visibility_sync(sync.clone()));

    let response = app
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/bots/bot-visible/visibility")
                .header("authorization", "Bearer visible-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "visibility": "protected"
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
    assert_eq!(json["data"]["bot_uuid"], "bot-visible");
    assert_eq!(json["data"]["visibility"], "protected");

    let stored = registry.get("bot-visible").await.unwrap();
    assert_eq!(stored.capabilities.visibility, "protected");

    let sync_request = tokio::time::timeout(std::time::Duration::from_secs(1), async {
        loop {
            let notified = sync.notify.notified();
            {
                let requests = sync.requests.lock().await;
                if let Some(request) = requests.first().cloned() {
                    return request;
                }
            }
            notified.await;
        }
    })
    .await
    .unwrap();
    assert_eq!(sync_request.bot_uuid, "bot-visible");
    assert_eq!(sync_request.visibility, "protected");
    assert_eq!(sync_request.capabilities.name.as_deref(), Some("Visible"));
    assert_eq!(
        sync_request.capabilities.summary.as_deref(),
        Some("Visibility test")
    );
    assert_eq!(sync_request.capabilities.domains, vec!["ops".to_string()]);
    assert_eq!(sync_request.capabilities.skills[0].name, "monitor");
}

#[tokio::test]
async fn query_bots_route_filters_to_onboarded_and_returns_status_fields() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "bot-alpha".to_string(),
            BotCapabilities {
                name: Some("Alpha".to_string()),
                summary: Some("Alpha bot".to_string()),
                visibility: "private".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .register("bot-unonboarded".to_string(), BotCapabilities::default())
        .await
        .unwrap();
    registry
        .register_streaming_connection("bot-alpha".to_string())
        .await
        .unwrap();
    let services = services_builder_with_bot_use_cases(registry).build_for_test();
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
                        "bot_uuids": ["bot-alpha", "bot-unonboarded", "missing-bot"]
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
    let items = json.as_array().unwrap();
    assert_eq!(items.len(), 1);
    assert_eq!(items[0]["bot_uuid"], "bot-alpha");
    assert_eq!(items[0]["visibility"], "private");
    assert_eq!(items[0]["actor_kind"], "bot");
    assert_eq!(items[0]["status"], "online");
    assert_eq!(items[0]["dynamic_status"]["status"], "active");
}

#[tokio::test]
async fn connect_bot_route_registers_http_connection() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    let services = services_builder_with_bot_use_cases(registry.clone()).build_for_test();
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

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["is_new"], true);
    assert_eq!(json["bot_uuid"], "bot-alpha");
    let token = json["token"].as_str().unwrap();
    assert_eq!(
        registry.find_bot_by_token(token).await.as_deref(),
        Some("bot-alpha")
    );
}

#[tokio::test]
async fn onboard_url_route_uses_adapter_config_and_encodes_query() {
    let app = build_router(HttpAppState::new(Services::noop()).with_onboard_url_config(
        Some("https://botchat.example.com/".to_string()),
        "/bcn/register".to_string(),
    ));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/onboard/url?token=t%201&name=Alpha%20Bot&summary=Hello%20World&skills=code,ops")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(
        json["onboard_url"],
        "https://botchat.example.com/bcn/register?token=t%201&name=Alpha%20Bot&summary=Hello%20World&skills=code%2Cops"
    );
}

#[tokio::test]
async fn cancel_chat_run_route_uses_a2a_run_service() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("caller-bot".to_string(), BotCapabilities::default())
        .await
        .unwrap();
    registry
        .store_token_mapping("token-1".to_string(), "caller-bot".to_string())
        .await;
    let a2a_chat = Arc::new(RecordingA2aChat::default());
    let cleanup = Arc::new(RecordingChatRunCleanup::default());
    let services = Services::builder()
        .registry(registry)
        .a2a_chat(a2a_chat.clone())
        .a2a_chat_runs(a2a_chat.clone())
        .build_for_test();
    let app = build_router(HttpAppState::new(services).with_chat_run_cleanup(cleanup.clone()));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/chat/runs/run-2/cancel")
                .header("authorization", "Bearer token-1")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["run_id"], "run-2");
    assert_eq!(json["cancelled"], true);
    assert_eq!(json["state"], "cancelled");
    assert_eq!(json["response"]["content"], "partial");

    let calls = a2a_chat.cancel_runs.lock().await;
    assert_eq!(
        calls.as_slice(),
        &[("caller-bot".to_string(), "run-2".to_string())]
    );
    let cleanup_calls = cleanup.unregistered.lock().await;
    assert!(cleanup_calls.is_empty());
}

#[tokio::test]
async fn bot_status_route_updates_dynamic_status_with_bot_token() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "bot-alpha".to_string(),
            BotCapabilities {
                name: Some("Alpha".to_string()),
                summary: Some("Alpha bot".to_string()),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .store_token_mapping("token-1".to_string(), "bot-alpha".to_string())
        .await;
    let services = services_builder_with_bot_use_cases(registry.clone()).build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bots/status")
                .header("authorization", "Bearer token-1")
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

    let stored = registry.get("bot-alpha").await.unwrap();
    assert_eq!(stored.dynamic_status.status, "busy");
    assert_eq!(
        stored.dynamic_status.dynamic_summary.as_deref(),
        Some("running task")
    );
}

#[tokio::test]
async fn my_bots_route_uses_mock_user_identity_and_creator_filter() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for bot_id in ["bot-owned", "bot-other"] {
        registry
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    summary: Some("Test bot".to_string()),
                    skills: vec![Skill::new("ops")],
                    visibility: "protected".to_string(),
                    ..BotCapabilities::default()
                },
            )
            .await
            .unwrap();
    }
    registry
        .save_created_by("bot-owned", "alice", true)
        .await
        .unwrap();
    registry
        .save_created_by("bot-other", "bob", true)
        .await
        .unwrap();
    registry
        .register_streaming_connection("bot-owned".to_string())
        .await
        .unwrap();
    let services = services_builder_with_bot_use_cases(registry).build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/my?offset=0&limit=10")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["total"], 1);
    assert_eq!(json["items"][0]["bot_uuid"], "bot-owned");
    assert_eq!(json["items"][0]["created_by"], "alice");
    assert_eq!(json["items"][0]["visibility"], "protected");
    assert_eq!(
        json["items"][0]["capabilities"]["skills"],
        serde_json::json!(["ops"])
    );
    assert_eq!(json["items"][0]["dynamic_status"]["status"], "active");
}

#[tokio::test]
async fn me_route_returns_mock_user_identity() {
    let app = build_router(
        HttpAppState::new(Services::noop()).with_user_identity(Arc::new(ChainUserIdentityPort::new(static_auth_chain("alice", "Alice")))),
    );

    let response = app
        .oneshot(Request::builder().uri("/me").body(Body::empty()).unwrap())
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["user_id"], "alice");
    assert_eq!(json["staff_no"], "alice");
    assert_eq!(json["nick_name"], "Alice");
    assert_eq!(json["actor_uuid"], "human_alice");
}

#[tokio::test]
async fn me_repair_info_route_returns_repair_response_for_fallback_human_name() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry.ensure_human_actor("alice", "alice").await.unwrap();
    let services = Services::builder()
        .registry(registry.clone())
        .human_actors(human_actor_use_cases(registry.clone()))
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/me/repair-info")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["ok"], true);
    assert_eq!(json["actor_uuid"], "human_alice");
    assert_eq!(json["previous_name"], "alice");
    assert_eq!(json["current_name"], "Alice");
    assert_eq!(json["name_repaired"], true);
}

#[tokio::test]
async fn ensure_human_route_creates_current_user_actor() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    let services = Services::builder()
        .registry(registry.clone())
        .human_actors(human_actor_use_cases(registry.clone()))
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/me/ensure-human")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["actor_uuid"], "human_alice");
    assert_eq!(json["human_created"], true);
    assert_eq!(json["matched_bots"], serde_json::json!([]));
    assert_eq!(json["edges_created"], 0);
    assert_eq!(json["edges_upgraded"], 0);

    let stored = registry.get("human_alice").await.unwrap();
    assert_eq!(stored.actor_kind, bcs_service_api::ActorKind::Human);
    assert_eq!(stored.capabilities.name.as_deref(), Some("Alice"));
}

#[tokio::test]
async fn actor_status_route_allows_actor_self_update() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("actor-bot".to_string(), BotCapabilities::default())
        .await
        .unwrap();
    registry
        .store_token_mapping("actor-token".to_string(), "actor-bot".to_string())
        .await;
    let friend = Arc::new(StaticFriendCoreService::default());
    let services = Services::builder()
        .registry(registry.clone())
        .friend(friend.clone())
        .actor_directory(actor_directory_use_cases(registry.clone(), friend))
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/actors/actor-bot/status")
                .header("authorization", "Bearer actor-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "status": "hidden"
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
    assert_eq!(json["data"]["actor_id"], "actor-bot");
    assert_eq!(json["data"]["status"], "hidden");
    assert_eq!(
        registry.get("actor-bot").await.unwrap().status,
        ActorStatus::Hidden
    );
}

#[tokio::test]
async fn discover_bots_route_filters_private_and_marks_collaboration_friends() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for (bot_id, name, visibility) in [
        ("caller-bot", "Caller", "public"),
        ("public-bot", "Public", "public"),
        ("friend-bot", "Friend", "protected"),
        ("stranger-bot", "Stranger", "protected"),
        ("private-bot", "Private", "private"),
    ] {
        registry
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(name.to_string()),
                    summary: Some(format!("{name} summary")),
                    visibility: visibility.to_string(),
                    ..BotCapabilities::default()
                },
            )
            .await
            .unwrap();
    }
    let friend = Arc::new(StaticFriendCoreService::new(vec![(
        "caller-bot",
        "friend-bot",
    )]));
    let services =
        services_builder_with_bot_use_cases_and_friend(registry, friend).build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/discover?collaborate_bot=caller-bot")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    let bots = json["bots"].as_array().unwrap();
    let mut ids: Vec<String> = bots
        .iter()
        .map(|bot| bot["bot_uuid"].as_str().unwrap().to_string())
        .collect();
    ids.sort();
    assert_eq!(ids, vec!["caller-bot", "friend-bot", "public-bot"]);
    let friend = bots
        .iter()
        .find(|bot| bot["bot_uuid"] == "friend-bot")
        .unwrap();
    assert_eq!(friend["is_friend"], true);
    assert_eq!(json["count"], 3);
}

#[tokio::test]
async fn actors_list_route_uses_cooperatable_filter_and_actor_shape() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for (bot_id, name, visibility, skills) in [
        ("current-bot", "Current", "public", vec!["self"]),
        ("public-bot", "Public", "public", vec!["search"]),
        ("friend-bot", "Friend", "private", vec!["ops"]),
        ("stranger-bot", "Stranger", "protected", vec!["hidden"]),
    ] {
        registry
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(name.to_string()),
                    summary: Some(format!("{name} summary")),
                    visibility: visibility.to_string(),
                    skills: skills.into_iter().map(Skill::new).collect(),
                    ..BotCapabilities::default()
                },
            )
            .await
            .unwrap();
    }
    registry
        .register_streaming_connection("public-bot".to_string())
        .await
        .unwrap();
    let friend = Arc::new(StaticFriendCoreService::new(vec![(
        "current-bot",
        "friend-bot",
    )]));
    let services = Services::builder()
        .registry(registry.clone())
        .friend(friend.clone())
        .actor_directory(actor_directory_use_cases(registry, friend))
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/actors/list?current_bot_uuid=current-bot&cooperatable_only=true&page_size=10&page_no=1")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    let bots = json["bots"].as_array().unwrap();
    let mut ids: Vec<String> = bots
        .iter()
        .map(|bot| bot["bot_uuid"].as_str().unwrap().to_string())
        .collect();
    ids.sort();
    assert_eq!(ids, vec!["friend-bot", "public-bot"]);
    assert_eq!(json["total"], 2);
    let public_bot = bots
        .iter()
        .find(|bot| bot["bot_uuid"] == "public-bot")
        .unwrap();
    assert_eq!(public_bot["capabilities"]["name"], "Public");
    assert_eq!(public_bot["dynamic_status"]["status"], "active");
    assert_eq!(public_bot["is_friend"], false);
    assert_eq!(public_bot["tags"], serde_json::json!({}));
    let friend_bot = bots
        .iter()
        .find(|bot| bot["bot_uuid"] == "friend-bot")
        .unwrap();
    assert_eq!(friend_bot["visibility"], "private");
    assert_eq!(friend_bot["is_friend"], true);
}

#[tokio::test]
async fn actors_search_route_falls_back_to_registry_when_no_worker_profile_port() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for (bot_id, name, visibility, skill) in [
        ("current-bot", "Current", "public", "self"),
        ("alpha-bot", "Alpha Helper", "public", "review"),
        ("private-alpha", "Alpha Private", "private", "secret"),
    ] {
        registry
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(name.to_string()),
                    summary: Some(format!("{name} summary")),
                    visibility: visibility.to_string(),
                    skills: vec![Skill::new(skill)],
                    ..BotCapabilities::default()
                },
            )
            .await
            .unwrap();
    }
    let friend = Arc::new(StaticFriendCoreService::default());
    let services = Services::builder()
        .registry(registry.clone())
        .friend(friend.clone())
        .actor_directory(actor_directory_use_cases(registry, friend))
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/actors/search?q=Alpha&current_bot_uuid=current-bot&cooperatable_only=false")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    let bots = json["bots"].as_array().unwrap();
    assert_eq!(bots.len(), 1);
    assert_eq!(bots[0]["bot_uuid"], "alpha-bot");
    assert_eq!(bots[0]["score"], 0.0);
    assert!(
        bots[0]["short_profile"]
            .as_str()
            .unwrap()
            .contains("review")
    );
    assert_eq!(json["context"]["recommend_response"], Value::Null);
}

#[tokio::test]
async fn onboard_route_persists_capabilities_from_token_and_user_identity() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "bot-alpha".to_string(),
            BotCapabilities {
                visibility: "public".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .store_token_mapping("token-1".to_string(), "bot-alpha".to_string())
        .await;
    let services = Services::builder()
        .registry(registry.clone())
        .bot_onboarding(bot_onboarding_use_cases(registry.clone()))
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bots/onboard")
                .header("authorization", "Bearer token-1")
                .header("x-agentclaw-agent-code", "agent-code-1")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "name": "Alpha",
                        "summary": "Alpha helper",
                        "domains": ["engineering"],
                        "skills": ["review"],
                        "scopes": ["repo"]
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
    assert_eq!(json["bot_uuid"], "bot-alpha");
    assert_eq!(json["onboarded"], true);
    assert_eq!(json["name"], "Alpha");
    assert_eq!(json["binding_results"], serde_json::json!({}));
    assert_eq!(json["unbound"], serde_json::json!([]));

    let stored = registry.get("bot-alpha").await.unwrap();
    assert_eq!(stored.capabilities.name.as_deref(), Some("Alpha"));
    assert_eq!(stored.capabilities.summary.as_deref(), Some("Alpha helper"));
    assert_eq!(stored.capabilities.visibility, "public");
    assert_eq!(stored.created_by.as_deref(), Some("alice"));

    let credentials = registry.get_agent_credentials("bot-alpha").await.unwrap();
    assert_eq!(
        credentials.agent_code.as_deref(),
        Some("agent-code-1")
    );
    assert_eq!(
        credentials.agent_token.as_deref(),
        Some("Bearer token-1")
    );
}

#[tokio::test]
async fn onboard_route_returns_before_visibility_sync_finishes() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "bot-alpha".to_string(),
            BotCapabilities {
                visibility: "public".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .store_token_mapping("token-1".to_string(), "bot-alpha".to_string())
        .await;
    let services = Services::builder()
        .registry(registry.clone())
        .bot_onboarding(bot_onboarding_use_cases(registry))
        .build_for_test();
    let sync = Arc::new(SlowVisibilitySyncPort::new(
        std::time::Duration::from_millis(250),
    ));
    let app = build_router(HttpAppState::new(services).with_visibility_sync(sync.clone()));

    let response = tokio::time::timeout(
        std::time::Duration::from_millis(100),
        app.oneshot(
            Request::builder()
                .method("POST")
                .uri("/bots/onboard")
                .header("authorization", "Bearer token-1")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "name": "Alpha",
                        "summary": "Alpha helper"
                    })
                    .to_string(),
                ))
                .unwrap(),
        ),
    )
    .await
    .expect("onboard route should not wait for visibility sync")
    .unwrap();

    assert_eq!(response.status(), StatusCode::OK);

    let sync_request = tokio::time::timeout(std::time::Duration::from_secs(1), async {
        loop {
            let notified = sync.notify.notified();
            {
                let requests = sync.requests.lock().await;
                if let Some(request) = requests.first().cloned() {
                    return request;
                }
            }
            notified.await;
        }
    })
    .await
    .unwrap();
    assert_eq!(sync_request.bot_uuid, "bot-alpha");
}

#[tokio::test]
async fn admin_onboard_route_persists_existing_bot_without_token() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("bot-admin".to_string(), BotCapabilities::default())
        .await
        .unwrap();
    let services = Services::builder()
        .registry(registry.clone())
        .bot_onboarding(bot_onboarding_use_cases(registry.clone()))
        .build_for_test();
    let chain = static_auth_chain("admin", "Admin");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/admin/bots/onboard")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "bot_id": "bot-admin",
                        "name": "Admin Bot",
                        "summary": "Admin onboarded",
                        "domains": ["ops"],
                        "skills": ["deploy"],
                        "scopes": ["prod"]
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
    assert_eq!(json["bot_uuid"], "bot-admin");
    assert_eq!(json["onboarded"], true);
    assert_eq!(json["name"], "Admin Bot");

    let stored = registry.get("bot-admin").await.unwrap();
    assert_eq!(stored.capabilities.name.as_deref(), Some("Admin Bot"));
    assert_eq!(
        stored.capabilities.summary.as_deref(),
        Some("Admin onboarded")
    );
    assert_eq!(stored.created_by.as_deref(), Some("admin"));
}

#[tokio::test]
async fn admin_onboard_route_returns_onboarded_false_for_missing_bot() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    let services = Services::builder()
        .registry(registry.clone())
        .bot_onboarding(bot_onboarding_use_cases(registry))
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/admin/bots/onboard")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "bot_id": "missing-bot",
                        "name": "Missing",
                        "summary": "Missing bot"
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
    assert_eq!(json["bot_uuid"], "missing-bot");
    assert_eq!(json["onboarded"], false);
}

#[tokio::test]
async fn create_friend_request_route_uses_from_bot_fallback_and_service() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for bot_id in ["from-bot", "to-bot"] {
        registry
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    visibility: "public".to_string(),
                    ..BotCapabilities::default()
                },
            )
            .await
            .unwrap();
    }
    registry
        .save_created_by("from-bot", "alice", true)
        .await
        .unwrap();
    let friend_request = Arc::new(RecordingFriendRequestCoreService::new(
        pending_friend_request("from-bot", "to-bot"),
    ));
    let friend = Arc::new(StaticFriendCoreService::default());
    let services = services_builder_with_bot_use_cases_and_friend(registry.clone(), friend.clone())
        .friend_use_cases(friend_use_cases(registry, friend, friend_request.clone()))
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/friends/request")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "from_bot": "from-bot",
                        "to_bot": "to-bot"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::CREATED);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["success"], true);
    assert_eq!(json["data"]["id"], "request-1");
    assert_eq!(json["data"]["from_bot"], "from-bot");
    assert_eq!(json["data"]["to_bot"], "to-bot");
    let calls = friend_request.created.lock().await;
    assert_eq!(
        calls.as_slice(),
        &[("from-bot".to_string(), "to-bot".to_string())]
    );
}

#[tokio::test]
async fn list_friend_requests_route_uses_query_filters() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("bot-alpha".to_string(), BotCapabilities::default())
        .await
        .unwrap();
    registry
        .save_created_by("bot-alpha", "alice", true)
        .await
        .unwrap();
    let friend_request = Arc::new(RecordingFriendRequestCoreService::new(
        pending_friend_request("sender-bot", "bot-alpha"),
    ));
    let friend = Arc::new(StaticFriendCoreService::default());
    let services = services_builder_with_bot_use_cases_and_friend(registry.clone(), friend.clone())
        .friend_use_cases(friend_use_cases(registry, friend, friend_request.clone()))
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/friends/requests?bot_uuid=bot-alpha&direction=received&status=pending")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["success"], true);
    assert_eq!(json["data"].as_array().unwrap().len(), 1);
    assert_eq!(json["data"][0]["id"], "request-1");
    let calls = friend_request.listed.lock().await;
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].0, "bot-alpha");
    assert_eq!(calls[0].1, FriendRequestDirection::Received);
    assert_eq!(calls[0].2, Some(FriendRequestStatus::Pending));
}

#[tokio::test]
async fn accept_and_reject_friend_request_routes_require_receiver_token() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("receiver-bot".to_string(), BotCapabilities::default())
        .await
        .unwrap();
    registry
        .store_token_mapping("receiver-token".to_string(), "receiver-bot".to_string())
        .await;
    let friend_request = Arc::new(RecordingFriendRequestCoreService::new(
        pending_friend_request("sender-bot", "receiver-bot"),
    ));
    let friend = Arc::new(StaticFriendCoreService::default());
    let services = Services::builder()
        .registry(registry.clone())
        .friend(friend.clone())
        .friend_use_cases(friend_use_cases(registry, friend, friend_request.clone()))
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let accept_response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/friends/requests/request-1/accept")
                .header("authorization", "Bearer receiver-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(accept_response.status(), StatusCode::OK);

    let reject_response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/friends/requests/request-1/reject")
                .header("authorization", "Bearer receiver-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(reject_response.status(), StatusCode::OK);

    assert_eq!(
        friend_request.accepted.lock().await.as_slice(),
        &["request-1".to_string()]
    );
    assert_eq!(
        friend_request.rejected.lock().await.as_slice(),
        &["request-1".to_string()]
    );
}

#[tokio::test]
async fn list_friends_route_returns_enriched_effective_online_entries() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for (bot_id, name) in [("owner-bot", "Owner"), ("friend-bot", "Friend")] {
        registry
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(name.to_string()),
                    summary: Some(format!("{name} summary")),
                    ..BotCapabilities::default()
                },
            )
            .await
            .unwrap();
    }
    registry
        .store_token_mapping("owner-token".to_string(), "owner-bot".to_string())
        .await;
    registry
        .register_streaming_connection("friend-bot".to_string())
        .await
        .unwrap();
    let friend = Arc::new(StaticFriendCoreService::new(vec![(
        "owner-bot",
        "friend-bot",
    )]));
    let friend_request = Arc::new(RecordingFriendRequestCoreService::new(
        pending_friend_request("owner-bot", "friend-bot"),
    ));
    let services = Services::builder()
        .registry(registry.clone())
        .friend(friend.clone())
        .friend_use_cases(friend_use_cases(registry, friend, friend_request))
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/owner-bot/friends")
                .header("authorization", "Bearer owner-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["success"], true);
    let data = json["data"].as_array().unwrap();
    assert_eq!(data.len(), 1);
    assert_eq!(data[0]["bot_uuid"], "friend-bot");
    assert_eq!(data[0]["name"], "Friend");
    assert_eq!(data[0]["summary"], "Friend summary");
    assert_eq!(data[0]["is_online"], true);
}

#[tokio::test]
async fn list_friends_route_returns_404_when_target_bot_missing() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    let friend = Arc::new(StaticFriendCoreService::default());
    let friend_request = Arc::new(RecordingFriendRequestCoreService::new(
        pending_friend_request("missing-bot", "friend-bot"),
    ));
    let services = services_builder_with_bot_use_cases_and_friend(registry.clone(), friend.clone())
        .friend_use_cases(friend_use_cases(registry, friend, friend_request))
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/missing-bot/friends")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["success"], false);
    assert_eq!(json["error"], "Bot 'missing-bot' not found");
}

#[tokio::test]
async fn list_friends_route_returns_403_when_target_bot_owned_by_other_user() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("other-bot".to_string(), BotCapabilities::default())
        .await
        .unwrap();
    registry
        .save_created_by("other-bot", "bob", true)
        .await
        .unwrap();
    let friend = Arc::new(StaticFriendCoreService::default());
    let friend_request = Arc::new(RecordingFriendRequestCoreService::new(
        pending_friend_request("other-bot", "friend-bot"),
    ));
    let services = services_builder_with_bot_use_cases_and_friend(registry.clone(), friend.clone())
        .friend_use_cases(friend_use_cases(registry, friend, friend_request))
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/other-bot/friends")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["success"], false);
    assert_eq!(json["error"], "Not authorized to access bot 'other-bot'");
}

#[tokio::test]
async fn list_friends_route_returns_401_when_user_identity_missing() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("owned-bot".to_string(), BotCapabilities::default())
        .await
        .unwrap();
    registry
        .save_created_by("owned-bot", "alice", true)
        .await
        .unwrap();
    let friend = Arc::new(StaticFriendCoreService::default());
    let friend_request = Arc::new(RecordingFriendRequestCoreService::new(
        pending_friend_request("owned-bot", "friend-bot"),
    ));
    let services = services_builder_with_bot_use_cases_and_friend(registry.clone(), friend.clone())
        .friend_use_cases(friend_use_cases(registry, friend, friend_request))
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/owned-bot/friends")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["success"], false);
    assert_eq!(
        json["error"],
        "Unauthorized: no valid token or login session"
    );
}

#[tokio::test]
async fn create_group_route_persists_normal_group_and_includes_driver() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for bot_id in ["driver-bot", "worker-bot"] {
        registry
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    visibility: "public".to_string(),
                    ..BotCapabilities::default()
                },
            )
            .await
            .unwrap();
    }
    registry
        .store_token_mapping("driver-token".to_string(), "driver-bot".to_string())
        .await;
    registry
        .save_created_by("driver-bot", "alice", true)
        .await
        .unwrap();
    let group = Arc::new(GroupStore::new());
    let services = Services::builder()
        .registry(registry.clone())
        .group(group.clone())
        .group_management(Arc::new(GroupManagement::with_defaults(
            group.clone(),
            registry,
            Arc::new(NoopFriendCoreService),
        )))
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "id": "group-1",
                        "label": "Launch",
                        "driver_bot": "driver-bot",
                        "participants": [
                            { "bot_uuid": "worker-bot", "role": "consultant" }
                        ],
                        "context": "Ship it"
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
    assert_eq!(json["id"], "group-1");
    assert_eq!(json["driver_bot"], "driver-bot");
    assert_eq!(json["group_kind"], "normal");
    assert_eq!(json["created"], true);
    let participants = json["participants"].as_array().unwrap();
    assert!(participants.iter().any(|id| id == "driver-bot"));
    assert!(participants.iter().any(|id| id == "worker-bot"));

    let stored = group.get("group-1").await.unwrap();
    assert_eq!(stored.label.as_deref(), Some("Launch"));
    assert_eq!(stored.context.as_deref(), Some("Ship it"));
    assert!(
        stored
            .participants
            .iter()
            .any(|p| p.bot_uuid == "driver-bot")
    );
}

#[tokio::test]
async fn list_and_get_group_routes_return_group_details() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    let group = Arc::new(GroupStore::new());
    let mut stored = Group::new(
        "group-1",
        "driver-bot",
        vec![
            Participant::bot("driver-bot", ParticipantRole::Driver),
            Participant::bot("worker-bot", ParticipantRole::Consultant),
        ],
    );
    stored.label = Some("Launch".to_string());
    stored.updated_at = 10;
    group.upsert(stored).await.unwrap();
    let mut newer = Group::new(
        "group-2",
        "driver-bot",
        vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
    );
    newer.updated_at = 20;
    group.upsert(newer).await.unwrap();
    let services =
        services_builder_with_group_use_cases(group, registry, Arc::new(NoopFriendCoreService))
            .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let list_response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/groups?offset=0&limit=10")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(list_response.status(), StatusCode::OK);
    let body = to_bytes(list_response.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["total"], 2);
    assert_eq!(json["items"][0]["id"], "group-2");
    assert_eq!(json["items"][1]["id"], "group-1");
    assert_eq!(json["items"][0]["group_kind"], "normal");

    let get_response = app
        .oneshot(
            Request::builder()
                .uri("/groups/group-1")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(get_response.status(), StatusCode::OK);
    let body = to_bytes(get_response.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["id"], "group-1");
    assert_eq!(json["label"], "Launch");
    assert_eq!(json["participants"].as_array().unwrap().len(), 2);
}

#[tokio::test]
async fn delete_group_route_removes_group_when_driver_matches() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    let group = Arc::new(GroupStore::new());
    group
        .upsert(Group::new(
            "group-1",
            "driver-bot",
            vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
        ))
        .await
        .unwrap();
    let services = Services::builder()
        .registry(registry.clone())
        .group(group.clone())
        .group_management(Arc::new(GroupManagement::with_defaults(
            group.clone(),
            registry,
            Arc::new(NoopFriendCoreService),
        )))
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri("/groups/group-1?bot_id=driver-bot")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["deleted"], true);
    assert_eq!(json["id"], "group-1");
    assert!(group.get("group-1").await.is_none());
}

#[tokio::test]
async fn bot_groups_route_lists_groups_for_participant() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "driver-bot".to_string(),
            BotCapabilities {
                name: Some("Driver Bot".to_string()),
                summary: Some("Driver".to_string()),
                skills: vec![Skill::new("manage")],
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .register(
            "worker-bot".to_string(),
            BotCapabilities {
                name: Some("Worker Bot".to_string()),
                summary: Some("Worker".to_string()),
                skills: vec![Skill::new("work")],
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    let group = Arc::new(GroupStore::new());
    let mut normal = Group::new(
        "group-1",
        "driver-bot",
        vec![
            Participant::bot("driver-bot", ParticipantRole::Driver),
            Participant::bot("worker-bot", ParticipantRole::Consultant),
        ],
    );
    normal.label = Some("Normal".to_string());
    normal.updated_at = 10;
    group.upsert(normal).await.unwrap();
    let mut newer = Group::new(
        "group-2",
        "driver-bot",
        vec![
            Participant::bot("driver-bot", ParticipantRole::Manager),
            Participant::bot("worker-bot", ParticipantRole::Worker),
        ],
    );
    newer.group_strategy = GroupStrategy::ManagerWorker;
    newer.updated_at = 20;
    group.upsert(newer).await.unwrap();
    let mut dm = Group::new(
        "dm-1",
        "driver-bot",
        vec![Participant::bot("worker-bot", ParticipantRole::Consultant)],
    );
    dm.group_kind = GroupKind::Dm;
    group.upsert(dm).await.unwrap();
    let services =
        services_builder_with_group_use_cases(group, registry, Arc::new(NoopFriendCoreService))
            .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/worker-bot/groups?offset=0&limit=10")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["bot_uuid"], "worker-bot");
    assert_eq!(json["total"], 2);
    assert_eq!(json["items"][0]["group_id"], "group-2");
    assert_eq!(json["items"][1]["group_id"], "group-1");
    assert_eq!(json["items"][0]["group_kind"], "normal");
    assert_eq!(json["items"][0]["group_strategy"], "manager_worker");
    assert_eq!(json["items"][0]["participants"][0]["bot_name"], "Driver Bot");
    assert_eq!(json["items"][0]["participants"][1]["bot_name"], "Worker Bot");
}

#[tokio::test]
async fn bot_groups_route_filters_absent_human_participant_groups() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    let group = Arc::new(GroupStore::new());
    let mut present = Group::new(
        "present-group",
        "driver-bot",
        vec![
            Participant::bot("driver-bot", ParticipantRole::Driver),
            Participant::human("human_100005", ParticipantRole::Observer),
        ],
    );
    present
        .participants
        .iter_mut()
        .find(|participant| participant.bot_uuid == "human_100005")
        .expect("human participant")
        .mode = Some(bcs_service_api::ParticipantMode::Present);
    group.upsert(present).await.unwrap();
    group
        .upsert(Group::new(
            "absent-group",
            "driver-bot",
            vec![
                Participant::bot("driver-bot", ParticipantRole::Driver),
                Participant::human("human_100005", ParticipantRole::Observer),
            ],
        ))
        .await
        .unwrap();
    let services =
        services_builder_with_group_use_cases(group, registry, Arc::new(NoopFriendCoreService))
            .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/human_100005/groups?offset=0&limit=10")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["bot_uuid"], "human_100005");
    assert_eq!(json["total"], 1);
    assert_eq!(json["items"].as_array().unwrap().len(), 1);
    assert_eq!(json["items"][0]["group_id"], "present-group");
}

#[tokio::test]
async fn bot_groups_route_can_exclude_session_only_groups() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    let group = Arc::new(GroupStore::new());
    group
        .upsert(Group::new(
            "session-only-group",
            "driver-bot",
            vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
        ))
        .await
        .unwrap();

    let session_repo = Arc::new(MemorySessionRepo::new());
    session_repo
        .create(
            "session-only-group",
            bcs_service_api::NewSessionParams {
                participants: vec![Participant::bot(
                    "worker-bot",
                    ParticipantRole::Consultant,
                )],
                ..Default::default()
            },
        )
        .await
        .unwrap();
    let session_management = Arc::new(SessionManagementServiceImpl::new(
        session_repo,
        Arc::new(MemoryGroupRepo::new()),
    ));
    let services = services_builder_with_group_use_cases(
        group,
        registry,
        Arc::new(NoopFriendCoreService),
    )
    .session_management(session_management)
    .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let default_response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/bots/worker-bot/groups?offset=0&limit=10")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(default_response.status(), StatusCode::OK);
    let body = to_bytes(default_response.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["total"], 1);
    assert_eq!(json["items"][0]["group_id"], "session-only-group");

    let formal_only_response = app
        .oneshot(
            Request::builder()
                .uri(
                    "/bots/worker-bot/groups?offset=0&limit=10&include_session_groups=false",
                )
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(formal_only_response.status(), StatusCode::OK);
    let body = to_bytes(formal_only_response.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["total"], 0);
    assert!(json["items"].as_array().unwrap().is_empty());
}

#[tokio::test]
async fn service_group_routes_are_not_registered() {
    let app = build_router(HttpAppState::new(Services::noop()));
    let cases = [
        ("GET", "/service-groups"),
        ("POST", "/service-groups"),
        ("GET", "/service-groups/sg-1"),
        ("POST", "/service-groups/sg-1/instances"),
    ];

    for (method, uri) in cases {
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .method(method)
                    .uri(uri)
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::NOT_FOUND, "{method} {uri}");
    }
}

#[tokio::test]
async fn group_secondary_routes_update_member_status_label_workspace_and_terminate() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for bot_id in ["driver-bot", "member-bot", "new-bot"] {
        registry
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    visibility: "public".to_string(),
                    ..BotCapabilities::default()
                },
            )
            .await
            .unwrap();
    }
    registry
        .store_token_mapping("driver-token".to_string(), "driver-bot".to_string())
        .await;
    let groups = Arc::new(GroupStore::new());
    let mut group = Group::new(
        "group-secondary",
        "driver-bot",
        vec![
            Participant::bot("driver-bot", ParticipantRole::Driver),
            Participant::bot("member-bot", ParticipantRole::Consultant),
        ],
    );
    group.originator = Some("driver-bot".to_string());
    groups.upsert(group).await.unwrap();
    let services = services_builder_with_group_use_cases(
        groups.clone(),
        registry,
        Arc::new(NoopFriendCoreService),
    )
    .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let add_response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-secondary/members")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "bot_uuid": "new-bot",
                        "role": "consultant"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(add_response.status(), StatusCode::OK);
    assert_eq!(
        groups
            .get("group-secondary")
            .await
            .unwrap()
            .participants
            .len(),
        3
    );

    let status_response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/groups/group-secondary/status")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "status": "completed",
                        "reason": "done"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(status_response.status(), StatusCode::OK);
    assert_eq!(
        groups.get("group-secondary").await.unwrap().status,
        GroupStatus::Completed
    );

    let label_response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/groups/group-secondary/label")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "label": "Updated Group"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(label_response.status(), StatusCode::OK);
    assert_eq!(
        groups
            .get("group-secondary")
            .await
            .unwrap()
            .label
            .as_deref(),
        Some("Updated Group")
    );

    let workspace_response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/groups/group-secondary/workspace")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "decisions": ["ship"],
                        "tasks": [],
                        "notes": ["note"],
                        "audit_log": []
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(workspace_response.status(), StatusCode::OK);
    assert_eq!(
        groups
            .get("group-secondary")
            .await
            .unwrap()
            .workspace
            .decisions,
        vec!["ship".to_string()]
    );

    let get_workspace_response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/groups/group-secondary/workspace")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(get_workspace_response.status(), StatusCode::OK);
    let body = to_bytes(get_workspace_response.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["decisions"][0], "ship");

    let terminate_response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-secondary/terminate")
                .header("authorization", "Bearer driver-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(terminate_response.status(), StatusCode::OK);
    assert_eq!(
        groups.get("group-secondary").await.unwrap().status,
        GroupStatus::Completed
    );
}

#[tokio::test]
async fn add_group_member_route_rejects_non_owner_human_identity() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for bot_id in ["driver-bot", "new-bot"] {
        registry
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    visibility: "public".to_string(),
                    ..BotCapabilities::default()
                },
            )
            .await
            .unwrap();
    }
    registry
        .save_created_by("driver-bot", "alice", true)
        .await
        .unwrap();
    registry
        .store_token_mapping("driver-token".to_string(), "driver-bot".to_string())
        .await;
    let groups = Arc::new(GroupStore::new());
    groups
        .upsert(Group::new(
            "group-owner",
            "driver-bot",
            vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
        ))
        .await
        .unwrap();
    let services = Services::builder()
        .registry(registry.clone())
        .group(groups.clone())
        .group_management(Arc::new(GroupManagement::with_defaults(
            groups.clone(),
            registry,
            Arc::new(NoopFriendCoreService),
        )))
        .build_for_test();
    let chain = static_auth_chain("bob", "Bob");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-owner/members")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "bot_uuid": "new-bot",
                        "role": "consultant"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let stored = groups.get("group-owner").await.unwrap();
    assert_eq!(stored.participants.len(), 1);
}

#[tokio::test]
async fn add_group_member_route_hides_private_friend_target() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for (bot_id, visibility) in [("driver-bot", "public"), ("private-bot", "private")] {
        registry
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    visibility: visibility.to_string(),
                    ..BotCapabilities::default()
                },
            )
            .await
            .unwrap();
    }
    registry
        .save_created_by("driver-bot", "alice", true)
        .await
        .unwrap();
    registry
        .store_token_mapping("driver-token".to_string(), "driver-bot".to_string())
        .await;
    let groups = Arc::new(GroupStore::new());
    groups
        .upsert(Group::new(
            "group-private-target",
            "driver-bot",
            vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
        ))
        .await
        .unwrap();
    let friend = Arc::new(StaticFriendCoreService::new(vec![(
        "driver-bot",
        "private-bot",
    )]));
    let services = Services::builder()
        .registry(registry.clone())
        .friend(friend.clone())
        .group(groups.clone())
        .group_management(Arc::new(GroupManagement::with_defaults(
            groups.clone(),
            registry,
            friend,
        )))
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-private-target/members")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "bot_uuid": "private-bot",
                        "role": "consultant"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    let stored = groups.get("group-private-target").await.unwrap();
    assert_eq!(stored.participants.len(), 1);
}

#[tokio::test]
async fn group_routing_policy_route_merges_and_persists_policy() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for bot_id in ["driver-bot", "member-bot"] {
        registry
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    visibility: "public".to_string(),
                    ..BotCapabilities::default()
                },
            )
            .await
            .unwrap();
    }
    registry
        .store_token_mapping("driver-token".to_string(), "driver-bot".to_string())
        .await;
    let groups = Arc::new(GroupStore::new());
    groups
        .upsert(Group::new(
            "group-routing",
            "driver-bot",
            vec![
                Participant::bot("driver-bot", ParticipantRole::Driver),
                Participant::bot("member-bot", ParticipantRole::Consultant),
            ],
        ))
        .await
        .unwrap();
    let services = Services::builder()
        .registry(registry.clone())
        .group(groups.clone())
        .group_management(Arc::new(GroupManagement::with_defaults(
            groups.clone(),
            registry,
            Arc::new(NoopFriendCoreService),
        )))
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/groups/group-routing/routing-policy")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "mode": "structured",
                        "default_bot_final_delivery": "inject_observers",
                        "sender_routes": {
                            "driver-bot": ["member-bot"]
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
    assert_eq!(json["ok"], true);
    assert_eq!(json["routing_policy"]["mode"], "structured");
    assert_eq!(
        json["routing_policy"]["default_bot_final_delivery"],
        "inject_observers"
    );
    assert_eq!(
        json["routing_policy"]["sender_routes"]["driver-bot"],
        serde_json::json!(["member-bot"])
    );

    let stored = groups.get("group-routing").await.unwrap();
    let policy = stored.routing_policy.unwrap();
    assert_eq!(
        policy.sender_routes.get("driver-bot").unwrap(),
        &vec!["member-bot".to_string()]
    );
}

#[tokio::test]
async fn participant_mode_route_allows_actor_self_update() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "member-bot".to_string(),
            BotCapabilities {
                name: Some("member-bot".to_string()),
                visibility: "public".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .store_token_mapping("member-token".to_string(), "member-bot".to_string())
        .await;
    let groups = Arc::new(GroupStore::new());
    groups
        .upsert(Group::new(
            "group-mode",
            "member-bot",
            vec![Participant::bot("member-bot", ParticipantRole::Driver)],
        ))
        .await
        .unwrap();
    let services = Services::builder()
        .registry(registry.clone())
        .group(groups.clone())
        .group_management(Arc::new(GroupManagement::with_defaults(
            groups.clone(),
            registry,
            Arc::new(NoopFriendCoreService),
        )))
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/groups/group-mode/participants/member-bot/mode")
                .header("authorization", "Bearer member-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "mode": "muted"
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
    assert_eq!(json["data"]["group_id"], "group-mode");
    assert_eq!(json["data"]["actor_id"], "member-bot");
    assert_eq!(json["data"]["mode"], "muted");

    let stored = groups.get("group-mode").await.unwrap();
    let participant = stored
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == "member-bot")
        .unwrap();
    assert_eq!(
        participant.mode,
        Some(bcs_service_api::ParticipantMode::Muted)
    );
}
