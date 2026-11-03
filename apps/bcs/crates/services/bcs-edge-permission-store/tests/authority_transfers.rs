//! Transfer write paths (`change_transfer`) against
//! [`DbBotAuthorityStore`] on SQLite (bot-authority plan
//! Task 3.1–3.6), running the SAME shared transfer
//! harness scenarios as the memory projection so the two
//! stores cannot drift (spec §6).
//!
//! Storage-level verification (owner uniqueness, version
//! growth only on accept, terminal/round audit shape) is
//! asserted directly against the SQL state below.
//!
//! The fixture DB mirrors `migrations/sqlite/037_bot_authority.sql`;
//! the Human registry the write paths validate targets
//! against is the `bcs_bots` actor rows (`actor_kind='human'`,
//! env-bound). MySQL runs the same store in CI (plan §6);
//! locally there is no MySQL server.

#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc as StdArc;

use async_trait::async_trait;

use bcs_db_api::{
    DbExecuteResult, DbHealth, DbPlugin, DbRow, DbStatement,
    DbTransactionOptions, DbTransactionStep, DbValue,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_edge_permission_store::DbBotAuthorityStore;
use bcs_service_api::port::repo::BotAuthorityRepoPort;
use bcs_service_api::types::bot_authority::{
    BotAccessRelation,
};
use bcs_test_support::contract::bot_authority_contract::{
    BotAuthorityContractFixture, run_bot_authority_contract,
};
use bcs_test_support::contract::bot_transfer_contract::{
    run_bot_transfer_contract, BotTransferContractFixture,
    HarnessTransferSeed,
};

const ENV: &str = "authority-env";
const OTHER_ENV: &str = "authority-env-other";

use bcs_test_support::bot_authority_sqlite::SqlAuthorityFixture;

async fn new_fixture() -> SqlAuthorityFixture {
    bcs_test_support::bot_authority_sqlite::new_fixture(ENV).await
}

async fn query(db: &dyn DbPlugin, sql: &str, params: Vec<DbValue>) -> Vec<DbRow> {
    db.query(DbStatement::with_params(sql, params))
        .await
        .expect("fixture query")
}

/// Deterministic first-two-transaction gate (review
/// Important#1): both racing commands must complete their
/// PRE-READS before either transaction runs, so the
/// create-path recompute after a guard failure is actually
/// exercised instead of being interleaving luck. Retries
/// (third+ entries) pass once both first entries registered.
#[derive(Default)]
struct TxGate {
    entries: AtomicUsize,
    entered: AtomicUsize,
    notify: tokio::sync::Notify,
}

impl TxGate {
    async fn enter(&self) {
        let index = self.entries.fetch_add(1, Ordering::SeqCst);
        if index >= 2 {
            // A recomputing retry: pass once both first
            // entries registered.
            while self.entered.load(Ordering::SeqCst) < 2 {
                tokio::task::yield_now().await;
            }
            return;
        }
        let entered = self.entered.fetch_add(1, Ordering::SeqCst) + 1;
        if entered == 2 {
            self.notify.notify_waiters();
            return;
        }
        while self.entered.load(Ordering::SeqCst) < 2 {
            self.notify.notified().await;
        }
    }
}

/// DbPlugin wrapper that gates the first two
/// `transaction_with_options` entries (everything else
/// passes straight through to the SQLite plugin).
struct GatedSqlite {
    inner: LocalSqliteDbPlugin,
    gate: StdArc<TxGate>,
}

#[async_trait]
impl DbPlugin for GatedSqlite {
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
        self.inner.transaction_with_options(steps, options).await
    }

    async fn health_check(&self) -> bcs_db_api::DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

/// Fixture whose store runs over the gated plugin.
async fn new_gated_fixture() -> SqlAuthorityFixture {
    let db: StdArc<dyn DbPlugin> = StdArc::new(GatedSqlite {
        inner: LocalSqliteDbPlugin::new().expect("local sqlite"),
        gate: StdArc::new(TxGate::default()),
    });
    bcs_test_support::bot_authority_sqlite::new_fixture_with_db(ENV, db).await
}

/// Review Important#1 regression: both same-target requests
/// race the stale (expired) row; the loser's classification
/// is stale by the time its transaction runs. The typed
/// per-row guard must fail that transaction so it
/// recomputes from a fresh pre-read — the audit trail must
/// contain NO system closure for the round that was never
/// stale (exactly one true `transfer_expire` for the
/// genuinely-expired row), and the fresh pending is reused
/// per normal same-target semantics.
#[tokio::test]
async fn create_race_on_stale_row_never_falsely_closes() {
    use bcs_test_support::contract::bot_transfer_contract::{
        human_mutation, next_mutation_id, transfer_cmd,
    };
    use bcs_service_api::types::bot_authority::{
        TransferAction, TransferChange, TransferOutcome,
    };

    let fixture = new_gated_fixture().await;
    let db = fixture.db.clone();
    fixture.seed_bot(ENV, "bt_race_stale", 1).await;
    fixture.seed_human(ENV, "own").await;
    fixture.seed_human(ENV, "t1").await;
    fixture
        .grant_edge(ENV, "bt_race_stale", "human_own", BotAccessRelation::Owner)
        .await;
    // The stale pending both racers classify against.
    fixture
        .seed_transfer(
            ENV,
            HarnessTransferSeed {
                bot_id: "bt_race_stale".to_string(),
                target_owner_actor_id: "human_t1".to_string(),
                from_owner_actor_id: "human_own".to_string(),
                expected_ownership_version: 1,
                status: bcs_service_api::types::bot_authority::TransferStatus::Pending,
                requested_at_offset_s: -8 * 24 * 3600,
                expires_at_offset_s: -3600,
                decided_at_offset_s: None,
                decided_by_kind: None,
                decided_by_id: None,
                reason: None,
                result_ownership_version: None,
            },
        )
        .await;
    let repo = fixture.authority_repo();
    let repo_a = repo.clone();
    let repo_b = repo.clone();

    let (first, second) = tokio::join!(
        repo_a.change_transfer(transfer_cmd(
            "bt_race_stale",
            "t1",
            TransferAction::RequestOrRefresh,
            human_mutation(next_mutation_id(), "own"),
        )),
        repo_b.change_transfer(transfer_cmd(
            "bt_race_stale",
            "t1",
            TransferAction::RequestOrRefresh,
            human_mutation(next_mutation_id(), "own"),
        )),
    );
    let outcome_a = first.expect("racing request A");
    let outcome_b = second.expect("racing request B");
    // 新鲜 pending 重用语义: the recompute replays as a
    // refresh; the genuine re-initiation created the round.
    let changes = [outcome_a, outcome_b]
        .into_iter()
        .map(|outcome| match outcome {
            TransferOutcome::Applied { change, .. } => change,
            other => panic!("racer must not conflict, got {other:?}"),
        })
        .collect::<Vec<_>>();
    assert!(
        changes.contains(&TransferChange::Created)
            && changes.contains(&TransferChange::Updated),
        "exactly one re-initiation + one refresh recompute:          got {changes:?}"
    );
    assert_eq!(changes.len(), 2);

    // Audit trail: NO false system closure for the never-
    // stale round — exactly one expire (the true closure of
    // the seeded stale row) and no invalidate at all.
    let rows = query(
        db.as_ref(),
        "SELECT action, COUNT(*) AS c FROM bot_authority_changes \
         WHERE env = ? AND bot_id = 'bt_race_stale' \
           AND actor_kind = 'system' \
         GROUP BY action",
        vec![DbValue::from(ENV)],
    )
    .await;
    let mut closures = std::collections::BTreeMap::new();
    for row in &rows {
        closures.insert(
            row.get_string("action").expect("action").unwrap(),
            row.get_i64("c").expect("c").unwrap(),
        );
    }
    assert_eq!(
        closures.get("transfer_expire"),
        Some(&1),
        "exactly one true closure, got {closures:?}"
    );
    assert_eq!(
        closures.get("transfer_invalidate"),
        None,
        "no invalidation closure may exist in this race: {closures:?}"
    );
}

/// The FULL shared read+manager harness (unchanged Task-1/2
/// semantics), then the shared transfer harness (Task 3).
#[tokio::test]
async fn authority_transfer_contract_sqlite() {
    let fixture = new_fixture().await;
    run_bot_authority_contract(&fixture, ENV, OTHER_ENV).await;
    run_bot_transfer_contract(&fixture, ENV).await;
}

async fn sql_scalar(
    db: &dyn DbPlugin,
    sql: &str,
    params: Vec<DbValue>,
) -> u64 {
    let rows = query(db, sql, params).await;
    rows.first()
        .map(|row| {
            row.get_i64("result")
                .expect("result col")
                .expect("value") as u64
        })
        .unwrap_or(0)
}

/// 3.6 direct-SQL verification: after two full accepted
/// rounds the owner edge is unique, the version grew exactly
/// by each accept (reset rows untouched otherwise), and the
/// terminal/round audits are exact.
#[tokio::test]
async fn accept_commits_owner_uniqueness_version_and_audits() {
    use bcs_service_api::types::bot_authority::{
        TransferAction, TransferChange, TransferOutcome,
    };
    use bcs_test_support::contract::bot_transfer_contract::{
        human_mutation, transfer_cmd, next_mutation_id,
    };

    let fixture = new_fixture().await;
    let db = fixture.db.clone();
    fixture.seed_bot(ENV, "bot_sql", 1).await;
    fixture.seed_human(ENV, "own").await;
    fixture.seed_human(ENV, "t1").await;
    fixture.seed_human(ENV, "t2").await;
    fixture
        .grant_edge(ENV, "bot_sql", "human_own", BotAccessRelation::Owner)
        .await;
    let repo = fixture.authority_repo();

    // Round 1: own → t1, accepted.
    let outcome = repo
        .change_transfer(transfer_cmd(
            "bot_sql",
            "t1",
            TransferAction::RequestOrRefresh,
            human_mutation(next_mutation_id(), "own"),
        ))
        .await
        .expect("round 1 request");
    assert!(matches!(
        outcome,
        TransferOutcome::Applied { change: TransferChange::Created, .. }
    ));
    let outcome = repo
        .change_transfer(transfer_cmd(
            "bot_sql",
            "t1",
            TransferAction::Accept,
            human_mutation(next_mutation_id(), "t1"),
        ))
        .await
        .expect("round 1 accept");
    assert!(matches!(outcome, TransferOutcome::Applied { .. }));

    // Owner uniqueness (guarded by the partial unique index,
    // verified directly): exactly one approved owner edge and
    // it is the accepted target.
    assert_eq!(
        sql_scalar(
            db.as_ref(),
            "SELECT COUNT(*) AS result FROM edge_grants \
             WHERE env = ? AND to_id = 'bot_sql' \
               AND grant_kind = 'owner' AND grant_ref_id = 0 \
               AND rules IS NULL AND status = 'approved'",
            vec![DbValue::from(ENV)],
        )
        .await,
        1,
    );
    assert_eq!(
        sql_scalar(
            db.as_ref(),
            "SELECT COUNT(*) AS result FROM edge_grants \
             WHERE env = ? AND to_id = 'bot_sql' \
               AND grant_kind = 'owner' AND status = 'approved' \
               AND from_id = 'human_t1'",
            vec![DbValue::from(ENV)],
        )
        .await,
        1,
    );
    assert_eq!(
        sql_scalar(
            db.as_ref(),
            "SELECT ownership_version AS result FROM bcs_bots \
             WHERE bot_uuid = 'bot_sql' AND env = ?",
            vec![DbValue::from(ENV)],
        )
        .await,
        2,
        "version grew exactly once for the first accept"
    );
    // The previous owner became a normal manager.
    assert_eq!(
        sql_scalar(
            db.as_ref(),
            "SELECT COUNT(*) AS result FROM edge_grants \
             WHERE env = ? AND to_id = 'bot_sql' \
               AND grant_kind = 'manager' AND status = 'approved' \
               AND from_id = 'human_own'",
            vec![DbValue::from(ENV)],
        )
        .await,
        1,
    );
    assert_eq!(
        sql_scalar(
            db.as_ref(),
            "SELECT COUNT(*) AS result FROM edge_grants \
             WHERE env = ? AND to_id = 'bot_sql' \
               AND grant_kind = 'manager' AND status = 'approved' \
               AND from_id = 'human_t1'",
            vec![DbValue::from(ENV)],
        )
        .await,
        0,
        "the new owner's manager edge was removed"
    );

    // The accepted row: decision + result version persisted.
    let rows = query(
        db.as_ref(),
        "SELECT status, decided_by_kind, decided_by_id, \
                result_ownership_version \
         FROM bot_ownership_transfers \
         WHERE env = ? AND bot_id = 'bot_sql' \
           AND target_owner_actor_id = 'human_t1'",
        vec![DbValue::from(ENV)],
    )
    .await;
    assert_eq!(rows.len(), 1, "one row per target");
    let row = &rows[0];
    assert_eq!(row.get_string("status").unwrap().as_deref(), Some("accepted"));
    assert_eq!(
        row.get_string("decided_by_kind").unwrap().as_deref(),
        Some("human")
    );
    assert_eq!(
        row.get_string("decided_by_id").unwrap().as_deref(),
        Some("human_t1")
    );
    assert_eq!(row.get_i64("result_ownership_version").unwrap(), Some(2));

    // Round 2: t1 → t2, accepted → version 3.
    repo.change_transfer(transfer_cmd(
        "bot_sql",
        "t2",
        TransferAction::RequestOrRefresh,
        human_mutation(next_mutation_id(), "t1"),
    ))
    .await
    .expect("round 2 request");
    repo.change_transfer(transfer_cmd(
        "bot_sql",
        "t2",
        TransferAction::Accept,
        human_mutation(next_mutation_id(), "t2"),
    ))
    .await
    .expect("round 2 accept");
    assert_eq!(
        sql_scalar(
            db.as_ref(),
            "SELECT ownership_version AS result FROM bcs_bots \
             WHERE bot_uuid = 'bot_sql' AND env = ?",
            vec![DbValue::from(ENV)],
        )
        .await,
        3,
        "each accepted round increments by exactly one"
    );

    // Terminal/round audits are exact: two accepts each
    // recorded with their round versions.
    let audit = query(
        db.as_ref(),
        "SELECT action, actor_kind, subject_id, after_state \
         FROM bot_authority_changes \
         WHERE env = ? AND bot_id = 'bot_sql' AND action LIKE 'transfer_%' \
         ORDER BY id",
        vec![DbValue::from(ENV)],
    )
    .await;
    let accepts: Vec<(String, String)> = audit
        .iter()
        .filter(|row| row.get_string("action").unwrap().as_deref() == Some("transfer_accept"))
        .map(|row| {
            (
                row.get_string("subject_id").unwrap().unwrap(),
                row.get_string("after_state").unwrap().unwrap(),
            )
        })
        .collect();
    assert_eq!(
        accepts,
        vec![
            (
                "human_t1".to_string(),
                "{\"status\":\"accepted\",\"from_owner\":\"human_t1\",\"version\":2}".to_string()
            ),
            (
                "human_t2".to_string(),
                "{\"status\":\"accepted\",\"from_owner\":\"human_t2\",\"version\":3}".to_string()
            ),
        ],
        "each accept audit carries its round target + result version"
    );
}

/// 3.6 direct-SQL verification: a cancelled round leaves
/// `ownership_version` untouched; a refresh updates only the
/// transfer row's clock fields.
#[tokio::test]
async fn version_stays_at_zero_growth_without_accept() {
    use bcs_service_api::types::bot_authority::{TransferAction};
    use bcs_test_support::contract::bot_transfer_contract::{
        human_mutation, transfer_cmd, next_mutation_id,
    };

    let fixture = new_fixture().await;
    let db = fixture.db.clone();
    fixture.seed_bot(ENV, "bot_nover", 1).await;
    fixture.seed_human(ENV, "own").await;
    fixture.seed_human(ENV, "t1").await;
    fixture
        .grant_edge(ENV, "bot_nover", "human_own", BotAccessRelation::Owner)
        .await;
    let repo = fixture.authority_repo();

    for action in [
        TransferAction::RequestOrRefresh,
        TransferAction::Reject,
        TransferAction::RequestOrRefresh,
        TransferAction::Cancel,
    ] {
        let operator = match action {
            TransferAction::RequestOrRefresh | TransferAction::Cancel => "own",
            _ => "t1",
        };
        let _ = repo
            .change_transfer(transfer_cmd(
                "bot_nover",
                "t1",
                action,
                human_mutation(next_mutation_id(), operator),
            ))
            .await;
    }
    assert_eq!(
        sql_scalar(
            db.as_ref(),
            "SELECT ownership_version AS result FROM bcs_bots \
             WHERE bot_uuid = 'bot_nover' AND env = ?",
            vec![DbValue::from(ENV)],
        )
        .await,
        1,
        "reject/cancel/refresh never touch the ownership version"
    );
    let transfer_audits = query(
        db.as_ref(),
        "SELECT action, actor_kind, actor_id, subject_id \
         FROM bot_authority_changes \
         WHERE env = ? AND bot_id = 'bot_nover' AND action LIKE 'transfer_%' \
         ORDER BY id",
        vec![DbValue::from(ENV)],
    )
    .await;
    let actions: Vec<String> = transfer_audits
        .iter()
        .map(|row| row.get_string("action").unwrap().unwrap())
        .collect();
    assert_eq!(
        actions,
        vec![
            "transfer_request".to_string(),
            "transfer_reject".to_string(),
            "transfer_request".to_string(),
            "transfer_cancel".to_string(),
        ],
        "terminal/round audits stay exact (creator operator, target subject)"
    );
    assert_eq!(
        transfer_audits[3]
            .get_string("actor_id")
            .unwrap()
            .as_deref(),
        Some("human_own"),
        "the cancel decision records the real initiator"
    );
    assert_eq!(
        transfer_audits[3]
            .get_string("subject_id")
            .unwrap()
            .as_deref(),
        Some("human_t1"),
        "the transfer audit subject is the target"
    );
}

// ---------------------------------------------------------------------------
// Review P2-3 regression (PR #2625): an Applied transfer response
// must carry the state its own transaction committed. The
// (bot, target) record is reused by later rounds, so before the
// fix the post-commit re-read could assemble the response from a
// racing NEXT round's pending row. This wrapper interleaves a
// same-target next round deterministically — after the reject's
// decision commit, before its response assembly.
// ---------------------------------------------------------------------------

/// DbPlugin wrapper that, exactly once, opens a competing
/// same-target transfer round after the first successful
/// transaction it observes (the decision commit) and before
/// the original caller resumes.
struct InterleaveAfterFirstCommit {
    inner: StdArc<dyn DbPlugin>,
    fired: AtomicUsize,
}

#[async_trait]
impl DbPlugin for InterleaveAfterFirstCommit {
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
        let result = self
            .inner
            .transaction_with_options(steps, options)
            .await?;
        if result.iter().all(|_| true)
            && self.fired.swap(1, Ordering::SeqCst) == 0
        {
            // The rejection has COMMITTED. An independent owner
            // request starts the next round for the same
            // (bot, target) — only then does the rejected
            // caller resume assembling its response.
            let concurrent = DbBotAuthorityStore::new(
                self.inner.clone(),
                bcs_db_api::DbSqlFlavor::Sqlite,
                ENV.to_string(),
            );
            concurrent
                .change_transfer(bcs_test_support::contract::bot_transfer_contract::transfer_cmd(
                    "bt_response_race",
                    "t1",
                    bcs_service_api::types::bot_authority::TransferAction::RequestOrRefresh,
                    bcs_test_support::contract::bot_transfer_contract::human_mutation(
                        bcs_test_support::contract::bot_transfer_contract::next_mutation_id(),
                        "own",
                    ),
                ))
                .await
                .expect("the competing next-round request commits");
        }
        Ok(result)
    }

    async fn health_check(&self) -> bcs_db_api::DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

#[tokio::test]
async fn decision_response_carries_its_own_committed_state() {
    use bcs_test_support::contract::bot_transfer_contract::{
        human_mutation, next_mutation_id, transfer_cmd,
    };
    use bcs_service_api::types::bot_authority::{
        TransferAction, TransferChange, TransferKey, TransferOutcome,
        TransferStatus, human_actor_id,
    };

    let fixture = new_fixture().await;
    fixture.seed_bot(ENV, "bt_response_race", 1).await;
    fixture
        .grant_edge(ENV, "bt_response_race", "human_own", BotAccessRelation::Owner)
        .await;
    fixture.seed_human(ENV, "t1").await;
    // Round 1 opens through the plain store, BEFORE the plugin
    // is wrapped (the wrapper must observe the decision as its
    // first transaction).
    fixture
        .store
        .change_transfer(transfer_cmd(
            "bt_response_race",
            "t1",
            TransferAction::RequestOrRefresh,
            human_mutation(next_mutation_id(), "own"),
        ))
        .await
        .expect("round 1 opens");

    let wrapped: StdArc<dyn DbPlugin> = StdArc::new(InterleaveAfterFirstCommit {
        inner: fixture.db.clone(),
        fired: AtomicUsize::new(0),
    });
    let store = DbBotAuthorityStore::new(
        wrapped,
        bcs_db_api::DbSqlFlavor::Sqlite,
        ENV.to_string(),
    );
    let outcome = store
        .change_transfer(transfer_cmd(
            "bt_response_race",
            "t1",
            TransferAction::Reject,
            human_mutation(next_mutation_id(), "t1"),
        ))
        .await
        .expect("the target rejects round 1");
    match outcome {
        TransferOutcome::Applied { transfer, change } => {
            assert_eq!(
                transfer.status,
                TransferStatus::Rejected,
                "the reject response must carry its OWN committed terminal \
                 state, not the interleaved next round's pending"
            );
            assert!(
                transfer.decided_at.is_some(),
                "a decided response keeps its decision timestamp"
            );
            assert_eq!(change, TransferChange::Updated);
        }
        other => panic!("expected an applied rejection, got {other:?}"),
    }

    // The interleave really did land: a fresh pending round now
    // holds the record — the fix is not vacuous.
    let row = fixture
        .store
        .transfer(&TransferKey {
            bot_id: "bt_response_race".to_string(),
            target_owner_actor_id: human_actor_id("t1"),
        })
        .await
        .expect("row read")
        .expect("the reused record exists");
    assert_eq!(
        row.status,
        TransferStatus::Pending,
        "the competing request reused the same-target record"
    );
}
