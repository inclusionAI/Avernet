//! I.7 — Visibility / Status / ActorKind orthogonality integration tests.
//!
//! These three actor dimensions are independent in the V1 design:
//!
//! | Dimension      | Values                          | Affects                                    |
//! |----------------|---------------------------------|--------------------------------------------|
//! | `visibility`   | public / protected / private    | Whether *new* friend requests are accepted |
//! | `status`       | online / hidden / offline       | Whether routing emits `silent=true`        |
//! | `actor_kind`   | Bot / Human                     | Group invites + friend pairing rules       |
//!
//! Crucially:
//! - changing `visibility` MUST NOT remove existing friendships
//! - flipping `status` to `hidden` MUST NOT change `visibility` or vice versa
//! - a `public` Bot is invitable as a one-way relation; a `public` Human is
//!   still rejected from group invites (kind wins over visibility)
//!
//! These properties are exercised at the service layer using the in-memory
//! implementations because:
//! 1. They are agnostic of the HTTP/WS layer (no auth dependency).
//! 2. The HTTP handlers already delegate to the same trait methods we test
//!    here, so a green service-layer matrix is the load-bearing guarantee.

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use tokio::sync::RwLock;

use bcs_friend::{FriendCore, FriendRequestCore};
use bcs_friend_store::{MemoryFriendRepo, MemoryFriendRequestRepo};
use bcs_relation::MemoryRelationStore;
use bcs_service_api::{
    ActorKind, ActorStatus, AgentCredentials, BotCapabilities, BotDynamicStatus,
    BotRegistryCoreService, FriendCoreService, FriendRequestCoreService, RegisteredBot,
    RelationCoreService, ServiceError, ServiceResult,
};

// ============================================================================
// Mutable test registry that lets us flip visibility / status / actor_kind
// independently to exercise the orthogonality matrix.
// ============================================================================

#[derive(Default)]
struct MutableRegistry {
    inner: RwLock<HashMap<String, RegisteredBot>>,
}

impl MutableRegistry {
    fn new() -> Self {
        Self::default()
    }

    async fn put(
        &self,
        bot_uuid: &str,
        actor_kind: ActorKind,
        visibility: &str,
        status: ActorStatus,
    ) {
        let mut caps = BotCapabilities::default();
        caps.visibility = visibility.to_string();
        let row = RegisteredBot {
            bot_uuid: bot_uuid.to_string(),
            capabilities: caps,
            dynamic_status: BotDynamicStatus::default(),
            env: None,
            created_by: None,
            actor_kind,
            status,
        };
        self.inner.write().await.insert(bot_uuid.to_string(), row);
    }

    async fn set_visibility(&self, bot_uuid: &str, visibility: &str) {
        let mut map = self.inner.write().await;
        if let Some(row) = map.get_mut(bot_uuid) {
            row.capabilities.visibility = visibility.to_string();
        }
    }

    async fn set_status(&self, bot_uuid: &str, status: ActorStatus) {
        let mut map = self.inner.write().await;
        if let Some(row) = map.get_mut(bot_uuid) {
            row.status = status;
        }
    }
}

#[async_trait]
impl BotRegistryCoreService for MutableRegistry {
    async fn register(&self, _bot_id: String, _capabilities: BotCapabilities) -> ServiceResult<()> {
        Ok(())
    }
    async fn update_status(&self, _bot_id: &str, _status: BotDynamicStatus) -> bool {
        false
    }
    async fn get(&self, bot_id: &str) -> Option<RegisteredBot> {
        self.inner.read().await.get(bot_id).cloned()
    }
    async fn get_agent_credentials(&self, _bot_id: &str) -> Option<AgentCredentials> {
        None
    }
    async fn list_active(&self) -> Vec<RegisteredBot> {
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
    async fn has_been_onboarded(&self, _bot_id: &str) -> bool {
        false
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
    async fn save_created_by(
        &self,
        _bot_id: &str,
        _created_by: &str,
        _overwrite: bool,
    ) -> ServiceResult<()> {
        Ok(())
    }
    async fn list_bots_by_creator(&self, _created_by: &str) -> Vec<RegisteredBot> {
        Vec::new()
    }
}

struct Fixture {
    friend: Arc<FriendCore>,
    request: Arc<FriendRequestCore>,
    relation: Arc<MemoryRelationStore>,
    registry: Arc<MutableRegistry>,
    env: String,
}

fn build_fixture() -> Fixture {
    let relation: Arc<MemoryRelationStore> = Arc::new(MemoryRelationStore::new());
    let friend_repo = Arc::new(MemoryFriendRepo::new());
    let friend: Arc<FriendCore> = Arc::new(
        FriendCore::with_repo(friend_repo)
            .with_relation(relation.clone() as Arc<dyn RelationCoreService>),
    );
    let registry: Arc<MutableRegistry> = Arc::new(MutableRegistry::new());
    let request_repo = Arc::new(MemoryFriendRequestRepo::new());
    let request = Arc::new(FriendRequestCore::with_repo(
        request_repo,
        friend.clone() as Arc<dyn FriendCoreService>,
        registry.clone() as Arc<dyn BotRegistryCoreService>,
    ));
    Fixture {
        friend,
        request,
        relation,
        registry,
        env: bcs_config::resolve_env_str(),
    }
}

// ============================================================================
// 1. visibility orthogonality
// ============================================================================

/// Toggling visibility from protected → private MUST NOT remove existing
/// friendships. Only *new* requests are blocked.
#[tokio::test]
async fn test_visibility_change_does_not_remove_existing_friendship() {
    let fx = build_fixture();
    fx.registry
        .put("alice", ActorKind::Bot, "protected", ActorStatus::Online)
        .await;
    fx.registry
        .put("bob", ActorKind::Bot, "protected", ActorStatus::Online)
        .await;

    // Establish friendship while bob is protected
    let req = fx.request.create_request("alice", "bob").await.unwrap();
    fx.request.accept_request(&req.id).await.unwrap();
    assert!(fx.friend.are_friends("alice", "bob").await);

    // Flip bob to private
    fx.registry.set_visibility("bob", "private").await;

    // Existing friendship persists
    assert!(
        fx.friend.are_friends("alice", "bob").await,
        "visibility flip must not retract existing friendship"
    );
    // Relation edges still there
    assert!(
        fx.relation
            .get_edge("alice", "bob", &fx.env)
            .await
            .unwrap()
            .is_some()
    );

    // But a fresh request from a third party is blocked
    fx.registry
        .put("carol", ActorKind::Bot, "protected", ActorStatus::Online)
        .await;
    let blocked = fx.request.create_request("carol", "bob").await;
    assert!(matches!(blocked, Err(ServiceError::Unauthorized(_))));
}

/// public visibility flips an incoming request to instant-accept; flipping
/// the *same* target back to protected restores the pending-request flow.
#[tokio::test]
async fn test_visibility_public_then_protected_round_trip() {
    let fx = build_fixture();
    fx.registry
        .put("alice", ActorKind::Bot, "protected", ActorStatus::Online)
        .await;
    fx.registry
        .put("pubbot", ActorKind::Bot, "public", ActorStatus::Online)
        .await;

    // First request: auto-accepted (persisted with real id)
    let r1 = fx.request.create_request("alice", "pubbot").await.unwrap();
    assert!(!r1.id.is_empty());
    assert_eq!(r1.status, bcs_service_api::FriendRequestStatus::Accepted);
    assert!(fx.friend.are_friends("alice", "pubbot").await);

    // Flip pubbot back to protected and try a *new* friendship from carol
    fx.registry.set_visibility("pubbot", "protected").await;
    fx.registry
        .put("carol", ActorKind::Bot, "protected", ActorStatus::Online)
        .await;
    let r2 = fx.request.create_request("carol", "pubbot").await.unwrap();
    // Now we get a real pending row
    assert!(!r2.id.is_empty());
    assert_eq!(r2.status, bcs_service_api::FriendRequestStatus::Pending);
    // And the alice friendship is still there
    assert!(fx.friend.are_friends("alice", "pubbot").await);
}

// ============================================================================
// 2. status orthogonality
// ============================================================================

/// Flipping status to Hidden MUST NOT touch visibility or friendships;
/// it only affects routing semantics (silent inject, tested in I.6).
#[tokio::test]
async fn test_status_hidden_does_not_affect_friendship_or_visibility() {
    let fx = build_fixture();
    fx.registry
        .put(
            "human_x",
            ActorKind::Human,
            "protected",
            ActorStatus::Online,
        )
        .await;
    fx.registry
        .put("bot_y", ActorKind::Bot, "protected", ActorStatus::Online)
        .await;

    // Establish friendship via owner edges + a friend pairing
    fx.relation
        .ensure_owner_edges("human_x", "bot_y", &fx.env)
        .await
        .unwrap();
    fx.friend.add_friendship("human_x", "bot_y").await.unwrap();

    // Flip human_x to Hidden
    fx.registry.set_status("human_x", ActorStatus::Hidden).await;

    // Visibility unchanged
    let row = fx.registry.get("human_x").await.unwrap();
    assert_eq!(row.capabilities.visibility, "protected");
    assert_eq!(row.status, ActorStatus::Hidden);

    // Friendship unchanged
    assert!(fx.friend.are_friends("human_x", "bot_y").await);

    // Owner edge intact (is_creator=TRUE preserved)
    let owner = fx
        .relation
        .get_edge("human_x", "bot_y", &fx.env)
        .await
        .unwrap()
        .unwrap();
    assert!(owner.is_creator);
}

/// Flipping visibility MUST NOT change status; the two move independently.
#[tokio::test]
async fn test_visibility_change_does_not_affect_status() {
    let fx = build_fixture();
    fx.registry
        .put("bot_a", ActorKind::Bot, "protected", ActorStatus::Hidden)
        .await;

    // Sanity: starts Hidden
    assert_eq!(
        fx.registry.get("bot_a").await.unwrap().status,
        ActorStatus::Hidden
    );

    // Flip visibility
    fx.registry.set_visibility("bot_a", "public").await;

    // Status MUST still be Hidden
    let row = fx.registry.get("bot_a").await.unwrap();
    assert_eq!(row.status, ActorStatus::Hidden);
    assert_eq!(row.capabilities.visibility, "public");
}

// ============================================================================
// 3. actor_kind orthogonality (kind dominates visibility for invite rules)
// ============================================================================

/// A `public` Human is still rejected from H↔H friend pairing — visibility
/// does not override the actor_kind guard.
#[tokio::test]
async fn test_public_human_still_rejected_from_human_human_pairing() {
    let fx = build_fixture();
    fx.registry
        .put("h_pub_a", ActorKind::Human, "public", ActorStatus::Online)
        .await;
    fx.registry
        .put("h_pub_b", ActorKind::Human, "public", ActorStatus::Online)
        .await;

    let result = fx.request.create_request("h_pub_a", "h_pub_b").await;
    assert!(matches!(
        result,
        Err(ServiceError::InvalidOperation { ref message, .. })
            if message.contains("用户之间")
    ));
}

/// Bot↔Human pairing is allowed regardless of visibility, as long as the
/// Human side is not `private`.
#[tokio::test]
async fn test_bot_to_human_pairing_works_across_visibility() {
    let fx = build_fixture();
    fx.registry
        .put("bot_x", ActorKind::Bot, "protected", ActorStatus::Online)
        .await;
    fx.registry
        .put(
            "human_y",
            ActorKind::Human,
            "protected",
            ActorStatus::Online,
        )
        .await;

    let req = fx.request.create_request("bot_x", "human_y").await.unwrap();
    fx.request.accept_request(&req.id).await.unwrap();

    assert!(fx.friend.are_friends("bot_x", "human_y").await);
    assert!(
        fx.relation
            .get_edge("bot_x", "human_y", &fx.env)
            .await
            .unwrap()
            .is_some()
    );
}

/// list_friends_via_relation results are independent of status — going
/// Hidden does not remove a friend from the list.
#[tokio::test]
async fn test_list_friends_independent_of_status() {
    let fx = build_fixture();
    let alice = "alice";
    let bob = "bob";
    fx.registry
        .put(alice, ActorKind::Bot, "protected", ActorStatus::Online)
        .await;
    fx.registry
        .put(bob, ActorKind::Bot, "protected", ActorStatus::Online)
        .await;

    // Establish friendship
    let req = fx.request.create_request(alice, bob).await.unwrap();
    fx.request.accept_request(&req.id).await.unwrap();

    let before = fx
        .relation
        .list_friends_via_relation(alice, &fx.env)
        .await
        .unwrap();
    assert!(before.contains(&bob.to_string()));

    // Flip bob to Hidden — must not affect alice's friend list
    fx.registry.set_status(bob, ActorStatus::Hidden).await;

    let after = fx
        .relation
        .list_friends_via_relation(alice, &fx.env)
        .await
        .unwrap();
    assert_eq!(
        before, after,
        "status flip must not change relation-graph membership"
    );
}
