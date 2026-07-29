use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_friend::{
    Friend, FriendCore, FriendRequestCore, MemoryFriendRepo, MemoryFriendRequestRepo,
};
use bcs_service_api::{
    ActorKind, ActorStatus, AgentCredentials, BotCapabilities, BotDynamicStatus,
    BotRegistryCoreService, FriendCoreService, FriendRequestCoreService, FriendRequestDirection,
    FriendRepoPort, FriendRequestRepoPort, FriendRequestStatus, FriendService, ListFriendsCommand,
    RegisteredBot, ServiceError, ServiceResult,
};
use bcs_test_support::{
    NoopBotRegistryCoreService, NoopFriendCoreService, NoopFriendRequestCoreService,
    NoopRelationCoreService,
};

#[tokio::test]
async fn memory_friend_store_passes_core_contract() {
    let repo = Arc::new(MemoryFriendRepo::new());
    let store = FriendCore::with_repo(repo.clone());

    bcs_test_support::contract::core::friend_core_service_contract_tests(&store).await;
    bcs_test_support::contract::repo::friend_repo_contract_tests(repo.as_ref()).await;
}

#[tokio::test]
async fn friend_core_propagates_friendship_lookup_failure() {
    let store = FriendCore::with_repo(Arc::new(FailingFriendRepo));

    let result = store.try_are_friends("alice", "bob").await;

    assert!(matches!(
        result,
        Err(ServiceError::InternalError(message)) if message == "friend store unavailable"
    ));
}

#[tokio::test]
async fn memory_friend_request_store_passes_core_contract() {
    let repo = Arc::new(MemoryFriendRequestRepo::new());
    let store = FriendRequestCore::new(
        Arc::new(NoopFriendCoreService),
        Arc::new(NoopBotRegistryCoreService),
    );
    let repo_backed_store = FriendRequestCore::with_repo(
        repo.clone(),
        Arc::new(NoopFriendCoreService),
        Arc::new(NoopBotRegistryCoreService),
    );

    bcs_test_support::contract::core::friend_request_core_service_contract_tests(&store).await;
    bcs_test_support::contract::core::friend_request_core_service_contract_tests(
        &repo_backed_store,
    )
    .await;
    bcs_test_support::contract::repo::friend_request_repo_contract_tests(repo.as_ref()).await;
}

struct FailingFriendRepo;

#[async_trait]
impl FriendRepoPort for FailingFriendRepo {
    async fn list_friends(&self, _bot_id: &str) -> ServiceResult<Vec<String>> {
        Err(ServiceError::InternalError(
            "friend store unavailable".into(),
        ))
    }

    async fn are_friends(&self, _bot_a: &str, _bot_b: &str) -> ServiceResult<bool> {
        Err(ServiceError::InternalError(
            "friend store unavailable".into(),
        ))
    }

    async fn add_friendship(&self, _bot_a: &str, _bot_b: &str) -> ServiceResult<()> {
        Err(ServiceError::InternalError(
            "friend store unavailable".into(),
        ))
    }

    async fn remove_all_friendships(&self, _bot_id: &str) -> ServiceResult<usize> {
        Err(ServiceError::InternalError(
            "friend store unavailable".into(),
        ))
    }
}

#[tokio::test]
async fn friend_request_core_rejects_duplicate_pending_request() {
    let (friend, requests, _repo) = core_fixture(&[
        ("alice", "protected", ActorKind::Bot),
        ("bob", "protected", ActorKind::Bot),
    ]);

    let request = requests
        .create_request("alice", "bob")
        .await
        .expect("create request");
    let duplicate = requests.create_request("alice", "bob").await;

    assert!(matches!(
        duplicate,
        Err(ServiceError::PendingRequestExists { request_id, .. }) if request_id == request.id
    ));
    assert!(!friend.are_friends("alice", "bob").await);
}

#[tokio::test]
async fn friend_request_core_public_target_auto_accepts_without_pending_row() {
    let (friend, requests, _repo) = core_fixture(&[
        ("alice", "protected", ActorKind::Bot),
        ("public-bot", "public", ActorKind::Bot),
    ]);

    let request = requests
        .create_request("alice", "public-bot")
        .await
        .expect("create public request");

    assert_eq!(request.status, FriendRequestStatus::Accepted);
    assert!(!request.id.is_empty());
    assert!(friend.are_friends("alice", "public-bot").await);
    let stored = requests
        .list_requests("alice", FriendRequestDirection::Sent, None)
        .await;
    assert_eq!(stored.len(), 1);
    assert_eq!(stored[0].id, request.id);
    assert_eq!(stored[0].status, FriendRequestStatus::Accepted);
}

#[tokio::test]
async fn friend_request_core_accepts_reverse_pending_request_and_adds_friendship() {
    let (friend, requests, repo) = core_fixture(&[
        ("alice", "protected", ActorKind::Bot),
        ("bob", "protected", ActorKind::Bot),
    ]);
    let forward = requests
        .create_request("alice", "bob")
        .await
        .expect("forward request");
    let reverse = requests
        .create_request("bob", "alice")
        .await
        .expect("reverse request");

    requests
        .accept_request(&forward.id)
        .await
        .expect("accept request");

    assert!(friend.are_friends("alice", "bob").await);
    assert_eq!(
        repo.get_request(&forward.id)
            .await
            .expect("forward request")
            .status,
        FriendRequestStatus::Accepted
    );
    assert_eq!(
        repo.get_request(&reverse.id)
            .await
            .expect("reverse request")
            .status,
        FriendRequestStatus::Accepted
    );
}

#[tokio::test]
async fn friend_request_core_reject_and_cancel_preserve_terminal_requests() {
    let (_friend, requests, repo) = core_fixture(&[
        ("alice", "protected", ActorKind::Bot),
        ("bob", "protected", ActorKind::Bot),
        ("carol", "protected", ActorKind::Bot),
    ]);
    let rejected = requests
        .create_request("alice", "bob")
        .await
        .expect("request to reject");
    requests
        .reject_request(&rejected.id)
        .await
        .expect("reject request");
    requests
        .reject_request(&rejected.id)
        .await
        .expect("reject request idempotent");

    let pending = requests
        .create_request("carol", "alice")
        .await
        .expect("pending request");

    assert_eq!(
        requests
            .cancel_pending_requests("alice")
            .await
            .expect("cancel pending"),
        1
    );
    assert_eq!(
        repo.get_request(&rejected.id)
            .await
            .expect("rejected request preserved")
            .status,
        FriendRequestStatus::Rejected
    );
    assert!(matches!(
        repo.get_request(&pending.id).await,
        Err(ServiceError::FriendRequestNotFound(_))
    ));
}

#[tokio::test]
async fn friend_use_case_passes_application_contract() {
    let svc = Friend::new(
        Arc::new(NoopBotRegistryCoreService),
        Arc::new(NoopFriendCoreService),
        Arc::new(NoopFriendRequestCoreService),
        Arc::new(NoopRelationCoreService),
    );

    bcs_test_support::contract::application::friend_service_contract_tests(&svc).await;
}

#[tokio::test]
async fn list_friends_excludes_deleted_or_unregistered_peers() {
    // alice is friends with bob (live) and carol (deleted / no longer registered).
    // `StaticRegistry` only knows alice and bob, so `get("carol")` returns `None`,
    // which is exactly how the listing path observes a soft-deleted friend.
    let friend_repo = Arc::new(MemoryFriendRepo::new());
    let friend_core = Arc::new(FriendCore::with_repo(friend_repo));
    friend_core
        .add_friendship("alice", "bob")
        .await
        .expect("add alice-bob friendship");
    friend_core
        .add_friendship("alice", "carol")
        .await
        .expect("add alice-carol friendship");

    let svc = Friend::new(
        Arc::new(StaticRegistry::new(&[
            ("alice", "protected", ActorKind::Bot),
            ("bob", "protected", ActorKind::Bot),
            // carol is intentionally omitted: it simulates a deleted friend.
        ])),
        friend_core,
        Arc::new(NoopFriendRequestCoreService),
        Arc::new(NoopRelationCoreService),
    );

    // Caller == target short-circuits the ownership check, so the only thing
    // exercising the filter is the enrichment loop in `list_friends`.
    let friends = svc
        .list_friends(ListFriendsCommand {
            caller_actor_id: "alice".to_string(),
            target_actor_id: "alice".to_string(),
        })
        .await
        .expect("list friends for alice");

    assert_eq!(
        friends.len(),
        1,
        "deleted/unregistered friends must be excluded from the list"
    );
    assert_eq!(friends[0].bot_uuid, "bob");
    assert!(
        friends.iter().all(|entry| entry.bot_uuid != "carol"),
        "carol (deleted) must not appear in the friend list"
    );
}

fn core_fixture(
    bots: &[(&str, &str, ActorKind)],
) -> (
    Arc<FriendCore>,
    Arc<FriendRequestCore>,
    Arc<MemoryFriendRequestRepo>,
) {
    let friend_repo = Arc::new(MemoryFriendRepo::new());
    let friend = Arc::new(FriendCore::with_repo(friend_repo));
    let request_repo = Arc::new(MemoryFriendRequestRepo::new());
    let registry = Arc::new(StaticRegistry::new(bots));
    let requests = Arc::new(FriendRequestCore::with_repo(
        request_repo.clone(),
        friend.clone(),
        registry,
    ));
    (friend, requests, request_repo)
}

struct StaticRegistry {
    bots: HashMap<String, (String, ActorKind)>,
}

impl StaticRegistry {
    fn new(bots: &[(&str, &str, ActorKind)]) -> Self {
        Self {
            bots: bots
                .iter()
                .map(|(id, visibility, actor_kind)| {
                    ((*id).to_string(), ((*visibility).to_string(), *actor_kind))
                })
                .collect(),
        }
    }
}

#[async_trait]
impl BotRegistryCoreService for StaticRegistry {
    async fn register(&self, _bot_id: String, _capabilities: BotCapabilities) -> ServiceResult<()> {
        Ok(())
    }

    async fn update_status(&self, _bot_id: &str, _status: BotDynamicStatus) -> bool {
        false
    }

    async fn get(&self, bot_id: &str) -> Option<RegisteredBot> {
        self.bots
            .get(bot_id)
            .map(|(visibility, actor_kind)| RegisteredBot {
                bot_uuid: bot_id.to_string(),
                capabilities: BotCapabilities {
                    visibility: visibility.clone(),
                    ..Default::default()
                },
                dynamic_status: Default::default(),
                env: None,
                created_by: None,
                actor_kind: *actor_kind,
                status: ActorStatus::default(),
            })
    }

    async fn get_agent_credentials(&self, _bot_id: &str) -> Option<AgentCredentials> {
        None
    }

    async fn list_active(&self) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn list_bots_by_creator(&self, _created_by: &str) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn discover(&self, _query: &str) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn find_by_skills(&self, _skills: &[&str]) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn find_by_domains(&self, _domains: &[&str]) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn find_by_scopes(&self, _scopes: &[&str]) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn unregister(&self, _bot_id: &str) -> bool {
        false
    }

    async fn cleanup_expired(&self) {}

    async fn load_from_storage(&self, _bot_id: &str) -> Option<BotCapabilities> {
        None
    }

    async fn save_to_storage(&self, _bot_id: &str, _caps: &BotCapabilities) -> ServiceResult<()> {
        Ok(())
    }

    async fn update_visibility(&self, _bot_id: &str, _visibility: &str) -> ServiceResult<()> {
        Ok(())
    }

    #[allow(deprecated)]
    async fn set_hidden(&self, _bot_id: &str, _hidden: bool) -> ServiceResult<()> {
        Ok(())
    }

    async fn update_actor_status(&self, _bot_id: &str, _status: ActorStatus) -> ServiceResult<()> {
        Ok(())
    }

    async fn ensure_human_actor(
        &self,
        _staff_no: &str,
        _nick_name: &str,
    ) -> ServiceResult<bcs_service_api::EnsureHumanResult> {
        Ok(bcs_service_api::EnsureHumanResult { created: false })
    }

    async fn has_been_onboarded(&self, _bot_id: &str) -> bool {
        false
    }

    async fn save_created_by(
        &self,
        _bot_id: &str,
        _created_by: &str,
        _overwrite: bool,
    ) -> ServiceResult<()> {
        Ok(())
    }

    async fn save_token(&self, _bot_id: &str, _token: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn load_token(&self, _bot_id: &str) -> Option<String> {
        None
    }

    async fn find_bot_by_token(&self, _token: &str) -> Option<String> {
        None
    }

    async fn register_streaming_connection(&self, _bot_id: String) -> Result<String, ()> {
        Err(())
    }

    async fn reconnect_streaming(&self, _existing_token: String) -> Result<(String, String), ()> {
        Err(())
    }

    async fn disconnect_streaming(&self, _bot_id: &str) {}

    async fn is_connected(&self, _bot_id: &str) -> bool {
        false
    }

    async fn send_frame(&self, _bot_id: &str, _frame: String) -> Result<(), ()> {
        Err(())
    }

    async fn list_connected(&self) -> Vec<String> {
        Vec::new()
    }

    async fn store_token_mapping(&self, _token: String, _bot_id: String) {}

    async fn register_http_connection(&self, _bot_id: String, token: String) -> String {
        token
    }
}
