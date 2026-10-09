//! I.5 — DM group integration tests (G.1-G.5 contract).
//!
//! Validates the `create_or_reuse_dm_group` contract that drives the
//! `POST /sessions { kind: "dm" }` HTTP path, plus the auxiliary
//! `find_dm_by_pair_key` / `count_by_kind` / `list_paginated_by_kind`
//! pieces the GET `/groups?group_kind=dm` listing depends on.
//!
//! Why service-layer: the HTTP `create_session_dm` handler is a thin
//! wrapper around these trait methods (CR-1 fix in `server.rs::create_session_dm`
//! delegates the entire create-or-reuse flow to `create_or_reuse_dm_group`).
//! Testing at the trait layer covers:
//!
//! - G.1 first-create: `created=true`, `group_kind=Dm`, canonical
//!       `dm_pair_key`, two Bot participants, driver_bot honoured.
//! - G.2 reverse-reuse (B,A) on an existing (A,B) row: returns the existing
//!       row with `created=false`; `driver_bot` / `label` / `participants`
//!       MUST NOT be mutated (CR-1 regression guard).
//! - G.2 same-direction repeat: also reuses; new caller's `id` / `label` are
//!       silently dropped.
//! - G.3 `find_dm_by_pair_key` parity: lookup returns the same row that
//!       `create_or_reuse_dm_group` produced.
//! - G.4 listing pushdown: `count_by_kind(Some(Dm))` and
//!       `list_paginated_by_kind(Some(Dm), ..)` agree on the same filter and
//!       both exclude Normal groups (CR-4 regression guard).
//! - G.5 DM kind invariants: `Group::new` defaults to Normal; explicit DM
//!       construction sets `group_kind=Dm` and `dm_pair_key` deterministically.
//!
//! NOTE: The `delete-dm` 400 acceptance referenced in tasks.md is currently
//! defended at the HTTP routing layer (no `DELETE /groups/{id}` handler is
//! wired for DM groups). There is no service-trait method for "delete dm
//! group", so this acceptance is not exercised here; it is covered by the
//! absence of the delete route in `routes()` (verified by code inspection).

use std::sync::Arc;

use bcs_group::GroupStore;
use bcs_service_api::{
    ActorKind, Group, GroupKind, GroupCoreService, ParticipantRole,
};

fn build_store() -> Arc<GroupStore> {
    Arc::new(GroupStore::default())
}

// ============================================================================
// G.1 — first-create
// ============================================================================

/// First call materializes the DM group with all the contracted defaults.
#[tokio::test]
async fn test_create_dm_group_first_call_returns_created_true() {
    let store = build_store();
    let (group, created) = store
        .create_or_reuse_dm_group(
            "g_dm_1",
            "alice",
            "alice",
            "bob",
            Some("dm:alice↔bob".to_string()),
        )
        .await
        .expect("first create must succeed");

    assert!(created, "first create must report created=true");
    assert_eq!(group.id, "g_dm_1");
    assert_eq!(group.group_kind, GroupKind::Dm);
    assert_eq!(group.driver_bot, "alice");
    assert_eq!(group.label.as_deref(), Some("dm:alice↔bob"));

    // pair_key is the canonical (sorted) form
    let expected_pair = Group::compute_dm_pair_key("alice", "bob");
    assert_eq!(group.dm_pair_key.as_deref(), Some(expected_pair.as_str()));

    // Two Bot participants with the right roles
    assert_eq!(group.participants.len(), 2);
    let alice_p = group
        .participants
        .iter()
        .find(|p| p.bot_uuid == "alice")
        .expect("alice participant present");
    let bob_p = group
        .participants
        .iter()
        .find(|p| p.bot_uuid == "bob")
        .expect("bob participant present");
    assert_eq!(alice_p.role, ParticipantRole::Driver);
    assert_eq!(bob_p.role, ParticipantRole::Consultant);
    assert_eq!(alice_p.actor_kind, ActorKind::Bot);
    assert_eq!(bob_p.actor_kind, ActorKind::Bot);
}

// ============================================================================
// G.2 — reverse-reuse and same-direction repeat
// ============================================================================

/// CR-1 regression: a (B,A) request on an existing (A,B) DM MUST NOT mutate
/// the existing row's `driver_bot`, `label`, or `participants`. The caller
/// gets back the canonical existing group with `created=false`.
#[tokio::test]
async fn test_create_dm_reverse_reuse_does_not_mutate_existing() {
    let store = build_store();

    // First create with (alice, bob), driver=alice
    let (first, first_created) = store
        .create_or_reuse_dm_group(
            "g_dm_orig",
            "alice",
            "alice",
            "bob",
            Some("dm:alice↔bob".to_string()),
        )
        .await
        .unwrap();
    assert!(first_created);
    assert_eq!(first.driver_bot, "alice");

    // Reverse call with (bob, alice), driver=bob, different label
    let (second, second_created) = store
        .create_or_reuse_dm_group(
            "g_dm_NEW_id_should_be_dropped",
            "bob",
            "bob",
            "alice",
            Some("DROPPED LABEL".to_string()),
        )
        .await
        .unwrap();

    assert!(!second_created, "reverse-direction call MUST be reuse, not create");
    // Identity columns from the existing row, NOT the caller's request
    assert_eq!(second.id, "g_dm_orig", "reused id wins");
    assert_eq!(
        second.driver_bot, "alice",
        "driver_bot MUST NOT be overwritten on reverse-reuse"
    );
    assert_eq!(
        second.label.as_deref(),
        Some("dm:alice↔bob"),
        "label MUST NOT be overwritten on reverse-reuse"
    );

    // pair_key matches and the canonical row is what get() returns
    let same = store.get("g_dm_orig").await.expect("original row still there");
    assert_eq!(same.driver_bot, "alice");
    assert_eq!(same.label.as_deref(), Some("dm:alice↔bob"));
}

/// Same-direction repeat: also a reuse; caller's id / label dropped.
#[tokio::test]
async fn test_create_dm_same_direction_repeat_is_reuse() {
    let store = build_store();

    let (orig, c1) = store
        .create_or_reuse_dm_group("g_a", "alice", "alice", "bob", Some("L1".into()))
        .await
        .unwrap();
    assert!(c1);

    let (again, c2) = store
        .create_or_reuse_dm_group("g_b_DIFFERENT", "alice", "alice", "bob", Some("L2".into()))
        .await
        .unwrap();
    assert!(!c2, "second same-direction call MUST be reuse");
    assert_eq!(again.id, orig.id);
    assert_eq!(again.label.as_deref(), Some("L1"));
}

// ============================================================================
// G.3 — find_dm_by_pair_key parity
// ============================================================================

/// `find_dm_by_pair_key` returns the same row that `create_or_reuse_dm_group`
/// just produced. This is the lookup the handler uses for the pre-flight
/// "does it already exist?" check.
#[tokio::test]
async fn test_find_dm_by_pair_key_round_trips_with_create() {
    let store = build_store();
    let (created, _) = store
        .create_or_reuse_dm_group("g1", "alice", "alice", "bob", None)
        .await
        .unwrap();
    let pair_key = Group::compute_dm_pair_key("alice", "bob");

    let found = store
        .find_dm_by_pair_key(&pair_key)
        .await
        .expect("must find DM by pair key");

    assert_eq!(found.id, created.id);
    assert_eq!(found.group_kind, GroupKind::Dm);
    assert_eq!(found.dm_pair_key.as_deref(), Some(pair_key.as_str()));
}

/// `find_dm_by_pair_key` returns `None` when the key was never inserted.
/// Critically, a **Normal** group with a coincidentally matching pair_key
/// MUST NOT be returned (the lookup is `kind=Dm AND dm_pair_key=?`).
#[tokio::test]
async fn test_find_dm_by_pair_key_ignores_normal_groups() {
    let store = build_store();
    let pair_key = Group::compute_dm_pair_key("ghost_a", "ghost_b");

    // Insert a Normal group with the same pair_key set explicitly — this
    // simulates a corrupted row that should still NOT be matched.
    let mut g = Group::new("g_normal", "x", vec![]);
    g.dm_pair_key = Some(pair_key.clone()); // intentionally weird
    g.group_kind = GroupKind::Normal;
    store.upsert(g).await.unwrap();

    let found = store.find_dm_by_pair_key(&pair_key).await;
    assert!(
        found.is_none(),
        "find_dm_by_pair_key MUST require group_kind=Dm; got {:?}",
        found
    );
}

// ============================================================================
// G.4 — count_by_kind / list_paginated_by_kind pushdown contract (CR-4)
// ============================================================================

/// `count_by_kind(None)` matches `count()`; `count_by_kind(Some(Dm))` agrees
/// with `list_paginated_by_kind(Some(Dm), ..)` length when the page covers
/// all rows. This is the consistency property that the HTTP `total` and
/// `page` rely on.
#[tokio::test]
async fn test_count_and_list_by_kind_are_consistent() {
    let store = build_store();

    // 2 DM groups, 3 Normal groups
    store
        .create_or_reuse_dm_group("dm1", "a", "a", "b", None)
        .await
        .unwrap();
    store
        .create_or_reuse_dm_group("dm2", "c", "c", "d", None)
        .await
        .unwrap();
    for id in ["n1", "n2", "n3"] {
        store
            .upsert(Group::new(id, "driver", vec![]))
            .await
            .unwrap();
    }

    // count
    assert_eq!(store.count_by_kind(None).await, 5);
    assert_eq!(store.count_by_kind(Some(GroupKind::Dm)).await, 2);
    assert_eq!(store.count_by_kind(Some(GroupKind::Normal)).await, 3);

    // list pagination, full page
    let dm_page = store
        .list_paginated_by_kind(Some(GroupKind::Dm), 0, 100)
        .await;
    assert_eq!(dm_page.len(), 2);
    for g in &dm_page {
        assert_eq!(g.group_kind, GroupKind::Dm);
    }

    let normal_page = store
        .list_paginated_by_kind(Some(GroupKind::Normal), 0, 100)
        .await;
    assert_eq!(normal_page.len(), 3);
    for g in &normal_page {
        assert_eq!(g.group_kind, GroupKind::Normal);
    }
}

/// CR-4 regression: paging by DM MUST return DM rows even when the natural
/// scan order interleaves Normal rows. The handler relied on this for
/// "page 2 of dm" not returning empty when page 1 happened to land on
/// Normal groups in the legacy in-memory filter path.
#[tokio::test]
async fn test_list_paginated_by_kind_filters_before_pagination() {
    let store = build_store();

    // Mix order: normal, dm, normal, dm, normal
    store
        .upsert(Group::new("n1", "d", vec![]))
        .await
        .unwrap();
    store
        .create_or_reuse_dm_group("dm1", "a", "a", "b", None)
        .await
        .unwrap();
    store
        .upsert(Group::new("n2", "d", vec![]))
        .await
        .unwrap();
    store
        .create_or_reuse_dm_group("dm2", "c", "c", "d", None)
        .await
        .unwrap();
    store
        .upsert(Group::new("n3", "d", vec![]))
        .await
        .unwrap();

    // First page of dm with limit=1 must return a DM row, not depend on
    // Normal-row positions in the natural scan
    let page = store
        .list_paginated_by_kind(Some(GroupKind::Dm), 0, 1)
        .await;
    assert_eq!(page.len(), 1);
    assert_eq!(page[0].group_kind, GroupKind::Dm);

    // Total agrees with what an unbounded list would yield
    let all_dm = store
        .list_paginated_by_kind(Some(GroupKind::Dm), 0, 100)
        .await;
    assert_eq!(all_dm.len(), 2);
}

// ============================================================================
// G.5 — DM group invariants
// ============================================================================

/// `Group::new` defaults to Normal; explicit DM must be set via the dedicated
/// `create_or_reuse_dm_group` factory.
#[test]
fn test_group_new_defaults_to_normal_kind() {
    let g = Group::new("plain", "driver", vec![]);
    assert_eq!(g.group_kind, GroupKind::Normal);
    assert!(g.dm_pair_key.is_none());
}

/// `compute_dm_pair_key` is order-insensitive: (A,B) and (B,A) produce the
/// same canonical key. This is the foundation of reverse-reuse.
#[test]
fn test_compute_dm_pair_key_is_order_insensitive() {
    let ab = Group::compute_dm_pair_key("alice", "bob");
    let ba = Group::compute_dm_pair_key("bob", "alice");
    assert_eq!(ab, ba);

    // Different pair → different key
    let ac = Group::compute_dm_pair_key("alice", "carol");
    assert_ne!(ab, ac);
}

// ============================================================================
// F.7 equivalent in DM context: DM groups never accept new participants
// (they have a fixed two-Bot membership). We assert the DM construction
// invariant here so any future regression that tries to invite a Human into
// a DM is caught at the data-shape level.
// ============================================================================

/// DM groups have exactly 2 Bot participants. There's no service trait method
/// to add a participant to a DM (the HTTP `add_participant` handler rejects
/// DMs at the routing/handler layer); the data shape itself prevents Human
/// participants in a DM as long as `create_or_reuse_dm_group` is the only
/// constructor.
#[tokio::test]
async fn test_dm_group_has_exactly_two_bot_participants() {
    let store = build_store();
    let (group, _) = store
        .create_or_reuse_dm_group("g", "alice", "alice", "bob", None)
        .await
        .unwrap();

    assert_eq!(group.participants.len(), 2);
    for p in &group.participants {
        assert_eq!(
            p.actor_kind,
            ActorKind::Bot,
            "DM participants are always Bot via this constructor"
        );
    }
}
