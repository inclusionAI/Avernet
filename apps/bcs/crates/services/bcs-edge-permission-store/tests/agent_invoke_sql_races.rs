//! Two independent file-SQLite connections, real migrations and deterministic
//! transaction boundaries. The same cases run in the remote native MySQL gate.
#[path = "support/agent_invoke_sql_driver.rs"]
mod lifecycle;
#[path = "support/agent_invoke_races.rs"]
mod races;
use bcs_db_api::DbSqlFlavor;
use std::sync::Arc;
use races::{Case, Operation, Quota};
async fn run(case: Case) {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("races.db");
    let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new_file(&file).unwrap());
    bcs_db_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let peer = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new_file(&file).unwrap());
    races::run_case(db, peer, DbSqlFlavor::Sqlite, "sql-races", case).await;
}
macro_rules! scenarios {
    ($($name:ident, $case:expr,)*) => {
        $(#[tokio::test] async fn $name() { run($case).await; })*
        #[test]
        fn native_selector_and_sqlite_catalog_have_identical_cases() {
            let mut local = vec![$(format!("{:?}", $case)),*];
            let mut native: Vec<_> = races::cases().into_iter().map(|case| format!("{case:?}")).collect();
            local.sort(); native.sort();
            assert_eq!(local, native, "one independently discoverable SQLite test per native scenario");
            assert_eq!(local.len(), 31);
        }
    };
}
scenarios! {
    approve_commits_before_cancel, Case::ApproveCancel(true),
    cancel_commits_before_approve, Case::ApproveCancel(false),
    approve_commits_before_revoke, Case::ApproveRevoke(true),
    revoke_commits_before_approve, Case::ApproveRevoke(false),
    out_degree_a_wins, Case::Quota(Quota::OutDegree, false),
    out_degree_b_wins, Case::Quota(Quota::OutDegree, true),
    in_degree_a_wins, Case::Quota(Quota::InDegree, false),
    in_degree_b_wins, Case::Quota(Quota::InDegree, true),
    pending_out_a_wins, Case::Quota(Quota::PendingOut, false),
    pending_out_b_wins, Case::Quota(Quota::PendingOut, true),
    pending_in_a_wins, Case::Quota(Quota::PendingIn, false),
    pending_in_b_wins, Case::Quota(Quota::PendingIn, true),
    pending_pair_a_wins, Case::Quota(Quota::PendingPair, false),
    pending_pair_b_wins, Case::Quota(Quota::PendingPair, true),
    old_key_during_regrant_never_reapproves, Case::OldKeyRegrant,
    lost_ack_before_concurrent_revoke_recovers_current_state, Case::UnknownRevoke,
    another_worker_recovers_same_key_before_unknown_ack, Case::SameKeyUnknown,
    same_key_prepared_twice_mutates_once, Case::SameKeyPlans(false),
    same_key_distinct_payloads_only_one_commits, Case::SameKeyPlans(true),
    grant_native_rollback, Case::Rollback(Operation::Grant),
    request_native_rollback, Case::Rollback(Operation::Request),
    approve_native_rollback, Case::Rollback(Operation::Approve),
    reject_native_rollback, Case::Rollback(Operation::Reject),
    cancel_native_rollback, Case::Rollback(Operation::Cancel),
    revoke_native_rollback, Case::Rollback(Operation::Revoke),
    grant_unknown_ack, Case::Unknown(Operation::Grant),
    request_unknown_ack, Case::Unknown(Operation::Request),
    approve_unknown_ack, Case::Unknown(Operation::Approve),
    reject_unknown_ack, Case::Unknown(Operation::Reject),
    cancel_unknown_ack, Case::Unknown(Operation::Cancel),
    revoke_unknown_ack, Case::Unknown(Operation::Revoke),
}
