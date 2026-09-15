//! SQL persistence for Event retention.

use super::*;

impl DbEventStore {
    pub(super) async fn sql_purge_expired(
        &self,
        command: EventRetentionRequest,
    ) -> Result<EventRetentionResult, EventRepoError> {
        if command.event_limit == 0 || command.audit_limit == 0 || command.env.is_empty() {
            return Err(EventRepoError::InvalidInput(
                "retention limits and env must be non-empty".into(),
            ));
        }
        let now = timestamp_value_from_ms(self.flavor, command.now_ms)?;
        let attempt_delete_step = 0;
        let delivery_delete_step = 1;
        let event_delete_step = 3;
        let results = self
            .db
            .transaction(vec![
                DbTransactionStep::Execute(DbStatement::with_params(
                    sql_with_timestamp_params(self.flavor, "DELETE FROM \
                       bcs_event_delivery_attempts WHERE delivery_id IN (\
                       SELECT delivery_id FROM (SELECT delivery.delivery_id \
                       FROM bcs_event_deliveries delivery WHERE delivery.env = ? \
                         AND delivery.event_id IN (SELECT event_id FROM (\
                           SELECT event.event_id FROM bcs_events event WHERE event.env = ? \
                             AND event.retention_until <= __bcs_timestamp_ms__ AND NOT EXISTS (\
                               SELECT 1 FROM bcs_event_fanout_targets target \
                               WHERE target.env = event.env AND target.event_id = event.event_id \
                                 AND (target.status = 'pending' OR EXISTS (\
                                   SELECT 1 FROM bcs_event_fanout_targets dependent \
                                   WHERE dependent.env = target.env \
                                     AND dependent.depends_on_target_id = target.target_id\
                                 ) OR EXISTS (\
                                   SELECT 1 FROM bcs_event_deliveries blocker \
                                   WHERE blocker.env = target.env \
                                     AND blocker.fanout_target_id = target.target_id \
                                     AND NOT (blocker.status IN ('succeeded', 'cancelled', 'skipped') \
                                       OR (blocker.status = 'dead_lettered' \
                                         AND blocker.resolved_by_delivery_id IS NOT NULL))\
                                 ))\
                             ) ORDER BY event.retention_until, event.event_id LIMIT ?\
                         ) eligible_events)\
                       ) eligible_deliveries)"),
                    vec![
                        DbValue::from(command.env.as_str()),
                        DbValue::from(command.env.as_str()),
                        now.clone(),
                        DbValue::from(command.event_limit),
                    ],
                )),
                DbTransactionStep::Execute(DbStatement::with_params(
                    sql_with_timestamp_params(self.flavor, "DELETE FROM bcs_event_deliveries \
                       WHERE delivery_id IN (\
                       SELECT delivery_id FROM (SELECT delivery.delivery_id \
                       FROM bcs_event_deliveries delivery WHERE delivery.env = ? \
                         AND delivery.event_id IN (SELECT event_id FROM (\
                           SELECT event.event_id FROM bcs_events event WHERE event.env = ? \
                             AND event.retention_until <= __bcs_timestamp_ms__ AND NOT EXISTS (\
                               SELECT 1 FROM bcs_event_fanout_targets target \
                               WHERE target.env = event.env AND target.event_id = event.event_id \
                                 AND (target.status = 'pending' OR EXISTS (\
                                   SELECT 1 FROM bcs_event_fanout_targets dependent \
                                   WHERE dependent.env = target.env \
                                     AND dependent.depends_on_target_id = target.target_id\
                                 ) OR EXISTS (\
                                   SELECT 1 FROM bcs_event_deliveries blocker \
                                   WHERE blocker.env = target.env \
                                     AND blocker.fanout_target_id = target.target_id \
                                     AND NOT (blocker.status IN ('succeeded', 'cancelled', 'skipped') \
                                       OR (blocker.status = 'dead_lettered' \
                                         AND blocker.resolved_by_delivery_id IS NOT NULL))\
                                 ))\
                             ) ORDER BY event.retention_until, event.event_id LIMIT ?\
                         ) eligible_events)\
                       ) eligible_deliveries)"),
                    vec![
                        DbValue::from(command.env.as_str()),
                        DbValue::from(command.env.as_str()),
                        now.clone(),
                        DbValue::from(command.event_limit),
                    ],
                )),
                DbTransactionStep::Execute(DbStatement::with_params(
                    sql_with_timestamp_params(self.flavor, "DELETE FROM \
                       bcs_event_fanout_targets WHERE target_id IN (\
                       SELECT target_id FROM (SELECT target.target_id \
                       FROM bcs_event_fanout_targets target \
                       JOIN bcs_events event ON event.env = target.env \
                         AND event.event_id = target.event_id \
                       WHERE target.env = ? \
                         AND event.retention_until <= __bcs_timestamp_ms__ \
                         AND target.status <> 'pending' AND NOT EXISTS (\
                           SELECT 1 FROM bcs_event_fanout_targets dependent \
                           WHERE dependent.env = target.env \
                             AND dependent.depends_on_target_id = target.target_id\
                         ) AND NOT EXISTS (\
                           SELECT 1 FROM bcs_event_deliveries delivery \
                           WHERE delivery.env = target.env \
                             AND delivery.fanout_target_id = target.target_id\
                         ) ORDER BY event.retention_until, event.event_id LIMIT ?\
                       ) eligible_targets)"),
                    vec![
                        DbValue::from(command.env.as_str()),
                        now.clone(),
                        DbValue::from(command.event_limit),
                    ],
                )),
                DbTransactionStep::Execute(DbStatement::with_params(
                    sql_with_timestamp_params(self.flavor, "DELETE FROM bcs_events \
                     WHERE event_id IN (SELECT event_id FROM (\
                   SELECT event.event_id FROM bcs_events event WHERE event.env = ? \
                     AND event.retention_until <= __bcs_timestamp_ms__ AND NOT EXISTS (\
                       SELECT 1 FROM bcs_event_fanout_targets target \
                       WHERE target.env = event.env AND target.event_id = event.event_id\
                     ) ORDER BY event.retention_until, event.event_id LIMIT ?\
                 ) eligible)"),
                    vec![
                        DbValue::from(command.env.as_str()),
                        now,
                        DbValue::from(command.event_limit),
                    ],
                )),
            ])
            .await
            .map_err(storage_error)?;
        Ok(EventRetentionResult {
            events_deleted: transaction_affected_rows(&results, event_delete_step)?,
            deliveries_deleted: transaction_affected_rows(&results, delivery_delete_step)?,
            attempts_deleted: transaction_affected_rows(&results, attempt_delete_step)?,
            ..EventRetentionResult::default()
        })
    }
}
