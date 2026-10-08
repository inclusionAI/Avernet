//! I.1 — Human onboard integration tests (service-layer).
//!
//! Covers tasks O.2 / O.3 / O.4 at the service-trait boundary:
//!
//! - O.3 `ensure_human_actor`: creates a `human_<staff_no>` row with
//!       `actor_kind=Human`, `visibility=protected`, `status=Online`,
//!       `created_by=<staff_no>`, and `name=<nick_name>`.
//! - O.3 idempotency: a second call with the *same* staff_no MUST NOT
//!       overwrite the previously stored `name` (Requirement 3.1#4) — a
//!       second user updating their nick_name from the IdP should not
//!       silently rewrite the stored canonical name.
//! - O.4 `ensure_owner_edges`: writes the canonical owner edge
//!       `(human → bot, is_creator=TRUE)` and the reverse traversal edge
//!       `(bot → human, is_creator=FALSE)` idempotently.
//! - Owner edges + friend edges share the same store: ensuring owner edges
//!       a second time MUST NOT downgrade `is_creator`.
//!
//! These run at the service-trait layer rather than against the HTTP
//! `onboard_bot` handler because the test harness has no real Buservice SSO
//! cookie path; the handler delegates straight through to the same trait
//! methods exercised here.

use std::sync::Arc;

use bcs_bot::BotCore;
use bcs_relation::MemoryRelationStore;
use bcs_service_api::{ActorKind, ActorStatus, BotRegistryCoreService, RelationCoreService};

fn temp_registry() -> Arc<BotCore> {
    let dir = tempfile::tempdir().expect("tempdir");
    Arc::new(BotCore::with_base_dir(dir.keep()))
}

// ============================================================================
// O.3 — ensure_human_actor
// ============================================================================

/// First `ensure_human_actor` call materializes the `human_<staff>` row with
/// the contracted defaults.
#[tokio::test]
async fn test_ensure_human_actor_creates_canonical_row() {
    let registry = temp_registry();
    let staff_no = "12345";
    let nick_name = "Alice";

    registry
        .ensure_human_actor(staff_no, nick_name)
        .await
        .expect("ensure_human_actor must succeed");

    let bot_uuid = format!("human_{}", staff_no);
    let row = registry
        .get(&bot_uuid)
        .await
        .expect("row must be present after ensure_human_actor");

    assert_eq!(row.bot_uuid, bot_uuid);
    assert_eq!(row.actor_kind, ActorKind::Human);
    assert_eq!(row.status, ActorStatus::Online);
    assert_eq!(row.capabilities.visibility, "protected");
    assert_eq!(row.capabilities.name.as_deref(), Some(nick_name));
    assert_eq!(row.created_by.as_deref(), Some(staff_no));
}

/// O.3 idempotency: a second `ensure_human_actor` call with the same staff_no
/// MUST NOT overwrite the originally stored nick_name.
#[tokio::test]
async fn test_ensure_human_actor_preserves_existing_name_on_repeat() {
    let registry = temp_registry();
    let staff_no = "67890";

    registry
        .ensure_human_actor(staff_no, "Original Name")
        .await
        .unwrap();
    registry
        .ensure_human_actor(staff_no, "Drifted Name From IdP")
        .await
        .unwrap();

    let row = registry.get(&format!("human_{}", staff_no)).await.unwrap();
    assert_eq!(
        row.capabilities.name.as_deref(),
        Some("Original Name"),
        "second ensure_human_actor MUST preserve the first nick_name (Requirement 3.1#4)"
    );
    assert_eq!(row.actor_kind, ActorKind::Human);
}

/// Two distinct staff_nos produce two distinct `human_<staff>` rows; the
/// `human_` prefix is the only namespacing rule we depend on.
#[tokio::test]
async fn test_ensure_human_actor_distinct_staff_yields_distinct_rows() {
    let registry = temp_registry();
    registry.ensure_human_actor("a1", "Alice").await.unwrap();
    registry.ensure_human_actor("b2", "Bob").await.unwrap();

    let alice = registry.get("human_a1").await.unwrap();
    let bob = registry.get("human_b2").await.unwrap();
    assert_eq!(alice.capabilities.name.as_deref(), Some("Alice"));
    assert_eq!(bob.capabilities.name.as_deref(), Some("Bob"));
    assert_eq!(alice.actor_kind, ActorKind::Human);
    assert_eq!(bob.actor_kind, ActorKind::Human);
}

// ============================================================================
// O.4 — ensure_owner_edges
// ============================================================================

/// O.4 first call: writes both owner edges, marking the human → bot edge as
/// `is_creator=TRUE`. The reverse bot → human edge MAY be written with
/// `is_creator=FALSE` for traversal — both presence cases are valid per
/// the trait contract; we only assert the canonical owner direction.
#[tokio::test]
async fn test_ensure_owner_edges_writes_canonical_owner_edge() {
    let relation = MemoryRelationStore::new();
    let env = bcs_config::resolve_env_str();

    relation
        .ensure_owner_edges("human_a1", "bot_x", &env)
        .await
        .unwrap();

    let owner = relation
        .get_edge("human_a1", "bot_x", &env)
        .await
        .unwrap()
        .expect("canonical owner edge must exist");
    assert!(
        owner.is_creator,
        "human → bot owner edge MUST have is_creator=TRUE"
    );

    let reverse = relation
        .get_edge("bot_x", "human_a1", &env)
        .await
        .unwrap()
        .expect("reverse traversal edge must exist for in-memory impl");
    assert!(
        !reverse.is_creator,
        "reverse edge MUST be is_creator=FALSE so it doesn't get treated as a second owner"
    );
}

/// O.4 idempotency: calling `ensure_owner_edges` twice MUST NOT downgrade the
/// `is_creator` flag on the canonical edge.
#[tokio::test]
async fn test_ensure_owner_edges_is_idempotent_and_preserves_creator_flag() {
    let relation = MemoryRelationStore::new();
    let env = bcs_config::resolve_env_str();

    relation
        .ensure_owner_edges("human_a1", "bot_x", &env)
        .await
        .unwrap();
    relation
        .ensure_owner_edges("human_a1", "bot_x", &env)
        .await
        .unwrap();

    let owner = relation
        .get_edge("human_a1", "bot_x", &env)
        .await
        .unwrap()
        .unwrap();
    assert!(
        owner.is_creator,
        "double ensure_owner_edges MUST NOT downgrade is_creator"
    );
}

/// One human can own multiple bots; each owner pair gets its own edges,
/// independent of the other.
#[tokio::test]
async fn test_one_human_can_own_multiple_bots() {
    let relation = MemoryRelationStore::new();
    let env = bcs_config::resolve_env_str();

    relation
        .ensure_owner_edges("human_creator", "bot_a", &env)
        .await
        .unwrap();
    relation
        .ensure_owner_edges("human_creator", "bot_b", &env)
        .await
        .unwrap();

    for bot in ["bot_a", "bot_b"] {
        let edge = relation
            .get_edge("human_creator", bot, &env)
            .await
            .unwrap()
            .unwrap();
        assert!(
            edge.is_creator,
            "owner edge for {} must be is_creator=TRUE",
            bot
        );
    }
}

/// One bot can be owned by only one human in V1 (the second `ensure_owner_edges`
/// call from a *different* human MUST still create its own owner edge — the
/// trait does not enforce single-ownership; that's a higher-layer policy).
/// We assert the trait behavior here so any future single-owner enforcement
/// is a deliberate, version-bumped contract change.
#[tokio::test]
async fn test_ensure_owner_edges_does_not_block_second_owner_at_trait_layer() {
    let relation = MemoryRelationStore::new();
    let env = bcs_config::resolve_env_str();

    relation
        .ensure_owner_edges("human_first", "bot_shared", &env)
        .await
        .unwrap();
    relation
        .ensure_owner_edges("human_second", "bot_shared", &env)
        .await
        .unwrap();

    // Both owner edges exist
    let first = relation
        .get_edge("human_first", "bot_shared", &env)
        .await
        .unwrap()
        .unwrap();
    let second = relation
        .get_edge("human_second", "bot_shared", &env)
        .await
        .unwrap()
        .unwrap();
    assert!(first.is_creator);
    assert!(second.is_creator);
}

// ============================================================================
// O.3 + O.4 combined: end-to-end onboard simulation at the service layer
// ============================================================================

/// Simulates the full onboard flow that `onboard_bot` walks through:
/// 1. `ensure_human_actor(staff_no, nick_name)` — provisions the Human row
/// 2. `ensure_owner_edges(human_id, bot_id, env)` — wires the owner edges
///
/// Verifies post-conditions on both stores.
#[tokio::test]
async fn test_combined_human_onboard_walks_registry_and_relation_in_order() {
    let registry = temp_registry();
    let relation = MemoryRelationStore::new();
    let env = bcs_config::resolve_env_str();

    let staff_no = "777";
    let nick_name = "Charlie";
    let bot_id = "bot_charlie_owns";
    let human_id = format!("human_{}", staff_no);

    // Step 1: ensure_human_actor
    registry
        .ensure_human_actor(staff_no, nick_name)
        .await
        .unwrap();
    let human = registry.get(&human_id).await.unwrap();
    assert_eq!(human.actor_kind, ActorKind::Human);

    // Step 2: ensure_owner_edges
    relation
        .ensure_owner_edges(&human_id, bot_id, &env)
        .await
        .unwrap();

    let owner = relation
        .get_edge(&human_id, bot_id, &env)
        .await
        .unwrap()
        .unwrap();
    assert!(owner.is_creator);
}
