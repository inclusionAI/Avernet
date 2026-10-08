//! Plan Task 8: the ownership transfer state machine and its atomic
//! acceptance — the test binary entry. The shared drivers (SQLite over the
//! FULL migration chain, Memory via `bcs_bot_store::MemoryBotRepo`), the
//! deterministic drift/counting plugin and the shared case suites live in
//! `ownership_transfer_common`; this file owns the case-suite runs
//! (SQLite + Memory) and the SQLite-only SQL-level proofs (statement
//! budgets, in-transaction drift, raw row/edge facts, the exact DB-clock
//! boundary). The MySQL live unique-key race lives, #[ignore]d, in crate
//! `bcs` (`bot_ownership_transfer_mysql.rs`) next to the Task 2 MySQL
//! conformance.

#[path = "ownership_transfer_common/mod.rs"]
mod common;

use bcs_db_api::{DbStatement, DbValue};
use bcs_domain::{TransferAction, TransferStatus};
use bcs_service_api::types::ownership_transfer::{
    CommittedTransferOutcome, ListOwnershipTransfers, TransferListDirection,
};

use common::suite::{expect_forbidden, fresh_key};
use common::{create_with_key, sqlite_harness};

#[tokio::test]
async fn sqlite_ownership_transfer_contract() {
    let (h, _) = sqlite_harness().await;
    common::suite::transferred_ownership_contract_tests(&h).await;
}

#[tokio::test]
async fn memory_ownership_transfer_contract() {
    let h = common::memory_harness().await;
    common::suite::transferred_ownership_contract_tests(&h).await;
}

// ---------------------------------------------------------------------------
// SQLite-specific proofs: raw row facts, in-transaction drift guards,
// statement budgets and the exact DB-clock boundary
// ---------------------------------------------------------------------------

/// OT02's raw edge facts (SQLite only — the memory twin keeps its rows
/// crate-private): the accepted transfer revokes the recipient's direct
/// and ownership_transfer sources but leaves the team row approved.
#[tokio::test]
async fn sqlite_ot02_raw_edge_facts_and_decided_at() {
    let (h, db) = sqlite_harness().await;
    h.seed_owned("bot-raw", "a").await;
    h.seed_human("b").await;
    h.seed_manager_source("bot-raw", "b", "team", "team-1").await;
    h.seed_manager_source("bot-raw", "b", "direct", "manual").await;
    h.seed_manager_source("bot-raw", "b", "ownership_transfer", "ot-seed").await;

    let created = h
        .repo
        .create_transfer(create_with_key("a", "bot-raw", "b", 1, &fresh_key()))
        .await
        .unwrap();
    h.repo
        .decide_transfer("b", &created.receipt.transfer_id, TransferAction::Accept)
        .await
        .unwrap();

    assert_eq!(
        h.driver.manager_edge_status("bot-raw", "b", "team", "team-1").await,
        Some("approved".to_string()),
        "the team source survives the acceptance"
    );
    assert_eq!(
        h.driver.manager_edge_status("bot-raw", "b", "direct", "manual").await,
        Some("revoked".to_string()),
        "the direct source is revoked with the acceptance"
    );
    assert_eq!(
        h.driver.manager_edge_status("bot-raw", "b", "ownership_transfer", "ot-seed").await,
        Some("revoked".to_string()),
        "the old transfer source is revoked with the acceptance"
    );
    // The previous owner's `ownership_transfer/<tid>` source exists,
    // approved, with the transfer's own id as the source id.
    assert_eq!(
        h.driver
            .manager_edge_status("bot-raw", "a", "ownership_transfer", &created.receipt.transfer_id)
            .await,
        Some("approved".to_string()),
        "the previous owner's transfer-source manager edge is written by the acceptance"
    );
    // Decided with the real DB clock: decided_at >= the row's gmt_create.
    let rows = db
        .inner
        .query(DbStatement::with_params(
            "SELECT decided_at, gmt_create FROM bot_ownership_transfers \
             WHERE transfer_id = ?",
            vec![DbValue::from(created.receipt.transfer_id.as_str())],
        ))
        .await
        .expect("decided row");
    let decided_at = rows[0]
        .get_string("decided_at")
        .ok()
        .flatten()
        .expect("decided_at");
    let gmt_create = rows[0]
        .get_string("gmt_create")
        .ok()
        .flatten()
        .expect("gmt_create");
    assert!(decided_at >= gmt_create, "the decision used the DB clock");
}

/// The exact deadline boundary on the DB clock (SQLite): a pending whose
/// `expires_at == CURRENT_TIMESTAMP` is already past the boundary
/// (`now >= expires_at` is INCLUSIVE — §9.1: 等于/之后 behavior 一致).
#[tokio::test]
async fn sqlite_deadline_boundary_at_the_database_clock() {
    let (h, db) = sqlite_harness().await;
    h.seed_owned("bot-boundary", "a").await;
    h.seed_human("b").await;
    // Read the DB's own CURRENT_TIMESTAMP (second granularity) and seed a
    // pending whose deadline is exactly that instant.
    let rows = db
        .inner
        .query(DbStatement::new("SELECT CURRENT_TIMESTAMP AS now_text"))
        .await
        .expect("db now");
    let now_text = rows[0]
        .get_string("now_text")
        .ok()
        .flatten()
        .expect("now column");
    let boundary_id = h
        .driver
        .seed_pending("bot-boundary", "a", "b", 1, &now_text)
        .await;

    // The decide runs strictly after the seeding read: the database clock
    // never moves backwards, so now' >= expires_at holds deterministically.
    let outcome = h
        .repo
        .decide_transfer("b", &boundary_id, TransferAction::Accept)
        .await
        .unwrap();
    assert_eq!(
        outcome,
        CommittedTransferOutcome::Expired,
        "the boundary instant itself is already unacceptable (now >= expires_at)"
    );
    assert_eq!(
        h.driver.raw_row("bot-boundary", &boundary_id).await,
        Some(common::RawTransferRow {
            stored_status: "expired".to_string(),
            terminal_reason: None,
        })
    );
}

/// Review-fix proof (Task 4's drift pattern applied to the transfer lanes):
/// the changing statements' in-transaction guards re-prove the validated
/// predicates under the write lock. Without them a drifted window would
/// still pin its expected rows and commit a transition the state no longer
/// describes.
#[tokio::test]
async fn sqlite_transfer_guards_hold_inside_the_write_transaction() {
    let (h, db) = sqlite_harness().await;
    h.seed_owned("bot-drift-create", "a").await;
    h.seed_human("b").await;

    // Create path: the owner edge is reassigned AFTER the validated read
    // and BEFORE the write transaction; the guarded INSERT fails its
    // actor-is-owner condition, the whole attempt rolls back and the
    // re-validation answers with the branch the drifted state demands.
    db.arm_drift(
        "UPDATE edge_grants SET from_id = 'human_other' \
         WHERE grant_kind = 'owner' AND to_id = 'bot-drift-create' AND env = 'local'",
    );
    expect_forbidden(
        h.repo
            .create_transfer(create_with_key("a", "bot-drift-create", "b", 1, &fresh_key()))
            .await,
        "a create whose actor lost ownership at commit time",
    );
    assert_eq!(
        h.driver.raw_row("bot-drift-create", "none").await,
        None,
        "the rolled-back attempt left no residue"
    );
    assert_eq!(
        h.driver.row_count("bot-drift-create").await,
        0,
        "no pending row may survive the drifted create"
    );

    // Accept path: the version bumps between the validated read and the
    // write window; the probes re-classify into the owner_changed
    // invalidation instead of committing a stale acceptance.
    h.seed_owned("bot-drift-accept", "a").await;
    let created = h
        .repo
        .create_transfer(create_with_key("a", "bot-drift-accept", "b", 1, &fresh_key()))
        .await
        .unwrap();
    db.arm_drift(
        "UPDATE bcs_bots SET ownership_version = ownership_version + 1 \
         WHERE bot_uuid = 'bot-drift-accept' AND env = 'local'",
    );
    let outcome = h
        .repo
        .decide_transfer("b", &created.receipt.transfer_id, TransferAction::Accept)
        .await
        .unwrap();
    assert_eq!(
        outcome,
        CommittedTransferOutcome::OwnerChanged,
        "a drifted version invalidates owner_changed instead of accepting the stale snapshot"
    );
    // The invalidation released the slot: a fresh, correctly-versioned
    // create succeeds.
    let fresh = h
        .repo
        .create_transfer(create_with_key("a", "bot-drift-accept", "b", 2, &fresh_key()))
        .await
        .unwrap();
    assert!(fresh.created);

    // Reject path: a concurrent version bump also protects the reject
    // lane through the owner_changed invalidation (never a phantom reject
    // of a snapshot the authority already replaced).
    h.seed_owned("bot-drift-reject", "a").await;
    let created = h
        .repo
        .create_transfer(create_with_key("a", "bot-drift-reject", "b", 1, &fresh_key()))
        .await
        .unwrap();
    db.arm_drift(
        "UPDATE bcs_bots SET ownership_version = ownership_version + 1 \
         WHERE bot_uuid = 'bot-drift-reject' AND env = 'local'",
    );
    let outcome = h
        .repo
        .decide_transfer("b", &created.receipt.transfer_id, TransferAction::Reject)
        .await
        .unwrap();
    assert_eq!(outcome, CommittedTransferOutcome::OwnerChanged);

    // A genuine write failure surfaces as an error and rolls back
    // everything (OT13: no best-effort persistence).
    h.seed_owned("bot-fail", "a").await;
    db.arm_write_failure();
    let create_outcome = h
        .repo
        .create_transfer(create_with_key("a", "bot-fail", "b", 1, &fresh_key()))
        .await;
    assert!(create_outcome.is_err(), "an injected write failure surfaces");
    assert_eq!(
        h.driver.row_count("bot-fail").await,
        0,
        "the failed create persisted nothing"
    );
}

/// Statement budgets (OT22's read/write cost side): constant, index-driven
/// statement counts independent of the OTHER rows' history — create,
/// decide, get and list all stay bounded even under 30 stale history rows.
#[tokio::test]
async fn sqlite_transfer_statement_budgets_bounded_by_history() {
    let (h, db) = sqlite_harness().await;
    h.seed_owned("bot-budget", "a").await;
    h.seed_human("b").await;

    // 30 historical ACCEPTED rows addressed to b (other bots), inserted
    // directly so they never touch the pending-slot lane.
    for index in 0..30 {
        let bot_id = format!("bot-hist-{index}");
        db.inner
            .execute(DbStatement::with_params(
                "INSERT INTO bot_ownership_transfers \
                     (transfer_id, env, bot_id, from_user_id, to_user_id, expected_owner_version, \
                      client_request_id, status, expires_at, decision_actor_kind, decided_by, \
                      decided_at, result_owner_version, bot_name_snapshot) \
                 VALUES (?, 'local', ?, 'hist-a', 'b', 1, ?, 'accepted', '2099-01-01 00:00:00', \
                         'human', 'hist-a', '2000-01-01 00:00:00', 2, ?)",
                vec![
                    DbValue::from(format!("hist-{index}")),
                    DbValue::from(bot_id),
                    DbValue::from(format!("hist-key-{index}")),
                    DbValue::from(format!("hist-{index}")),
                ],
            ))
            .await
            .expect("seed history row");
    }

    // CREATE: the validated read transaction + the one write transaction +
    // the receipt re-read.
    let tx_before = db.transaction_count();
    let q_before = db.query_count();
    let created = h
        .repo
        .create_transfer(create_with_key("a", "bot-budget", "b", 1, &fresh_key()))
        .await
        .unwrap();
    let create_tx_used = db.transaction_count() - tx_before;
    let create_q_used = db.query_count() - q_before;
    assert!(
        create_tx_used <= 3 && create_q_used <= 3,
        "create stays a handful of statements; used tx={create_tx_used}, standalone q={create_q_used}"
    );

    // DECIDE (accept): minimal read + validation + bounded probes + ONE
    // action transaction.
    let tx_before = db.transaction_count();
    let q_before = db.query_count();
    h.repo
        .decide_transfer("b", &created.receipt.transfer_id, TransferAction::Accept)
        .await
        .unwrap();
    let decide_tx_used = db.transaction_count() - tx_before;
    let decide_q_used = db.query_count() - q_before;
    assert!(
        decide_tx_used <= 6 && decide_q_used <= 4,
        "decide stays a handful of statements; used tx={decide_tx_used}, standalone q={decide_q_used}"
    );

    // GET: ONE index-driven statement — and the history-grown inboxes of
    // OTHER viewers never change the cost.
    let q_before = db.query_count();
    h.repo.get_transfer("b", &created.receipt.transfer_id).await.unwrap();
    assert_eq!(
        db.query_count() - q_before,
        1,
        "get_transfer is exactly one statement"
    );

    // LIST: ONE read transaction of two statements (count + page), and the
    // page over the 30-row inbox stays one snapshot.
    let tx_before = db.transaction_count();
    let q_before = db.query_count();
    let page = h
        .repo
        .list_transfers(ListOwnershipTransfers {
            viewer_user_id: "b".to_string(),
            direction: TransferListDirection::Received,
            status: Some(TransferStatus::Accepted),
            offset: 20,
            limit: 10,
        })
        .await
        .unwrap();
    assert_eq!(page.total, 31, "30 history rows + this accept");
    assert_eq!(page.items.len(), 10);
    assert_eq!(db.transaction_count() - tx_before, 1, "one read transaction");
    assert_eq!(db.query_count() - q_before, 0, "no standalone statements");
}