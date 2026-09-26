//! SQL persistence for Event events.

use super::*;

impl DbEventStore {
    pub(super) async fn sql_append_event(
        &self,
        command: AppendEventRecord,
    ) -> Result<AppendEventRecordResult, EventRepoError> {
        validate_event_command(&command)?;
        if let Some(cause_id) = command.event.causation_event_id.as_ref() {
            if cause_id == &command.event.event_id {
                return Err(EventRepoError::CausationViolation(
                    "event cannot cause itself".to_string(),
                ));
            }
            if self.get_event(cause_id, &command.env).await?.is_none() {
                return Err(EventRepoError::CausationViolation(format!(
                    "causation event {cause_id} must already exist in the same environment"
                )));
            }
        }

        let plan = self.append_transaction_plan(&command, 0)?;
        let target_query_step = plan.target_query_step;
        match self.db.transaction(plan.steps).await {
            Ok(results) => {
                let target_ids = transaction_target_ids(&results, target_query_step)?;
                let event = self
                    .get_event(&command.event.event_id, &command.env)
                    .await?
                    .ok_or_else(|| {
                        EventRepoError::Storage("committed Event missing".to_string())
                    })?;
                Ok(AppendEventRecordResult {
                    event,
                    fanout_target_ids: target_ids,
                    deduplicated: false,
                })
            }
            Err(error) if error.is_duplicate_key() => {
                if let Some(event) = self
                    .get_event_by_producer(
                        &command.env,
                        &command.event.producer,
                        &command.event.producer_key,
                        &command.event.event_type,
                    )
                    .await?
                {
                    let target_ids = self
                        .target_ids(&command.env, &event.envelope.event_id)
                        .await?;
                    return Ok(AppendEventRecordResult {
                        event,
                        fanout_target_ids: target_ids,
                        deduplicated: true,
                    });
                }
                Err(EventRepoError::Conflict(format!(
                    "event id {} or stream sequence already exists",
                    command.event.event_id
                )))
            }
            Err(error) => Err(storage_error(error)),
        }
    }

    pub(super) async fn sql_get_event(
        &self,
        event_id: &str,
        env: &str,
    ) -> Result<Option<EventRecord>, EventRepoError> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                self.event_select_sql("WHERE env = ? AND event_id = ?"),
                vec![DbValue::from(env), DbValue::from(event_id)],
            ))
            .await
            .map_err(storage_error)?;
        rows.first().map(event_from_row).transpose()
    }

    pub(super) async fn sql_claim_fanout_targets(
        &self,
        command: ClaimFanoutTargets,
    ) -> Result<Vec<EventFanoutTargetRecord>, EventRepoError> {
        validate_claim(
            &command.worker_id,
            command.now_ms,
            command.lease_until_ms,
            command.limit,
            &command.env,
        )?;
        let now = timestamp_value_from_ms(self.flavor, command.now_ms)?;
        let lease_until = timestamp_value_from_ms(self.flavor, command.lease_until_ms)?;
        let lease_owner = claim_owner(&command.worker_id);
        let steps = vec![
            DbTransactionStep::Execute(DbStatement::with_params(
                claim_fanout_targets_sql(self.flavor),
                vec![
                    DbValue::from(lease_owner.as_str()),
                    lease_until,
                    DbValue::from(command.env.as_str()),
                    now.clone(),
                    DbValue::from(command.limit),
                    DbValue::from(command.env.as_str()),
                    now,
                ],
            ).with_transaction_stop_on_no_rows()),
            DbTransactionStep::Query(DbStatement::with_params(
                self.target_select_sql(claimed_fanout_targets_clause()),
                vec![
                    DbValue::from(command.env.as_str()),
                    DbValue::from(lease_owner.as_str()),
                ],
            )),
        ];
        let results = self.db.transaction(steps).await.map_err(storage_error)?;
        let claimed_count = transaction_affected_rows(&results, 0)?;
        if claimed_count == 0 {
            return Ok(Vec::new());
        }
        let claimed_rows = transaction_rows(&results, 1)?;
        let returned_count = u64::try_from(claimed_rows.len())
            .map_err(|_| EventRepoError::Storage("fanout claim result is too large".into()))?;
        if claimed_count != returned_count {
            return Err(EventRepoError::Storage(format!(
                "fanout claim returned {} rows after updating {claimed_count}",
                claimed_rows.len()
            )));
        }
        claimed_rows
            .iter()
            .map(target_from_row)
            .collect()
    }

    pub(super) async fn sql_materialize_fanout_target(
        &self,
        command: MaterializeFanoutTarget,
    ) -> Result<EventDeliveryRecord, EventRepoError> {
        validate_materialization(&command)?;
        if let Some(existing) = self
            .get_delivery_by_target(&command.target_id, &command.delivery.env)
            .await?
        {
            return Ok(existing);
        }
        let target = self
            .get_target(&command.target_id, &command.delivery.env)
            .await?
            .ok_or_else(|| EventRepoError::NotFound(command.target_id.clone()))?;
        let event = self
            .get_event(&target.event_id, &command.delivery.env)
            .await?
            .ok_or_else(|| EventRepoError::Storage("target Event is missing".into()))?;
        if command.delivery.fanout_target_id != target.target_id
            || command.delivery.event_id != target.event_id
            || command.delivery.event_type != event.envelope.event_type
            || command.delivery.subscription_id != target.subscription_id
            || command.delivery.subscription_revision != target.subscription_revision
            || command.delivery.stream_key != event.envelope.stream.key
            || command.delivery.sequence != event.envelope.stream.sequence
            || command.delivery.replay_of_delivery_id != target.replay_of_delivery_id
        {
            return Err(EventRepoError::InvalidInput(
                "Delivery does not match its immutable fanout target and Event".into(),
            ));
        }
        let materialized_at = timestamp_value_from_ms(self.flavor, command.materialized_at_ms)?;
        let created_at = timestamp_value_from_ms(self.flavor, command.delivery.created_at_ms)?;
        let dead_lettered_at = optional_timestamp_value_from_ms(
            self.flavor,
            command.delivery.dead_lettered_at_ms,
        )?;
        let insert_step = 0;
        let steps = vec![
            DbTransactionStep::Execute(DbStatement::with_params(
                sql_with_timestamp_params(self.flavor, "INSERT INTO bcs_event_deliveries \
                 (delivery_id, fanout_target_id, event_id, \
                 subscription_id, subscription_revision, stream_key, sequence, payload_bytes, \
                 payload_sha256, status, attempt_count, first_attempt_at, last_attempt_at, \
                 next_attempt_at, lease_owner, lease_until, last_http_status, \
                 last_error_category, last_error_summary, dead_lettered_at, cancelled_at, \
                 skipped_at, skip_actor, skip_reason, replay_of_delivery_id, \
                 resolved_by_delivery_id, resolved_at, created_at, succeeded_at, env) \
                 SELECT ?, target.target_id, target.event_id, target.subscription_id, \
                 target.subscription_revision, event.stream_key, event.sequence, ?, ?, ?, \
                 0, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?, __bcs_timestamp_ms__, NULL, NULL, \
                 NULL, NULL, target.replay_of_delivery_id, NULL, NULL, \
                 __bcs_timestamp_ms__, NULL, target.env \
                 FROM bcs_event_fanout_targets target JOIN bcs_events event \
                   ON event.env = target.env AND event.event_id = target.event_id \
                 WHERE target.env = ? AND target.target_id = ? AND target.status = 'pending' \
                   AND target.lease_owner = ? \
                   AND target.lease_until > __bcs_timestamp_ms__"),
                vec![
                    DbValue::from(command.delivery.delivery_id.as_str()),
                    DbValue::from(command.delivery.payload_bytes.clone()),
                    DbValue::from(command.delivery.payload_sha256.as_str()),
                    DbValue::from(delivery_status_name(command.delivery.status)),
                    DbValue::from(command.delivery.last_error_category.clone()),
                    DbValue::from(command.delivery.last_error_summary.clone()),
                    dead_lettered_at,
                    created_at,
                    DbValue::from(command.delivery.env.as_str()),
                    DbValue::from(command.target_id.as_str()),
                    DbValue::from(command.expected_lease_owner.as_str()),
                    materialized_at.clone(),
                ],
            )),
            DbTransactionStep::Execute(DbStatement::with_params(
                sql_with_timestamp_params(self.flavor, "UPDATE bcs_event_fanout_targets SET \
                 status = 'materialized', materialized_at = __bcs_timestamp_ms__, \
                 lease_owner = NULL, lease_until = NULL \
                 WHERE env = ? AND target_id = ? AND status = 'pending' \
                   AND lease_owner = ? AND lease_until > __bcs_timestamp_ms__"),
                vec![
                    materialized_at.clone(),
                    DbValue::from(command.delivery.env.as_str()),
                    DbValue::from(command.target_id.as_str()),
                    DbValue::from(command.expected_lease_owner.as_str()),
                    materialized_at,
                ],
            )),
            DbTransactionStep::Execute(DbStatement::with_params(
                "UPDATE bcs_events SET fanout_status = 'completed' \
                 WHERE env = ? AND event_id = ? AND NOT EXISTS (\
                   SELECT 1 FROM bcs_event_fanout_targets target \
                   WHERE target.env = ? AND target.event_id = ? AND target.status = 'pending'\
                 )",
                vec![
                    DbValue::from(command.delivery.env.as_str()),
                    DbValue::from(command.delivery.event_id.as_str()),
                    DbValue::from(command.delivery.env.as_str()),
                    DbValue::from(command.delivery.event_id.as_str()),
                ],
            )),
        ];
        match self.db.transaction(steps).await {
            Ok(results) if transaction_affected_rows(&results, insert_step)? == 1 => self
                .get_delivery(&command.delivery.delivery_id, &command.delivery.env)
                .await?
                .map(|(delivery, _)| delivery)
                .ok_or_else(|| EventRepoError::Storage("materialized Delivery missing".into())),
            Ok(_) => Err(EventRepoError::LeaseLost(command.target_id)),
            Err(error) if error.is_duplicate_key() => self
                .get_delivery_by_target(&command.target_id, &command.delivery.env)
                .await?
                .ok_or_else(|| EventRepoError::Conflict(error.to_string())),
            Err(error) => Err(storage_error(error)),
        }
    }
}
