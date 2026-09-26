use super::*;

#[test]
fn task_six_sql_has_expected_parameter_counts_for_both_dialects() {
    for flavor in [DbSqlFlavor::Mysql, DbSqlFlavor::Sqlite] {
        assert_eq!(claim_fanout_targets_sql(flavor).matches('?').count(), 7);
        assert_eq!(claim_deliveries_sql(flavor).matches('?').count(), 11);
        assert_eq!(replay_target_insert_sql(flavor).matches('?').count(), 11);
        assert_eq!(causal_replay_insert_sql(flavor).matches('?').count(), 8);
        assert_eq!(replay_delivery_lock_sql(flavor).matches('?').count(), 2);
        assert!(!claim_deliveries_sql(flavor).contains("__bcs_timestamp_ms__"));
    }
}

#[test]
fn post_claim_lookup_is_fenced_by_unique_owner_not_timestamp_precision() {
    for sql in [
        claimed_fanout_targets_clause().to_string(),
        recover_reclaimed_delivery_attempts_sql(DbSqlFlavor::Mysql),
        insert_claimed_delivery_attempts_sql(DbSqlFlavor::Mysql),
        recovered_delivery_attempts_sql(DbSqlFlavor::Mysql),
        clear_claim_recovery_markers_sql().to_string(),
        claimed_deliveries_clause().to_string(),
    ] {
        assert!(sql.contains("lease_owner = ?"));
        assert!(!sql.contains("lease_until ="));
    }

    let claim_sql = claim_deliveries_sql(DbSqlFlavor::Mysql);
    assert!(
        claim_sql
            .find("next_attempt_at = CASE")
            .expect("recovery marker assignment")
            < claim_sql
                .find("status = 'in_flight',")
                .expect("claim status assignment")
    );
}
