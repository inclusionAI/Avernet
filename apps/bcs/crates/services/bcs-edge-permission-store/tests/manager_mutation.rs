//! Plan Task 4: manual manager mutations, source scoping, real-operator
//! audit — the test binary entry. The shared drivers and the Memory/SQLite
//! conformance suite live in `manager_mutation_common` (loaded via
//! `#[path]`); the individual `#[tokio::test]`s are declared here.

#[path = "manager_mutation_common/mod.rs"]
mod common;

use bcs_db_api::{DbStatement, DbValue};
use bcs_domain::BotAccessRelation;
use bcs_service_api::types::error::AuthorityError;
use bcs_service_api::ServiceError;

use common::{
    grant_direct, human, manager_mutation_contract_tests, memory_harness, revoke_non_team,
    sqlite_harness, ENV,
};

#[tokio::test]
async fn sqlite_manager_mutation_contract() {
    let (h, _) = sqlite_harness().await;
    manager_mutation_contract_tests(&h).await;
}

#[tokio::test]
async fn memory_manager_mutation_contract() {
    let h = memory_harness().await;
    manager_mutation_contract_tests(&h).await;
}
// ---------------------------------------------------------------------------
// SQLite-specific proofs: real DB locking, batched SQL, raw row inspection
// ---------------------------------------------------------------------------

/// Same-Bot manager mutations serialize: two managers revoking each other
/// concurrently produce exactly ONE successful revoke and one 403 — never
/// both sides losing authority through a stale validation window (§5.4/§10).
#[tokio::test]
async fn sqlite_concurrent_mutual_manager_revocation_lock_order() {
    let (h, _) = sqlite_harness().await;
    h.seed_owned("bot-race", "ro").await;
    h.seed_human("mmm").await;
    h.seed_human("nnn").await;
    h.seed_manager_source("bot-race", "mmm", "direct", "manual").await;
    h.seed_manager_source("bot-race", "nnn", "direct", "manual").await;

    let a = h.repo.clone();
    let b = h.repo.clone();
    let (ra, rb) = tokio::join!(
        async { a.mutate_manager(human("mmm"), "bot-race", revoke_non_team("nnn")).await },
        async { b.mutate_manager(human("nnn"), "bot-race", revoke_non_team("mmm")).await },
    );
    let changed = [ra.as_ref().map(|r| r.changed).unwrap_or(false),
        rb.as_ref().map(|r| r.changed).unwrap_or(false)];
    let forbidden = [&ra, &rb].iter().any(|outcome| matches!(
        outcome,
        Err(ServiceError::Authority(AuthorityError::Forbidden(_)))
    ));
    assert_eq!(
        changed.iter().filter(|c| **c).count(),
        1,
        "exactly one side must revoke; got {:?}",
        (
            ra.as_ref().map(|r| r.changed).unwrap_or(false),
            rb.as_ref().map(|r| r.changed).unwrap_or(false)
        )
    );
    assert!(forbidden, "the loser must surface 403, not a phantom success");
    // Exactly one audit row records the one actual change.
    assert_eq!(h.driver.audit_count().await, 1);
    assert_eq!(h.driver.audit_count().await, 1, "the winner is audited once");

    // Post-state: whichever manager won, the winner is still a manager and
    // the loser is gone — the loser's authorization cannot outlive its edge.
    let m = h.repo.role("mmm", "bot-race").await.unwrap();
    let n = h.repo.role("nnn", "bot-race").await.unwrap();
    assert_ne!(
        m.is_some(),
        n.is_some(),
        "exactly one of the two managers survives"
    );
}

/// Concurrent duplicate grants of the same user: one change, one idempotent
/// no-change — audit grows by exactly one row.
#[tokio::test]
async fn sqlite_concurrent_duplicate_grant_single_change() {
    let (h, _) = sqlite_harness().await;
    h.seed_owned("bot-race2", "ro").await;
    h.seed_human("g").await;

    let a = h.repo.clone();
    let b = h.repo.clone();
    let (ra, rb) = tokio::join!(
        async { a.mutate_manager(human("ro"), "bot-race2", grant_direct("g")).await },
        async { b.mutate_manager(human("ro"), "bot-race2", grant_direct("g")).await },
    );
    assert!(ra.as_ref().map(|r| r.changed).unwrap_or(false) ^ rb.as_ref().map(|r| r.changed).unwrap_or(false),
        "exactly one concurrent grant changes the edge");
    assert_eq!(h.driver.audit_count().await, 1, "one change → one audit row");
    assert_eq!(
        h.repo.role("g", "bot-race2").await.unwrap(),
        Some(BotAccessRelation::Manager)
    );
    assert_eq!(
        h.driver.direct_manager_edge_ids("bot-race2", "g").await.len(),
        1,
        "the unique source slot keeps the direct row singular"
    );
}

/// Large source sets: a subject holding 1 direct + 600 ownership_transfer
/// sources is revoked by ONE batched audit INSERT SELECT + ONE batched UPDATE
/// — the statement budget stays independent of the source count and no
/// statement ever scans all bots.
#[tokio::test]
async fn sqlite_bulk_revoke_uses_batched_sql_with_bounded_statements() {
    let (h, db) = sqlite_harness().await;
    h.seed_owned("bot-bulk", "bo").await;
    h.seed_human("bulk").await;
    h.seed_manager_source("bot-bulk", "bulk", "direct", "manual").await;
    // 600 ownership_transfer sources, seeded in two parameter-bounded
    // multi-VALUES statements (SQLite ~999 bind ceiling).
    for chunk_start in [0, 300] {
        let mut sql = String::from(
            "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
             originator_policy_type, originator_policy_data, management_source_kind, management_source_id) VALUES ",
        );
        let mut params = Vec::with_capacity(300 * 2);
        for i in chunk_start..chunk_start + 300 {
            if i > chunk_start {
                sql.push_str(", ");
            }
            sql.push_str("(?, 'human_bulk', 'bot-bulk', 'manager', 0, NULL, 'approved', 'same_as_from', NULL, 'ownership_transfer', ?)");
            params.push(DbValue::from(ENV));
            params.push(DbValue::from(format!("ot-{i:04}")));
        }
        db.inner
            .execute(DbStatement::with_params(&sql, params))
            .await
            .expect("seed bulk sources");
    }

    let tx_before = db.transaction_count();
    let q_before = db.query_count();
    let removed = h
        .repo
        .mutate_manager(human("bo"), "bot-bulk", revoke_non_team("bulk"))
        .await
        .unwrap();
    assert!(removed.changed);
    assert!(removed.remaining_team_sources.is_empty());
    let tx_used = db.transaction_count() - tx_before;
    let q_used = db.query_count() - q_before;
    assert!(
        tx_used <= 4 && q_used <= 4,
        "the mutation's statement budget must be independent of the 601 changed edges \
         (transactions used: {tx_used}, standalone queries used: {q_used})"
    );
    assert_eq!(
        h.driver.audit_count().await,
        601,
        "every one of the 601 changed edges is audited through the batched INSERT SELECT"
    );
    assert_eq!(
        h.repo.role("bulk", "bot-bulk").await.unwrap(),
        None,
        "all 601 non-team sources revoked"
    );
    // Team rows for OTHER subjects on the same bot are untouched (no
    // scan-wide side effects).
    h.seed_manager_source("bot-bulk", "other", "team", "team-z").await;
    assert!(h
        .repo
        .mutate_manager(human("bo"), "bot-bulk", revoke_non_team("bulk"))
        .await
        .unwrap()
        .changed == false);
    assert_eq!(
        h.repo.role("other", "bot-bulk").await.unwrap(),
        Some(BotAccessRelation::Manager)
    );
}

/// Review-fix proof: the subject predicates hold INSIDE the write
/// transaction. Drift is injected deterministically between the validated
/// read and the write transaction (applied the moment the write
/// transaction arrives): the changing statements' in-transaction guards
/// flip them to zero rows, the ExecuteChecked pin rolls the whole attempt
/// back, and the retry surfaces the branch the drifted state demands.
/// Without the guards the grant lane would still have matched its 1-row
/// pin and committed a mutation the validation no longer described.
#[tokio::test]
async fn sqlite_subject_predicates_hold_inside_the_write_transaction() {
    let (h, db) = sqlite_harness().await;
    h.seed_owned("bot-drift", "dr").await;
    h.seed_human("dg").await;

    // Grant path: the subject's human row dies after validation.
    db.arm_drift("UPDATE bcs_bots SET is_deleted = 1 WHERE bot_uuid = 'human_dg'");
    match h
        .repo
        .mutate_manager(human("dr"), "bot-drift", grant_direct("dg"))
        .await
    {
        Err(ServiceError::Authority(AuthorityError::InvalidSubject(_))) => {}
        other => panic!(
            "a subject dead at commit time must be InvalidSubject after the retry, got {:?}",
            other.map(|r| r.changed)
        ),
    }
    assert_eq!(
        h.repo.role("dg", "bot-drift").await.unwrap(),
        None,
        "no edge may survive the rolled-back grant"
    );
    assert_eq!(h.driver.audit_count().await, 0, "no audit rows for the aborted grant");

    // Grant path: the subject BECOMES the owner between validation and the
    // write transaction (the shape an accepted transfer lane will produce).
    db.inner
        .execute(DbStatement::new(
            "UPDATE bcs_bots SET is_deleted = 0 WHERE bot_uuid = 'human_dg'",
        ))
        .await
        .expect("restore the subject human row");
    // The acting owner ALSO holds a manager edge, so it stays authorized
    // after the transfer-style drift reassigns the owner slot to the
    // subject; the branch the retry must surface is the target-owner
    // Conflict, not an actor Forbidden.
    h.seed_manager_source("bot-drift", "dr", "direct", "manual").await;
    db.arm_drift(
        "UPDATE edge_grants SET from_id = 'human_dg' \
         WHERE grant_kind = 'owner' AND to_id = 'bot-drift' AND env = 'local'",
    );
    match h
        .repo
        .mutate_manager(human("dr"), "bot-drift", grant_direct("dg"))
        .await
    {
        Err(ServiceError::Authority(AuthorityError::Conflict(_))) => {}
        other => panic!(
            "a subject that became the owner at commit time must Conflict after the retry, got {:?}",
            other.map(|r| r.changed)
        ),
    }
    assert_eq!(
        h.repo.role("dg", "bot-drift").await.unwrap(),
        Some(BotAccessRelation::Owner),
        "the drifted ownership itself is readable — the mutation just must not commit"
    );
    assert_eq!(h.driver.audit_count().await, 0);

    // Revoke path: the subject's own direct edge (granted cleanly first) is
    // protected by the same in-transaction guards.
    db.inner
        .execute(DbStatement::new(
            "UPDATE edge_grants SET from_id = 'human_dr' \
             WHERE grant_kind = 'owner' AND to_id = 'bot-drift' AND env = 'local'",
        ))
        .await
        .expect("restore the original owner edge");
    assert!(h
        .repo
        .mutate_manager(human("dr"), "bot-drift", grant_direct("dg"))
        .await
        .unwrap()
        .changed);
    assert_eq!(h.driver.audit_count().await, 1);
    db.arm_drift("UPDATE bcs_bots SET is_deleted = 1 WHERE bot_uuid = 'human_dg'");
    match h
        .repo
        .mutate_manager(human("dr"), "bot-drift", revoke_non_team("dg"))
        .await
    {
        Err(ServiceError::Authority(AuthorityError::InvalidSubject(_))) => {}
        other => panic!(
            "a subject dead at commit time must invalidate the revoke too, got {:?}",
            other.map(|r| r.changed)
        ),
    }
    assert_eq!(
        h.repo.role("dg", "bot-drift").await.unwrap(),
        Some(BotAccessRelation::Manager),
        "the direct edge survives the rolled-back revoke"
    );
    assert_eq!(
        h.driver.audit_count().await,
        1,
        "only the one successful grant is audited"
    );
}

/// The revoke predicate itself: only direct/ownership_transfer rows flip to
/// revoked; team rows remain approved; the audit rows record the true
/// operator (`actor_id` = the acting human, never the subject).
#[tokio::test]
async fn sqlite_revoke_predicate_preserves_team_sources_and_records_operator() {
    let (h, db) = sqlite_harness().await;
    h.seed_owned("bot-p", "po").await;
    h.seed_human("pg").await;
    assert!(h
        .repo
        .mutate_manager(human("po"), "bot-p", grant_direct("pg"))
        .await
        .unwrap()
        .changed);
    // Re-grant after first revoke exercises the restore path before the row
    // is finally revoked again.
    assert!(h.repo.mutate_manager(human("po"), "bot-p", revoke_non_team("pg")).await.unwrap().changed);
    // Raw predicate proof: seed team + ot rows for the same subject.
    h.seed_manager_source("bot-p", "pg", "team", "team-p").await;
    h.seed_manager_source("bot-p", "pg", "ownership_transfer", "ot-9").await;
    assert!(h.repo.mutate_manager(human("po"), "bot-p", grant_direct("pg")).await.unwrap().changed);
    let removed = h
        .repo
        .mutate_manager(human("po"), "bot-p", revoke_non_team("pg"))
        .await
        .unwrap();
    assert!(removed.changed);
    assert_eq!(removed.remaining_team_sources, vec!["team-p"]);
    assert_eq!(h.repo.role("pg", "bot-p").await.unwrap(), Some(BotAccessRelation::Manager));

    let audit = db
        .inner
        .query(DbStatement::new(
            "SELECT actor_kind, actor_id, action, subject_user_id, management_source_kind, \
             management_source_id FROM bot_manager_changes WHERE operation_id = \
             (SELECT operation_id FROM bot_manager_changes ORDER BY id DESC LIMIT 1) ORDER BY id",
        ))
        .await
        .expect("audit rows");
    assert_eq!(audit.len(), 2, "the revoke audited the direct + ot-9 edges");
    for row in &audit {
        assert_eq!(row.get_string("actor_kind").ok().flatten(), Some("human".to_string()));
        assert_eq!(
            row.get_string("actor_id").ok().flatten(),
            Some("po".to_string()),
            "the audit records the TRUE operator, never the subject"
        );
        assert_eq!(row.get_string("action").ok().flatten(), Some("revoke".to_string()));
        assert_eq!(
            row.get_string("subject_user_id").ok().flatten(),
            Some("pg".to_string())
        );
    }
    let kinds: Vec<String> = audit
        .iter()
        .map(|row| {
            row.get_string("management_source_kind")
                .ok()
                .flatten()
                .expect("kind")
        })
        .collect();
    assert_eq!(
        kinds,
        vec!["direct".to_string(), "ownership_transfer".to_string()],
        "one operation_id groups all changed non-team edges"
    );
}
