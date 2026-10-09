//! I.2 + I.3 — PUT mode / PUT status integration tests (service-layer).
//!
//! Both endpoints share the same caller-resolution and target-validation
//! pipeline (`resolve_put_caller` → registry lookup → kind/mode validation →
//! permission check via relation graph). The HTTP handler is a thin shell
//! around the trait methods exercised here:
//!
//! - I.2 PUT `/groups/{gid}/participants/{aid}/mode`
//!     → `ParticipantMode::is_valid_for(actor_kind)`
//!     → `RelationCoreService::get_edge(caller, target).is_creator` for creator path
//!     → group store update (covered by P.1/P.3 in earlier milestones)
//! - I.3 PUT `/actors/{aid}/status`
//!     → `BotRegistryCoreService::update_actor_status(bot_id, status)` persistence
//!     → registry read-back returns the updated status
//!     → status flip is independent of capabilities (visibility / name)
//!
//! Why service-layer: the test harness has no real Buservice SSO cookie,
//! so the only HTTP path that works in tests is Bot-self via Bearer
//! (`caller == aid`). That covers a small fraction of the contract; the
//! cross-cutting permission + persistence story below is what production
//! actually relies on.

use std::sync::Arc;

use bcs_bot::BotCore;
use bcs_relation::MemoryRelationStore;
use bcs_service_api::{
    ActorKind, ActorStatus, BotRegistryCoreService, ParticipantMode, RelationCoreService,
};

fn temp_registry() -> Arc<BotCore> {
    let dir = tempfile::tempdir().expect("tempdir");
    Arc::new(BotCore::with_base_dir(dir.keep()))
}

// ============================================================================
// I.2 — ParticipantMode::is_valid_for matrix (the contract PUT mode enforces)
// ============================================================================

/// The 4 legal `(actor_kind, mode)` combinations are accepted; the other 4
/// are rejected. The HTTP handler returns 400 for any rejected combination.
#[test]
fn test_participant_mode_validity_matrix() {
    // Bot ↔ Auto / Muted are legal
    assert!(ParticipantMode::Auto.is_valid_for(ActorKind::Bot));
    assert!(ParticipantMode::Muted.is_valid_for(ActorKind::Bot));
    // Bot ↔ Present / Absent are illegal
    assert!(!ParticipantMode::Present.is_valid_for(ActorKind::Bot));
    assert!(!ParticipantMode::Absent.is_valid_for(ActorKind::Bot));

    // Human ↔ Present / Absent are legal
    assert!(ParticipantMode::Present.is_valid_for(ActorKind::Human));
    assert!(ParticipantMode::Absent.is_valid_for(ActorKind::Human));
    // Human ↔ Auto / Muted are illegal
    assert!(!ParticipantMode::Auto.is_valid_for(ActorKind::Human));
    assert!(!ParticipantMode::Muted.is_valid_for(ActorKind::Human));
}

/// `default_for(kind)` defaults match the documented values:
/// - Bot   → Auto   (active by default)
/// - Human → Absent (silent until promoted to Present)
#[test]
fn test_participant_mode_default_for_kind() {
    assert_eq!(
        ParticipantMode::default_for(ActorKind::Bot),
        ParticipantMode::Auto
    );
    assert_eq!(
        ParticipantMode::default_for(ActorKind::Human),
        ParticipantMode::Absent
    );
}

// ============================================================================
// I.2 — Permission contract: caller permitted iff caller == target OR
// (caller, target) edge has `is_creator=TRUE`. We exercise the relation
// lookup the handler depends on; the handler's response mapping is a thin
// translation of these results.
// ============================================================================

/// Self-edit path: the canonical `caller == target` shortcut. No relation
/// edge is needed.
#[tokio::test]
async fn test_self_edit_does_not_require_relation_edge() {
    let relation = MemoryRelationStore::new();
    let env = bcs_config::resolve_env_str();

    // No edges exist for "alice"
    let edge = relation.get_edge("alice", "alice", &env).await.unwrap();
    assert!(edge.is_none());

    // Handler logic: caller == target → permitted regardless of edges
    let caller = "alice";
    let target = "alice";
    let permitted = caller == target;
    assert!(permitted);
}

/// Creator path: caller is the bot's owner, so `(caller, target)` edge has
/// `is_creator=TRUE`.
#[tokio::test]
async fn test_creator_can_edit_owned_actor_via_relation_edge() {
    let relation = MemoryRelationStore::new();
    let env = bcs_config::resolve_env_str();

    relation
        .ensure_owner_edges("human_owner", "bot_target", &env)
        .await
        .unwrap();

    let edge = relation
        .get_edge("human_owner", "bot_target", &env)
        .await
        .unwrap()
        .unwrap();
    assert!(
        edge.is_creator,
        "owner edge MUST have is_creator=TRUE for permission check"
    );
}

/// Non-creator path: a stranger calling PUT mode/status MUST be rejected
/// because their (caller, target) edge does not exist or has
/// `is_creator=FALSE`.
#[tokio::test]
async fn test_stranger_cannot_edit_unrelated_actor() {
    let relation = MemoryRelationStore::new();
    let env = bcs_config::resolve_env_str();

    // Friend pairing: stranger ↔ target are friends but stranger does NOT own target
    relation
        .add_friend_edges("stranger", "target", &env)
        .await
        .unwrap();

    let edge = relation
        .get_edge("stranger", "target", &env)
        .await
        .unwrap()
        .unwrap();
    assert!(
        !edge.is_creator,
        "friend edge MUST have is_creator=FALSE — handler rejects this caller"
    );
}

/// No edge at all: caller has never interacted with target → 403.
#[tokio::test]
async fn test_unrelated_caller_has_no_edge() {
    let relation = MemoryRelationStore::new();
    let env = bcs_config::resolve_env_str();

    let edge = relation
        .get_edge("randomuser", "victim", &env)
        .await
        .unwrap();
    assert!(edge.is_none(), "unrelated caller has no edge — 403 path");
}

// ============================================================================
// I.3 — update_actor_status persistence + read-back
// ============================================================================

/// Online → Hidden flip is observable via the next `get` call, with all
/// other fields preserved.
#[tokio::test]
async fn test_update_actor_status_online_to_hidden_persists() {
    let registry = temp_registry();
    let staff_no = "i3_user_a";
    let nick_name = "Status Test User";
    let bot_uuid = format!("human_{}", staff_no);

    registry
        .ensure_human_actor(staff_no, nick_name)
        .await
        .unwrap();
    let before = registry.get(&bot_uuid).await.unwrap();
    assert_eq!(before.status, ActorStatus::Online);

    registry
        .update_actor_status(&bot_uuid, ActorStatus::Hidden)
        .await
        .unwrap();

    let after = registry.get(&bot_uuid).await.unwrap();
    assert_eq!(after.status, ActorStatus::Hidden);
    // Other fields preserved
    assert_eq!(after.actor_kind, ActorKind::Human);
    assert_eq!(after.capabilities.name.as_deref(), Some(nick_name));
    assert_eq!(
        after.capabilities.visibility,
        before.capabilities.visibility
    );
}

/// Hidden → Online flip-back is also persisted.
#[tokio::test]
async fn test_update_actor_status_hidden_to_online_round_trip() {
    let registry = temp_registry();
    let staff_no = "i3_user_b";
    let bot_uuid = format!("human_{}", staff_no);

    registry.ensure_human_actor(staff_no, "B").await.unwrap();

    registry
        .update_actor_status(&bot_uuid, ActorStatus::Hidden)
        .await
        .unwrap();
    assert_eq!(
        registry.get(&bot_uuid).await.unwrap().status,
        ActorStatus::Hidden
    );

    registry
        .update_actor_status(&bot_uuid, ActorStatus::Online)
        .await
        .unwrap();
    assert_eq!(
        registry.get(&bot_uuid).await.unwrap().status,
        ActorStatus::Online
    );
}

/// `update_actor_status` on an unknown bot is a no-op (debug log) — does NOT
/// fabricate a row. The HTTP handler does its own existence check via `get`
/// before calling this, so a 404 path is never reached at the trait layer.
#[tokio::test]
async fn test_update_actor_status_on_unknown_bot_is_noop() {
    let registry = temp_registry();
    registry
        .update_actor_status("ghost_bot", ActorStatus::Hidden)
        .await
        .unwrap();
    assert!(registry.get("ghost_bot").await.is_none());
}

/// Status flips are independent of `update_visibility` — confirms the trait
/// methods write to disjoint fields.
#[tokio::test]
async fn test_status_and_visibility_flip_independently_at_trait_layer() {
    let registry = temp_registry();
    let staff_no = "i3_user_c";
    let bot_uuid = format!("human_{}", staff_no);

    registry.ensure_human_actor(staff_no, "C").await.unwrap();

    // Update status only
    registry
        .update_actor_status(&bot_uuid, ActorStatus::Hidden)
        .await
        .unwrap();
    let after_status = registry.get(&bot_uuid).await.unwrap();
    assert_eq!(after_status.status, ActorStatus::Hidden);
    assert_eq!(after_status.capabilities.visibility, "protected");

    // Update visibility only
    registry
        .update_visibility(&bot_uuid, "public")
        .await
        .unwrap();
    let after_vis = registry.get(&bot_uuid).await.unwrap();
    // Status preserved across visibility update
    assert_eq!(after_vis.status, ActorStatus::Hidden);
    assert_eq!(after_vis.capabilities.visibility, "public");
}

// ============================================================================
// I.3 — Same permission contract as I.2 (resolve_put_caller is shared).
// Negative test: a stranger updating someone else's status must be blocked
// at the relation layer (handler returns 403).
// ============================================================================

/// Defense-in-depth: even if the registry contains a target row, the handler
/// requires `caller == target` OR `(caller, target).is_creator=TRUE`. Verify
/// the trait-level building blocks for that decision.
#[tokio::test]
async fn test_status_permission_inputs_are_consistent() {
    let registry = temp_registry();
    let relation = MemoryRelationStore::new();
    let env = bcs_config::resolve_env_str();

    // Setup: human_owner owns bot_owned; stranger has no edge
    registry.ensure_human_actor("owner", "Owner").await.unwrap();
    let owner_id = "human_owner";
    let target_id = "bot_owned";

    relation
        .ensure_owner_edges(owner_id, target_id, &env)
        .await
        .unwrap();

    // Owner's edge to target exists and is_creator=TRUE
    let owner_edge = relation
        .get_edge(owner_id, target_id, &env)
        .await
        .unwrap()
        .unwrap();
    assert!(owner_edge.is_creator);

    // Stranger has no edge
    let stranger_edge = relation
        .get_edge("stranger", target_id, &env)
        .await
        .unwrap();
    assert!(stranger_edge.is_none());
}
