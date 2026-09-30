//! Event store claim sql helpers.

use super::*;

pub(super) fn claim_fanout_targets_sql(flavor: DbSqlFlavor) -> String {
    sql_with_timestamp_params(flavor, "UPDATE bcs_event_fanout_targets SET lease_owner = ?, \
     lease_until = __bcs_timestamp_ms__ \
     WHERE target_id IN (SELECT target_id FROM (\
       SELECT target.target_id FROM bcs_event_fanout_targets target \
       JOIN bcs_events event ON event.env = target.env AND event.event_id = target.event_id \
       WHERE target.env = ? AND target.status = 'pending' \
         AND (target.lease_until IS NULL \
           OR target.lease_until <= __bcs_timestamp_ms__) \
       ORDER BY target.created_at, event.stream_key, event.sequence, target.target_id LIMIT ?\
     ) claimable) AND env = ? AND status = 'pending' \
       AND (lease_until IS NULL OR lease_until <= __bcs_timestamp_ms__)"
    )
}

pub(super) fn claim_deliveries_sql(flavor: DbSqlFlavor) -> String {
    // Keep the recovery marker assignment before status and lease_until:
    // MySQL evaluates single-table UPDATE assignments from left to right. The
    // marker snapshots an expired in-flight lease for the audit query later in
    // the same transaction, then clear_claim_recovery_markers_sql removes it.
    sql_with_timestamp_params(flavor, "UPDATE bcs_event_deliveries SET \
     next_attempt_at = CASE WHEN status = 'in_flight' THEN lease_until ELSE NULL END, \
     status = 'in_flight', \
     attempt_count = attempt_count + 1, lease_owner = ?, \
     lease_until = __bcs_timestamp_ms__, \
     first_attempt_at = COALESCE(first_attempt_at, __bcs_timestamp_ms__), \
     last_attempt_at = __bcs_timestamp_ms__ \
     WHERE delivery_id IN (SELECT delivery_id FROM (\
       SELECT delivery.delivery_id FROM bcs_event_deliveries delivery \
       JOIN bcs_event_subscription_revisions revision \
         ON revision.env = delivery.env \
        AND revision.subscription_id = delivery.subscription_id \
        AND revision.revision = delivery.subscription_revision \
       JOIN bcs_event_fanout_targets target \
         ON target.env = delivery.env AND target.target_id = delivery.fanout_target_id \
       WHERE delivery.env = ? \
         AND (delivery.lease_until IS NULL \
           OR delivery.lease_until <= __bcs_timestamp_ms__) \
         AND (delivery.status = 'pending' \
           OR (delivery.status = 'retry_wait' \
             AND delivery.next_attempt_at <= __bcs_timestamp_ms__) \
           OR delivery.status = 'in_flight') \
         AND (target.depends_on_target_id IS NULL OR EXISTS (\
           SELECT 1 FROM bcs_event_deliveries dependency \
           WHERE dependency.env = delivery.env \
             AND dependency.fanout_target_id = target.depends_on_target_id \
             AND (dependency.status IN ('succeeded', 'skipped') \
               OR (dependency.status = 'dead_lettered' \
                 AND dependency.resolved_by_delivery_id IS NOT NULL))\
         )) \
         AND NOT EXISTS (\
           SELECT 1 FROM bcs_event_deliveries previous \
           WHERE previous.env = delivery.env \
             AND previous.subscription_id = delivery.subscription_id \
             AND previous.stream_key = delivery.stream_key \
             AND previous.sequence < delivery.sequence \
             AND NOT (previous.status IN ('succeeded', 'cancelled', 'skipped') \
               OR (previous.status = 'dead_lettered' \
                 AND previous.resolved_by_delivery_id IS NOT NULL))\
         ) \
       ORDER BY delivery.created_at, delivery.sequence, delivery.delivery_id LIMIT ?\
     ) claimable) AND env = ? \
       AND (lease_until IS NULL OR lease_until <= __bcs_timestamp_ms__) \
       AND (status = 'pending' OR (status = 'retry_wait' \
         AND next_attempt_at <= __bcs_timestamp_ms__) OR status = 'in_flight')"
    )
}

pub(super) fn claimed_fanout_targets_clause() -> &'static str {
    // claim_owner() appends a fresh UUID for every claim transaction, so the
    // owner is a stronger batch fence than lease_until. Do not compare the
    // stored timestamp with the millisecond input: some MySQL-compatible
    // deployments persist TIMESTAMP without fractional-second precision.
    "WHERE env = ? AND status = 'pending' AND lease_owner = ? \
     ORDER BY created_at, \
     (SELECT event.stream_key FROM bcs_events event \
       WHERE event.env = bcs_event_fanout_targets.env \
         AND event.event_id = bcs_event_fanout_targets.event_id), \
     (SELECT event.sequence FROM bcs_events event \
       WHERE event.env = bcs_event_fanout_targets.env \
         AND event.event_id = bcs_event_fanout_targets.event_id), target_id"
}

pub(super) fn recover_reclaimed_delivery_attempts_sql(flavor: DbSqlFlavor) -> String {
    sql_with_timestamp_params(flavor, "UPDATE bcs_event_delivery_attempts SET \
     completed_at = __bcs_timestamp_ms__, result = 'retryable', \
     error_category = 'lease_expired', \
     error_summary = 'Delivery lease expired before completion; remote outcome is unknown', \
     response_bytes_observed = 0 \
     WHERE completed_at IS NULL AND EXISTS (\
       SELECT 1 FROM bcs_event_deliveries delivery \
       WHERE delivery.delivery_id = bcs_event_delivery_attempts.delivery_id \
         AND delivery.env = ? AND delivery.status = 'in_flight' \
         AND delivery.lease_owner = ? \
         AND delivery.attempt_count = bcs_event_delivery_attempts.attempt_no + 1\
     )")
}

pub(super) fn insert_claimed_delivery_attempts_sql(flavor: DbSqlFlavor) -> String {
    sql_with_timestamp_params(flavor, "INSERT INTO bcs_event_delivery_attempts \
     (delivery_id, attempt_no, started_at, completed_at, latency_ms, result, http_status, \
      error_category, error_summary, response_bytes_observed, worker_id) \
     SELECT delivery_id, attempt_count, __bcs_timestamp_ms__, NULL, NULL, NULL, \
            NULL, NULL, NULL, NULL, ? \
     FROM bcs_event_deliveries WHERE env = ? AND status = 'in_flight' \
       AND lease_owner = ?")
}

pub(super) fn recovered_delivery_attempts_sql(flavor: DbSqlFlavor) -> String {
    format!(
        "SELECT delivery_id, attempt_count - 1 AS recovered_attempt_no, {} \
         FROM bcs_event_deliveries WHERE env = ? AND status = 'in_flight' \
           AND lease_owner = ? AND next_attempt_at IS NOT NULL \
         ORDER BY created_at, sequence, delivery_id",
        timestamp_ms_expr(
            flavor,
            "next_attempt_at",
            "expired_lease_until_ms"
        )
    )
}

pub(super) fn clear_claim_recovery_markers_sql() -> &'static str {
    "UPDATE bcs_event_deliveries SET next_attempt_at = NULL \
     WHERE env = ? AND status = 'in_flight' AND lease_owner = ? \
       AND next_attempt_at IS NOT NULL"
}

pub(super) fn log_recovered_delivery_attempt(row: &DbRow) -> Result<(), EventRepoError> {
    warn!(
        target: "bcs_event_webhook",
        component = "delivery",
        delivery_id = %column::<String>(row, "delivery_id")?,
        old_attempt_no = column::<u32>(row, "recovered_attempt_no")?,
        expired_lease_until_ms = column::<u64>(row, "expired_lease_until_ms")?,
        "recovered expired webhook delivery attempt"
    );
    Ok(())
}

pub(super) fn claimed_deliveries_clause() -> &'static str {
    "WHERE d.env = ? AND d.status = 'in_flight' AND d.lease_owner = ? \
     ORDER BY d.created_at, d.sequence, d.delivery_id"
}

pub(super) fn causal_replay_insert_sql(flavor: DbSqlFlavor) -> String {
    sql_with_timestamp_params(flavor, match flavor {
        DbSqlFlavor::Mysql => {
            "INSERT IGNORE INTO bcs_event_fanout_targets (target_id, event_id, subscription_id, \
             subscription_revision, purpose, replay_request_id, replay_of_delivery_id, \
             depends_on_target_id, status, lease_owner, lease_until, created_at, materialized_at, \
             cancelled_at, env) SELECT ?, ?, ?, ?, 'causal_prerequisite', '', NULL, NULL, \
             'pending', NULL, NULL, __bcs_timestamp_ms__, NULL, NULL, ? WHERE EXISTS (\
               SELECT 1 FROM bcs_event_deliveries original WHERE original.env = ? \
                 AND original.delivery_id = ? AND original.status = 'dead_lettered' \
                 AND original.resolved_by_delivery_id IS NULL\
             )"
        }
        DbSqlFlavor::Sqlite => {
            "INSERT OR IGNORE INTO bcs_event_fanout_targets (target_id, event_id, subscription_id, \
             subscription_revision, purpose, replay_request_id, replay_of_delivery_id, \
             depends_on_target_id, status, lease_owner, lease_until, created_at, materialized_at, \
             cancelled_at, env) SELECT ?, ?, ?, ?, 'causal_prerequisite', '', NULL, NULL, \
             'pending', NULL, NULL, __bcs_timestamp_ms__, NULL, NULL, ? WHERE EXISTS (\
               SELECT 1 FROM bcs_event_deliveries original WHERE original.env = ? \
                 AND original.delivery_id = ? AND original.status = 'dead_lettered' \
                 AND original.resolved_by_delivery_id IS NULL\
             )"
        }
    })
}

pub(super) fn replay_target_insert_sql(flavor: DbSqlFlavor) -> String {
    sql_with_timestamp_params(flavor, "INSERT INTO bcs_event_fanout_targets \
     (target_id, event_id, subscription_id, \
     subscription_revision, purpose, replay_request_id, replay_of_delivery_id, \
     depends_on_target_id, status, lease_owner, lease_until, created_at, materialized_at, \
     cancelled_at, env) SELECT ?, original.event_id, original.subscription_id, ?, \
     'manual_replay', ?, ?, ?, 'pending', NULL, NULL, __bcs_timestamp_ms__, \
     NULL, NULL, original.env \
     FROM bcs_event_deliveries original JOIN bcs_events event \
       ON event.env = original.env AND event.event_id = original.event_id \
     JOIN bcs_event_subscriptions subscription \
       ON subscription.env = original.env \
      AND subscription.subscription_id = original.subscription_id \
     WHERE original.env = ? AND original.delivery_id = ? AND original.subscription_id = ? \
       AND original.status = 'dead_lettered' AND original.resolved_by_delivery_id IS NULL \
       AND subscription.current_revision = ? \
       AND subscription.status = 'active' \
       AND event.retention_until > __bcs_timestamp_ms__ \
       AND NOT EXISTS (\
         SELECT 1 FROM bcs_event_fanout_targets replay \
         LEFT JOIN bcs_event_deliveries replacement \
           ON replacement.env = replay.env AND replacement.fanout_target_id = replay.target_id \
         WHERE replay.env = original.env AND replay.purpose = 'manual_replay' \
           AND replay.replay_of_delivery_id = original.delivery_id \
           AND (replay.status = 'pending' \
             OR replacement.status IN ('pending', 'in_flight', 'retry_wait'))\
       )")
}

pub(super) fn replay_delivery_lock_sql(flavor: DbSqlFlavor) -> &'static str {
    match flavor {
        DbSqlFlavor::Mysql => {
            "SELECT delivery_id FROM bcs_event_deliveries \
             WHERE env = ? AND delivery_id = ? FOR UPDATE"
        }
        DbSqlFlavor::Sqlite => {
            "SELECT delivery_id FROM bcs_event_deliveries WHERE env = ? AND delivery_id = ?"
        }
    }
}
