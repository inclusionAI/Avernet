//! SQL lifecycle composites (bot-authority plan Task 4)
//! on the SQLite store — the thin local driver.
//!
//! The scenario bodies live in `common/authority_lifecycle.rs`
//! and are also driven against the MySQL store over the FULL
//! MySQL migration chain by the plan §6 gate
//! (`authority_mysql.rs`). This file keeps the SQLite
//! entrypoints and the dialect-pin lock-order tests.
//!
//! The REAL composite transaction over the real schema is
//! driven with one deterministic infrastructure-failure
//! injection at a time; post-state touches the same
//! database: NO half registration, NO owner edge, NO
//! profile, NO audit, NO token mapping and NO runtime
//! activation after any failure.

#![allow(clippy::expect_used, clippy::unwrap_used)]

#[path = "common/authority_lifecycle.rs"]
mod lifecycle;

use std::sync::{Arc, Mutex};

use bcs_db_api::{
    DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow,
    DbStatement, DbTransactionOptions, DbTransactionStep,
    DbTransactionStepResult,
};
use bcs_bot_store::{DbProviderStore, PersistentBotRepo};
use bcs_service_api::core::provider::{
    ProviderRetire, ProviderRetireBranch,
};
use bcs_service_api::port::repo::{
    bot_provider::BotProviderRepoPort, BotRepoPort,
};

use lifecycle::{SCHEMA,resolve_env,retire_mutation_provider,retire_mutation_human};
#[tokio::test]
async fn trusted_v1_registration_commits_the_whole_state() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::trusted_v1_registration_commits_the_whole_state(&fixture).await;
}

#[tokio::test]
async fn trusted_v1_reused_bot_id_never_resets_owner() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::trusted_v1_reused_bot_id_never_resets_owner(&fixture).await;
}

#[tokio::test]
async fn trusted_v1_tombstoned_bot_id_never_resurrects() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::trusted_v1_tombstoned_bot_id_never_resurrects(&fixture).await;
}

#[tokio::test]
async fn trusted_v1_registration_refuses_a_tombstoned_owner() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::trusted_v1_registration_refuses_a_tombstoned_owner(&fixture).await;
}

// Injected failure points (4.1): owner write, profile write and
// audit write abort the WHOLE transaction.
#[tokio::test]
async fn owner_edge_failure_rolls_back_everything() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::owner_edge_failure_rolls_back_everything(&fixture).await;
}

#[tokio::test]
async fn profile_failure_rolls_back_everything() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::profile_failure_rolls_back_everything(&fixture).await;
}

#[tokio::test]
async fn audit_failure_rolls_back_everything() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::audit_failure_rolls_back_everything(&fixture).await;
}

#[tokio::test]
async fn human_actor_write_failure_rolls_back_everything() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::human_actor_write_failure_rolls_back_everything(&fixture).await;
}

/// Ruling T4-1 acceptance (spec §10.2 literal): a
/// fresh-Human registration creates the owner's Human
/// ACTOR row in the SAME transaction, and that owner can
/// afterwards pass the Task-2 human-existence guards (a
/// manager grant by that owner succeeds). The OAuth
/// identity table is never touched.
#[tokio::test]
async fn registration_creates_the_owner_actor_row_and_the_owner_can_manage() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::registration_creates_the_owner_actor_row_and_the_owner_can_manage(&fixture).await;
}

#[tokio::test]
async fn mutation_must_match_the_verified_initiator() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::mutation_must_match_the_verified_initiator(&fixture).await;
}

#[tokio::test]
async fn governance_initialize_backfills_once_and_is_idempotent() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::governance_initialize_backfills_once_and_is_idempotent(&fixture).await;
}

#[tokio::test]
async fn governance_initialize_refuses_missing_and_deleted_bots() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::governance_initialize_refuses_missing_and_deleted_bots(&fixture).await;
}

#[tokio::test]
async fn governance_initialize_refuses_creator_mismatch() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::governance_initialize_refuses_creator_mismatch(&fixture).await;
}

#[tokio::test]
async fn governance_initialize_never_targets_a_human_actor_row() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::governance_initialize_never_targets_a_human_actor_row(&fixture).await;
}

#[tokio::test]
async fn trusted_v2_registration_commits_binding_owner_and_authority() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::trusted_v2_registration_commits_binding_owner_and_authority(&fixture).await;
}

#[tokio::test]
async fn trusted_v2_binding_failure_rolls_back_everything() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::trusted_v2_binding_failure_rolls_back_everything(&fixture).await;
}

#[tokio::test]
async fn trusted_v2_owner_write_failure_rolls_back_everything() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::trusted_v2_owner_write_failure_rolls_back_everything(&fixture).await;
}

#[tokio::test]
async fn committed_registration_does_not_activate_runtime() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::committed_registration_does_not_activate_runtime(&fixture).await;
}

#[tokio::test]
async fn provider_retire_metadata_branch_commits_the_whole_state() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::provider_retire_metadata_branch_commits_the_whole_state(&fixture).await;
}

#[tokio::test]
async fn provider_retire_failure_is_atomic() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::provider_retire_failure_is_atomic(&fixture).await;
}

#[tokio::test]
async fn provider_retire_retry_is_idempotent_without_duplicate_audit() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::provider_retire_retry_is_idempotent_without_duplicate_audit(&fixture).await;
}

#[tokio::test]
async fn provider_retire_binding_fallback_branch_disables_binding() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::provider_retire_binding_fallback_branch_disables_binding(&fixture).await;
}

#[tokio::test]
async fn provider_retire_legacy_unbound_and_branch_mismatch() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::provider_retire_legacy_unbound_and_branch_mismatch(&fixture).await;
}

#[tokio::test]
async fn normal_retire_revokes_authority_and_never_chains_provider() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::normal_retire_revokes_authority_and_never_chains_provider(&fixture).await;
}

/// A retired Bot NEVER resurrects through the lifecycle
/// write paths: the governance backfill refuses tombstoned
/// IDs (already for pre-authority tombstones); the retire
/// tombstone gets the same refusal, and creation stays
/// create-only. The Human-deletion full-flow competition
/// (deleting a Human that still owns live Bots) is Task 6;
/// this pins the same "no approved owner edge to a dying
/// identity" store invariant on the Bot side.
#[tokio::test]
async fn retired_bot_id_never_resurrects_through_lifecycle_writes() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::retired_bot_id_never_resurrects_through_lifecycle_writes(&fixture).await;
}

/// The retire ↔ accept barrier (spec §6.3: accept and Bot
/// retirement serialize on the same Bot row): the retirement
/// closes the pending first; the later accept decision sees
/// terminal state and refuses — one side wins, the other
/// observes, no half-way resurrection of the pending.
#[tokio::test]
async fn retire_serializes_against_the_transfer_accept() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::retire_serializes_against_the_transfer_accept(&fixture).await;
}

// Review Finding #8: the pending closure identity is
// read inside the transaction (deterministic one-shot
// transaction-hook race drivers).

#[tokio::test]
async fn retire_closes_the_pending_identity_seen_in_tx() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::retire_closes_the_pending_identity_seen_in_tx(&fixture).await;
}

#[tokio::test]
async fn provider_retire_closes_the_pending_identity_seen_in_tx() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::provider_retire_closes_the_pending_identity_seen_in_tx(&fixture).await;
}

#[tokio::test]
async fn retire_retries_when_the_pending_vanishes_between_preread_and_tx() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::retire_retries_when_the_pending_vanishes_between_preread_and_tx(&fixture).await;
}

#[tokio::test]
async fn retire_with_no_pendings_writes_no_closure_audit() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::retire_with_no_pendings_writes_no_closure_audit(&fixture).await;
}

// ---------------------------------------------------------------------------
// Dialect pin (spec §7 统一锁序): the retirement composites'
// MySQL flavor steps must carry REAL row locks on the
// binding row, the Bot row and the pending rows — plain
// Query steps are snapshot reads in the plugin runner,
// which would silently invert the unified binding → Bot →
// transfer order on MySQL. SQLite keeps no suffix
// (single-writer); the tests pin the SHAPE of the composed
// steps, real MySQL execution stays CI-gated.
// ---------------------------------------------------------------------------

struct RecordingTransactionsDb {
    inner: Arc<dyn DbPlugin>,
    recorded: Mutex<Vec<Vec<DbTransactionStep>>>,
}

#[async_trait::async_trait]
impl DbPlugin for RecordingTransactionsDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.inner.query(statement).await
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.inner.execute(statement).await
    }

    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.inner.transaction(steps).await
    }

    async fn transaction_with_options(
        &self,
        steps: Vec<DbTransactionStep>,
        options: DbTransactionOptions,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.recorded
            .lock()
            .unwrap()
            .push(steps.clone());
        self.inner.transaction_with_options(steps, options).await
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

fn recorded_sql(steps: &[DbTransactionStep]) -> Vec<String> {
    steps
        .iter()
        .map(|step| match step {
            DbTransactionStep::Query(statement)
            | DbTransactionStep::Execute(statement) => {
                statement.sql().to_string()
            }
            DbTransactionStep::ExecuteChecked {
                statement, ..
            } => statement.sql().to_string(),
        })
        .collect()
}

#[tokio::test]
async fn mysql_provider_retire_steps_carry_the_for_update_lock_order() {
    let inner = Arc::new(
        bcs_db_local::LocalSqliteDbPlugin::new().expect("sqlite"),
    );
    for statement in SCHEMA {
        inner
            .execute(DbStatement::new(*statement))
            .await
            .expect("fixture schema");
    }
    // Seed the durable rows with plain SQL so the mysql-
    // flavored composite reaches its transaction (the
    // FOR UPDATE suffix intentionally fails on SQLite —
    // only the SHAPE matters here).
    inner
        .execute(DbStatement::with_params(
            "INSERT INTO bcs_bots (bot_uuid, env, name, is_deleted) \
             VALUES ('bot_lock', ?, 'x', 0)",
            vec![resolve_env().as_str().into()],
        ))
        .await
        .expect("seed bot");
    inner
        .execute(DbStatement::with_params(
            "INSERT INTO bcs_provider_bot_bindings \
             (bot_uuid, env, provider_id, provider_bot_ref, disabled) \
             VALUES ('bot_lock', ?, 'provider-a', 'lock-ref', 0)",
            vec![resolve_env().as_str().into()],
        ))
        .await
        .expect("seed binding");
    let db = Arc::new(RecordingTransactionsDb {
        inner,
        recorded: Mutex::new(Vec::new()),
    });
    let provider_repo = DbProviderStore::mysql(db.clone());
    let outcome = provider_repo
        .retire_with_authority(ProviderRetire {
            provider_id: "provider-a".into(),
            bot_id: "bot_lock".into(),
            provider_bot_ref: "lock-ref".into(),
            mutation: retire_mutation_provider("provider-a"),
            expected_branch: ProviderRetireBranch::BindingFallback,
        })
        .await;
    if outcome.is_err() {
        eprintln!("provider retire (expected on sqlite): {outcome:?}");
    }
    let first = db
        .recorded
        .lock()
        .unwrap()
        .first()
        .cloned()
        .expect("the composite composed an attempt");
    let sqls = recorded_sql(&first);
    // Step 0: the binding row lock — BEFORE the Bot row
    // (binding → Bot → transfer).
    assert!(
        sqls[0].contains("bcs_provider_bot_bindings")
            && sqls[0].contains("FOR UPDATE"),
        "the binding lock must be a row lock: {}",
        sqls[0]
    );
    // Step 1: the Bot row membership guard — the FOR UPDATE
    // suffix (Task-3 count-guard style) takes the Bot row
    // locks INTO the transaction.
    assert!(
        sqls[1].contains("bcs_bots") && sqls[1].contains("FOR UPDATE"),
        "the Bot row lock must be a row lock: {}",
        sqls[1]
    );
    // The pending locks follow the Bot row.
    assert!(
        sqls.iter()
            .any(|sql| sql.contains("bot_ownership_transfers")
                && sql.contains("FOR UPDATE")),
        "the pending transfers must be locked after the Bot row"
    );
}

#[tokio::test]
async fn mysql_ordinary_retire_steps_carry_the_for_update_lock_order() {
    let inner = Arc::new(
        bcs_db_local::LocalSqliteDbPlugin::new().expect("sqlite"),
    );
    for statement in SCHEMA {
        inner
            .execute(DbStatement::new(*statement))
            .await
            .expect("fixture schema");
    }
    inner
        .execute(DbStatement::with_params(
            "INSERT INTO bcs_bots (bot_uuid, env, name, is_deleted, ownership_version) \
             VALUES ('bot_lock2', ?, 'x', 0, 1)",
            vec![resolve_env().as_str().into()],
        ))
        .await
        .expect("seed bot");
    // The ordinary retirement 事务内复验s the Human operator's
    // current owner/manager edge on an authority-INITIALIZED
    // Bot (spec §7): seed one so the composite reaches its
    // statement sequence.
    inner
        .execute(DbStatement::with_params(
            "INSERT INTO edge_grants \
             (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
              originator_policy_type, originator_policy_data) \
             VALUES (?, 'human_11111111', 'bot_lock2', 'owner', 0, NULL, \
                     'approved', 'same_as_from', NULL)",
            vec![resolve_env().as_str().into()],
        ))
        .await
        .expect("seed operator edge");
    let db = Arc::new(RecordingTransactionsDb {
        inner,
        recorded: Mutex::new(Vec::new()),
    });
    let bot_repo = PersistentBotRepo::with_sql_flavor(
        db.clone(),
        bcs_db_api::DbSqlFlavor::Mysql,
    );
    let outcome = bot_repo
        .retire_with_authority(
            "bot_lock2",
            retire_mutation_human("11111111"),
        )
        .await;
    if outcome.is_err() {
        eprintln!("ordinary retire (expected on sqlite): {outcome:?}");
    }
    let first = db
        .recorded
        .lock()
        .unwrap()
        .first()
        .cloned()
        .expect("the composite composed an attempt");
    let sqls = recorded_sql(&first);
    // Step 0: the Bot row lock; step 1: the membership
    // guard under the same locks; the pending locks follow.
    assert!(
        sqls[0].contains("bcs_bots") && sqls[0].contains("FOR UPDATE"),
        "the Bot row lock must be a row lock: {}",
        sqls[0]
    );
    assert!(
        sqls[1].contains("bcs_bots") && sqls[1].contains("FOR UPDATE"),
        "the membership guard must take the Bot row locks: {}",
        sqls[1]
    );
    // Step 2: the operator-authority guard (spec §7
    // 事务内复验) locks under the same row guards.
    assert!(
        sqls[2].contains("edge_grants") && sqls[2].contains("FOR UPDATE"),
        "the operator-authority guard must lock with the row guards: {}",
        sqls[2]
    );
    assert!(
        sqls.iter()
            .any(|sql| sql.contains("bot_ownership_transfers")
                && sql.contains("FOR UPDATE")),
        "the pending transfers must be locked after the Bot row"
    );
}

/// The operator's owner/manager edge is re-verified INSIDE the
/// retirement transaction (spec §7 事务内复验): a Human whose
/// edge was revoked after any application-time check can
/// neither tombstone the Bot nor write the retire audit, while
/// a still-authorized owner retires, and the already-tombstoned
/// retry keeps its idempotent no-op outcome.
#[tokio::test]
async fn sqlite_retirement_rechecks_the_operator_edge_in_transaction() {
    let db =
        Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().expect("sqlite"));
    for statement in SCHEMA {
        db.execute(DbStatement::new(*statement))
            .await
            .expect("fixture schema");
    }
    let env = resolve_env();
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid, env, name, is_deleted, ownership_version) \
         VALUES ('bot_r1', ?, 'x', 0, 1)",
        vec![env.as_str().into()],
    ))
    .await
    .expect("seed bot");
    for (from, kind) in [("human_own", "owner"), ("human_alice", "manager")] {
        db.execute(DbStatement::with_params(
            "INSERT INTO edge_grants \
             (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
              originator_policy_type, originator_policy_data) \
             VALUES (?, ?, 'bot_r1', ?, 0, NULL, 'approved', 'same_as_from', NULL)",
            vec![env.as_str().into(), from.into(), kind.into()],
        ))
        .await
        .expect("seed edge");
    }
    let bot_repo =
        PersistentBotRepo::with_sql_flavor(db.clone(), bcs_db_api::DbSqlFlavor::Sqlite);

    // The manager lost their edge AFTER any pre-check could
    // have run: the transaction must refuse the tombstone.
    db.execute(DbStatement::new(
        "UPDATE edge_grants SET status = 'revoked' \
         WHERE from_id = 'human_alice' AND to_id = 'bot_r1'",
    ))
    .await
    .expect("revoke the manager edge");
    let outcome = bot_repo
        .retire_with_authority("bot_r1", retire_mutation_human("alice"))
        .await;
    assert!(
        matches!(
            outcome,
            Err(bcs_service_api::ServiceError::Forbidden(_))
        ),
        "a revoked operator gets 403, got {outcome:?}"
    );
    let rows = db
        .query(DbStatement::new(
            "SELECT is_deleted FROM bcs_bots WHERE bot_uuid = 'bot_r1'",
        ))
        .await
        .expect("read bot after forbidden retirement");
    assert_eq!(
        rows[0].get_i64("is_deleted").expect("flag"),
        Some(0),
        "a revoked operator must never commit the tombstone"
    );

    // The still-authorized owner retires: changed = true, and
    // the retry is the idempotent no-op (never a second audit).
    let changed = bot_repo
        .retire_with_authority("bot_r1", retire_mutation_human("own"))
        .await
        .expect("owner retirement");
    assert!(changed, "owner transition reports changed");
    let rows = db
        .query(DbStatement::new(
            "SELECT is_deleted FROM bcs_bots WHERE bot_uuid = 'bot_r1'",
        ))
        .await
        .expect("read bot after owner retirement");
    assert_eq!(
        rows[0].get_i64("is_deleted").expect("flag"),
        Some(1)
    );
    let again = bot_repo
        .retire_with_authority("bot_r1", retire_mutation_human("stranger"))
        .await
        .expect("idempotent retry");
    assert!(
        !again,
        "already-tombstoned retry reports no change even for a stranger"
    );
    let audits = db
        .query(DbStatement::new(
            "SELECT COUNT(*) AS c FROM bot_authority_changes \
             WHERE bot_id = 'bot_r1' AND action = 'retire'",
        ))
        .await
        .expect("count retire audits");
    assert_eq!(
        audits[0].get_i64("c").expect("count"),
        Some(1),
        "exactly one retire audit row"
    );
}

/// A runtime version-0 Bot carries no owner/manager register:
/// the ordinary lifecycle delete keeps its legacy contract,
/// audit included, and no operator edge is demanded.
#[tokio::test]
async fn sqlite_retirement_of_version0_runtime_keeps_working() {
    let db =
        Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().expect("sqlite"));
    for statement in SCHEMA {
        db.execute(DbStatement::new(*statement))
            .await
            .expect("fixture schema");
    }
    let env = resolve_env();
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid, env, name, is_deleted, ownership_version) \
         VALUES ('bot_v0', ?, 'x', 0, 0)",
        vec![env.as_str().into()],
    ))
    .await
    .expect("seed runtime bot");
    let bot_repo =
        PersistentBotRepo::with_sql_flavor(db.clone(), bcs_db_api::DbSqlFlavor::Sqlite);

    let changed = bot_repo
        .retire_with_authority("bot_v0", retire_mutation_human("11111111"))
        .await
        .expect("version-0 runtime retire");
    assert!(changed);
    let audits = db
        .query(DbStatement::new(
            "SELECT COUNT(*) AS c FROM bot_authority_changes \
             WHERE bot_id = 'bot_v0' AND action = 'retire'",
        ))
        .await
        .expect("count retire audits");
    assert_eq!(
        audits[0].get_i64("c").expect("count"),
        Some(1),
        "the version-0 transition audits its real Human initiator"
    );
}

#[tokio::test]
async fn uninitialized_owned_bots_prefilter_in_the_store() {
    let fixture = lifecycle::new_sqlite_fixture().await;
    lifecycle::uninitialized_owned_bots_prefilter_in_the_store(&fixture).await;
}
