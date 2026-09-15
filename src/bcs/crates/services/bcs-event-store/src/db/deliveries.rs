//! SQL persistence for Event deliveries.

use super::*;

impl DbEventStore {
    pub(super) async fn sql_claim_deliveries(
        &self,
        command: ClaimEventDeliveries,
    ) -> Result<Vec<EventDeliveryRecord>, EventRepoError> {
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
                claim_deliveries_sql(self.flavor),
                vec![
                    DbValue::from(lease_owner.as_str()),
                    lease_until,
                    now.clone(),
                    now.clone(),
                    DbValue::from(command.env.as_str()),
                    now.clone(),
                    now.clone(),
                    DbValue::from(command.limit),
                    DbValue::from(command.env.as_str()),
                    now.clone(),
                    now,
                ],
            ).with_transaction_stop_on_no_rows()),
            DbTransactionStep::Execute(DbStatement::with_params(
                recover_reclaimed_delivery_attempts_sql(self.flavor),
                vec![
                    timestamp_value_from_ms(self.flavor, command.now_ms)?,
                    DbValue::from(command.env.as_str()),
                    DbValue::from(lease_owner.as_str()),
                ],
            )),
            DbTransactionStep::Execute(DbStatement::with_params(
                insert_claimed_delivery_attempts_sql(self.flavor),
                vec![
                    timestamp_value_from_ms(self.flavor, command.now_ms)?,
                    DbValue::from(command.worker_id.as_str()),
                    DbValue::from(command.env.as_str()),
                    DbValue::from(lease_owner.as_str()),
                ],
            )),
            DbTransactionStep::Query(DbStatement::with_params(
                recovered_delivery_attempts_sql(self.flavor),
                vec![
                    DbValue::from(command.env.as_str()),
                    DbValue::from(lease_owner.as_str()),
                ],
            )),
            DbTransactionStep::Execute(DbStatement::with_params(
                clear_claim_recovery_markers_sql(),
                vec![
                    DbValue::from(command.env.as_str()),
                    DbValue::from(lease_owner.as_str()),
                ],
            )),
            DbTransactionStep::Query(DbStatement::with_params(
                self.delivery_select_sql(claimed_deliveries_clause()),
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
        let attempt_count = transaction_affected_rows(&results, 2)?;
        let recovered_rows = transaction_rows(&results, 3)?;
        let cleared_recovery_markers = transaction_affected_rows(&results, 4)?;
        let claimed_rows = transaction_rows(&results, 5)?;
        let returned_count = u64::try_from(claimed_rows.len())
            .map_err(|_| EventRepoError::Storage("Delivery claim result is too large".into()))?;
        if claimed_count != attempt_count || claimed_count != returned_count {
            return Err(EventRepoError::Storage(format!(
                "Delivery claim updated {claimed_count} rows, inserted {attempt_count} Attempts, and returned {} rows",
                claimed_rows.len()
            )));
        }
        let recovered_row_count = u64::try_from(recovered_rows.len()).map_err(|_| {
            EventRepoError::Storage("Delivery recovery result is too large".into())
        })?;
        if recovered_row_count != cleared_recovery_markers {
            return Err(EventRepoError::Storage(format!(
                "Delivery claim returned {} recovery rows but cleared {cleared_recovery_markers} recovery markers",
                recovered_rows.len()
            )));
        }
        for row in recovered_rows {
            if let Err(error) = log_recovered_delivery_attempt(row) {
                warn!(
                    target: "bcs_event_webhook",
                    component = "delivery",
                    error = %error,
                    "failed to decode recovered webhook delivery attempt audit row"
                );
            }
        }
        claimed_rows
            .iter()
            .map(delivery_from_row)
            .collect()
    }

    pub(super) async fn sql_renew_delivery_lease(
        &self,
        command: RenewEventDeliveryLease,
    ) -> Result<EventDeliveryRecord, EventRepoError> {
        validate_lease_renewal(&command)?;
        let now = timestamp_value_from_ms(self.flavor, command.now_ms)?;
        let lease_until = timestamp_value_from_ms(self.flavor, command.lease_until_ms)?;
        let result = self
            .db
            .execute(DbStatement::with_params(
                sql_with_timestamp_params(self.flavor, "UPDATE bcs_event_deliveries SET \
                 lease_until = __bcs_timestamp_ms__ \
                 WHERE env = ? AND delivery_id = ? AND status = 'in_flight' \
                   AND lease_owner = ? AND attempt_count = ? \
                   AND lease_until > __bcs_timestamp_ms__"),
                vec![
                    lease_until,
                    DbValue::from(command.env.as_str()),
                    DbValue::from(command.delivery_id.as_str()),
                    DbValue::from(command.expected_lease_owner.as_str()),
                    DbValue::from(command.attempt_no),
                    now,
                ],
            ))
            .await
            .map_err(storage_error)?;
        if result.affected_rows != 1 {
            return Err(EventRepoError::LeaseLost(command.delivery_id));
        }
        self.get_delivery(&command.delivery_id, &command.env)
            .await?
            .map(|(delivery, _)| delivery)
            .ok_or_else(|| EventRepoError::Storage("renewed Delivery disappeared".to_string()))
    }

    pub(super) async fn sql_complete_delivery_attempt(
        &self,
        command: CompleteEventDeliveryAttempt,
    ) -> Result<EventDeliveryRecord, EventRepoError> {
        validate_completion(&command)?;
        let started_at = timestamp_value_from_ms(self.flavor, command.started_at_ms)?;
        let completed_at = timestamp_value_from_ms(self.flavor, command.completed_at_ms)?;
        let next_attempt_at =
            optional_timestamp_value_from_ms(self.flavor, command.next_attempt_at_ms)?;
        let succeeded_at = (command.next_status == EventDeliveryStatus::Succeeded)
            .then_some(command.completed_at_ms);
        let dead_lettered_at = (command.next_status == EventDeliveryStatus::DeadLettered)
            .then_some(command.completed_at_ms);
        let attempt_update_step = 0;
        let mut steps = vec![
            DbTransactionStep::Execute(DbStatement::with_params(
                sql_with_timestamp_params(self.flavor, "UPDATE bcs_event_delivery_attempts SET \
                 started_at = __bcs_timestamp_ms__, completed_at = __bcs_timestamp_ms__, \
                 latency_ms = ?, result = ?, http_status = ?, error_category = ?, \
                 error_summary = ?, response_bytes_observed = ? \
                 WHERE delivery_id = ? AND attempt_no = ? AND completed_at IS NULL \
                   AND EXISTS (SELECT 1 FROM bcs_event_deliveries delivery \
                     WHERE delivery.delivery_id = bcs_event_delivery_attempts.delivery_id \
                       AND delivery.status = 'in_flight' AND delivery.lease_owner = ? \
                       AND delivery.lease_until > __bcs_timestamp_ms__ \
                       AND delivery.attempt_count = ?)"),
                vec![
                    started_at,
                    completed_at.clone(),
                    DbValue::from(command.completed_at_ms - command.started_at_ms),
                    DbValue::from(attempt_result_name(command.result)),
                    optional_u64_value(command.http_status.map(u64::from)),
                    DbValue::from(command.error_category.clone()),
                    DbValue::from(command.error_summary.clone()),
                    DbValue::from(command.response_bytes_observed),
                    DbValue::from(command.delivery_id.as_str()),
                    DbValue::from(command.attempt_no),
                    DbValue::from(command.expected_lease_owner.as_str()),
                    completed_at.clone(),
                    DbValue::from(command.attempt_no),
                ],
            )),
            DbTransactionStep::Execute(DbStatement::with_params(
                sql_with_timestamp_params(self.flavor, "UPDATE bcs_event_deliveries SET \
                 status = ?, last_attempt_at = __bcs_timestamp_ms__, \
                 next_attempt_at = __bcs_timestamp_ms__, lease_owner = NULL, lease_until = NULL, \
                 last_http_status = ?, last_error_category = ?, last_error_summary = ?, \
                 dead_lettered_at = __bcs_timestamp_ms__, \
                 succeeded_at = __bcs_timestamp_ms__ \
                 WHERE delivery_id = ? AND status = 'in_flight' AND lease_owner = ? \
                   AND lease_until > __bcs_timestamp_ms__ AND attempt_count = ?"),
                vec![
                    DbValue::from(delivery_status_name(command.next_status)),
                    completed_at.clone(),
                    next_attempt_at,
                    optional_u64_value(command.http_status.map(u64::from)),
                    DbValue::from(command.error_category.clone()),
                    DbValue::from(command.error_summary.clone()),
                    optional_timestamp_value_from_ms(self.flavor, dead_lettered_at)?,
                    optional_timestamp_value_from_ms(self.flavor, succeeded_at)?,
                    DbValue::from(command.delivery_id.as_str()),
                    DbValue::from(command.expected_lease_owner.as_str()),
                    completed_at.clone(),
                    DbValue::from(command.attempt_no),
                ],
            )),
        ];
        if command.next_status == EventDeliveryStatus::Succeeded {
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                sql_with_timestamp_params(self.flavor, "UPDATE bcs_event_deliveries SET \
                 resolved_by_delivery_id = ?, resolved_at = __bcs_timestamp_ms__ \
                 WHERE delivery_id = (SELECT replay_of_delivery_id FROM (\
                   SELECT replay_of_delivery_id FROM bcs_event_deliveries WHERE delivery_id = ?\
                 ) replacement) AND status = 'dead_lettered' \
                   AND resolved_by_delivery_id IS NULL"),
                vec![
                    DbValue::from(command.delivery_id.as_str()),
                    completed_at,
                    DbValue::from(command.delivery_id.as_str()),
                ],
            )));
        }
        let delivery_query_step = steps.len();
        steps.push(DbTransactionStep::Query(DbStatement::with_params(
            self.delivery_select_sql("WHERE d.delivery_id = ?"),
            vec![DbValue::from(command.delivery_id.as_str())],
        )));
        let results = self.db.transaction(steps).await.map_err(map_write_error)?;
        if transaction_affected_rows(&results, attempt_update_step)? != 1 {
            return Err(EventRepoError::LeaseLost(command.delivery_id));
        }
        transaction_rows(&results, delivery_query_step)?
            .first()
            .map(delivery_from_row)
            .transpose()?
            .ok_or_else(|| EventRepoError::Storage("completed Delivery missing".into()))
    }

    pub(super) async fn sql_get_delivery(
        &self,
        delivery_id: &str,
        env: &str,
    ) -> Result<Option<(EventDeliveryRecord, Vec<EventDeliveryAttemptRecord>)>, EventRepoError>
    {
        let rows = self
            .db
            .query(DbStatement::with_params(
                self.delivery_select_sql("WHERE d.env = ? AND d.delivery_id = ?"),
                vec![DbValue::from(env), DbValue::from(delivery_id)],
            ))
            .await
            .map_err(storage_error)?;
        let Some(delivery) = rows.first().map(delivery_from_row).transpose()? else {
            return Ok(None);
        };
        let attempt_rows = self
            .db
            .query(DbStatement::with_params(
                format!(
                    "SELECT delivery_id, attempt_no, {}, {}, latency_ms, result, http_status, \
                     error_category, error_summary, response_bytes_observed, worker_id \
                     FROM bcs_event_delivery_attempts WHERE delivery_id = ? ORDER BY attempt_no",
                    timestamp_ms_expr(self.flavor, "started_at", "started_at_ms"),
                    timestamp_ms_expr(self.flavor, "completed_at", "completed_at_ms"),
                ),
                vec![DbValue::from(delivery_id)],
            ))
            .await
            .map_err(storage_error)?;
        let attempts = attempt_rows
            .iter()
            .map(attempt_from_row)
            .collect::<Result<Vec<_>, _>>()?;
        Ok(Some((delivery, attempts)))
    }

    pub(super) async fn sql_list_deliveries(
        &self,
        query: ListEventDeliveryRecords,
    ) -> Result<Vec<EventDeliveryRecord>, EventRepoError> {
        validate_list_limit(query.limit, "Delivery")?;
        let mut clause = "WHERE d.env = ?".to_string();
        let mut params = vec![DbValue::from(query.env.as_str())];
        if let Some(subscription_id) = query.subscription_id.as_deref() {
            clause.push_str(" AND d.subscription_id = ?");
            params.push(DbValue::from(subscription_id));
        }
        if let Some(event_id) = query.event_id.as_deref() {
            clause.push_str(" AND d.event_id = ?");
            params.push(DbValue::from(event_id));
        }
        if let Some(status) = query.status {
            clause.push_str(" AND d.status = ?");
            params.push(DbValue::from(delivery_status_name(status)));
        }
        if let Some(after_delivery_id) = query.after_delivery_id.as_deref() {
            clause.push_str(" AND d.delivery_id > ?");
            params.push(DbValue::from(after_delivery_id));
        }
        clause.push_str(" ORDER BY d.delivery_id LIMIT ?");
        params.push(DbValue::from(query.limit));
        let rows = self
            .db
            .query(DbStatement::with_params(
                self.delivery_select_sql(&clause),
                params,
            ))
            .await
            .map_err(storage_error)?;
        rows.iter().map(delivery_from_row).collect()
    }
}
