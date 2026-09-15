//! SQL persistence for Event subscriptions.

use super::*;

impl DbEventStore {
    pub(super) async fn sql_create_subscription(
        &self,
        record: CreateEventSubscriptionRecord,
    ) -> Result<EventSubscriptionRecord, EventRepoError> {
        validate_new_subscription(&record)?;
        let scope_id = scope_storage_id(&record.subscription.scope);
        let scope_type = scope_type_name(record.subscription.scope.scope_type);
        let created_at = timestamp_value_from_ms(self.flavor, record.subscription.created_at_ms)?;
        let updated_at = timestamp_value_from_ms(self.flavor, record.subscription.updated_at_ms)?;
        let activated_at = timestamp_value_from_ms(self.flavor, record.revision.activated_at_ms)?;
        let audit_id = uuid::Uuid::new_v4().to_string();
        let event_filters = serde_json::to_string(&record.revision.event_filters)
            .map_err(|error| EventRepoError::InvalidInput(format!("serialize filters: {error}")))?;

        let mut steps = scope_lock_steps(
            self.flavor,
            &record.subscription.env,
            scope_type,
            scope_id.as_str(),
        );
        let reserved_count_step = steps.len();
        steps.push(DbTransactionStep::Query(DbStatement::with_params(
            "SELECT COUNT(*) AS reserved_count FROM bcs_event_subscriptions \
             WHERE env = ? AND scope_type = ? AND scope_id = ? \
               AND status IN ('pending', 'active')",
            vec![
                DbValue::from(record.subscription.env.as_str()),
                DbValue::from(scope_type),
                DbValue::from(scope_id.as_str()),
            ],
        )));
        let subscription_insert_step = steps.len();
        steps.push(DbTransactionStep::Execute(
            DbStatement::with_transaction_params(
                sql_with_timestamp_params(self.flavor, "INSERT INTO bcs_event_subscriptions \
                 (subscription_id, name, scope_type, scope_id, \
                 status, current_revision, created_by_type, created_by_id, \
                 created_at, updated_at, deleted_at, env) \
                 SELECT ?, ?, ?, ?, ?, ?, ?, ?, __bcs_timestamp_ms__, \
                 __bcs_timestamp_ms__, __bcs_timestamp_ms__, ? WHERE ? < ?"),
                vec![
                    DbValue::from(record.subscription.subscription_id.as_str()).into(),
                    DbValue::from(record.subscription.name.as_str()).into(),
                    DbValue::from(scope_type).into(),
                    scope_db_value(&record.subscription.scope).into(),
                    DbValue::from(subscription_status_name(record.subscription.status)).into(),
                    DbValue::from(record.subscription.current_revision).into(),
                    DbValue::from(actor_type_name(record.subscription.created_by.actor_type))
                        .into(),
                    DbValue::from(record.subscription.created_by.id.as_str()).into(),
                    created_at.clone().into(),
                    updated_at.clone().into(),
                    optional_timestamp_value_from_ms(
                        self.flavor,
                        record.subscription.deleted_at_ms,
                    )?
                    .into(),
                    DbValue::from(record.subscription.env.as_str()).into(),
                    DbTransactionParam::query_result(reserved_count_step, 0, "reserved_count"),
                    DbValue::from(record.scope_limit).into(),
                ],
            ),
        ));
        steps.push(DbTransactionStep::Execute(
            revision_insert_if_subscription_exists_statement(
                &record.revision,
                &record.subscription.env,
                &event_filters,
                self.flavor,
                &activated_at,
            )?,
        ));
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            sql_with_timestamp_params(self.flavor, "INSERT INTO bcs_event_subscription_audits \
             (audit_id, subscription_id, revision, \
             action, actor_type, actor_id, reason, details_json, created_at, env) \
             SELECT ?, ?, ?, 'created', ?, ?, NULL, NULL, __bcs_timestamp_ms__, ? WHERE EXISTS (\
               SELECT 1 FROM bcs_event_subscriptions WHERE env = ? AND subscription_id = ?\
             )"),
            vec![
                DbValue::from(audit_id),
                DbValue::from(record.subscription.subscription_id.as_str()),
                DbValue::from(record.subscription.current_revision),
                DbValue::from(actor_type_name(record.subscription.created_by.actor_type)),
                DbValue::from(record.subscription.created_by.id.as_str()),
                created_at,
                DbValue::from(record.subscription.env.as_str()),
                DbValue::from(record.subscription.env.as_str()),
                DbValue::from(record.subscription.subscription_id.as_str()),
            ],
        )));
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            "UPDATE bcs_event_scope_epochs SET epoch = epoch + 1, updated_at = CURRENT_TIMESTAMP \
             WHERE env = ? AND scope_type = ? AND scope_id = ? AND EXISTS (\
               SELECT 1 FROM bcs_event_subscriptions WHERE env = ? AND subscription_id = ?\
             )",
            vec![
                DbValue::from(record.subscription.env.as_str()),
                DbValue::from(scope_type),
                DbValue::from(scope_id.as_str()),
                DbValue::from(record.subscription.env.as_str()),
                DbValue::from(record.subscription.subscription_id.as_str()),
            ],
        )));
        let results = self.db.transaction(steps).await.map_err(map_write_error)?;
        if transaction_affected_rows(&results, subscription_insert_step)? == 0 {
            return Err(EventRepoError::LimitReached(
                "Scope has reached its Event Subscription limit".to_string(),
            ));
        }
        Ok(record.subscription)
    }

    pub(super) async fn sql_cancel_pending_subscriptions(
        &self,
        command: CancelPendingEventSubscriptions,
    ) -> Result<u64, EventRepoError> {
        if command.reason.trim().is_empty() {
            return Err(EventRepoError::InvalidInput(
                "pending Subscription cancellation reason must not be empty".to_string(),
            ));
        }
        let cancelled_at = timestamp_value_from_ms(self.flavor, command.cancelled_at_ms)?;
        let mut steps = Vec::with_capacity(command.subscription_ids.len() * 2);
        let mut update_steps = Vec::with_capacity(command.subscription_ids.len());
        for subscription_id in &command.subscription_ids {
            update_steps.push(steps.len());
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                sql_with_timestamp_params(self.flavor, "UPDATE bcs_event_subscriptions SET \
                 status = 'deleted', updated_at = __bcs_timestamp_ms__, \
                 deleted_at = __bcs_timestamp_ms__ WHERE env = ? AND subscription_id = ? \
                 AND status = 'pending'"),
                vec![
                    cancelled_at.clone(),
                    cancelled_at.clone(),
                    DbValue::from(command.env.as_str()),
                    DbValue::from(subscription_id.as_str()),
                ],
            )));
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                sql_with_timestamp_params(self.flavor, "INSERT INTO bcs_event_subscription_audits \
                 (audit_id, subscription_id, revision, action, actor_type, actor_id, reason, \
                  details_json, created_at, env) \
                 SELECT ?, subscription_id, current_revision, 'provisioning_cancelled', ?, ?, ?, \
                        NULL, __bcs_timestamp_ms__, env FROM bcs_event_subscriptions \
                 WHERE env = ? AND subscription_id = ? AND status = 'deleted' \
                   AND deleted_at = __bcs_timestamp_ms__"),
                vec![
                    DbValue::from(uuid::Uuid::new_v4().to_string()),
                    DbValue::from(actor_type_name(command.actor.actor_type)),
                    DbValue::from(command.actor.id.as_str()),
                    DbValue::from(command.reason.as_str()),
                    cancelled_at.clone(),
                    DbValue::from(command.env.as_str()),
                    DbValue::from(subscription_id.as_str()),
                    cancelled_at.clone(),
                ],
            )));
        }
        if steps.is_empty() {
            return Ok(0);
        }
        let results = self.db.transaction(steps).await.map_err(map_write_error)?;
        update_steps.into_iter().try_fold(0u64, |total, step| {
            transaction_affected_rows(&results, step).map(|affected| total + affected)
        })
    }

    pub(super) async fn sql_get_subscription(
        &self,
        subscription_id: &str,
        env: &str,
    ) -> Result<Option<(EventSubscriptionRecord, EventSubscriptionRevisionRecord)>, EventRepoError>
    {
        let rows = self
            .db
            .query(DbStatement::with_params(
                self.subscription_select_sql("WHERE env = ? AND subscription_id = ?"),
                vec![DbValue::from(env), DbValue::from(subscription_id)],
            ))
            .await
            .map_err(storage_error)?;
        let Some(row) = rows.first() else {
            return Ok(None);
        };
        let subscription = subscription_from_row(row)?;
        let revision_rows = self
            .db
            .query(DbStatement::with_params(
                self.revision_select_sql(),
                vec![
                    DbValue::from(env),
                    DbValue::from(subscription_id),
                    DbValue::from(subscription.current_revision),
                ],
            ))
            .await
            .map_err(storage_error)?;
        let revision = revision_rows
            .first()
            .ok_or_else(|| {
                EventRepoError::Storage("current subscription revision missing".to_string())
            })
            .and_then(revision_from_row)?;
        Ok(Some((subscription, revision)))
    }

    pub(super) async fn sql_get_subscription_revision(
        &self,
        subscription_id: &str,
        revision: u64,
        env: &str,
    ) -> Result<Option<EventSubscriptionRevisionRecord>, EventRepoError> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                self.revision_select_sql(),
                vec![
                    DbValue::from(env),
                    DbValue::from(subscription_id),
                    DbValue::from(revision),
                ],
            ))
            .await
            .map_err(storage_error)?;
        rows.first().map(revision_from_row).transpose()
    }

    pub(super) async fn sql_list_subscriptions(
        &self,
        query: ListEventSubscriptionRecords,
    ) -> Result<Vec<EventSubscriptionRecord>, EventRepoError> {
        if query.limit == 0 || query.limit > 100 {
            return Err(EventRepoError::InvalidInput(
                "subscription list limit must be between 1 and 100".to_string(),
            ));
        }
        let rows = self
            .db
            .query(DbStatement::with_params(
                self.subscription_select_sql("WHERE env = ? ORDER BY subscription_id"),
                vec![DbValue::from(query.env.as_str())],
            ))
            .await
            .map_err(storage_error)?;
        let mut records = rows
            .iter()
            .map(subscription_from_row)
            .collect::<Result<Vec<_>, _>>()?;
        records.retain(|record| query.status.is_none_or(|status| record.status == status));
        records.retain(|record| {
            query
                .scope
                .as_ref()
                .is_none_or(|scope| record.scope == *scope)
        });
        records.retain(|record| {
            query
                .after_subscription_id
                .as_ref()
                .is_none_or(|after| record.subscription_id > *after)
        });
        records.truncate(query.limit as usize);
        Ok(records)
    }

    pub(super) async fn sql_replace_subscription_revision(
        &self,
        command: ReplaceEventSubscriptionRevision,
    ) -> Result<EventSubscriptionRecord, EventRepoError> {
        validate_replacement(&command)?;
        let (current, _) = self
            .get_subscription(&command.subscription_id, &command.env)
            .await?
            .ok_or_else(|| EventRepoError::NotFound(command.subscription_id.clone()))?;
        if current.current_revision != command.expected_revision {
            return Err(EventRepoError::Conflict(format!(
                "expected revision {}, found {}",
                command.expected_revision, current.current_revision
            )));
        }
        if current.status != command.status && !current.status.can_transition_to(command.status) {
            return Err(EventRepoError::Conflict(format!(
                "invalid subscription status transition {:?} -> {:?}",
                current.status, command.status
            )));
        }
        let audit_action = match (current.status, command.status) {
            (_, EventSubscriptionStatus::Deleted) => "deleted",
            (EventSubscriptionStatus::Disabled, EventSubscriptionStatus::Active) => "enabled",
            (_, EventSubscriptionStatus::Disabled) => "disabled",
            _ => "updated",
        };

        let scope_id = scope_storage_id(&current.scope);
        let scope_type = scope_type_name(current.scope.scope_type);
        let updated_at = timestamp_value_from_ms(self.flavor, command.updated_at_ms)?;
        let event_filters = serde_json::to_string(&command.revision.event_filters)
            .map_err(|error| EventRepoError::InvalidInput(format!("serialize filters: {error}")))?;
        let activated_at = timestamp_value_from_ms(self.flavor, command.revision.activated_at_ms)?;
        let audit_id = uuid::Uuid::new_v4().to_string();
        let mut steps = scope_lock_steps(self.flavor, &command.env, scope_type, scope_id.as_str());
        steps.push(DbTransactionStep::Query(DbStatement::with_params(
            subscription_lock_sql(self.flavor),
            vec![
                DbValue::from(command.env.as_str()),
                DbValue::from(command.subscription_id.as_str()),
            ],
        )));
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            sql_with_timestamp_params(self.flavor, "UPDATE bcs_event_subscription_revisions \
             SET retired_at = __bcs_timestamp_ms__ \
             WHERE env = ? AND subscription_id = ? AND revision = ? AND retired_at IS NULL"),
            vec![
                updated_at.clone(),
                DbValue::from(command.env.as_str()),
                DbValue::from(command.subscription_id.as_str()),
                DbValue::from(command.expected_revision),
            ],
        )));
        steps.push(DbTransactionStep::Execute(revision_insert_statement(
            &command.revision,
            &command.env,
            &event_filters,
            self.flavor,
            &activated_at,
        )?));
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            sql_with_timestamp_params(self.flavor, "UPDATE bcs_event_subscriptions SET \
             name = ?, status = ?, current_revision = ?, updated_at = __bcs_timestamp_ms__, \
             deleted_at = CASE WHEN ? = 'deleted' THEN __bcs_timestamp_ms__ ELSE deleted_at END \
             WHERE env = ? AND subscription_id = ? AND current_revision = ?"),
            vec![
                DbValue::from(command.name.as_str()),
                DbValue::from(subscription_status_name(command.status)),
                DbValue::from(command.revision.revision),
                updated_at.clone(),
                DbValue::from(subscription_status_name(command.status)),
                updated_at.clone(),
                DbValue::from(command.env.as_str()),
                DbValue::from(command.subscription_id.as_str()),
                DbValue::from(command.expected_revision),
            ],
        )));
        if command.cancel_retired_pending_deliveries {
            let cancel_all_revisions = matches!(
                command.status,
                EventSubscriptionStatus::Disabled | EventSubscriptionStatus::Deleted
            );
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                sql_with_timestamp_params(self.flavor, "UPDATE bcs_event_deliveries SET \
                 status = 'cancelled', cancelled_at = __bcs_timestamp_ms__, \
                 lease_owner = NULL, lease_until = NULL \
                 WHERE env = ? AND subscription_id = ? \
                   AND (? = TRUE OR subscription_revision = ?) \
                   AND status IN ('pending', 'retry_wait')"),
                vec![
                    updated_at.clone(),
                    DbValue::from(command.env.as_str()),
                    DbValue::from(command.subscription_id.as_str()),
                    DbValue::from(cancel_all_revisions),
                    DbValue::from(command.expected_revision),
                ],
            )));
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                sql_with_timestamp_params(self.flavor, "UPDATE bcs_event_fanout_targets SET \
                 status = 'cancelled', cancelled_at = __bcs_timestamp_ms__ \
                 WHERE env = ? AND subscription_id = ? \
                   AND (? = TRUE OR subscription_revision = ?) \
                   AND status = 'pending'"),
                vec![
                    updated_at.clone(),
                    DbValue::from(command.env.as_str()),
                    DbValue::from(command.subscription_id.as_str()),
                    DbValue::from(cancel_all_revisions),
                    DbValue::from(command.expected_revision),
                ],
            )));
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                "UPDATE bcs_events SET fanout_status = 'completed' WHERE env = ? \
                 AND fanout_status = 'pending' AND NOT EXISTS (\
                   SELECT 1 FROM bcs_event_fanout_targets target \
                   WHERE target.env = bcs_events.env AND target.event_id = bcs_events.event_id \
                     AND target.status = 'pending'\
                 )",
                vec![DbValue::from(command.env.as_str())],
            )));
        }
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            sql_with_timestamp_params(self.flavor, "INSERT INTO bcs_event_subscription_audits \
             (audit_id, subscription_id, revision, \
             action, actor_type, actor_id, reason, details_json, created_at, env) \
             VALUES (?, ?, ?, ?, ?, ?, ?, NULL, __bcs_timestamp_ms__, ?)"),
            vec![
                DbValue::from(audit_id),
                DbValue::from(command.subscription_id.as_str()),
                DbValue::from(command.revision.revision),
                DbValue::from(audit_action),
                DbValue::from(actor_type_name(command.actor.actor_type)),
                DbValue::from(command.actor.id.as_str()),
                DbValue::from(command.reason.clone()),
                updated_at,
                DbValue::from(command.env.as_str()),
            ],
        )));
        steps.push(scope_epoch_increment_step(
            &command.env,
            scope_type,
            scope_id.as_str(),
        ));
        self.db.transaction(steps).await.map_err(map_write_error)?;
        self.get_subscription(&command.subscription_id, &command.env)
            .await?
            .map(|(record, _)| record)
            .ok_or_else(|| EventRepoError::Storage("updated subscription disappeared".to_string()))
    }
}
