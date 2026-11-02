//! Ownership-sync (`BotAuthorityRepoPort::sync_ownership`)
//! conformance scenarios (2026-10-29 design §9.2 S01–S06,
//! S08 parity). Runs identically against the SQL store and the
//! memory projection via the shared fixture.

#![allow(clippy::expect_used, reason = "test harness — panic on failure is the contract")]

use bcs_service_api::types::bot_authority::{
    human_actor_id, AuthorityActor, AuthorityMutation, BotAccessRelation,
    ManagerListQuery, OwnershipSync,
};

use super::bot_authority_contract::{
    mentions, next_mutation_id, BotAuthorityContractFixture,
};

fn ownership_sync(bot: &str, owner: &str, app_id: i64, mutation_id: String) -> OwnershipSync {
    OwnershipSync {
        bot_id: bot.to_string(),
        owner_user_id: human_actor_id(owner),
        mutation: AuthorityMutation {
            mutation_id,
            actor: AuthorityActor::Service { app_id },
        },
    }
}

/// The scenarios (reported in order for the MySQL gate pins).
pub async fn run_ownership_sync_contract(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
    mut report: impl FnMut(&'static str),
) {
    report("ownership_sync_swaps_owner_and_strips_edges");
    ownership_sync_swaps_owner_and_strips_edges(fixture, env).await;
    report("ownership_sync_same_owner_is_zero_diff");
    ownership_sync_same_owner_is_zero_diff(fixture, env).await;
    report("ownership_sync_missing_bot_noop_uninitialized_rejects");
    ownership_sync_missing_bot_noop_uninitialized_rejects(fixture, env).await;
    report("ownership_sync_unknown_or_deleted_target_rejected");
    ownership_sync_unknown_or_deleted_target_rejected(fixture, env).await;
    report("ownership_sync_closes_pending_transfer");
    ownership_sync_closes_pending_transfer(fixture, env).await;
    report("ownership_sync_actor_kind_and_integrity_errors");
    ownership_sync_actor_kind_and_integrity_errors(fixture, env).await;
    report("ownership_sync_concurrent_writers_serialize");
    ownership_sync_concurrent_writers_serialize(fixture, env).await;
    report("ownership_sync_audit_failure_rolls_back_everything");
    ownership_sync_audit_failure_rolls_back_everything(fixture, env).await;
}

/// Atomic swap (设计 S01): the old owner loses BOTH edge kinds (no
/// manager demotion), the target's manager edge is stripped, other
/// managers stand, the version increments exactly once and the
/// audit names the real Service actor with both owners.
async fn ownership_sync_swaps_owner_and_strips_edges(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_own_sync";
    fixture.seed_bot(env, bot, 1).await;
    for user in ["own", "keep", "next"] {
        fixture.seed_human(env, user).await;
    }
    fixture.grant_edge(env, bot, &human_actor_id("own"), BotAccessRelation::Owner).await;
    fixture.grant_edge(env, bot, &human_actor_id("keep"), BotAccessRelation::Manager).await;
    // The target being a manager proves its manager edge is
    // stripped by the swap (owner 不占 manager 名额).
    fixture.grant_edge(env, bot, &human_actor_id("next"), BotAccessRelation::Manager).await;

    let mutation_id = next_mutation_id();
    let change = repo
        .sync_ownership(ownership_sync(bot, "next", 7, mutation_id.clone()))
        .await
        .expect("ownership sync");
    assert_eq!(
        change.owner_user_id.as_deref(),
        Some("next"),
        "post-commit owner is the target"
    );
    assert_eq!(change.previous_owner_user_id.as_deref(), Some("own"));
    assert!(change.changed);

    assert_eq!(fixture.ownership_version(env, bot).await, 2, "exactly one CAS increment");
    let ownership = repo.ownership(bot).await.expect("ownership after sync");
    assert_eq!(ownership.owner_actor_id, human_actor_id("next"));
    assert_eq!(repo.role(&human_actor_id("own"), bot).await.expect("old owner role"), None,
        "the old owner is FULLY removed — no manager demotion (设计 §2)");
    assert_eq!(repo.role(&human_actor_id("next"), bot).await.expect("new owner role"),
        Some(BotAccessRelation::Owner));
    assert_eq!(repo.role(&human_actor_id("keep"), bot).await.expect("bystander role"),
        Some(BotAccessRelation::Manager), "other managers untouched");
    let page = repo.managers(ManagerListQuery { bot_id: bot.to_string(), ..Default::default() })
        .await.expect("page");
    assert!(page.items.iter().all(|user| user != "next"),
        "the new owner's manager edge was stripped: {:?}", page.items);

    let rows = fixture.audit_rows(env).await;
    let rows: Vec<_> = rows.iter().filter(|row| row.mutation_id == mutation_id).collect();
    assert_eq!(rows.len(), 1, "exactly one ownership_sync audit row: {rows:?}");
    let row = rows[0];
    assert_eq!(row.action, "ownership_sync");
    assert_eq!(row.actor_kind, "service");
    assert_eq!(row.actor_id, "app_7");
    assert_eq!(row.subject_id, human_actor_id("next"));
    assert_eq!(row.before_state.as_deref(),
        Some("{\"owner\":\"human_own\",\"version\":1}"),
        "before state carries the previous owner + version");
    assert_eq!(row.after_state.as_deref(),
        Some("{\"owner\":\"human_next\",\"version\":2}"),
        "after state carries the new owner + incremented version");
}

/// Idempotent replay (设计 S02): same-owner resubmission is a
/// zero-diff success — no audit, version and pendings untouched.
/// The same holds when the current owner's Human row is already
/// deleted: idempotency is judged BEFORE any target-liveness
/// check (设计 §5.1 步骤 3) — this leg pins both stores to that
/// order. (Usernames are scenario-private: the shared env keeps
/// "own"/"t1" for the other scenarios.)
async fn ownership_sync_same_owner_is_zero_diff(fixture: &(impl BotAuthorityContractFixture + ?Sized), env: &str) {
    let repo = fixture.authority_repo();
    let bot = "bot_own_replay";
    fixture.seed_bot(env, bot, 1).await;
    fixture.seed_human(env, "r_own").await;
    fixture.seed_human(env, "r_t1").await;
    fixture.grant_edge(env, bot, &human_actor_id("r_own"), BotAccessRelation::Owner).await;
    fixture.seed_pending_transfer(env, bot, &human_actor_id("r_own"), &human_actor_id("r_t1"), 1).await;

    let audit_before = fixture.audit_rows(env).await.len();
    let change = repo
        .sync_ownership(ownership_sync(bot, "r_own", 7, next_mutation_id()))
        .await
        .expect("replay");
    assert!(!change.changed);
    assert_eq!(change.owner_user_id.as_deref(), Some("r_own"));
    assert_eq!(change.previous_owner_user_id, None);
    assert_eq!(fixture.ownership_version(env, bot).await, 1, "no CAS increment");
    assert_eq!(fixture.audit_rows(env).await.len(), audit_before, "no audit rows");
    let decision = fixture.transfer_decision_state(env, bot, &human_actor_id("r_t1")).await
        .expect("pending row survives the replay untouched");
    assert_eq!(decision.status, "pending");

    // Deleted-current-owner replay (设计 §5.1 步骤 3): still a
    // zero-diff no-op, never a human_unknown — the replay writes
    // nothing, so no liveness check may gate it.
    fixture.remove_human(env, "r_own").await;
    let change = repo
        .sync_ownership(ownership_sync(bot, "r_own", 7, next_mutation_id()))
        .await
        .expect("replay with a deleted current owner stays a zero-diff no-op");
    assert!(!change.changed);
    assert_eq!(fixture.audit_rows(env).await.len(), audit_before, "still no audit rows");
    assert_eq!(fixture.ownership_version(env, bot).await, 1);
    fixture.seed_human(env, "r_own").await; // leave the shared env clean
}

/// Missing bot → no-op unchanged + zero audit (设计 S03);
/// version-0 bot → the pinned uninitialized conflict.
async fn ownership_sync_missing_bot_noop_uninitialized_rejects(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    fixture.seed_human(env, "next").await;
    let audit_before = fixture.audit_rows(env).await.len();
    let change = repo
        .sync_ownership(ownership_sync("bot_own_missing", "next", 7, next_mutation_id()))
        .await
        .expect("missing bot is a tolerated no-op");
    assert_eq!(change, bcs_service_api::types::bot_authority::OwnershipChange::unchanged("bot_own_missing"));
    assert_eq!(fixture.audit_rows(env).await.len(), audit_before);

    let v0 = "bot_own_v0";
    fixture.seed_bot(env, v0, 0).await;
    let err = repo
        .sync_ownership(ownership_sync(v0, "next", 7, next_mutation_id()))
        .await
        .expect_err("version-0 bot rejects");
    assert!(mentions(&err, "bot_ownership_not_initialized"), "{err}");
}

/// Unknown or deleted target Human → whole-request
/// `bot_authority_human_unknown` with zero writes (设计 S04).
async fn ownership_sync_unknown_or_deleted_target_rejected(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_own_bad_target";
    fixture.seed_bot(env, bot, 1).await;
    fixture.seed_human(env, "own").await;
    fixture.grant_edge(env, bot, &human_actor_id("own"), BotAccessRelation::Owner).await;
    fixture.seed_human(env, "gone").await;
    fixture.remove_human(env, "gone").await;

    let audit_before = fixture.audit_rows(env).await.len();
    for target in ["nobody", "gone"] {
        let err = repo
            .sync_ownership(ownership_sync(bot, target, 7, next_mutation_id()))
            .await
            .expect_err("unknown/deleted target");
        assert!(mentions(&err, "bot_authority_human_unknown"), "{target}: {err}");
    }
    assert_eq!(fixture.audit_rows(env).await.len(), audit_before, "no partial write");
    assert_eq!(fixture.ownership_version(env, bot).await, 1);
    assert_eq!(repo.ownership(bot).await.expect("ownership").owner_actor_id, human_actor_id("own"));
}

/// A REAL swap closes the bot's valid pending transfer in the
/// same transaction: status invalidated, fixed system marker,
/// pinned reason, one transfer_invalidate audit row (设计 S05).
async fn ownership_sync_closes_pending_transfer(fixture: &(impl BotAuthorityContractFixture + ?Sized), env: &str) {
    let repo = fixture.authority_repo();
    let bot = "bot_own_pending";
    fixture.seed_bot(env, bot, 1).await;
    for user in ["own", "next", "t1"] {
        fixture.seed_human(env, user).await;
    }
    fixture.grant_edge(env, bot, &human_actor_id("own"), BotAccessRelation::Owner).await;
    fixture.seed_pending_transfer(env, bot, &human_actor_id("own"), &human_actor_id("t1"), 1).await;

    let mutation_id = next_mutation_id();
    repo.sync_ownership(ownership_sync(bot, "next", 7, mutation_id.clone()))
        .await
        .expect("swap under pending");

    let decision = fixture.transfer_decision_state(env, bot, &human_actor_id("t1")).await
        .expect("closed row still exists");
    assert_eq!(decision.status, "invalidated");
    assert_eq!(decision.decided_by_kind.as_deref(), Some("system"));
    assert_eq!(decision.decided_by_id.as_deref(), Some("system_transfer_close"));
    assert_eq!(decision.reason.as_deref(), Some("ownership_sync"));

    let rows = fixture.audit_rows(env).await;
    let mut rows: Vec<_> = rows.iter().filter(|row| row.mutation_id == mutation_id).collect();
    rows.sort_by_key(|row| row.sequence);
    assert_eq!(rows.len(), 2, "swap audit + closure audit under one mutation: {rows:?}");
    assert_eq!(rows[0].action, "ownership_sync");
    assert_eq!(rows[0].sequence, 0);
    assert_eq!(rows[1].action, "transfer_invalidate");
    assert_eq!(rows[1].sequence, 1);
    assert_eq!(rows[1].actor_kind, "system");
    assert_eq!(rows[1].actor_id, "system_transfer_close");
    assert_eq!(rows[1].subject_id, human_actor_id("t1"));
    assert!(rows[1].before_state.as_deref().expect("before").contains("pending"));
    assert!(rows[1].after_state.as_deref().expect("after").contains("invalidated"));
}

/// Non-Service actors never run the sync (设计 S06), and a
/// corrupt owner state is the integrity ERROR, never silently
/// repaired by the sync (设计 §2).
async fn ownership_sync_actor_kind_and_integrity_errors(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_own_guards";
    fixture.seed_bot(env, bot, 1).await;
    fixture.seed_human(env, "own").await;
    fixture.seed_human(env, "next").await;
    fixture.grant_edge(env, bot, &human_actor_id("own"), BotAccessRelation::Owner).await;

    let human_mutation = OwnershipSync {
        bot_id: bot.to_string(),
        owner_user_id: human_actor_id("next"),
        mutation: AuthorityMutation {
            mutation_id: next_mutation_id(),
            actor: AuthorityActor::Human { user_id: "own".to_string() },
        },
    };
    let err = repo.sync_ownership(human_mutation).await.expect_err("Human actor denied");
    assert!(mentions(&err, "bot_authority_operator_forbidden"), "{err}");

    let corrupt = "bot_own_corrupt";
    fixture.seed_bot(env, corrupt, 1).await;
    fixture.grant_edge(env, corrupt, &human_actor_id("own"), BotAccessRelation::Owner).await;
    fixture.seed_corrupt_duplicate_owner(env, corrupt, &human_actor_id("next")).await;
    let err = repo
        .sync_ownership(ownership_sync(corrupt, "next", 7, next_mutation_id()))
        .await
        .expect_err("corrupt owner state");
    assert!(err.to_string().contains("bot_owner_integrity"), "{err}");
}

/// Two racing ownership syncs serialize on the bot row (设计 S07):
/// both calls succeed by re-reading after a CAS conflict (each
/// applies its own snapshot — later commit wins), the version
/// grows exactly once per APPLIED swap, and the old owner is gone.
async fn ownership_sync_concurrent_writers_serialize(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_own_race";
    fixture.seed_bot(env, bot, 1).await;
    for user in ["own", "next_a", "next_b"] {
        fixture.seed_human(env, user).await;
    }
    fixture.grant_edge(env, bot, &human_actor_id("own"), BotAccessRelation::Owner).await;

    // Mirror the concurrency primitive the neighboring scenario
    // `sync_concurrent_writers_serialize_and_recompute` uses
    // (tokio::join! on two &repo futures — copied verbatim).
    let (res_a, res_b) = tokio::join!(
        repo.sync_ownership(ownership_sync(bot, "next_a", 7, next_mutation_id())),
        repo.sync_ownership(ownership_sync(bot, "next_b", 7, next_mutation_id())),
    );
    let outcome_a = res_a.expect("sync a commits (serialized, never error-swallowed)");
    let outcome_b = res_b.expect("sync b commits");
    assert!(outcome_a.changed && outcome_b.changed,
        "each sync applied its snapshot after serialization");
    let final_owner = repo.ownership(bot).await.expect("ownership").owner_actor_id;
    assert!(
        final_owner == human_actor_id("next_a") || final_owner == human_actor_id("next_b"),
        "the later commit wins exactly one snapshot: {final_owner}"
    );
    assert_eq!(fixture.ownership_version(env, bot).await, 3,
        "two applied swaps, exactly one CAS increment each");
    assert_eq!(repo.role(&human_actor_id("own"), bot).await.expect("old owner role"), None);
}

/// An injected audit-unique conflict rolls the WHOLE swap back
/// (设计 S08): no edges move, the version does not grow, the
/// pending row survives pending — deterministic across stores
/// via the fixture's `seed_audit_conflict` injection point.
async fn ownership_sync_audit_failure_rolls_back_everything(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bot_own_rollback";
    fixture.seed_bot(env, bot, 1).await;
    for user in ["own", "next", "t1"] {
        fixture.seed_human(env, user).await;
    }
    fixture.grant_edge(env, bot, &human_actor_id("own"), BotAccessRelation::Owner).await;
    fixture.seed_pending_transfer(env, bot, &human_actor_id("own"), &human_actor_id("t1"), 1).await;

    let mutation_id = next_mutation_id();
    fixture.seed_audit_conflict(env, &mutation_id, 0).await;
    let _err = repo
        .sync_ownership(ownership_sync(bot, "next", 7, mutation_id))
        .await
        .expect_err("audit conflict rolls the whole command back");

    assert_eq!(repo.ownership(bot).await.expect("ownership").owner_actor_id,
        human_actor_id("own"), "owner NOT swapped");
    assert_eq!(fixture.ownership_version(env, bot).await, 1, "version NOT bumped");
    assert_eq!(repo.role(&human_actor_id("own"), bot).await.expect("role"),
        Some(BotAccessRelation::Owner), "old owner edge intact");
    assert_eq!(repo.role(&human_actor_id("next"), bot).await.expect("target role"), None);
    let decision = fixture.transfer_decision_state(env, bot, &human_actor_id("t1")).await
        .expect("pending row untouched");
    assert_eq!(decision.status, "pending", "no closure leaked out of the rolled-back tx");
}
