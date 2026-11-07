use super::*;
impl MySqlCollaborationStore {

    pub(super) async fn sql_upsert_delivery_correlation(
        &self,
        correlation: StateMachineDeliveryCorrelation,
    ) -> ServiceResult<()> {
        let now = current_millis();
        let coalesce_bot_run = match self.flavor {
            DbSqlFlavor::Mysql => "COALESCE(VALUES(bot_delivery_run_id), bot_delivery_run_id)",
            DbSqlFlavor::Sqlite => "COALESCE(excluded.bot_delivery_run_id, bot_delivery_run_id)",
        };
        let correlation_upsert = self.flavor.on_conflict_update(
            &["env", "delivery_request_id"],
            &[
                "state_machine_run_id",
                "node_id",
                "attempt",
                "assignee_bot_id",
                "updated_at_ms",
            ],
            &[
                ("bot_delivery_run_id", coalesce_bot_run),
                ("record_status", "'active'"),
                ("gmt_modified", self.flavor.now()),
            ],
        );
        let sql = format!(
            "INSERT INTO bcs_state_machine_delivery_correlations \
             (env, state_machine_run_id, node_id, attempt, assignee_bot_id, \
              delivery_request_id, bot_delivery_run_id, created_at_ms, \
              updated_at_ms, record_status) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active') \
             {correlation_upsert}"
        );
        self.db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(correlation.state_machine_run_id.as_str()),
                    DbValue::from(correlation.node_id.as_str()),
                    DbValue::from(correlation.attempt),
                    DbValue::from(correlation.assignee_bot_id.as_str()),
                    DbValue::from(correlation.delivery_request_id.as_str()),
                    DbValue::from(correlation.bot_delivery_run_id.as_deref()),
                    DbValue::from(now),
                    DbValue::from(now),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("delivery correlation upsert: {error}"))
            })?;
        Ok(())
    }

    pub(super) async fn sql_register_delivery_alias(
        &self,
        delivery_request_id: &str,
        bot_delivery_run_id: String,
    ) -> ServiceResult<()> {
        let now = current_millis();
        let correlation_sql = format!(
            "UPDATE bcs_state_machine_delivery_correlations \
             SET bot_delivery_run_id = ?, updated_at_ms = ?, \
                 {} \
             WHERE env = ? AND delivery_request_id = ? \
               AND record_status = 'active'",
            self.flavor.set_modified_now()
        );
        let node_sql = format!(
            "UPDATE bcs_state_machine_node_runs \
             SET bot_delivery_run_id = ?, {} \
             WHERE env = ? AND delivery_request_id = ? \
               AND record_status = 'active'",
            self.flavor.set_modified_now()
        );
        let results = self
            .db
            .transaction(vec![
                DbTransactionStep::Execute(DbStatement::with_params(
                    correlation_sql,
                    vec![
                        DbValue::from(bot_delivery_run_id.as_str()),
                        DbValue::from(now),
                        DbValue::from(self.env.as_str()),
                        DbValue::from(delivery_request_id),
                    ],
                )),
                DbTransactionStep::Execute(DbStatement::with_params(
                    node_sql,
                    vec![
                        DbValue::from(bot_delivery_run_id.as_str()),
                        DbValue::from(self.env.as_str()),
                        DbValue::from(delivery_request_id),
                    ],
                )),
            ])
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("delivery correlation alias: {error}"))
            })?;
        let correlation_affected = match results.first() {
            Some(DbTransactionStepResult::Executed(result)) => result.affected_rows,
            _ => 0,
        };
        if correlation_affected == 0 {
            return Err(ServiceError::InternalError(format!(
                "delivery correlation not found: {delivery_request_id}"
            )));
        }
        Ok(())
    }

    pub(super) async fn sql_lookup_delivery_correlation(
        &self,
        run_id: &str,
    ) -> ServiceResult<Option<StateMachineDeliveryCorrelation>> {
        let request_sql = format!(
            "SELECT {SM_CORRELATION_SELECT_COLS} \
             FROM bcs_state_machine_delivery_correlations \
             WHERE env = ? AND delivery_request_id = ? AND record_status = 'active' \
             LIMIT 1"
        );
        let rows = self
            .db
            .query(DbStatement::with_params(
                request_sql,
                vec![DbValue::from(self.env.as_str()), DbValue::from(run_id)],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("delivery correlation lookup: {error}"))
            })?;
        if let Some(row) = rows.into_iter().next() {
            return row_to_delivery_correlation(row).map(Some);
        }

        let bot_run_sql = format!(
            "SELECT {SM_CORRELATION_SELECT_COLS} \
             FROM bcs_state_machine_delivery_correlations \
             WHERE env = ? AND bot_delivery_run_id = ? AND record_status = 'active' \
             LIMIT 1"
        );
        let rows = self
            .db
            .query(DbStatement::with_params(
                bot_run_sql,
                vec![DbValue::from(self.env.as_str()), DbValue::from(run_id)],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("delivery correlation alias lookup: {error}"))
            })?;
        rows.into_iter()
            .next()
            .map(row_to_delivery_correlation)
            .transpose()
    }
}
