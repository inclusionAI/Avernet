//! Ownership-sync legs the shared contract harness does not
//! seed (Task-4 reviewer carryover, 2026-10-29 design §2):
//!
//! 1. The old owner ALSO holding an approved manager edge —
//!    the swap must revoke that edge too (write #5: full
//!    removal, no accept-style manager demotion/残留).
//! 2. A version-1 bot whose approved owner edge is missing
//!    entirely — the pinned `bot_owner_integrity` error,
//!    never a silent repair.
//!
//! SQLite-only like the neighboring authority suites (the
//! MySQL chain re-runs the shared harness in CI).

#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Arc as StdArc;

use async_trait::async_trait;

use bcs_db_api::{
    DbExecuteResult, DbHealth, DbPlugin, DbRow, DbStatement,
    DbTransactionOptions, DbTransactionStep,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::types::bot_authority::{
    human_actor_id, AuthorityActor, AuthorityMutation, BotAccessRelation,
    ManagerListQuery, OwnershipSync, TransferAction,
};
use bcs_test_support::bot_authority_sqlite::new_fixture;
use bcs_test_support::contract::bot_authority_contract::BotAuthorityContractFixture;
use bcs_test_support::contract::bot_transfer_contract::{human_mutation, transfer_cmd};

const ENV: &str = "authority-ownership-sync-edges";

/// Service mutation command built inline (the harness's
/// `next_mutation_id` is crate-internal; each test pins its
/// own unique id instead).
fn ownership_sync_command(
    bot: &str,
    owner_user: &str,
    mutation_id: &str,
) -> OwnershipSync {
    OwnershipSync {
        bot_id: bot.to_string(),
        owner_user_id: human_actor_id(owner_user),
        mutation: AuthorityMutation {
            mutation_id: mutation_id.to_string(),
            actor: AuthorityActor::Service { app_id: 7 },
        },
    }
}

/// An owner who ALSO manages the bot loses BOTH edges on the
/// swap (设计 §2 full removal — case S01 never exercises the
/// `old_mgr_approved == 1` revoke branch).
#[tokio::test]
async fn old_owner_manager_edge_is_revoked_by_the_swap() {
    let fixture = new_fixture(ENV).await;
    let bot = "bot_own_sync_oldmgr";
    for user in ["own", "keep", "next"] {
        fixture.seed_human(ENV, user).await;
    }
    fixture.seed_bot(ENV, bot, 1).await;
    fixture
        .grant_edge(ENV, bot, &human_actor_id("own"), BotAccessRelation::Owner)
        .await;
    // The leg S01 never seeds: the current owner ALSO holds an
    // approved manager edge.
    fixture
        .grant_edge(ENV, bot, &human_actor_id("own"), BotAccessRelation::Manager)
        .await;
    fixture
        .grant_edge(ENV, bot, &human_actor_id("keep"), BotAccessRelation::Manager)
        .await;

    let mutation_id = "mutation-ownsync-oldmgr";
    let repo = fixture.authority_repo();
    let change = repo
        .sync_ownership(ownership_sync_command(bot, "next", mutation_id))
        .await
        .expect("ownership sync");
    assert!(change.changed);
    assert_eq!(change.owner_user_id.as_deref(), Some("next"));
    assert_eq!(change.previous_owner_user_id.as_deref(), Some("own"));
    assert_eq!(
        fixture.ownership_version(ENV, bot).await,
        2,
        "exactly one CAS increment"
    );

    assert_eq!(
        repo.role(&human_actor_id("own"), bot).await.expect("old owner role"),
        None,
        "old owner FULLY removed — the manager edge went with \
         the owner edge (设计 §2)"
    );
    assert_eq!(
        repo.role(&human_actor_id("next"), bot).await.expect("new owner role"),
        Some(BotAccessRelation::Owner)
    );
    let page = repo
        .managers(ManagerListQuery {
            bot_id: bot.to_string(),
            ..Default::default()
        })
        .await
        .expect("managers page");
    assert!(
        page.items.iter().all(|user| user != "own"),
        "the old owner is not demoted into the manager set: {:?}",
        page.items
    );
    assert!(
        page.items.contains(&"keep".to_string()),
        "bystander managers stand: {:?}",
        page.items
    );

    let rows = fixture.audit_rows(ENV).await;
    let rows: Vec<_> = rows.iter().filter(|row| row.mutation_id == mutation_id).collect();
    assert_eq!(rows.len(), 1, "exactly one ownership_sync audit row");
    assert_eq!(rows[0].action, "ownership_sync");
}

/// A version-1 bot with NO approved owner edge is corrupt
/// state: the pinned integrity error (`pre_read_owner`
/// surfaces `bot_owner_integrity`), never a silent repair.
#[tokio::test]
async fn bot_without_owner_edge_is_the_integrity_error() {
    let fixture = new_fixture(ENV).await;
    let bot = "bot_own_no_owner_edge";
    fixture.seed_human(ENV, "next").await;
    fixture.seed_bot(ENV, bot, 1).await;

    let err = fixture
        .authority_repo()
        .sync_ownership(ownership_sync_command(bot, "next", "mutation-ownsync-noedge"))
        .await
        .expect_err("a bot with no owner edge must not be swapped");
    assert!(
        err.to_string().contains("bot_owner_integrity"),
        "pinned integrity code, got: {err}"
    );
}

// ===================== interleave gate =====================

/// Entry-ordering gate for transaction_with_options (same
/// gating family as the first-two-transactions gate in
/// tests/authority_transfers.rs): the FIRST entry (the
/// ownership sync's) is held at entry until the test releases
/// it; every later entry (the racing transfer initiation, the
/// sync's own recomputing retry) passes straight through. This
/// yields the Critical-1 race ordering deterministically — the
/// sync finishes its pre-reads seeing NO pending, an initiation
/// commits in between, and the sync's transaction must fail on
/// the None-leg zero-live-pending guard — no interleaving
/// luck involved.
#[derive(Default)]
struct HoldFirstTxGate {
    entries: AtomicUsize,
    release: AtomicBool,
}

impl HoldFirstTxGate {
    async fn wait_until_held(&self) {
        tokio::time::timeout(std::time::Duration::from_secs(5), async {
            while self.entries.load(Ordering::SeqCst) == 0 {
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("sync reached its transaction entry");
    }

    async fn enter(&self) {
        if self.entries.fetch_add(1, Ordering::SeqCst) == 0 {
            while !self.release.load(Ordering::SeqCst) {
                tokio::task::yield_now().await;
            }
        }
    }
}

/// DbPlugin wrapper gating transaction entries; everything
/// else passes straight through to the SQLite plugin.
struct HoldFirstSqlite {
    inner: LocalSqliteDbPlugin,
    gate: StdArc<HoldFirstTxGate>,
}

#[async_trait]
impl DbPlugin for HoldFirstSqlite {
    async fn query(
        &self,
        statement: DbStatement,
    ) -> bcs_db_api::DbResult<Vec<DbRow>> {
        self.inner.query(statement).await
    }

    async fn execute(
        &self,
        statement: DbStatement,
    ) -> bcs_db_api::DbResult<DbExecuteResult> {
        self.inner.execute(statement).await
    }

    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> bcs_db_api::DbResult<Vec<bcs_db_api::DbTransactionStepResult>> {
        self.inner.transaction(steps).await
    }

    async fn transaction_with_options(
        &self,
        steps: Vec<DbTransactionStep>,
        options: DbTransactionOptions,
    ) -> bcs_db_api::DbResult<Vec<bcs_db_api::DbTransactionStepResult>> {
        self.gate.enter().await;
        self.inner
            .transaction_with_options(steps, options)
            .await
    }

    async fn health_check(&self) -> bcs_db_api::DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

/// Critical-1 regression: a transfer initiation that commits
/// between the sync's pending pre-read (which saw none) and the
/// sync's transaction must NOT survive under the swapped
/// ownership — the None-leg zero-live-pending guard fails that
/// attempt, the retry re-reads the pending and closes it in the
/// SAME transaction as the swap (设计 §2 closure mandate).
#[tokio::test]
async fn initiation_racing_the_sync_is_closed_by_the_retry() {
    let gate = StdArc::new(HoldFirstTxGate::default());
    let db: StdArc<dyn DbPlugin> = StdArc::new(HoldFirstSqlite {
        inner: LocalSqliteDbPlugin::new().expect("local sqlite"),
        gate: gate.clone(),
    });
    let fixture =
        bcs_test_support::bot_authority_sqlite::new_fixture_with_db(ENV, db).await;
    let bot = "bot_own_sync_race";
    for user in ["own", "next", "t1"] {
        fixture.seed_human(ENV, user).await;
    }
    fixture.seed_bot(ENV, bot, 1).await;
    fixture
        .grant_edge(ENV, bot, &human_actor_id("own"), BotAccessRelation::Owner)
        .await;

    let mutation_id = "mutation-ownsync-interleave";
    let sync_repo = fixture.authority_repo();
    let cmd = ownership_sync_command(bot, "next", mutation_id);
    let sync_task = tokio::spawn(async move { sync_repo.sync_ownership(cmd).await });
    // Hold the sync at its transaction entry AFTER its pre-reads
    // (pending: None) all completed.
    while gate.entries.load(Ordering::SeqCst) == 0 {
        tokio::task::yield_now().await;
    }
    // The racy initiation: commits between the sync's pre-read
    // and its transaction (touches neither owner nor version).
    fixture
        .authority_repo()
        .change_transfer(transfer_cmd(
            bot,
            "t1",
            TransferAction::RequestOrRefresh,
            human_mutation(
                "mutation-ownsync-interleave-init".to_string(),
                "own",
            ),
        ))
        .await
        .expect("initiation commits while the sync holds its entry");
    gate.release.store(true, Ordering::SeqCst);

    let change = sync_task
        .await
        .expect("sync task joined")
        .expect("ownership sync retried after the guard failure and committed");
    assert!(change.changed);
    assert_eq!(change.owner_user_id.as_deref(), Some("next"));
    assert_eq!(
        fixture.ownership_version(ENV, bot).await,
        2,
        "exactly one CAS increment across the retry"
    );

    // The race's pending was closed by the retry, never left
    // live under the swapped ownership.
    let decision = fixture
        .transfer_decision_state(ENV, bot, &human_actor_id("t1"))
        .await
        .expect("racing pending row");
    assert_eq!(decision.status, "invalidated");
    assert_eq!(decision.decided_by_kind.as_deref(), Some("system"));
    assert_eq!(
        decision.decided_by_id.as_deref(),
        Some("system_transfer_close")
    );
    assert_eq!(decision.reason.as_deref(), Some("ownership_sync"));

    let rows = fixture.audit_rows(ENV).await;
    let mut rows: Vec<_> = rows
        .iter()
        .filter(|row| row.mutation_id == mutation_id)
        .collect();
    rows.sort_by_key(|row| row.sequence);
    assert_eq!(rows.len(), 2, "swap audit + closure audit: {rows:?}");
    assert_eq!(rows[0].action, "ownership_sync");
    assert_eq!(rows[0].sequence, 0);
    assert_eq!(rows[1].action, "transfer_invalidate");
    assert_eq!(rows[1].sequence, 1);
}

#[tokio::test]
async fn manager_granted_after_pre_read_is_stripped_by_the_swap() {
    use bcs_service_api::types::bot_authority::{ManagerEdit, ManagerEditAction};

    let gate = StdArc::new(HoldFirstTxGate::default());
    let db: StdArc<dyn DbPlugin> = StdArc::new(HoldFirstSqlite {
        inner: LocalSqliteDbPlugin::new().expect("local sqlite"),
        gate: gate.clone(),
    });
    let fixture =
        bcs_test_support::bot_authority_sqlite::new_fixture_with_db(ENV, db).await;
    let bot = "bot_sync_manager_race";
    fixture.seed_bot(ENV, bot, 1).await;
    fixture.seed_human(ENV, "next").await;
    fixture.grant_edge(ENV, bot, "human_own", BotAccessRelation::Owner).await;
    let repo = fixture.authority_repo();
    let sync = tokio::spawn(async move {
        repo.sync_ownership(ownership_sync_command(bot, "next", "sync-manager-race"))
            .await
    });
    gate.wait_until_held().await;
    fixture.authority_repo().edit_manager(ManagerEdit {
        bot_id: bot.to_string(),
        user_id: "human_next".to_string(),
        action: ManagerEditAction::Grant,
        mutation: human_mutation("manager-race-grant".to_string(), "own"),
    }).await.expect("concurrent manager grant");
    gate.release.store(true, Ordering::SeqCst);
    assert!(sync.await.expect("sync task").expect("sync result").changed);
    assert_eq!(gate.entries.load(Ordering::SeqCst), 3, "grant plus two sync attempts");
    assert_eq!(fixture.ownership_version(ENV, bot).await, 2);
    // The managers read excludes the owner, so inspect the actual stored edge.
    let rows = fixture.db.query(DbStatement::new(
        "SELECT from_id FROM edge_grants \
         WHERE grant_kind = 'manager' AND status = 'approved'",
    )).await.expect("stored manager edges");
    assert!(rows.is_empty(), "new owner's manager edge survived: {rows:?}");
}

#[tokio::test]
async fn corrupt_old_owner_is_rejected_without_writes() {
    let fixture = new_fixture(ENV).await;
    let bot = "bot_sync_corrupt_owner";
    fixture.seed_bot(ENV, bot, 1).await;
    fixture.seed_human(ENV, "next").await;
    fixture.grant_edge(ENV, bot, "bot_not_a_human", BotAccessRelation::Owner).await;
    let before = fixture.db.query(DbStatement::new("SELECT * FROM edge_grants"))
        .await.expect("edges before");
    let err = fixture.authority_repo().sync_ownership(
        ownership_sync_command(bot, "next", "sync-corrupt-owner"),
    ).await.expect_err("corrupt owner rejected");
    assert!(err.to_string().contains("bot_owner_integrity"), "{err}");
    assert_eq!(fixture.ownership_version(ENV, bot).await, 1);
    assert!(fixture.audit_rows(ENV).await.is_empty());
    assert_eq!(
        fixture.db.query(DbStatement::new("SELECT * FROM edge_grants"))
            .await.expect("edges after"),
        before,
    );
}

#[tokio::test]
async fn ownership_audits_preserve_escaped_user_ids() {
    let fixture = new_fixture(ENV).await;
    let bot = "bot_sync_escaped_audit";
    let old = "old\"\\user";
    let next = "next\"\\user";
    fixture.seed_bot(ENV, bot, 1).await;
    fixture.seed_human(ENV, next).await;
    fixture.grant_edge(ENV, bot, &human_actor_id(old), BotAccessRelation::Owner).await;
    fixture.authority_repo().sync_ownership(
        ownership_sync_command(bot, next, "sync-escaped-audit"),
    ).await.expect("ownership sync");
    let rows = fixture.audit_rows(ENV).await;
    assert_eq!(rows.len(), 1);
    let before: serde_json::Value = serde_json::from_str(
        rows[0].before_state.as_deref().expect("before state"),
    ).expect("valid before JSON");
    let after: serde_json::Value = serde_json::from_str(
        rows[0].after_state.as_deref().expect("after state"),
    ).expect("valid after JSON");
    assert_eq!(before, serde_json::json!({"owner": human_actor_id(old), "version": 1}));
    assert_eq!(after, serde_json::json!({"owner": human_actor_id(next), "version": 2}));
}

#[tokio::test]
async fn retirement_before_the_transaction_returns_unchanged() {
    let gate = StdArc::new(HoldFirstTxGate::default());
    let db: StdArc<dyn DbPlugin> = StdArc::new(HoldFirstSqlite {
        inner: LocalSqliteDbPlugin::new().expect("local sqlite"),
        gate: gate.clone(),
    });
    let fixture =
        bcs_test_support::bot_authority_sqlite::new_fixture_with_db(ENV, db).await;
    let bot = "bot_sync_retirement_race";
    fixture.seed_bot(ENV, bot, 1).await;
    fixture.seed_human(ENV, "next").await;
    fixture.grant_edge(ENV, bot, "human_own", BotAccessRelation::Owner).await;
    let repo = fixture.authority_repo();
    let sync = tokio::spawn(async move {
        repo.sync_ownership(ownership_sync_command(bot, "next", "sync-retirement-race"))
            .await
    });
    gate.wait_until_held().await;
    // Commit the retirement's tombstone and edge revocations atomically.
    fixture.db.transaction(vec![
        DbTransactionStep::Execute(DbStatement::new(
            "UPDATE bcs_bots SET is_deleted = 1 WHERE actor_kind = 'bot'",
        )),
        DbTransactionStep::Execute(DbStatement::new(
            "UPDATE edge_grants SET status = 'revoked' \
             WHERE grant_kind IN ('owner', 'manager')",
        )),
    ]).await.expect("committed retirement");
    gate.release.store(true, Ordering::SeqCst);
    let result = sync.await.expect("sync task").expect("retired bot is a no-op");
    assert_eq!(result, bcs_service_api::types::bot_authority::OwnershipChange::unchanged(bot));
    assert!(fixture.audit_rows(ENV).await.is_empty());
}
