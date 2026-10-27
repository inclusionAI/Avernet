//! `BotAuthorityRepoPort` conformance harness (bot-authority plan
//! Task 1.2/1.5).
//!
//! The SQL authority Store (`bcs-edge-permission-store` /
//! `DbBotAuthorityStore`) and the memory projection
//! (`bcs-bot-store` / `MemoryBotRepo`) both implement the same
//! strict-read port; this module runs the SAME semantic scenarios
//! against both so results and errors cannot drift.
//!
//! The harness is generic over a [`BotAuthorityContractFixture`]:
//! each store's test driver seeds/corrupts state through its own
//! access channel and supplies the bound-environment repo. Assertion
//! failures panic (harness convention: panic on failure is the
//! contract).
//!
//! Storage-failure behavior (`storage_error_is_not_none`) is
//! inherently store-specific and lives in each store's own test
//! suite, not here.

#![allow(clippy::expect_used, reason = "test harness — panic on failure is the contract")]

use std::sync::Arc;

use async_trait::async_trait;

use bcs_service_api::port::repo::BotAuthorityRepoPort;
use bcs_service_api::types::bot_authority::{
    AuthorityActor, AuthorityMutation, BotAccessRelation, BotRoleQuery,
    ManagerEdit, ManagerEditAction, ManagerListQuery, ManagerSetChange, ManagerSync,
    TransferAction, TransferCommand, TransferDirection, TransferKey,
    TransferListQuery, TransferOutcome, TransferStatus,
    check_page_bounds, human_actor_id,
};

/// Storage access the harness uses to prepare/corrupt state.
///
/// Implementations must keep every prepared edge in the unified
/// management shape (`grant_ref_id = 0`, `rules` null) and the
/// unified `originator_policy = same_as_from`.
#[async_trait]
pub trait BotAuthorityContractFixture: Send + Sync {
    /// The repo under test for the primary (bound) environment.
    fn authority_repo(&self) -> Arc<dyn BotAuthorityRepoPort>;

    /// Create a live Bot row with the given `ownership_version`
    /// (0 = uninitialized; 1 = initialized) in `env`.
    async fn seed_bot(&self, env: &str, bot_id: &str, ownership_version: u64);

    /// Tombstone a Bot (spec §3.2.7: deleted bots authorize nobody).
    async fn delete_bot(&self, env: &str, bot_id: &str);

    /// Insert/restore (idempotent row reuse) an approved
    /// owner/manager edge for `actor_id → bot_id` in `env`.
    async fn grant_edge(&self, env: &str, bot_id: &str, actor_id: &str, kind: BotAccessRelation);

    /// Revoke (not delete) an approved owner/manager edge.
    async fn revoke_edge(&self, env: &str, bot_id: &str, actor_id: &str, kind: BotAccessRelation);

    /// Insert a runtime `permission_profile`-kind edge
    /// (`from → bot`, arbitrary profile ref) so the harness can prove
    /// authority reads never hit runtime edges nor let management
    /// edges mimic them (spec §3.2.5 / plan A16).
    async fn seed_runtime_edge(&self, env: &str, bot_id: &str, actor_id: &str);

    /// Corrupt the owner state of an initialized bot: a second
    /// approved owner edge beyond the store's own uniqueness
    /// constraint. Stores may realize this through a
    /// storage-level backdoor; the point is that the strict reads
    /// must detect it.
    async fn seed_corrupt_duplicate_owner(&self, env: &str, bot_id: &str, actor_id: &str);

    /// Register a verified Human: one live `bcs_bots` actor
    /// row (`actor_kind='human'`, `human_<user_id>`, env-bound,
    /// not tombstoned) — the authority Human registry (spec
    /// §3.1). Idempotent.
    async fn seed_human(&self, env: &str, user_id: &str);

    /// Remove a previously seeded Human (its actor row is
    /// tombstoned — the deleted/departed Human scenario).
    /// Idempotent.
    async fn remove_human(&self, env: &str, user_id: &str);

    /// Reserve an audit sequence so the next manager command
    /// using `mutation_id` collides at `(env, mutation_id,
    /// sequence)` — the deterministic audit-write-failure
    /// injection shared by both stores.
    async fn seed_audit_conflict(&self, env: &str, mutation_id: &str, sequence: u64);

    /// Committed authority audit rows for `env`, ordered by
    /// sequence. The manager-write scenarios use this to
    /// pin "no-op leaves no audit" and rollback semantics.
    async fn audit_rows(&self, env: &str) -> Vec<HarnessAuditRow>;
}

/// One authority audit row as the write scenarios observe it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HarnessAuditRow {
    pub mutation_id: String,
    pub sequence: u64,
    pub action: String,
    pub actor_kind: String,
    pub actor_id: String,
    pub subject_id: String,
    pub before_state: Option<String>,
    pub after_state: Option<String>,
}

/// Full shared semantic suite for one bound environment.
///
/// `env` is the bound env of `fixture.authority_repo()`; `other_env`
/// must be a different env string.
pub async fn run_bot_authority_contract(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
    other_env: &str,
) {
    run_bot_authority_contract_with_scenarios(fixture, env, other_env, |_| {}).await;
}

/// [`run_bot_authority_contract`] plus a per-scenario
/// callback so dialect gates (plan Task 8) can report the
/// exact scenario inventory they ran. The scenario
/// sequence below is the single source of truth.
pub async fn run_bot_authority_contract_with_scenarios(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
    other_env: &str,
    mut report: impl FnMut(&'static str),
) {
    assert_ne!(env, other_env, "harness needs a distinct second env");
    report("owner_manager_union");
    owner_manager_union(fixture, env).await;
    report("case_and_env_are_exact");
    case_and_env_are_exact(fixture, env, other_env).await;
    report("deleted_human_or_bot_is_not_authorized");
    deleted_human_or_bot_is_not_authorized(fixture, env).await;
    report("version_zero_denies");
    version_zero_denies(fixture, env).await;
    report("missing_or_duplicate_owner_is_error");
    missing_or_duplicate_owner_is_error(fixture, env).await;
    report("runtime_edges_never_become_roles");
    runtime_edges_never_become_roles(fixture, env).await;
    report("manager_page_shape_and_bounds");
    manager_page_shape_and_bounds(fixture, env).await;
    report("transfer_reads_are_strict");
    transfer_reads_are_strict(fixture, env).await;
    report("roles_for_batches_exactly");
    roles_for_batches_exactly(fixture, env).await;
    report("deleted_human_loses_all_authority");
    deleted_human_loses_all_authority(fixture, env).await;
    report("deleted_operator_cannot_edit_managers");
    deleted_operator_cannot_edit_managers(fixture, env).await;
    // Manager write paths (Task 2): the same legal results and
    // errors for every store that implements the port.
    report("manager_edit_grant_revoke_and_self_revoke");
    manager_edit_grant_revoke_and_self_revoke(fixture, env).await;
    report("manager_owner_target_is_conflict");
    manager_owner_target_is_conflict(fixture, env).await;
    report("manager_unknown_human_is_rejected");
    manager_unknown_human_is_rejected(fixture, env).await;
    report("manager_repeat_commands_leave_no_audit");
    manager_repeat_commands_leave_no_audit(fixture, env).await;
    report("manager_actor_kind_policy");
    manager_actor_kind_policy(fixture, env).await;
    report("manager_write_state_guards");
    manager_write_state_guards(fixture, env).await;
    report("sync_skips_unknown_and_malformed_members");
    sync_skips_unknown_and_malformed_members(fixture, env).await;
    report("sync_empty_remainder_clears_set");
    sync_empty_remainder_clears_set(fixture, env).await;
    report("sync_cross_env_human_is_unknown");
    sync_cross_env_human_is_unknown(fixture, env, other_env).await;
    report("sync_deleted_human_is_skipped_and_revocable");
    sync_deleted_human_is_skipped_and_revocable(fixture, env).await;
    report("sync_ensure_actor_only_human_is_accepted");
    sync_ensure_actor_only_human_is_accepted(fixture, env).await;
    report("manager_error_codes_are_stable");
    manager_error_codes_are_stable(fixture, env).await;
    report("sync_replaces_full_set_and_normalizes_owner");
    sync_replaces_full_set_and_normalizes_owner(fixture, env).await;
    report("sync_empty_array_clears_all");
    sync_empty_array_clears_all(fixture, env).await;
    report("sync_two_syncs_and_manual_write_serialize");
    sync_two_syncs_and_manual_write_serialize(fixture, env).await;
    report("sync_concurrent_writers_serialize_and_recompute");
    sync_concurrent_writers_serialize_and_recompute(fixture, env).await;
    report("sync_second_chunk_audit_failure_is_atomic");
    sync_second_chunk_audit_failure_is_atomic(fixture, env).await;
    // The pinned sequence runs this scenario twice; keep
    // both passes to preserve the original suite shape.
    report("manager_repeat_commands_leave_no_audit");
    manager_repeat_commands_leave_no_audit(fixture, env).await;
    report("sync_accepts_1000_and_rejects_1001");
    sync_accepts_1000_and_rejects_1001(fixture, env).await;
    report("sync_over_1000_stock_clears_beyond_first_page");
    sync_over_1000_stock_clears_beyond_first_page(fixture, env).await;
}

/// Roles are the union of the owner and manager edges, owner taking
/// precedence, with a complete owner summary for the same bot.
async fn owner_manager_union(fixture: &(impl BotAuthorityContractFixture + ?Sized), env: &str) {
    let repo = fixture.authority_repo();
    let (owner, manager, outsider) = ("union_owner", "union_manager", "union_outside");
    let bot = "bot_union";
    fixture.seed_bot(env, bot, 1).await;
    fixture.grant_edge(env, bot, &human_actor_id(owner), BotAccessRelation::Owner)
        .await;
    fixture.grant_edge(env, bot, &human_actor_id(manager), BotAccessRelation::Manager)
        .await;

    assert_eq!(
        repo.role(&human_actor_id(owner), bot).await.expect("owner role"),
        Some(BotAccessRelation::Owner),
        "owner edge resolves to the owner role"
    );
    assert_eq!(
        repo.role(&human_actor_id(manager), bot).await.expect("manager role"),
        Some(BotAccessRelation::Manager),
        "manager edge resolves to the manager role"
    );
    assert_eq!(
        repo.role(&human_actor_id(outsider), bot).await.expect("no role for outsider"),
        None,
        "stray principals must deny"
    );

    let ownership = repo.ownership(bot).await.expect("ownership view");
    assert_eq!(ownership.bot_id, bot);
    assert_eq!(ownership.owner_user_id, owner);
    assert_eq!(ownership.owner_actor_id, human_actor_id(owner));

    let page = repo
        .managers(ManagerListQuery { bot_id: bot.to_string(), ..Default::default() })
        .await
        .expect("manager page");
    assert_eq!(page.owner.owner_actor_id, human_actor_id(owner));
    // Manager items are User IDs (owner summary carries both forms;
    // spec §5.1 manager API talks in User IDs).
    assert_eq!(page.items, vec![manager], "owner is not a manager item");
    assert_eq!(page.total, 1);
}

/// Identity comparison is byte-exact (case-sensitive) and
/// environment-scoped: same-case bytes only.
async fn case_and_env_are_exact(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
    other_env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_exact";
    fixture.seed_bot(env, bot, 1).await;
    fixture.seed_bot(other_env, bot, 1).await;
    fixture
        .grant_edge(env, bot, &human_actor_id("alice"), BotAccessRelation::Owner)
        .await;
    fixture
        .grant_edge(other_env, bot, &human_actor_id("mallory"), BotAccessRelation::Owner)
        .await;

    assert_eq!(
        repo.role(&human_actor_id("alice"), bot).await.expect("exact role"),
        Some(BotAccessRelation::Owner),
        "exact-case owner resolves"
    );
    assert_eq!(
        repo.role(&human_actor_id("Alice"), bot).await.expect("case variant"),
        None,
        "different case must be a different principal"
    );
    assert_eq!(
        repo.role(&human_actor_id("mallory"), bot).await.expect("cross-env principal"),
        None,
        "roles granted in another env must not leak"
    );
    // Owner identity reads are env-scoped too: the same bot id
    // carries a different owner in the other env, so a wrong
    // lookup would return `mallory` here.
    let ownership = repo.ownership(bot).await.expect("cross-env ownership");
    assert_eq!(
        ownership.owner_actor_id,
        human_actor_id("alice"),
        "ownership() must resolve the bound env's owner only"
    );
    assert_eq!(ownership.owner_user_id, "alice");
    // A manager edge seeded only in the other env must not page
    // in through the bound env's owner summary either.
    fixture
        .grant_edge(other_env, bot, &human_actor_id("mallory"), BotAccessRelation::Manager)
        .await;
    let page = repo
        .managers(ManagerListQuery { bot_id: bot.to_string(), ..Default::default() })
        .await
        .expect("cross-env manager page");
    assert_eq!(
        page.owner.owner_actor_id,
        human_actor_id("alice"),
        "manager page owner summary is env-scoped"
    );
    assert!(
        page.items.is_empty(),
        "manager items from another env must not appear: {:?}",
        page.items
    );
    assert_eq!(page.total, 0);
}

/// Tombstoned bots deny; so does a principal whose
/// authority edge is gone (Human deletion realizes as owner/manager
/// edge revocation at the read layer — spec §3.2.7 — which the
/// write paths in Tasks 2/4 exercise directly).
async fn deleted_human_or_bot_is_not_authorized(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let live = "bot_deleted_live";
    let gone = "bot_deleted";
    fixture.seed_bot(env, live, 1).await;
    fixture.seed_bot(env, gone, 1).await;
    // Complete owner state for the live bot: a role needs it.
    fixture.grant_edge(env, live, &human_actor_id("steady"), BotAccessRelation::Owner).await;
    fixture.grant_edge(env, live, &human_actor_id("keeper"), BotAccessRelation::Manager).await;
    fixture.grant_edge(env, gone, &human_actor_id("keeper"), BotAccessRelation::Manager).await;
    fixture.delete_bot(env, gone).await;

    assert_eq!(
        repo.role(&human_actor_id("keeper"), gone).await.expect("deleted bot role"),
        None,
        "a deleted bot authorizes nobody"
    );
    assert_eq!(
        repo.role(&human_actor_id("keeper"), live).await.expect("live bot role"),
        Some(BotAccessRelation::Manager)
    );

    // A deleted (or otherwise absent) Human target cannot hold a role
    // once its edge is gone: the revoked edge must deny through the
    // same path that Human deletion uses (edges revoked, spec §3.2.7).
    fixture
        .revoke_edge(env, live, &human_actor_id("keeper"), BotAccessRelation::Manager)
        .await;
    let gone_user = "departed";
    fixture.grant_edge(env, live, &human_actor_id(gone_user), BotAccessRelation::Manager).await;
    assert_eq!(
        repo.role(&human_actor_id(gone_user), live).await.expect("pre-revoke role"),
        Some(BotAccessRelation::Manager),
        "sanity: granted before deletion"
    );
    fixture
        .revoke_edge(env, live, &human_actor_id(gone_user), BotAccessRelation::Manager)
        .await;
    assert_eq!(
        repo.role(&human_actor_id(gone_user), live).await.expect("revoked edge role"),
        None,
        "a (deleted) principal without an approved edge must deny"
    );
    // Re-grant restores the same row (revoked-state row reuse).
    fixture.grant_edge(env, live, &human_actor_id("keeper"), BotAccessRelation::Manager).await;
    assert_eq!(
        repo.role(&human_actor_id("keeper"), live).await.expect("restored role"),
        Some(BotAccessRelation::Manager),
        "revoked rows are restored, not duplicated"
    );
}

/// Version 0 means uninitialized: no Human role, no owner view, no
/// manager page.
async fn version_zero_denies(fixture: &(impl BotAuthorityContractFixture + ?Sized), env: &str) {
    let repo = fixture.authority_repo();
    let bot = "bot_uninit";
    fixture.seed_bot(env, bot, 0).await;
    fixture.grant_edge(env, bot, &human_actor_id("preinit"), BotAccessRelation::Owner).await;
    fixture.grant_edge(env, bot, &human_actor_id("stray"), BotAccessRelation::Manager).await;

    assert_eq!(
        repo.role(&human_actor_id("preinit"), bot).await.expect("v0 owner role"),
        None,
        "version 0 denies Human management"
    );
    assert_eq!(
        repo.role(&human_actor_id("stray"), bot).await.expect("v0 manager role"),
        None,
        "version 0 denies manager edges too"
    );
    assert!(
        repo.ownership(bot).await.is_err(),
        "ownership() on an uninitialized bot is an error, not a synthesized view"
    );
    assert!(
        repo.managers(ManagerListQuery { bot_id: bot.to_string(), ..Default::default() })
            .await
            .is_err(),
        "manager list on an uninitialized bot is an error"
    );
    repo.role("human_preinit", "bot_never_existed").await.expect("absent bot role");
}

/// Owner completeness: `ownership_version > 0` with a missing or
/// duplicated approved owner is an integrity error, never a deny, for
/// every read that looks at the bot's roles.
async fn missing_or_duplicate_owner_is_error(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let lost = "bot_lost_owner";
    fixture.seed_bot(env, lost, 1).await;
    fixture.grant_edge(env, lost, &human_actor_id("gone_owner"), BotAccessRelation::Owner).await;
    fixture.grant_edge(env, lost, &human_actor_id("bystander"), BotAccessRelation::Manager).await;
    fixture
        .revoke_edge(env, lost, &human_actor_id("gone_owner"), BotAccessRelation::Owner)
        .await;

    for label in ["former owner", "manager"] {
        let user = if label == "former owner" {
            human_actor_id("gone_owner")
        } else {
            human_actor_id("bystander")
        };
        let err = repo
            .role(&user, lost)
            .await
            .expect_err(&format!("role({label}) must fail on corrupt owner state"));
        let _ = err;
    }
    assert!(
        repo.ownership(lost).await.is_err(),
        "ownership must fail on missing owner"
    );

    let duped = "bot_dup_owner";
    fixture.seed_bot(env, duped, 1).await;
    fixture.grant_edge(env, duped, &human_actor_id("owner_a"), BotAccessRelation::Owner).await;
    fixture.seed_corrupt_duplicate_owner(env, duped, &human_actor_id("owner_b")).await;
    for user in ["owner_a", "owner_b", "owner_c"] {
        let err = repo
            .role(&human_actor_id(user), duped)
            .await
            .expect_err("duplicate owner must be a hard error");
        let _ = err; // strict error path; never a deny
    }
    assert!(repo.ownership(duped).await.is_err(), "ownership must fail on duplicate owner");
}

/// Runtime grant edges (permission_profile/rules) never yield an
/// authority role, and management edges never satisfy runtime reads
/// (A16, both directions).
async fn runtime_edges_never_become_roles(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_runtime_split";
    fixture.seed_bot(env, bot, 1).await;
    fixture.grant_edge(env, bot, &human_actor_id("resident"), BotAccessRelation::Owner).await;
    fixture.grant_edge(env, bot, &human_actor_id("friend"), BotAccessRelation::Manager).await;
    fixture.seed_runtime_edge(env, bot, &human_actor_id("friend")).await;
    fixture.seed_runtime_edge(env, bot, &human_actor_id("runtime_peer")).await;

    assert_eq!(
        repo.role(&human_actor_id("friend"), bot).await.expect("manager stays manager"),
        Some(BotAccessRelation::Manager),
        "management edge grants exactly the manager relation"
    );
    assert_eq!(
        repo.role(&human_actor_id("runtime_peer"), bot).await.expect("runtime peer role"),
        None,
        "a runtime permission edge is not management authority"
    );
}

/// Manager page is owner-first, ordered, envelope-bounded.
async fn manager_page_shape_and_bounds(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_page";
    fixture.seed_bot(env, bot, 1).await;
    fixture.grant_edge(env, bot, &human_actor_id("owner"), BotAccessRelation::Owner).await;
    let managers: Vec<String> = (0..7).map(|i| format!("mgr{i}")).collect();
    for manager in &managers {
        fixture.grant_edge(env, bot, &human_actor_id(manager), BotAccessRelation::Manager).await;
    }
    let full = repo
        .managers(ManagerListQuery { bot_id: bot.to_string(), offset: 0, limit: 5 })
        .await
        .expect("page 1");
    assert_eq!(full.total, 7);
    assert_eq!(full.items.len(), 5);
    // Page items are User IDs; order is byte-exact actor order.
    let mut expected: Vec<String> = managers.clone();
    expected.sort();
    expected.dedup();
    assert_eq!(full.items, expected[..5]);

    let second = repo
        .managers(ManagerListQuery { bot_id: bot.to_string(), offset: 5, limit: 5 })
        .await
        .expect("page 2");
    assert_eq!(second.items, expected[5..], "page 2 keeps order");

    for bad in [(0, 0), (0, 101), (u64::MAX, 5)] {
        assert!(
            repo.managers(ManagerListQuery {
                bot_id: bot.to_string(),
                offset: bad.0,
                limit: bad.1,
            })
            .await
            .is_err(),
            "page bounds {bad:?} must be rejected"
        );
    }
    assert!(check_page_bounds(0, 100).is_ok());
}

/// Transfer reads before any write path exists: missing rows read as
/// `None` / empty pages, list filters share the page bounds, and the
/// read contract normalizes nothing.
async fn transfer_reads_are_strict(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    _env: &str,
) {
    let repo = fixture.authority_repo();
    let key = TransferKey {
        bot_id: "bot_transfer_absent".to_string(),
        target_owner_actor_id: human_actor_id("somebody"),
    };
    assert!(
        repo.transfer(&key).await.expect("absent transfer").is_none(),
        "no transfer row yet → None"
    );

    for bad in [(0, 0), (0, 101)] {
        let page = repo
            .transfers(TransferListQuery {
                user_id: human_actor_id("somebody"),
                direction: TransferDirection::Received,
                statuses: vec![TransferStatus::Pending],
                offset: bad.0,
                limit: bad.1,
            })
            .await;
        assert!(page.is_err(), "transfer page bounds {bad:?} must be rejected");
    }
    let empty = repo
        .transfers(TransferListQuery {
            user_id: human_actor_id("somebody"),
            direction: TransferDirection::Received,
            statuses: vec![],
            offset: 0,
            limit: 50,
        })
        .await
        .expect("empty transfer page");
    assert_eq!(empty.items.len(), 0);
    assert_eq!(empty.total, 0);
}

/// `roles_for` keeps input length and order, supports empty/single/
/// 128/129 batches through bounded chunks, and propagates strict
/// errors instead of aligned `None`s.
async fn roles_for_batches_exactly(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let role_bot = "bot_batch_role";
    fixture.seed_bot(env, role_bot, 1).await;
    fixture.grant_edge(env, role_bot, &human_actor_id("batch_owner"), BotAccessRelation::Owner).await;
    fixture
        .grant_edge(env, role_bot, &human_actor_id("batch_manager"), BotAccessRelation::Manager)
        .await;

    // Empty input → empty aligned result, no error.
    let empty = repo.roles_for(&[]).await.expect("empty batch");
    assert!(empty.is_empty(), "empty input keeps empty output");

    // One input.
    let one = repo
        .roles_for(&[BotRoleQuery {
            user_id: human_actor_id("batch_owner"),
            bot_id: role_bot.to_string(),
        }])
        .await
        .expect("single batch");
    assert_eq!(one, vec![Some(BotAccessRelation::Owner)]);

    // 0/128/129 inputs: length + order preserved across chunking.
    for batch_size in [128usize, 129] {
        let mut queries = Vec::with_capacity(batch_size);
        let mut expected = Vec::with_capacity(batch_size);
        for index in 0..batch_size {
            let (user, expected_role) = if index % 3 == 0 {
                ("batch_owner", Some(BotAccessRelation::Owner))
            } else if index % 3 == 1 {
                ("batch_manager", Some(BotAccessRelation::Manager))
            } else {
                ("batch_outsider", None)
            };
            // Interleave two bots so chunk assembly cannot collide.
            let bot = if index % 2 == 0 { role_bot } else { "bot_batch_shadow" };
            queries.push(BotRoleQuery { user_id: human_actor_id(user), bot_id: bot.to_string() });
            expected.push(if bot == role_bot { expected_role } else { None });
        }
        fixture.seed_bot(env, "bot_batch_shadow", 1).await;
        fixture
            .grant_edge(env, "bot_batch_shadow", &human_actor_id("batch_shadow_owner"), BotAccessRelation::Owner)
            .await;
        let result = repo.roles_for(&queries).await.expect("batch roles");
        assert_eq!(result.len(), queries.len(), "result length matches input");
        for (index, (got, want)) in result.iter().zip(&expected).enumerate() {
            assert_eq!(got, want, "aligned role at {index}");
        }
    }

    // A corrupt member in the batch is a strict error, not a None.
    let corrupt = "bot_batch_corrupt";
    fixture.seed_bot(env, corrupt, 1).await;
    fixture.grant_edge(env, corrupt, &human_actor_id("pending_owner"), BotAccessRelation::Owner).await;
    fixture.revoke_edge(env, corrupt, &human_actor_id("pending_owner"), BotAccessRelation::Owner).await;
    let queries = vec![
        BotRoleQuery { user_id: human_actor_id("batch_owner"), bot_id: role_bot.to_string() },
        BotRoleQuery { user_id: human_actor_id("pending_owner"), bot_id: corrupt.to_string() },
    ];
    assert!(
        repo.roles_for(&queries).await.is_err(),
        "batch must propagate the corrupt-owner error"
    );
}

/// spec §3.1: only a LIVE Human actor authorizes through an
/// edge — tombstoning the manager's actor row turns every
/// strict role read into a deny, single and batch, while a
/// still-alive owner keeps its role (and the corrupt-owner
/// integrity error still outranks the per-user deny).
async fn deleted_human_loses_all_authority(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_gone_human";
    fixture.seed_bot(env, bot, 1).await;
    fixture
        .grant_edge(env, bot, &human_actor_id("gone_owner"), BotAccessRelation::Owner)
        .await;
    fixture
        .grant_edge(env, bot, &human_actor_id("gone_manager"), BotAccessRelation::Manager)
        .await;
    assert_eq!(
        repo.role(&human_actor_id("gone_manager"), bot)
            .await
            .expect("live manager role"),
        Some(BotAccessRelation::Manager),
        "a live Human resolves their edge"
    );

    fixture.remove_human(env, "gone_manager").await;

    assert_eq!(
        repo.role(&human_actor_id("gone_manager"), bot)
            .await
            .expect("role after tombstone"),
        None,
        "a tombstoned Human keeps no authority"
    );
    let roles = repo
        .roles_for(&[
            BotRoleQuery {
                user_id: human_actor_id("gone_manager"),
                bot_id: bot.to_string(),
            },
            BotRoleQuery {
                user_id: human_actor_id("gone_owner"),
                bot_id: bot.to_string(),
            },
        ])
        .await
        .expect("batch roles after tombstone");
    assert_eq!(
        roles,
        vec![None, Some(BotAccessRelation::Owner)],
        "the affected user denies; the live owner keeps resolving"
    );

    // 复活 is a different actor lifecycle: ensuring the Human
    // again restores the edge-based role (ids never silently
    // authorize only BECAUSE an edge row exists).
    fixture.seed_human(env, "gone_manager").await;
    assert_eq!(
        repo.role(&human_actor_id("gone_manager"), bot)
            .await
            .expect("role after re-ensure"),
        Some(BotAccessRelation::Manager),
        "a re-ensured Human resolves their edge again"
    );
}

/// P1 review finding (PR #2625): write-side symmetry of the
/// scenario above. A tombstoned Human whose manager edge
/// remains must also lose the ability to DRIVE manager
/// writes — the write paths re-verify the operator's
/// survival (pre-check + in-transaction/atomic-span guard),
/// never just the presence of the residual edge. After
/// deletion, both PUT/DELETE-style edits are Forbidden, no
/// permission changes and no audit rows are produced, and
/// re-ensuring the actor restores write authority.
async fn deleted_operator_cannot_edit_managers(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_gone_operator";
    fixture.seed_bot(env, bot, 1).await;
    fixture
        .grant_edge(env, bot, &human_actor_id("stay_owner"), BotAccessRelation::Owner)
        .await;
    fixture
        .grant_edge(env, bot, &human_actor_id("gone_operator"), BotAccessRelation::Manager)
        .await;
    fixture
        .grant_edge(env, bot, &human_actor_id("third_manager"), BotAccessRelation::Manager)
        .await;

    fixture.remove_human(env, "gone_operator").await;
    let audit_before = fixture.audit_rows(env).await.len();

    // Grant by the tombstoned operator: Forbidden.
    let err = repo
        .edit_manager(human_edit(
            bot,
            "new_target",
            ManagerEditAction::Grant,
            "gone_operator",
            next_mutation_id(),
        ))
        .await
        .expect_err("grant by a tombstoned operator must be rejected");
    assert!(
        mentions(&err, "bot_authority_operator_forbidden"),
        "operator-survival grant denial: {err}"
    );

    // Revoke by the tombstoned operator: same rejection class;
    // the victim's edge must survive untouched.
    let err = repo
        .edit_manager(human_edit(
            bot,
            "third_manager",
            ManagerEditAction::Revoke,
            "gone_operator",
            next_mutation_id(),
        ))
        .await
        .expect_err("revoke by a tombstoned operator must be rejected");
    assert!(
        mentions(&err, "bot_authority_operator_forbidden"),
        "operator-survival revoke denial: {err}"
    );

    assert_eq!(
        fixture.audit_rows(env).await.len(),
        audit_before,
        "a rejected operator produces no audit rows"
    );
    assert_eq!(
        repo.role(&human_actor_id("third_manager"), bot)
            .await
            .expect("victim role read"),
        Some(BotAccessRelation::Manager),
        "no permission change: the victim keeps the edge"
    );
    assert_eq!(
        repo.role(&human_actor_id("new_target"), bot)
            .await
            .expect("non-grant role read"),
        None,
        "no permission change: nothing was granted"
    );

    // Re-ensure is a fresh actor lifecycle: write authority
    // returns with the live row (the grant target is a live
    // Human for this leg).
    fixture.seed_human(env, "gone_operator").await;
    fixture.seed_human(env, "new_target").await;
    let change = repo
        .edit_manager(human_edit(
            bot,
            "new_target",
            ManagerEditAction::Grant,
            "gone_operator",
            next_mutation_id(),
        ))
        .await
        .expect("re-ensured operator edits again");
    assert_eq!(
        (change.added, change.removed),
        (1, 0),
        "the re-ensured operator's grant applies"
    );
}

// ====================== manager write scenarios ======================
//
// The steps 2.1–2.5 scenarios. All run against every
// `BotAuthorityRepoPort` implementation through the same harness so
// legal results and error semantics cannot drift between the SQL
// authority Store and the memory projection. Repo-port level
// `user_id` fields carry persisted Human actor ids (`human_<id>`,
// port contract), so commands here speak actor ids; the mutation
// actor names the verified operator (Human for edit, trusted
// service for sync, spec §5.1/§5.2).

/// Global unique mutation id per harness command: repeated ids in
/// one env would collide on `(env, mutation_id, sequence)`.
static HARNESS_MUTATION: std::sync::atomic::AtomicU64 =
    std::sync::atomic::AtomicU64::new(1);

fn next_mutation_id() -> String {
    format!(
        "mutation-{}",
        HARNESS_MUTATION.fetch_add(1, std::sync::atomic::Ordering::SeqCst)
    )
}

fn human_edit(
    bot: &str,
    target: &str,
    action: ManagerEditAction,
    operator: &str,
    mutation_id: String,
) -> ManagerEdit {
    ManagerEdit {
        bot_id: bot.to_string(),
        user_id: human_actor_id(target),
        action,
        mutation: AuthorityMutation {
            mutation_id,
            actor: AuthorityActor::Human {
                user_id: operator.to_string(),
            },
        },
    }
}

fn service_sync(bot: &str, user_ids: Vec<&str>, app_id: i64, mutation_id: String) -> ManagerSync {
    ManagerSync {
        bot_id: bot.to_string(),
        user_ids: user_ids.into_iter().map(human_actor_id).collect(),
        mutation: AuthorityMutation {
            mutation_id,
            actor: AuthorityActor::Service { app_id },
        },
    }
}

fn mentions(err: &bcs_service_api::ServiceError, code: &str) -> bool {
    err.to_string().contains(code)
}

fn big_humans(count: usize) -> Vec<String> {
    (0..count).map(|index| format!("mn{index:0>5}")).collect()
}

/// manager 可授权/撤权和自撤权，重复命令无差异审计（spec §5.1）。
async fn manager_edit_grant_revoke_and_self_revoke(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_edit";
    fixture.seed_bot(env, bot, 1).await;
    fixture
        .grant_edge(env, bot, &human_actor_id("own"), BotAccessRelation::Owner)
        .await;
    for user in ["own", "alice", "bob"] {
        fixture.seed_human(env, user).await;
    }

    // Owner grants a manager.
    let granted = repo
        .edit_manager(human_edit(bot, "alice", ManagerEditAction::Grant, "own", next_mutation_id()))
        .await
        .expect("owner grants a manager");
    assert_eq!(granted, ManagerSetChange { added: 1, removed: 0, total: 1 });
    assert_eq!(
        repo.role(&human_actor_id("alice"), bot).await.expect("granted role"),
        Some(BotAccessRelation::Manager)
    );

    // Repeated grant: success without a new manager or audit row.
    let audit_before = fixture.audit_rows(env).await.len();
    let again = repo
        .edit_manager(human_edit(bot, "alice", ManagerEditAction::Grant, "own", next_mutation_id()))
        .await
        .expect("repeat grant");
    assert_eq!(again, ManagerSetChange { added: 0, removed: 0, total: 1 });
    assert_eq!(
        fixture.audit_rows(env).await.len(),
        audit_before,
        "no-op PUT must not write audit rows"
    );

    // A manager can grant another manager.
    let granted_by_manager = repo
        .edit_manager(human_edit(bot, "bob", ManagerEditAction::Grant, "alice", next_mutation_id()))
        .await
        .expect("manager grants another manager");
    assert_eq!(granted_by_manager, ManagerSetChange { added: 1, removed: 0, total: 2 });

    // Self-revoke by a manager (spec §5.1 自撤权).
    let self_revoked = repo
        .edit_manager(human_edit(bot, "bob", ManagerEditAction::Revoke, "bob", next_mutation_id()))
        .await
        .expect("manager self-revokes");
    assert_eq!(self_revoked, ManagerSetChange { added: 0, removed: 1, total: 1 });
    assert_eq!(
        repo.role(&human_actor_id("bob"), bot).await.expect("revoked role"),
        None,
        "self-revoked manager loses the role"
    );

    // Owner revokes the remaining manager; repeated DELETE is a
    // no-op without audit.
    let revoked = repo
        .edit_manager(human_edit(bot, "alice", ManagerEditAction::Revoke, "own", next_mutation_id()))
        .await
        .expect("owner revokes manager");
    assert_eq!(revoked, ManagerSetChange { added: 0, removed: 1, total: 0 });

    // Re-grant walks the revoked-row restore path (no new
    // edge row).
    let restored = repo
        .edit_manager(human_edit(bot, "alice", ManagerEditAction::Grant, "own", next_mutation_id()))
        .await
        .expect("restore from a revoked row");
    assert_eq!(restored, ManagerSetChange { added: 1, removed: 0, total: 1 });
    let audit_snapshot = fixture.audit_rows(env).await;
    let restore_rows: Vec<&HarnessAuditRow> = audit_snapshot
        .iter()
        .filter(|row| row.action == "manager_restore")
        .collect();
    assert_eq!(restore_rows.len(), 1, "the restore writes one audit row");
    assert_eq!(
        restore_rows[0].before_state.as_deref(),
        Some("revoked")
    );
    assert_eq!(restore_rows[0].after_state.as_deref(), Some("approved"));
    // Owner revokes alice again for the following scenarios.
    let revoked = repo
        .edit_manager(human_edit(bot, "alice", ManagerEditAction::Revoke, "own", next_mutation_id()))
        .await
        .expect("owner revokes manager again");
    assert_eq!(revoked, ManagerSetChange { added: 0, removed: 1, total: 0 });
    let audit_mid = fixture.audit_rows(env).await.len();
    let revoked_again = repo
        .edit_manager(human_edit(bot, "alice", ManagerEditAction::Revoke, "own", next_mutation_id()))
        .await
        .expect("repeat revoke");
    assert_eq!(revoked_again, ManagerSetChange { added: 0, removed: 0, total: 0 });
    assert_eq!(
        fixture.audit_rows(env).await.len(),
        audit_mid,
        "repeat DELETE must not write audit rows"
    );
    let page = repo
        .managers(ManagerListQuery { bot_id: bot.to_string(), ..Default::default() })
        .await
        .expect("empty page");
    assert_eq!(page.total, 0);
    assert!(page.items.is_empty());
    assert_eq!(page.owner.owner_actor_id, human_actor_id("own"));

    // Non-privileged operator is rejected and writes nothing.
    fixture.seed_human(env, "outsider").await;
    let audit_before = fixture.audit_rows(env).await.len();
    let denied = repo
        .edit_manager(human_edit(bot, "bob", ManagerEditAction::Grant, "outsider", next_mutation_id()))
        .await;
    assert!(denied.is_err(), "operator without an edge must be rejected");
    assert!(mentions(denied.as_ref().unwrap_err(), "bot_authority_operator_forbidden"));
    assert_eq!(fixture.audit_rows(env).await.len(), audit_before);
    assert_eq!(
        repo.role(&human_actor_id("bob"), bot).await.expect("role"),
        None,
        "denied edit must not grant"
    );
}

/// PUT/DELETE targeting the owner is 409 `owner_not_manager`
/// (spec §5.1) — for both actions, and it writes nothing.
async fn manager_owner_target_is_conflict(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_owner_target";
    fixture.seed_bot(env, bot, 1).await;
    fixture
        .grant_edge(env, bot, &human_actor_id("own"), BotAccessRelation::Owner)
        .await;
    for user in ["own", "alice"] {
        fixture.seed_human(env, user).await;
    }
    fixture
        .grant_edge(env, bot, &human_actor_id("alice"), BotAccessRelation::Manager)
        .await;

    for action in [ManagerEditAction::Grant, ManagerEditAction::Revoke] {
        let audit_before = fixture.audit_rows(env).await.len();
        let err = repo
            .edit_manager(human_edit(bot, "own", action, "alice", next_mutation_id()))
            .await
            .expect_err("owner target must conflict");
        // spec §5.1 literal code (adjudicated spelling).
        assert!(mentions(&err, "owner_not_manager"), "got {err}");
        assert_eq!(fixture.audit_rows(env).await.len(), audit_before);
    }
    assert_eq!(
        repo.role(&human_actor_id("own"), bot).await.expect("owner role"),
        Some(BotAccessRelation::Owner),
        "owner role survives both attempts"
    );
}

/// Unknown or removed Humans are rejected for the STRICT
/// single-item adds; the lenient sync skips unknown members
/// and applies the valid remainder (spec §5.2). Revokes
/// need no Human existence.
async fn manager_unknown_human_is_rejected(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_ghost";
    fixture.seed_bot(env, bot, 1).await;
    fixture
        .grant_edge(env, bot, &human_actor_id("own"), BotAccessRelation::Owner)
        .await;
    fixture.seed_human(env, "own").await;
    fixture.seed_human(env, "gone").await;

    // Unknown target add (edit stays strict).
    let audit_before = fixture.audit_rows(env).await.len();
    let err = repo
        .edit_manager(human_edit(bot, "ghost", ManagerEditAction::Grant, "own", next_mutation_id()))
        .await
        .expect_err("unknown target must be rejected");
    assert!(mentions(&err, "bot_authority_human_unknown"), "got {err}");
    assert_eq!(fixture.audit_rows(env).await.len(), audit_before);

    // Lenient sync (spec §5.2): the unknown member is
    // skipped, the valid remainder is applied — the
    // resident edge survives, the skipped member never
    // lands, and only the granted member audits.
    fixture.seed_human(env, "resident").await;
    fixture.seed_human(env, "alice").await;
    fixture
        .grant_edge(env, bot, &human_actor_id("resident"), BotAccessRelation::Manager)
        .await;
    let audit_before = fixture.audit_rows(env).await.len();
    let change = repo
        .sync_managers(service_sync(
            bot,
            vec!["resident", "stranger", "alice"],
            9,
            next_mutation_id(),
        ))
        .await
        .expect("lenient sync skips unknown members");
    assert_eq!(
        change,
        ManagerSetChange { added: 1, removed: 0, total: 2 },
        "stranger is skipped; the valid remainder is applied"
    );
    assert_eq!(
        repo.role(&human_actor_id("resident"), bot).await.expect("role"),
        Some(BotAccessRelation::Manager),
        "a lenient sync must not revoke the kept member"
    );
    assert_eq!(
        repo.role(&human_actor_id("stranger"), bot).await.expect("role"),
        None,
        "the skipped member must not be added"
    );
    assert_eq!(
        repo.role(&human_actor_id("alice"), bot).await.expect("role"),
        Some(BotAccessRelation::Manager),
        "the valid new member is added"
    );
    assert_eq!(
        fixture.audit_rows(env).await.len() - audit_before,
        1,
        "only the granted member writes an audit row"
    );

    // A Human that existed and was removed: re-add rejected.
    let granted = repo
        .edit_manager(human_edit(bot, "gone", ManagerEditAction::Grant, "own", next_mutation_id()))
        .await
        .expect("seed a manager while alive");
    assert_eq!(granted.added, 1);
    fixture.remove_human(env, "gone").await;
    let err = repo
        .edit_manager(human_edit(bot, "newone", ManagerEditAction::Grant, "own", next_mutation_id()))
        .await
        .expect_err("still unknown human");
    assert!(mentions(&err, "bot_authority_human_unknown"), "got {err}");
    // Removing is still legitimate for a departed Human.
    let revoked = repo
        .edit_manager(human_edit(bot, "gone", ManagerEditAction::Revoke, "own", next_mutation_id()))
        .await
        .expect("revoke needs no Human existence");
    assert_eq!(
        revoked,
        ManagerSetChange { added: 0, removed: 1, total: 2 },
        "resident and alice remain after the revoked departed member"
    );
}

/// PUT/DELETE on an uninitialized or missing bot fails as state
/// errors, not as writes.
async fn manager_write_state_guards(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    fixture.seed_bot(env, "bot_v0", 0).await;
    fixture.seed_human(env, "own").await;
    let audit_before = fixture.audit_rows(env).await.len();

    let err = repo
        .edit_manager(human_edit(
            "bot_v0",
            "own",
            ManagerEditAction::Grant,
            "own",
            next_mutation_id(),
        ))
        .await
        .expect_err("version 0 bot cannot be edited");
    assert!(mentions(&err, "bot_ownership_not_initialized"), "got {err}");
    let err = repo
        .sync_managers(service_sync("bot_v0", vec!["own"], 9, next_mutation_id()))
        .await
        .expect_err("uninitialized bot cannot be synced");
    assert!(mentions(&err, "bot_ownership_not_initialized"), "got {err}");

    let err = repo
        .edit_manager(human_edit(
            "bot_missing",
            "own",
            ManagerEditAction::Grant,
            "own",
            next_mutation_id(),
        ))
        .await
        .expect_err("missing bot");
    assert!(matches!(err, bcs_service_api::ServiceError::BotNotFound(_)));
    // Lenient sync (spec §5.2): a missing target bot
    // returns success zeros with a warn log — no audit row,
    // no state change.
    let change = repo
        .sync_managers(service_sync("bot_missing", vec!["own"], 9, next_mutation_id()))
        .await
        .expect("lenient sync on a missing bot returns zeros");
    assert_eq!(change, ManagerSetChange { added: 0, removed: 0, total: 0 });
    assert_eq!(fixture.audit_rows(env).await.len(), audit_before);
}

/// Lenient sync (spec §5.2): members that are NOT
/// `human_*` actor shapes (e.g. a Bot id like `bot_x`) are
/// treated exactly like unknown Humans — skipped with a
/// warn log, never a whole-request failure; the valid
/// remainder is the final set.
async fn sync_skips_unknown_and_malformed_members(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_lenient";
    fixture.seed_bot(env, bot, 1).await;
    fixture
        .grant_edge(env, bot, &human_actor_id("own"), BotAccessRelation::Owner)
        .await;
    fixture.seed_human(env, "own").await;
    fixture.seed_human(env, "first").await;
    fixture.seed_human(env, "second").await;

    let change = repo
        .sync_managers(ManagerSync {
            bot_id: bot.to_string(),
            user_ids: vec![
                human_actor_id("first"),
                // Well-shaped but no live actor row.
                human_actor_id("never-exists"),
                // NOT a Human actor shape — skipped alike.
                "bot_mallory".to_string(),
                human_actor_id("second"),
            ],
            mutation: AuthorityMutation {
                mutation_id: next_mutation_id(),
                actor: AuthorityActor::Service { app_id: 9 },
            },
        })
        .await
        .expect("lenient sync skips unknown and malformed members");
    assert_eq!(
        change,
        ManagerSetChange { added: 2, removed: 0, total: 2 },
        "only the two valid members become the final set"
    );
    for user in ["first", "second"] {
        assert_eq!(
            repo.role(&human_actor_id(user), bot).await.expect("role"),
            Some(BotAccessRelation::Manager),
            "{user} must be in the synced set"
        );
    }
    for actor in ["human_never-exists", "bot_mallory"] {
        assert_eq!(
            repo.role(actor, bot).await.expect("role"),
            None,
            "'{actor}' must have been skipped"
        );
    }
}

/// Corner case A (spec §5.2 ruling): a non-empty input whose
/// valid remainder is EMPTY still clears the manager set —
/// the final set always equals the valid remainder.
async fn sync_empty_remainder_clears_set(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_clear";
    fixture.seed_bot(env, bot, 1).await;
    fixture
        .grant_edge(env, bot, &human_actor_id("own"), BotAccessRelation::Owner)
        .await;
    fixture.seed_human(env, "own").await;
    fixture.seed_human(env, "alice").await;
    fixture
        .grant_edge(env, bot, &human_actor_id("alice"), BotAccessRelation::Manager)
        .await;

    let change = repo
        .sync_managers(service_sync(bot, vec!["unknown"], 9, next_mutation_id()))
        .await
        .expect("the unknown-only remainder clears the set");
    assert_eq!(
        change,
        ManagerSetChange { added: 0, removed: 1, total: 0 },
        "the sole current member is revoked, nothing is added"
    );
    let page = repo
        .managers(ManagerListQuery { bot_id: bot.to_string(), ..Default::default() })
        .await
        .expect("cleared page");
    assert_eq!(page.total, 0);
    assert!(page.items.is_empty());
}

/// Env binding (spec §3.1): a Human actor row in ANOTHER env
/// is unknown to the bound env — lenient sync skips it, the
/// strict edit still rejects it. (The user id is unique to
/// this scenario so earlier in-run seeds cannot satisfy the
/// bound-env registry on the same fixture.)
async fn sync_cross_env_human_is_unknown(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
    other_env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_xenv";
    let user = "xenv-only";
    fixture.seed_bot(env, bot, 1).await;
    fixture
        .grant_edge(env, bot, &human_actor_id("own"), BotAccessRelation::Owner)
        .await;
    fixture.seed_human(env, "own").await;
    // The Human exists ONLY in the other env.
    fixture.seed_human(other_env, user).await;

    let change = repo
        .sync_managers(service_sync(bot, vec![user], 9, next_mutation_id()))
        .await
        .expect("lenient sync skips the cross-env Human");
    assert_eq!(
        change,
        ManagerSetChange { added: 0, removed: 0, total: 0 },
        "the cross-env Human is skipped, the remainder is empty"
    );
    assert_eq!(
        repo.role(&human_actor_id(user), bot).await.expect("role"),
        None,
        "another env's Human actor row must not authorize here"
    );
    let err = repo
        .edit_manager(human_edit(bot, user, ManagerEditAction::Grant, "own", next_mutation_id()))
        .await
        .expect_err("cross-env Human is a strict-edit unknown");
    assert!(mentions(&err, "bot_authority_human_unknown"), "got {err}");
}

/// A departed Human (tombstoned actor row) is skipped by
/// sync — its still-current edge reads as `current \ input`
/// and is revoked normally; the strict edit rejects re-add.
async fn sync_deleted_human_is_skipped_and_revocable(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_departed";
    fixture.seed_bot(env, bot, 1).await;
    fixture
        .grant_edge(env, bot, &human_actor_id("own"), BotAccessRelation::Owner)
        .await;
    fixture.seed_human(env, "own").await;
    fixture.seed_human(env, "departed").await;
    fixture
        .grant_edge(env, bot, &human_actor_id("departed"), BotAccessRelation::Manager)
        .await;
    fixture.remove_human(env, "departed").await;

    let change = repo
        .sync_managers(service_sync(bot, vec!["departed"], 9, next_mutation_id()))
        .await
        .expect("lenient sync skips the departed Human");
    assert_eq!(
        change,
        ManagerSetChange { added: 0, removed: 1, total: 0 },
        "the departed member is revoked as part of the (empty) remainder"
    );
    assert_eq!(
        repo.role(&human_actor_id("departed"), bot).await.expect("role"),
        None,
        "the departed member's edge must be gone"
    );
    let err = repo
        .edit_manager(human_edit(bot, "departed", ManagerEditAction::Grant, "own", next_mutation_id()))
        .await
        .expect_err("a departed Human is a strict-edit unknown");
    assert!(mentions(&err, "bot_authority_human_unknown"), "got {err}");
}

/// A Human with ONLY an actor row (no OAuth identity row
/// exists anywhere) is a first-class authority Human: sync,
/// the strict edit and the transfer chain all accept it.
/// This pins the registry fix — nothing may consult the
/// OAuth identity table any more.
async fn sync_ensure_actor_only_human_is_accepted(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_actor_only";
    let (owner, target) = ("aown", "atarget");
    fixture.seed_bot(env, bot, 1).await;
    fixture
        .grant_edge(env, bot, &human_actor_id(owner), BotAccessRelation::Owner)
        .await;
    // Both Humans exist ONLY as actor rows (spec §3.1: the
    // registry is `bcs_bots`; `seed_human` creates no
    // identity row).
    fixture.seed_human(env, owner).await;
    fixture.seed_human(env, target).await;

    let change = repo
        .sync_managers(service_sync(bot, vec![target], 9, next_mutation_id()))
        .await
        .expect("sync accepts the actor-only Human");
    assert_eq!(change, ManagerSetChange { added: 1, removed: 0, total: 1 });
    assert_eq!(
        repo.role(&human_actor_id(target), bot).await.expect("role"),
        Some(BotAccessRelation::Manager)
    );

    // The strict edit on a second actor-only Human.
    fixture.seed_human(env, "second").await;
    let granted = repo
        .edit_manager(human_edit(bot, "second", ManagerEditAction::Grant, owner, next_mutation_id()))
        .await
        .expect("edit accepts the actor-only Human");
    assert_eq!(granted.added, 1);

    // The transfer chain: initiate → accept, both sides are
    // actor-only Humans.
    let transfer_key = TransferKey {
        bot_id: bot.to_string(),
        target_owner_actor_id: human_actor_id(target),
    };
    let requested = repo
        .change_transfer(TransferCommand {
            key: transfer_key.clone(),
            action: TransferAction::RequestOrRefresh,
            mutation: AuthorityMutation {
                mutation_id: next_mutation_id(),
                actor: AuthorityActor::Human { user_id: owner.to_string() },
            },
        })
        .await
        .expect("transfer initiation accepts the actor-only target");
    assert!(matches!(requested, TransferOutcome::Applied { .. }));
    let accepted = repo
        .change_transfer(TransferCommand {
            key: transfer_key,
            action: TransferAction::Accept,
            mutation: AuthorityMutation {
                mutation_id: next_mutation_id(),
                actor: AuthorityActor::Human { user_id: target.to_string() },
            },
        })
        .await
        .expect("accept accepts the actor-only owner");
    assert!(matches!(accepted, TransferOutcome::Applied { .. }));
    assert_eq!(
        repo.ownership(bot).await.expect("ownership").owner_user_id,
        target,
        "the actor-only Human is the new owner"
    );
}

/// edit is Human-only; sync is trusted-App only (spec §5.1/§5.2
/// split; Provider/System actors never reach these commands).
async fn manager_actor_kind_policy(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_actor_kind";
    fixture.seed_bot(env, bot, 1).await;
    fixture
        .grant_edge(env, bot, &human_actor_id("own"), BotAccessRelation::Owner)
        .await;
    fixture.seed_human(env, "own").await;
    fixture.seed_human(env, "alice").await;
    let audit_before = fixture.audit_rows(env).await.len();

    let err = repo
        .edit_manager(ManagerEdit {
            bot_id: bot.to_string(),
            user_id: human_actor_id("alice"),
            action: ManagerEditAction::Grant,
            mutation: AuthorityMutation {
                mutation_id: next_mutation_id(),
                actor: AuthorityActor::Service { app_id: 3 },
            },
        })
        .await
        .expect_err("service must not use single-item edit");
    assert!(mentions(&err, "bot_authority_operator_forbidden"), "got {err}");

    let err = repo
        .sync_managers(ManagerSync {
            bot_id: bot.to_string(),
            user_ids: vec![human_actor_id("alice")],
            mutation: AuthorityMutation {
                mutation_id: next_mutation_id(),
                actor: AuthorityActor::Human {
                    user_id: "own".to_string(),
                },
            },
        })
        .await
        .expect_err("sync is trusted-App only");
    assert!(mentions(&err, "bot_authority_operator_forbidden"), "got {err}");

    assert_eq!(fixture.audit_rows(env).await.len(), audit_before);
    assert_eq!(
        repo.role(&human_actor_id("alice"), bot).await.expect("role"),
        None,
        "denied actor kinds must not write"
    );
}

/// Stable error-code spellings the Task-5 adapter maps to HTTP.
async fn manager_error_codes_are_stable(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_codes";
    fixture.seed_bot(env, bot, 1).await;
    fixture.seed_bot(env, "bot_v0_codes", 0).await;
    fixture
        .grant_edge(env, bot, &human_actor_id("own"), BotAccessRelation::Owner)
        .await;
    fixture.seed_human(env, "own").await;
    fixture.seed_human(env, "stranger").await;
    let not_manager_target = repo
        .edit_manager(human_edit(bot, "own", ManagerEditAction::Grant, "own", next_mutation_id()))
        .await
        .expect_err("owner target")
        .to_string();
    assert!(not_manager_target.contains("owner_not_manager"));
    let uninitialized = repo
        .edit_manager(human_edit("bot_v0_codes", "own", ManagerEditAction::Grant, "own", next_mutation_id()))
        .await
        .expect_err("uninitialized")
        .to_string();
    assert!(uninitialized.contains("bot_ownership_not_initialized"));
    let unknown_human = repo
        .edit_manager(human_edit(bot, "nobody", ManagerEditAction::Grant, "own", next_mutation_id()))
        .await
        .expect_err("unknown target human")
        .to_string();
    assert!(unknown_human.contains("bot_authority_human_unknown"));
}

/// sync `{a,b} → {b,c}` yields added=1/removed=1 via a single
/// mutation, covers manually created manager rows, keeps the
/// owner, and normalizes the owner out of the input; a repeated
/// identical sync is a zero-diff no-op without audit.
async fn sync_replaces_full_set_and_normalizes_owner(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_sync";
    fixture.seed_bot(env, bot, 1).await;
    fixture
        .grant_edge(env, bot, &human_actor_id("boss"), BotAccessRelation::Owner)
        .await;
    for user in ["boss", "a", "b", "c"] {
        fixture.seed_human(env, user).await;
    }
    // Manual/transfer-created manager rows are equivalent targets.
    fixture
        .grant_edge(env, bot, &human_actor_id("a"), BotAccessRelation::Manager)
        .await;
    fixture
        .grant_edge(env, bot, &human_actor_id("b"), BotAccessRelation::Manager)
        .await;

    let mutation_id = next_mutation_id();
    let change = repo
        .sync_managers(service_sync(bot, vec!["b", "c", "boss", "boss"], 9, mutation_id.clone()))
        .await
        .expect("first sync");
    // {a,b} → {b,c}: one revoke, one add, and the final
    // net set has 2 members (owner excluded).
    assert_eq!(change, ManagerSetChange { added: 1, removed: 1, total: 2 });

    // Owner preserved; owner normalized out of the manager set;
    // duplicates collapse to one contribution.
    assert_eq!(
        repo.ownership(bot).await.expect("ownership").owner_actor_id,
        human_actor_id("boss")
    );
    let page = repo
        .managers(ManagerListQuery { bot_id: bot.to_string(), ..Default::default() })
        .await
        .expect("page after sync");
    assert_eq!(page.items, vec!["b".to_string(), "c".to_string()]);
    assert_eq!(
        repo.role(&human_actor_id("boss"), bot).await.expect("owner role"),
        Some(BotAccessRelation::Owner)
    );
    assert_eq!(
        repo.role(&human_actor_id("a"), bot).await.expect("removed role"),
        None
    );

    // Sorted diff with stable sequences under one mutation id.
    let rows = fixture.audit_rows(env).await;
    let mutation_rows: Vec<&HarnessAuditRow> =
        rows.iter().filter(|row| row.mutation_id == mutation_id).collect();
    assert_eq!(mutation_rows.len(), 2, "one row per diff item: {mutation_rows:?}");
    assert_eq!(mutation_rows[0].sequence, 0);
    assert_eq!(mutation_rows[0].action, "manager_revoke");
    assert_eq!(mutation_rows[0].subject_id, human_actor_id("a"));
    assert_eq!(mutation_rows[0].before_state.as_deref(), Some("approved"));
    assert_eq!(mutation_rows[0].after_state.as_deref(), Some("revoked"));
    assert_eq!(mutation_rows[0].actor_kind, "service");
    assert_eq!(mutation_rows[0].actor_id, "app_9");
    assert_eq!(mutation_rows[1].sequence, 1);
    assert_eq!(mutation_rows[1].action, "manager_grant");
    assert_eq!(mutation_rows[1].subject_id, human_actor_id("c"));
    assert_eq!(mutation_rows[1].before_state, None);
    assert_eq!(mutation_rows[1].after_state.as_deref(), Some("approved"));

    // Identical sync again: zero diff, zero audit.
    let audit_before = rows.len();
    let change = repo
        .sync_managers(service_sync(bot, vec!["b", "c"], 9, next_mutation_id()))
        .await
        .expect("repeat sync");
    assert_eq!(change, ManagerSetChange { added: 0, removed: 0, total: 2 });
    assert_eq!(fixture.audit_rows(env).await.len(), audit_before);
}

/// Empty array is an explicit clear of all managers; the owner is
/// untouched.
async fn sync_empty_array_clears_all(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_sync_empty";
    fixture.seed_bot(env, bot, 1).await;
    fixture
        .grant_edge(env, bot, &human_actor_id("boss"), BotAccessRelation::Owner)
        .await;
    for user in ["boss", "a", "b"] {
        fixture.seed_human(env, user).await;
        fixture
            .grant_edge(env, bot, &human_actor_id(user), BotAccessRelation::Manager)
            .await;
    }
    fixture
        .revoke_edge(env, bot, &human_actor_id("boss"), BotAccessRelation::Manager)
        .await;

    let change = repo
        .sync_managers(service_sync(bot, vec![], 9, next_mutation_id()))
        .await
        .expect("empty sync");
    assert_eq!(change, ManagerSetChange { added: 0, removed: 2, total: 0 });
    assert_eq!(
        repo.role(&human_actor_id("boss"), bot).await.expect("owner role"),
        Some(BotAccessRelation::Owner),
        "empty sync preserves the owner"
    );
    for user in ["a", "b"] {
        assert_eq!(
            repo.role(&human_actor_id(user), bot).await.expect("role"),
            None,
            "sync must clear {user}"
        );
    }
}

/// Two syncs and manual writes serialize by commit order; every
/// sync overwrites manual rows of that moment.
async fn sync_two_syncs_and_manual_write_serialize(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_sync_manual";
    fixture.seed_bot(env, bot, 1).await;
    fixture
        .grant_edge(env, bot, &human_actor_id("boss"), BotAccessRelation::Owner)
        .await;
    for user in ["boss", "w", "x", "y", "z"] {
        fixture.seed_human(env, user).await;
    }
    fixture
        .grant_edge(env, bot, &human_actor_id("w"), BotAccessRelation::Manager)
        .await;

    let first = repo
        .sync_managers(service_sync(bot, vec!["x"], 9, next_mutation_id()))
        .await
        .expect("sync 1");
    assert_eq!(first, ManagerSetChange { added: 1, removed: 1, total: 1 });

    let manual = repo
        .edit_manager(human_edit(bot, "y", ManagerEditAction::Grant, "x", next_mutation_id()))
        .await
        .expect("manual grant between syncs");
    assert_eq!(manual, ManagerSetChange { added: 1, removed: 0, total: 2 });

    let second = repo
        .sync_managers(service_sync(bot, vec!["z"], 9, next_mutation_id()))
        .await
        .expect("sync 2");
    assert_eq!(second, ManagerSetChange { added: 1, removed: 2, total: 1 });
    let page = repo
        .managers(ManagerListQuery { bot_id: bot.to_string(), ..Default::default() })
        .await
        .expect("page");
    assert_eq!(page.items.len(), 1);
    assert_eq!(page.items[0], "z");

    // A restored member: x's revoked row gets restored by
    // the next sync covering it (restore classified, one
    // audit row).
    let restore_sync = repo
        .sync_managers(service_sync(bot, vec!["x", "z"], 9, next_mutation_id()))
        .await
        .expect("sync restores the revoked x");
    assert_eq!(restore_sync.added, 1);
    let audit_snapshot = fixture.audit_rows(env).await;
    assert!(
        audit_snapshot
            .iter()
            .any(|row| row.action == "manager_restore"
                && row.subject_id == human_actor_id("x")),
        "the sync restore must be audited, got {audit_snapshot:?}"
    );
}

/// Two concurrent syncs of same-size disjoint sets: exactly one
/// commit order wins; guards on the stale writer roll the whole
/// transaction back and the writer recomputes from a fresh
/// pre-read; both recorded diffs stay consistent, nothing mixes.
async fn sync_concurrent_writers_serialize_and_recompute(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_sync_race";
    fixture.seed_bot(env, bot, 1).await;
    fixture
        .grant_edge(env, bot, &human_actor_id("boss"), BotAccessRelation::Owner)
        .await;
    let mut users = vec!["boss".to_string()];
    users.extend((0..4).map(|i| format!("s{i}")));
    users.extend((0..4).map(|i| format!("p{i}")));
    users.extend((0..4).map(|i| format!("q{i}")));
    for user in &users {
        fixture.seed_human(env, user).await;
    }
    for index in 0..4 {
        fixture
            .grant_edge(env, bot, &human_actor_id(format!("s{index}").as_str()), BotAccessRelation::Manager)
            .await;
    }

    let repo_a = repo.clone();
    let repo_b = repo.clone();
    // Pre-register both writers' mutation ids so the
    // post-commit assertions below cannot pass vacuously:
    // a lost update would silently drop one writer's
    // committed audit rows.
    let mutation_a = next_mutation_id();
    let mutation_b = next_mutation_id();
    let (result_a, result_b) = tokio::join!(
        repo_a.sync_managers(service_sync(
            bot,
            vec!["p0", "p1", "p2", "p3"],
            9,
            mutation_a.clone(),
        )),
        repo_b.sync_managers(service_sync(
            bot,
            vec!["q0", "q1", "q2", "q3"],
            9,
            mutation_b.clone(),
        )),
    );
    let change_a = result_a.expect("sync A must succeed after recompute");
    let change_b = result_b.expect("sync B must succeed after recompute");
    for change in [&change_a, &change_b] {
        assert_eq!(
            (change.added, change.removed),
            (4, 4),
            "each committed sync recorded its own diff: {change:?}"
        );
    }

    let page = repo
        .managers(ManagerListQuery { bot_id: bot.to_string(), ..Default::default() })
        .await
        .expect("post-race page");
    assert_eq!(page.total, 4);
    // Page items are User IDs (reads project
    // actor ids back), so compare raw ids.
    let finals: Vec<Vec<String>> = [
        vec!["p0", "p1", "p2", "p3"],
        vec!["q0", "q1", "q2", "q3"],
    ]
    .iter()
    .map(|set| set.iter().map(|u| u.to_string()).collect())
    .collect();
    assert!(
        finals.contains(&page.items),
        "the committed sync's set must win exactly, got {:?}",
        page.items
    );

    // Serialized commit order: BOTH writers' audit diffs
    // are committed (a lost update would drop the earlier
    // writer's rows), each recording its full 8-row diff.
    let rows = fixture.audit_rows(env).await;
    let grant_subjects = |mutation: &str| -> Vec<String> {
        rows.iter()
            .filter(|row| {
                &row.mutation_id == mutation
                    && row.action == "manager_grant"
            })
            .map(|row| row.subject_id.clone())
            .collect()
    };
    let revoke_count = |mutation: &str| -> usize {
        rows.iter()
            .filter(|row| &row.mutation_id == mutation
                && row.action == "manager_revoke")
            .count()
    };
    for (mutation, input) in [
        (mutation_a.as_str(), &finals[0]),
        (mutation_b.as_str(), &finals[1]),
    ] {
        assert_eq!(revoke_count(mutation), 4, "{mutation} committed its revokes");
        let mut grants = grant_subjects(mutation);
        grants.sort();
        let mut wanted = input.clone();
        wanted.sort();
        assert_eq!(grants.len(), 4, "{mutation} committed its grants");
    }
    // The surviving set is exactly the LAST committed
    // writer's input: one mutation's grant subjects equal
    // the final page, and the other writer's grants were
    // overwritten by the winner's diff.
    // Audit subjects are actor ids; page items are
    // User IDs.
    let survivors: Vec<String> = page
        .items
        .iter()
        .map(|user| human_actor_id(user))
        .collect();
    let winner = [&mutation_a, &mutation_b]
        .into_iter()
        .find(|mutation| {
            let mut grants = grant_subjects(mutation);
            grants.sort();
            grants == survivors
        })
        .expect("the final set must equal one writer's input");
    let loser = if winner == &mutation_a { &mutation_b } else { &mutation_a };
    assert!(
        !grant_subjects(loser)
            .iter()
            .all(|subject| survivors.contains(subject)),
        "the losing writer's full grant set must have been \
         overwritten by the last commit"
    );
}

/// Repeated identical PUT and DELETE commands succeed without any
/// additional audit rows (spec §5.1 "已存在返回成功且不写重复审计").
async fn manager_repeat_commands_leave_no_audit(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_repeat";
    fixture.seed_bot(env, bot, 1).await;
    fixture
        .grant_edge(env, bot, &human_actor_id("own"), BotAccessRelation::Owner)
        .await;
    fixture.seed_human(env, "own").await;
    fixture.seed_human(env, "cal").await;

    let audit_start = fixture.audit_rows(env).await.len();
    for (index, expected_added) in [1, 0, 0].iter().enumerate() {
        let change = repo
            .edit_manager(human_edit(
                bot,
                "cal",
                ManagerEditAction::Grant,
                "own",
                next_mutation_id(),
            ))
            .await
            .expect("repeat grant");
        assert_eq!(
            change.added, *expected_added,
            "grant #{index} must record added={expected_added}: {change:?}"
        );
    }
    let after_grants = fixture.audit_rows(env).await.len();
    assert_eq!(after_grants - audit_start, 1, "only the first grant audited");

    for _ in 0..3 {
        let change = repo
            .edit_manager(human_edit(
                bot,
                "cal",
                ManagerEditAction::Revoke,
                "own",
                next_mutation_id(),
            ))
            .await
            .expect("repeat revoke");
        assert_eq!(change.added, 0);
    }
    let after_revokes = fixture.audit_rows(env).await.len();
    assert_eq!(after_revokes - after_grants, 1, "only the first revoke audited");
}
/// A second SQL chunk / audit failure leaves everything unchanged:
/// the reserved audit sequence collides mid-diff, the whole
/// transaction rolls back, and no partial coverage happens.
async fn sync_second_chunk_audit_failure_is_atomic(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_sync_audit_fail";
    fixture.seed_bot(env, bot, 1).await;
    fixture
        .grant_edge(env, bot, &human_actor_id("boss"), BotAccessRelation::Owner)
        .await;
    let stock: Vec<String> = big_humans(150);
    fixture.seed_human(env, "boss").await;
    for user in &stock {
        fixture.seed_human(env, user).await;
        fixture
            .grant_edge(
                env,
                bot,
                human_actor_id(user).as_str(),
                BotAccessRelation::Manager,
            )
            .await;
    }
    fixture
        .revoke_edge(env, bot, &human_actor_id("boss"), BotAccessRelation::Manager)
        .await;

    // Reserve the sequence the sorted revoke diff would write deep
    // into the second chunk.
    fixture
        .seed_audit_conflict(env, "mutation-conflict", 137)
        .await;
    let audit_before = fixture.audit_rows(env).await.len();

    let result = repo
        .sync_managers(ManagerSync {
            bot_id: bot.to_string(),
            user_ids: vec![],
            mutation: AuthorityMutation {
                mutation_id: "mutation-conflict".to_string(),
                actor: AuthorityActor::Service { app_id: 9 },
            },
        })
        .await;
    assert!(result.is_err(), "the collision must fail the whole request");
    assert_eq!(
        fixture.audit_rows(env).await.len(),
        audit_before,
        "failed sync must not commit partial audit rows"
    );
    assert_eq!(
        repo.role(&human_actor_id("mn00000"), bot).await.expect("role"),
        Some(BotAccessRelation::Manager),
        "failed sync must not revoke anything"
    );
    assert_eq!(
        repo.role(&human_actor_id("mn00149"), bot).await.expect("role"),
        Some(BotAccessRelation::Manager),
        "managers beyond the first chunk stay"
    );
}

/// `1,000` deduped managers succeed; `1,001` is rejected as a
/// whole request (spec §5.2) with the current set untouched.
async fn sync_accepts_1000_and_rejects_1001(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_sync_cap";
    fixture.seed_bot(env, bot, 1).await;
    fixture
        .grant_edge(env, bot, &human_actor_id("boss"), BotAccessRelation::Owner)
        .await;
    fixture.seed_human(env, "boss").await;
    let users = big_humans(1_001);
    for user in &users {
        fixture.seed_human(env, user).await;
    }

    let at_cap: Vec<String> = users[..1_000].to_vec();
    let refs: Vec<&str> = at_cap.iter().map(String::as_str).collect();
    let change = repo
        .sync_managers(service_sync(bot, refs.clone(), 9, next_mutation_id()))
        .await
        .expect("1,000 deduped managers succeed");
    assert_eq!(change, ManagerSetChange { added: 1_000, removed: 0, total: 1_000 });

    let audit_before = fixture.audit_rows(env).await.len();
    // 1,001 distinct users — over the input cap.
    let over: Vec<&str> = users.iter().map(String::as_str).collect();
    let err = repo
        .sync_managers(service_sync(bot, over, 9, next_mutation_id()))
        .await
        .expect_err("1,001 must be rejected");
    assert!(mentions(&err, "bot_manager_input_limit"), "got {err}");
    let page = repo
        .managers(ManagerListQuery { bot_id: bot.to_string(), ..Default::default() })
        .await
        .expect("page after rejection");
    assert_eq!(page.total, 1_000, "rejected sync must not change the set");
    assert_eq!(fixture.audit_rows(env).await.len(), audit_before);
}

/// Stock beyond the first page (>1,000) is fully cleared by an
/// empty sync — bounded pre-read pagination must not miss
/// members.
async fn sync_over_1000_stock_clears_beyond_first_page(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_sync_stock";
    fixture.seed_bot(env, bot, 1).await;
    fixture
        .grant_edge(env, bot, &human_actor_id("boss"), BotAccessRelation::Owner)
        .await;
    fixture.seed_human(env, "boss").await;
    let users = big_humans(1_001);
    for user in &users {
        fixture.seed_human(env, user).await;
        fixture
            .grant_edge(
                env,
                bot,
                human_actor_id(user).as_str(),
                BotAccessRelation::Manager,
            )
            .await;
    }
    fixture
        .revoke_edge(env, bot, &human_actor_id("boss"), BotAccessRelation::Manager)
        .await;

    let change = repo
        .sync_managers(service_sync(bot, vec![], 9, next_mutation_id()))
        .await
        .expect("clear 1,001 stock");
    assert_eq!(change, ManagerSetChange { added: 0, removed: 1_001, total: 0 });
    let page = repo
        .managers(ManagerListQuery { bot_id: bot.to_string(), ..Default::default() })
        .await
        .expect("cleared page");
    assert_eq!(page.total, 0);
    assert!(page.items.is_empty());
    assert_eq!(
        repo.role(&human_actor_id("mn01000"), bot).await.expect("role"),
        None,
        "members beyond the first pre-read page must be cleared too"
    );
}

/// Rule 25 conformance entry: the [`BotAuthorityRepoPort`]
/// conformance over the shared fixture — the exact same
/// real scenario body as [`run_bot_authority_contract`]
/// (reads + manager writes + transfer writes for both the
/// SQL store and the memory projection).
pub async fn bot_authority_repo_port_contract_tests(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
    other_env: &str,
) {
    run_bot_authority_contract(fixture, env, other_env).await;
}
