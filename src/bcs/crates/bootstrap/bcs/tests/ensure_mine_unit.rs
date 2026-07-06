//! T-6 — Unit tests (in-memory) for the `POST /me/ensure-human` feature.
//!
//! Exercises `BotCore` (in-memory) + `MemoryRelationStore` directly,
//! covering:
//! - `ensure_human_actor` idempotency and `EnsureHumanResult.created`
//! - `list_legacy_bots_for_owner` whitelist matching rules
//! - `ensure_owner_edges_counted` creation / upgrade / idempotency
//! - `is_legacy_namespace` boundary cases

use bcs_bot::BotCore;
use bcs_relation::MemoryRelationStore;
use bcs_service_api::{ActorKind, BotCapabilities, BotRegistryCoreService, RelationCoreService};

// ============================================================================
// Helpers
// ============================================================================

/// Register a bot in the in-memory registry with the given parameters.
///
/// `created_by` and `env` are set after initial registration via
/// `save_created_by` and a direct `register` call with env-aware capabilities.
async fn seed_bot(registry: &BotCore, bot_uuid: &str, created_by: Option<&str>, _env: &str) {
    let caps = BotCapabilities {
        name: Some(format!("Bot-{}", bot_uuid)),
        ..Default::default()
    };
    registry
        .register(bot_uuid.to_string(), caps)
        .await
        .expect("register bot");

    // The in-memory register() does not set `env` by default — it falls back
    // to `resolve_env()`. We need to set `created_by` explicitly.
    if let Some(creator) = created_by {
        registry
            .save_created_by(bot_uuid, creator, true)
            .await
            .expect("save_created_by");
    }
}

fn resolve_env() -> String {
    bcs_config::resolve_env_str()
}

// ============================================================================
// Section 1: ensure_human_actor
// ============================================================================

/// New user → `human_created=true`.
#[tokio::test]
async fn ensure_human_new_user_created_true() {
    let registry = BotCore::new();
    let result = registry
        .ensure_human_actor("12345", "Alice")
        .await
        .expect("ensure_human_actor");
    assert!(result.created, "new Human row must report created=true");
}

/// Existing Human → `human_created=false`, name preserved.
#[tokio::test]
async fn ensure_human_existing_user_created_false() {
    let registry = BotCore::new();
    registry
        .ensure_human_actor("12345", "Alice")
        .await
        .expect("first call");

    let result = registry
        .ensure_human_actor("12345", "Bob")
        .await
        .expect("second call");
    assert!(
        !result.created,
        "existing Human row must report created=false"
    );

    // Name from first call is preserved (Requirement 3.1#4).
    let bot = registry.get("human_12345").await;
    assert!(bot.is_some());
    assert_eq!(
        bot.unwrap().capabilities.name.as_deref(),
        Some("Alice"),
        "original name must be preserved on subsequent calls"
    );
}

/// New Human → default summary is set.
#[tokio::test]
async fn ensure_human_new_user_has_default_summary() {
    let registry = BotCore::new();
    registry
        .ensure_human_actor("s001", "Alice")
        .await
        .expect("ensure_human_actor");

    let bot = registry.get("human_s001").await.expect("human must exist");
    assert_eq!(
        bot.capabilities.summary.as_deref(),
        Some("写点什么介绍自己"),
        "new Human must have default summary"
    );
}

/// Existing Human with empty summary → summary is backfilled.
///
/// Simulates a legacy Human (created before the summary feature) by first
/// registering a "human_s002" bot via `register()` with no summary, then
/// calling `ensure_human_actor` which should detect the empty summary and
/// backfill it.
#[tokio::test]
async fn ensure_human_empty_summary_backfilled() {
    let registry = BotCore::new();

    // Simulate a legacy Human entry that has no summary.
    let caps_no_summary = BotCapabilities {
        name: Some("Bob".to_string()),
        visibility: "protected".to_string(),
        ..Default::default() // summary is None
    };
    registry
        .register("human_s002".to_string(), caps_no_summary)
        .await
        .expect("pre-register legacy human");

    // Verify summary is indeed empty before the test.
    let before = registry.get("human_s002").await.expect("must exist");
    assert!(
        before.capabilities.summary.is_none(),
        "pre-condition: summary must be None"
    );

    // ensure_human_actor should detect the existing entry and backfill summary.
    let result = registry
        .ensure_human_actor("s002", "Bob")
        .await
        .expect("ensure_human_actor");
    assert!(
        !result.created,
        "existing entry must not report created=true"
    );

    let after = registry.get("human_s002").await.expect("must exist");
    assert_eq!(
        after.capabilities.summary.as_deref(),
        Some("写点什么介绍自己"),
        "empty summary must be backfilled"
    );
}

/// Existing Human with non-empty summary → summary is NOT overwritten.
#[tokio::test]
async fn ensure_human_nonempty_summary_preserved() {
    let registry = BotCore::new();

    // Pre-register a Human entry with a custom summary.
    let caps_custom_summary = BotCapabilities {
        name: Some("Carol".to_string()),
        summary: Some("I am a DBA expert".to_string()),
        visibility: "protected".to_string(),
        ..Default::default()
    };
    registry
        .register("human_s003".to_string(), caps_custom_summary)
        .await
        .expect("pre-register human with custom summary");

    // ensure_human_actor should NOT overwrite the custom summary.
    let result = registry
        .ensure_human_actor("s003", "Carol")
        .await
        .expect("ensure_human_actor");
    assert!(!result.created);

    let bot = registry.get("human_s003").await.expect("must exist");
    assert_eq!(
        bot.capabilities.summary.as_deref(),
        Some("I am a DBA expert"),
        "non-empty summary must NOT be overwritten"
    );
}

// ============================================================================
// Section 2: list_legacy_bots_for_owner
// ============================================================================

/// No bots → empty list.
#[tokio::test]
async fn list_legacy_no_bots_returns_empty() {
    let registry = BotCore::new();
    let env = resolve_env();
    let bots = registry
        .list_legacy_bots_for_owner("staff1", &env)
        .await
        .expect("list_legacy_bots_for_owner");
    assert!(bots.is_empty());
}

/// Rule (a): bot with `created_by = staff_no` is matched.
#[tokio::test]
async fn list_legacy_created_by_match() {
    let registry = BotCore::new();
    let env = resolve_env();

    seed_bot(&registry, "bot_aaa", Some("staff1"), &env).await;
    seed_bot(&registry, "bot_bbb", Some("staff2"), &env).await;

    let bots = registry
        .list_legacy_bots_for_owner("staff1", &env)
        .await
        .expect("list_legacy_bots_for_owner");
    assert_eq!(bots.len(), 1);
    assert_eq!(bots[0].bot_uuid, "bot_aaa");
}

/// Rule (b): `default:{staff_no}` with `created_by=None` is matched.
#[tokio::test]
async fn list_legacy_default_namespace_match() {
    let registry = BotCore::new();
    let env = resolve_env();

    seed_bot(&registry, "default:staff1", None, &env).await;

    let bots = registry
        .list_legacy_bots_for_owner("staff1", &env)
        .await
        .expect("list_legacy_bots_for_owner");
    assert_eq!(bots.len(), 1);
    assert_eq!(bots[0].bot_uuid, "default:staff1");
}

/// Rule (b): `{yyyymmdd}_{8chars}:{staff_no}` with `created_by=None` is matched.
#[tokio::test]
async fn list_legacy_dated_namespace_match() {
    let registry = BotCore::new();
    let env = resolve_env();

    seed_bot(&registry, "20260101_abcd1234:staff1", None, &env).await;

    let bots = registry
        .list_legacy_bots_for_owner("staff1", &env)
        .await
        .expect("list_legacy_bots_for_owner");
    assert_eq!(bots.len(), 1);
    assert_eq!(bots[0].bot_uuid, "20260101_abcd1234:staff1");
}

/// Non-whitelisted namespace with `created_by=None` → not matched.
#[tokio::test]
async fn list_legacy_custom_namespace_not_matched() {
    let registry = BotCore::new();
    let env = resolve_env();

    seed_bot(&registry, "custom:staff1", None, &env).await;

    let bots = registry
        .list_legacy_bots_for_owner("staff1", &env)
        .await
        .expect("list_legacy_bots_for_owner");
    assert!(bots.is_empty(), "non-whitelisted namespace must not match");
}

/// Human Actor rows must not be returned by `list_legacy_bots_for_owner`.
#[tokio::test]
async fn list_legacy_excludes_human_actors() {
    let registry = BotCore::new();
    let env = resolve_env();

    // Create a Human actor
    registry
        .ensure_human_actor("staff1", "Alice")
        .await
        .expect("ensure_human_actor");

    // Also seed a real bot
    seed_bot(&registry, "default:staff1", None, &env).await;

    let bots = registry
        .list_legacy_bots_for_owner("staff1", &env)
        .await
        .expect("list_legacy_bots_for_owner");

    // Must only contain the bot, not the human
    assert_eq!(bots.len(), 1);
    assert_eq!(bots[0].bot_uuid, "default:staff1");
    assert_eq!(bots[0].actor_kind, ActorKind::Bot);
}

/// Both rule (a) and rule (b) bots are returned together.
#[tokio::test]
async fn list_legacy_both_rules_combined() {
    let registry = BotCore::new();
    let env = resolve_env();

    seed_bot(&registry, "bot_explicit", Some("staff1"), &env).await;
    seed_bot(&registry, "default:staff1", None, &env).await;
    seed_bot(&registry, "20260507_xyzw9876:staff1", None, &env).await;

    let bots = registry
        .list_legacy_bots_for_owner("staff1", &env)
        .await
        .expect("list_legacy_bots_for_owner");
    assert_eq!(bots.len(), 3);

    let ids: Vec<&str> = bots.iter().map(|b| b.bot_uuid.as_str()).collect();
    assert!(ids.contains(&"bot_explicit"));
    assert!(ids.contains(&"default:staff1"));
    assert!(ids.contains(&"20260507_xyzw9876:staff1"));
}

// ============================================================================
// Section 3: ensure_owner_edges_counted
// ============================================================================

/// New edges → `created=2, upgraded=0` (forward + reverse).
#[tokio::test]
async fn edges_counted_new_pair_created_two() {
    let relation = MemoryRelationStore::new();
    let env = resolve_env();
    let result = relation
        .ensure_owner_edges_counted("human_staff1", "bot_a", &env)
        .await
        .expect("ensure_owner_edges_counted");
    assert_eq!(
        result.created, 2,
        "both forward and reverse edges should be created"
    );
    assert_eq!(result.upgraded, 0);
}

/// Idempotent: second call → `created=0, upgraded=0`.
#[tokio::test]
async fn edges_counted_idempotent_second_call() {
    let relation = MemoryRelationStore::new();
    let env = resolve_env();

    relation
        .ensure_owner_edges_counted("human_staff1", "bot_a", &env)
        .await
        .expect("first call");

    let result = relation
        .ensure_owner_edges_counted("human_staff1", "bot_a", &env)
        .await
        .expect("second call");
    assert_eq!(result.created, 0);
    assert_eq!(result.upgraded, 0);
}

/// Existing friend edge (`is_creator=false`) → upgrade to `is_creator=true`.
#[tokio::test]
async fn edges_counted_upgrade_friend_to_owner() {
    let relation = MemoryRelationStore::new();
    let env = resolve_env();

    // Pre-create a friend edge (is_creator=false) in the forward direction
    relation
        .add_friend_edges("human_staff1", "bot_a", &env)
        .await
        .expect("add_friend_edges");

    // Verify the forward edge exists with is_creator=false
    let edge_before = relation
        .get_edge("human_staff1", "bot_a", &env)
        .await
        .expect("get_edge")
        .expect("edge should exist");
    assert!(
        !edge_before.is_creator,
        "pre-condition: friend edge is_creator=false"
    );

    let result = relation
        .ensure_owner_edges_counted("human_staff1", "bot_a", &env)
        .await
        .expect("ensure_owner_edges_counted");

    // Forward edge: upgraded from false→true; reverse edge: already existed (from add_friend_edges)
    assert_eq!(result.upgraded, 1, "forward edge should be upgraded");
    assert_eq!(result.created, 0, "no new edges should be created");

    // Verify the edge is now is_creator=true
    let edge_after = relation
        .get_edge("human_staff1", "bot_a", &env)
        .await
        .expect("get_edge")
        .expect("edge should exist");
    assert!(
        edge_after.is_creator,
        "forward edge must be upgraded to is_creator=true"
    );
}

/// Verify the edge state after ensure_owner_edges_counted.
#[tokio::test]
async fn edges_counted_produces_correct_edge_state() {
    let relation = MemoryRelationStore::new();
    let env = resolve_env();

    relation
        .ensure_owner_edges_counted("human_staff1", "bot_a", &env)
        .await
        .expect("ensure_owner_edges_counted");

    let forward = relation
        .get_edge("human_staff1", "bot_a", &env)
        .await
        .expect("get forward")
        .expect("forward edge should exist");
    assert!(forward.is_creator, "forward edge must be is_creator=true");

    let reverse = relation
        .get_edge("bot_a", "human_staff1", &env)
        .await
        .expect("get reverse")
        .expect("reverse edge should exist");
    assert!(!reverse.is_creator, "reverse edge must be is_creator=false");
}

// ============================================================================
// Section 4: is_legacy_namespace boundary tests
//
// `is_legacy_namespace` is `pub(crate)` in bcs-bot, so we test it
// indirectly through `list_legacy_bots_for_owner`.
// ============================================================================

/// Exactly "default" → match.
#[tokio::test]
async fn namespace_default_matches() {
    let registry = BotCore::new();
    let env = resolve_env();
    seed_bot(&registry, "default:user1", None, &env).await;
    let bots = registry
        .list_legacy_bots_for_owner("user1", &env)
        .await
        .unwrap();
    assert_eq!(bots.len(), 1);
}

/// "defaults" (extra char) → no match.
#[tokio::test]
async fn namespace_defaults_no_match() {
    let registry = BotCore::new();
    let env = resolve_env();
    seed_bot(&registry, "defaults:user1", None, &env).await;
    let bots = registry
        .list_legacy_bots_for_owner("user1", &env)
        .await
        .unwrap();
    assert!(
        bots.is_empty(),
        "'defaults' is not a valid legacy namespace"
    );
}

/// "defaul" (too short) → no match.
#[tokio::test]
async fn namespace_defaul_no_match() {
    let registry = BotCore::new();
    let env = resolve_env();
    seed_bot(&registry, "defaul:user1", None, &env).await;
    let bots = registry
        .list_legacy_bots_for_owner("user1", &env)
        .await
        .unwrap();
    assert!(bots.is_empty());
}

/// Dated namespace: exactly 17 chars `yyyymmdd_xxxxxxxx` → match.
#[tokio::test]
async fn namespace_dated_17_chars_match() {
    let registry = BotCore::new();
    let env = resolve_env();
    // 8 digits + '_' + 8 lowercase alphanumeric = 17 chars
    seed_bot(&registry, "20260507_abcd0123:user1", None, &env).await;
    let bots = registry
        .list_legacy_bots_for_owner("user1", &env)
        .await
        .unwrap();
    assert_eq!(bots.len(), 1);
}

/// Dated namespace with uppercase letters → no match.
#[tokio::test]
async fn namespace_dated_uppercase_no_match() {
    let registry = BotCore::new();
    let env = resolve_env();
    seed_bot(&registry, "20260507_ABCD0123:user1", None, &env).await;
    let bots = registry
        .list_legacy_bots_for_owner("user1", &env)
        .await
        .unwrap();
    assert!(
        bots.is_empty(),
        "uppercase letters in suffix should not match"
    );
}

/// Dated namespace with wrong separator position → no match (16 chars).
#[tokio::test]
async fn namespace_dated_too_short_no_match() {
    let registry = BotCore::new();
    let env = resolve_env();
    seed_bot(&registry, "2026050_abcd0123:user1", None, &env).await;
    let bots = registry
        .list_legacy_bots_for_owner("user1", &env)
        .await
        .unwrap();
    assert!(bots.is_empty(), "16-char namespace should not match");
}

/// Dated namespace with extra char → no match (18 chars).
#[tokio::test]
async fn namespace_dated_too_long_no_match() {
    let registry = BotCore::new();
    let env = resolve_env();
    seed_bot(&registry, "202605071_abcd0123:user1", None, &env).await;
    let bots = registry
        .list_legacy_bots_for_owner("user1", &env)
        .await
        .unwrap();
    assert!(bots.is_empty(), "18-char namespace should not match");
}

/// Dated namespace with non-digit in date part → no match.
#[tokio::test]
async fn namespace_dated_non_digit_in_date_no_match() {
    let registry = BotCore::new();
    let env = resolve_env();
    seed_bot(&registry, "2026050x_abcd0123:user1", None, &env).await;
    let bots = registry
        .list_legacy_bots_for_owner("user1", &env)
        .await
        .unwrap();
    assert!(bots.is_empty(), "non-digit in date part should not match");
}

/// Dated namespace with special char in suffix → no match.
#[tokio::test]
async fn namespace_dated_special_char_no_match() {
    let registry = BotCore::new();
    let env = resolve_env();
    seed_bot(&registry, "20260507_abcd012!:user1", None, &env).await;
    let bots = registry
        .list_legacy_bots_for_owner("user1", &env)
        .await
        .unwrap();
    assert!(bots.is_empty(), "special char in suffix should not match");
}

/// Empty namespace → no match.
#[tokio::test]
async fn namespace_empty_no_match() {
    let registry = BotCore::new();
    let env = resolve_env();
    seed_bot(&registry, ":user1", None, &env).await;
    let bots = registry
        .list_legacy_bots_for_owner("user1", &env)
        .await
        .unwrap();
    assert!(bots.is_empty(), "empty namespace should not match");
}

// ============================================================================
// Section 5: End-to-end in-memory flow (simulate the handler logic)
// ============================================================================

/// Full flow: new user + 0 legacy bots.
#[tokio::test]
async fn full_flow_new_user_no_bots() {
    let registry = BotCore::new();
    let relation = MemoryRelationStore::new();
    let env = resolve_env();
    let staff_no = "newuser";

    let human_result = registry
        .ensure_human_actor(staff_no, "NewUser")
        .await
        .expect("ensure_human_actor");
    assert!(human_result.created);

    let matched_bots = registry
        .list_legacy_bots_for_owner(staff_no, &env)
        .await
        .expect("list_legacy_bots_for_owner");
    assert!(matched_bots.is_empty());

    // No edges to create — totals should be zero
    let mut total_created: u32 = 0;
    let mut total_upgraded: u32 = 0;
    for bot in &matched_bots {
        let result = relation
            .ensure_owner_edges_counted(&format!("human_{}", staff_no), &bot.bot_uuid, &env)
            .await
            .expect("ensure_owner_edges_counted");
        total_created += result.created;
        total_upgraded += result.upgraded;
    }
    assert_eq!(total_created, 0);
    assert_eq!(total_upgraded, 0);
}

/// Full flow: existing user + legacy bots → edges created.
#[tokio::test]
async fn full_flow_existing_user_with_legacy_bots() {
    let registry = BotCore::new();
    let relation = MemoryRelationStore::new();
    let env = resolve_env();
    let staff_no = "staff42";

    // Pre-create the human
    registry
        .ensure_human_actor(staff_no, "Staff42")
        .await
        .expect("first ensure_human_actor");

    // Seed two legacy bots
    seed_bot(&registry, "default:staff42", None, &env).await;
    seed_bot(&registry, "bot_explicit", Some("staff42"), &env).await;

    // Second call → human_created=false
    let human_result = registry
        .ensure_human_actor(staff_no, "Staff42")
        .await
        .expect("second ensure_human_actor");
    assert!(!human_result.created);

    let matched_bots = registry
        .list_legacy_bots_for_owner(staff_no, &env)
        .await
        .expect("list_legacy_bots_for_owner");
    assert_eq!(matched_bots.len(), 2);

    let human_id = format!("human_{}", staff_no);
    let mut total_created: u32 = 0;
    let mut total_upgraded: u32 = 0;
    for bot in &matched_bots {
        let result = relation
            .ensure_owner_edges_counted(&human_id, &bot.bot_uuid, &env)
            .await
            .expect("ensure_owner_edges_counted");
        total_created += result.created;
        total_upgraded += result.upgraded;
    }

    // 2 bots × 2 edges each = 4 new edges
    assert_eq!(total_created, 4);
    assert_eq!(total_upgraded, 0);
}

/// Idempotency: calling the full flow twice produces no new edges.
#[tokio::test]
async fn full_flow_idempotent() {
    let registry = BotCore::new();
    let relation = MemoryRelationStore::new();
    let env = resolve_env();
    let staff_no = "idem";

    seed_bot(&registry, "default:idem", None, &env).await;

    let human_id = format!("human_{}", staff_no);

    // First pass
    registry.ensure_human_actor(staff_no, "Idem").await.unwrap();
    let bots = registry
        .list_legacy_bots_for_owner(staff_no, &env)
        .await
        .unwrap();
    for bot in &bots {
        relation
            .ensure_owner_edges_counted(&human_id, &bot.bot_uuid, &env)
            .await
            .unwrap();
    }

    // Second pass
    let human_result = registry.ensure_human_actor(staff_no, "Idem").await.unwrap();
    assert!(!human_result.created);

    let bots = registry
        .list_legacy_bots_for_owner(staff_no, &env)
        .await
        .unwrap();
    let mut total_created: u32 = 0;
    let mut total_upgraded: u32 = 0;
    for bot in &bots {
        let result = relation
            .ensure_owner_edges_counted(&human_id, &bot.bot_uuid, &env)
            .await
            .unwrap();
        total_created += result.created;
        total_upgraded += result.upgraded;
    }
    assert_eq!(total_created, 0, "second pass should create no new edges");
    assert_eq!(total_upgraded, 0, "second pass should upgrade no edges");
}
