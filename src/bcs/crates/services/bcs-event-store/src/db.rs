//! SQL-backed Event repository over the replaceable `DbPlugin` contract.

use std::collections::BTreeMap;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_db_api::{
    DbError, DbPlugin, DbRow, DbSqlFlavor, DbStatement, DbTransactionParam, DbTransactionStep,
    DbTransactionStepResult, DbValue, db_get_column, db_get_column_opt,
};
use bcs_service_api::port::repo::{
    AppendEventRecord, AppendEventRecordResult, CancelPendingEventSubscriptions,
    ClaimEventDeliveries, ClaimFanoutTargets, CompleteEventDeliveryAttempt,
    CreateEventReplayTarget, CreateEventSubscriptionRecord, EventDeliveryAttemptRecord,
    EventDeliveryAttemptRecordResult, EventDeliveryRecord, EventFanoutStatus,
    EventFanoutTargetPurpose, EventFanoutTargetRecord, EventFanoutTargetStatus, EventRecord,
    EventRepoError, EventRepoPort, EventRetentionRequest, EventRetentionResult,
    EventSubscriptionRecord, EventSubscriptionRevisionRecord, ListEventDeliveryRecords,
    ListEventSubscriptionRecords, MaterializeFanoutTarget, RenewEventDeliveryLease,
    ReplaceEventSubscriptionRevision, SkipDeadLetteredEventDelivery,
};
use bcs_service_api::types::{
    EVENT_SOURCE, EVENT_SPEC_VERSION, EventActor, EventActorType, EventDeliveryStatus,
    EventEnvelope, EventPayloadMode, EventScope, EventStream, EventSubscriptionScope,
    EventSubscriptionScopeType, EventSubscriptionStatus,
};
use chrono::{SecondsFormat, TimeZone, Utc};
use sha2::{Digest, Sha256};

use crate::transaction_plan::EventAppendTransactionPlan;
use crate::timestamp::{
    optional_timestamp_value_from_ms, sql_with_timestamp_params, timestamp_value_from_ms,
};
use crate::{event_filter_matches, validate_scope};

#[derive(Clone)]
pub struct DbEventStore {
    db: Arc<dyn DbPlugin>,
    flavor: DbSqlFlavor,
}

impl DbEventStore {
    pub fn mysql(db: Arc<dyn DbPlugin>) -> Self {
        Self {
            db,
            flavor: DbSqlFlavor::Mysql,
        }
    }

    pub fn sqlite(db: Arc<dyn DbPlugin>) -> Self {
        Self {
            db,
            flavor: DbSqlFlavor::Sqlite,
        }
    }

    /// Build the store-owned fragment for composition into a business
    /// repository transaction. Callers prepend their mutation steps and pass
    /// that count so transaction-result bindings remain correct.
    pub fn append_transaction_plan(
        &self,
        command: &AppendEventRecord,
        preceding_step_count: usize,
    ) -> Result<EventAppendTransactionPlan, EventRepoError> {
        EventAppendTransactionPlan::build(command, self.flavor, preceding_step_count)
    }

    async fn get_event_by_producer(
        &self,
        env: &str,
        producer: &str,
        producer_key: &str,
        event_type: &str,
    ) -> Result<Option<EventRecord>, EventRepoError> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                self.event_select_sql(
                    "WHERE env = ? AND producer = ? AND producer_key = ? AND event_type = ?",
                ),
                vec![
                    DbValue::from(env),
                    DbValue::from(producer),
                    DbValue::from(producer_key),
                    DbValue::from(event_type),
                ],
            ))
            .await
            .map_err(storage_error)?;
        rows.first().map(event_from_row).transpose()
    }

    async fn target_ids(&self, env: &str, event_id: &str) -> Result<Vec<String>, EventRepoError> {
        self.db
            .query(DbStatement::with_params(
                "SELECT target_id FROM bcs_event_fanout_targets \
                 WHERE env = ? AND event_id = ? ORDER BY target_id",
                vec![DbValue::from(env), DbValue::from(event_id)],
            ))
            .await
            .map_err(storage_error)?
            .iter()
            .map(|row| db_get_column(row, "target_id").map_err(storage_error))
            .collect()
    }

    fn event_select_sql(&self, clause: &str) -> String {
        format!(
            "SELECT event_id, event_type, schema_version, producer, producer_key, subject_type, \
             subject_id, group_id, session_id, task_id, run_id, \
             stream_key, sequence, actor_json, correlation_id, causation_event_id, trace_id, \
             data_json, fanout_status, env, {}, {}, {} FROM bcs_events {}",
            timestamp_ms_expr(self.flavor, "occurred_at", "occurred_at_ms"),
            timestamp_ms_expr(self.flavor, "recorded_at", "recorded_at_ms"),
            timestamp_ms_expr(self.flavor, "retention_until", "retention_until_ms"),
            clause
        )
    }

    fn subscription_select_sql(&self, clause: &str) -> String {
        format!(
            "SELECT subscription_id, name, scope_type, scope_id, status, current_revision, \
             created_by_type, created_by_id, {}, {}, {}, env \
             FROM bcs_event_subscriptions {}",
            timestamp_ms_expr(self.flavor, "created_at", "created_at_ms"),
            timestamp_ms_expr(self.flavor, "updated_at", "updated_at_ms"),
            timestamp_ms_expr(self.flavor, "deleted_at", "deleted_at_ms"),
            clause
        )
    }

    fn revision_select_sql(&self) -> String {
        format!(
            "SELECT subscription_id, revision, event_filters_json, payload_mode, \
             endpoint_url, \
             request_timeout_ms, {}, {} FROM bcs_event_subscription_revisions \
             WHERE env = ? AND subscription_id = ? AND revision = ?",
            timestamp_ms_expr(self.flavor, "activated_at", "activated_at_ms"),
            timestamp_ms_expr(self.flavor, "retired_at", "retired_at_ms"),
        )
    }

    fn target_select_sql(&self, clause: &str) -> String {
        format!(
            "SELECT target_id, event_id, subscription_id, subscription_revision, purpose, \
             replay_request_id, replay_of_delivery_id, depends_on_target_id, status, \
             lease_owner, {}, {}, {}, {}, env FROM bcs_event_fanout_targets {}",
            timestamp_ms_expr(self.flavor, "lease_until", "lease_until_ms"),
            timestamp_ms_expr(self.flavor, "created_at", "created_at_ms"),
            timestamp_ms_expr(self.flavor, "materialized_at", "materialized_at_ms"),
            timestamp_ms_expr(self.flavor, "cancelled_at", "cancelled_at_ms"),
            clause
        )
    }

    fn delivery_select_sql(&self, clause: &str) -> String {
        format!(
            "SELECT d.delivery_id, d.fanout_target_id, d.event_id, e.event_type, \
             d.subscription_id, d.subscription_revision, d.stream_key, d.sequence, \
             d.payload_bytes, d.payload_sha256, d.status, d.attempt_count, {}, {}, {}, {}, \
             d.lease_owner, {}, d.last_http_status, d.last_error_category, \
             d.last_error_summary, {}, {}, {}, d.skip_actor, d.skip_reason, \
             d.replay_of_delivery_id, d.resolved_by_delivery_id, {}, {}, d.env \
             FROM bcs_event_deliveries d JOIN bcs_events e \
               ON e.env = d.env AND e.event_id = d.event_id {}",
            timestamp_ms_expr(self.flavor, "d.first_attempt_at", "first_attempt_at_ms"),
            timestamp_ms_expr(self.flavor, "d.last_attempt_at", "last_attempt_at_ms"),
            timestamp_ms_expr(self.flavor, "d.next_attempt_at", "next_attempt_at_ms"),
            timestamp_ms_expr(self.flavor, "d.created_at", "created_at_ms"),
            timestamp_ms_expr(self.flavor, "d.lease_until", "lease_until_ms"),
            timestamp_ms_expr(self.flavor, "d.dead_lettered_at", "dead_lettered_at_ms"),
            timestamp_ms_expr(self.flavor, "d.cancelled_at", "cancelled_at_ms"),
            timestamp_ms_expr(self.flavor, "d.skipped_at", "skipped_at_ms"),
            timestamp_ms_expr(self.flavor, "d.resolved_at", "resolved_at_ms"),
            timestamp_ms_expr(self.flavor, "d.succeeded_at", "succeeded_at_ms"),
            clause
        )
    }

    async fn get_delivery_by_target(
        &self,
        target_id: &str,
        env: &str,
    ) -> Result<Option<EventDeliveryRecord>, EventRepoError> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                self.delivery_select_sql("WHERE d.env = ? AND d.fanout_target_id = ?"),
                vec![DbValue::from(env), DbValue::from(target_id)],
            ))
            .await
            .map_err(storage_error)?;
        rows.first().map(delivery_from_row).transpose()
    }

    async fn get_target(
        &self,
        target_id: &str,
        env: &str,
    ) -> Result<Option<EventFanoutTargetRecord>, EventRepoError> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                self.target_select_sql("WHERE env = ? AND target_id = ?"),
                vec![DbValue::from(env), DbValue::from(target_id)],
            ))
            .await
            .map_err(storage_error)?;
        rows.first().map(target_from_row).transpose()
    }

    async fn replay_target_for_request(
        &self,
        command: &CreateEventReplayTarget,
    ) -> Result<Option<EventFanoutTargetRecord>, EventRepoError> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                self.target_select_sql(
                    "WHERE env = ? AND subscription_id = ? AND subscription_revision = ? \
                     AND purpose = 'manual_replay' AND replay_request_id = ? \
                     AND replay_of_delivery_id = ? LIMIT 1",
                ),
                vec![
                    DbValue::from(command.env.as_str()),
                    DbValue::from(command.subscription_id.as_str()),
                    DbValue::from(command.subscription_revision),
                    DbValue::from(command.replay_request_id.as_str()),
                    DbValue::from(command.original_delivery_id.as_str()),
                ],
            ))
            .await
            .map_err(storage_error)?;
        rows.first().map(target_from_row).transpose()
    }
}

#[async_trait]
impl EventRepoPort for DbEventStore {
    async fn create_subscription(
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

    async fn cancel_pending_subscriptions(
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

    async fn get_subscription(
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

    async fn get_subscription_revision(
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

    async fn list_subscriptions(
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

    async fn replace_subscription_revision(
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

    async fn append_event(
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

    async fn get_event(
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

    async fn claim_fanout_targets(
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
                    lease_until.clone(),
                    DbValue::from(command.env.as_str()),
                    now.clone(),
                    DbValue::from(command.limit),
                    DbValue::from(command.env.as_str()),
                    now,
                ],
            )),
            DbTransactionStep::Query(DbStatement::with_params(
                sql_with_timestamp_params(self.flavor, &self.target_select_sql(
                    "WHERE env = ? AND status = 'pending' AND lease_owner = ? \
                     AND lease_until = __bcs_timestamp_ms__ ORDER BY created_at, \
                     (SELECT event.stream_key FROM bcs_events event \
                       WHERE event.env = bcs_event_fanout_targets.env \
                         AND event.event_id = bcs_event_fanout_targets.event_id), \
                     (SELECT event.sequence FROM bcs_events event \
                       WHERE event.env = bcs_event_fanout_targets.env \
                         AND event.event_id = bcs_event_fanout_targets.event_id), target_id",
                )),
                vec![
                    DbValue::from(command.env.as_str()),
                    DbValue::from(lease_owner.as_str()),
                    lease_until,
                ],
            )),
        ];
        let results = self.db.transaction(steps).await.map_err(storage_error)?;
        transaction_rows(&results, 1)?
            .iter()
            .map(target_from_row)
            .collect()
    }

    async fn materialize_fanout_target(
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

    async fn claim_deliveries(
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
                    lease_until.clone(),
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
            )),
            DbTransactionStep::Execute(DbStatement::with_params(
                sql_with_timestamp_params(self.flavor, "UPDATE bcs_event_delivery_attempts SET \
                 completed_at = __bcs_timestamp_ms__, result = 'retryable', \
                 error_category = 'lease_expired', \
                 error_summary = 'Delivery lease expired before completion; remote outcome is unknown', \
                 response_bytes_observed = 0 \
                 WHERE completed_at IS NULL AND EXISTS (\
                   SELECT 1 FROM bcs_event_deliveries delivery \
                   WHERE delivery.delivery_id = bcs_event_delivery_attempts.delivery_id \
                     AND delivery.env = ? AND delivery.status = 'in_flight' \
                     AND delivery.lease_owner = ? \
                     AND delivery.lease_until = __bcs_timestamp_ms__ \
                     AND delivery.attempt_count = bcs_event_delivery_attempts.attempt_no + 1\
                 )"),
                vec![
                    timestamp_value_from_ms(self.flavor, command.now_ms)?,
                    DbValue::from(command.env.as_str()),
                    DbValue::from(lease_owner.as_str()),
                    timestamp_value_from_ms(self.flavor, command.lease_until_ms)?,
                ],
            )),
            DbTransactionStep::Execute(DbStatement::with_params(
                sql_with_timestamp_params(self.flavor, "INSERT INTO \
                 bcs_event_delivery_attempts (delivery_id, attempt_no, started_at, \
                 completed_at, latency_ms, result, http_status, error_category, error_summary, \
                 response_bytes_observed, worker_id) \
                 SELECT delivery_id, attempt_count, __bcs_timestamp_ms__, NULL, NULL, NULL, \
                        NULL, NULL, NULL, NULL, ? \
                 FROM bcs_event_deliveries WHERE env = ? AND status = 'in_flight' \
                   AND lease_owner = ? AND lease_until = __bcs_timestamp_ms__"),
                vec![
                    timestamp_value_from_ms(self.flavor, command.now_ms)?,
                    DbValue::from(command.worker_id.as_str()),
                    DbValue::from(command.env.as_str()),
                    DbValue::from(lease_owner.as_str()),
                    timestamp_value_from_ms(self.flavor, command.lease_until_ms)?,
                ],
            )),
            DbTransactionStep::Query(DbStatement::with_params(
                sql_with_timestamp_params(self.flavor, &self.delivery_select_sql(
                    "WHERE d.env = ? AND d.status = 'in_flight' AND d.lease_owner = ? \
                     AND d.lease_until = __bcs_timestamp_ms__ \
                     ORDER BY d.created_at, d.sequence, d.delivery_id",
                )),
                vec![
                    DbValue::from(command.env.as_str()),
                    DbValue::from(lease_owner.as_str()),
                    lease_until,
                ],
            )),
        ];
        let results = self.db.transaction(steps).await.map_err(storage_error)?;
        transaction_rows(&results, 3)?
            .iter()
            .map(delivery_from_row)
            .collect()
    }

    async fn renew_delivery_lease(
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

    async fn complete_delivery_attempt(
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

    async fn get_delivery(
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

    async fn list_deliveries(
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

    async fn create_replay_target(
        &self,
        command: CreateEventReplayTarget,
    ) -> Result<EventFanoutTargetRecord, EventRepoError> {
        validate_replay(&command)?;
        if let Some(existing) = self.replay_target_for_request(&command).await? {
            return Ok(existing);
        }
        let (original, _) = self
            .get_delivery(&command.original_delivery_id, &command.env)
            .await?
            .ok_or_else(|| EventRepoError::NotFound(command.original_delivery_id.clone()))?;
        if original.subscription_id != command.subscription_id
            || original.status != EventDeliveryStatus::DeadLettered
            || original.resolved_by_delivery_id.is_some()
        {
            return Err(EventRepoError::Conflict(
                "only an unresolved dead-lettered Delivery can be replayed".into(),
            ));
        }
        let (subscription, revision) = self
            .get_subscription(&command.subscription_id, &command.env)
            .await?
            .ok_or_else(|| EventRepoError::NotFound(command.subscription_id.clone()))?;
        if subscription.current_revision != command.subscription_revision
            || subscription.status != EventSubscriptionStatus::Active
        {
            return Err(EventRepoError::Conflict(
                "replay revision must be the current active revision".into(),
            ));
        }
        let event = self
            .get_event(&original.event_id, &command.env)
            .await?
            .ok_or_else(|| EventRepoError::NotFound(original.event_id.clone()))?;
        if event.retention_until_ms <= command.created_at_ms {
            return Err(EventRepoError::Conflict(
                "Event payload retention has expired".into(),
            ));
        }

        let mut dependency = None;
        let mut causal_insert = None;
        if let Some(cause_event_id) = event.envelope.causation_event_id.as_deref() {
            let cause = self
                .get_event(cause_event_id, &command.env)
                .await?
                .ok_or_else(|| EventRepoError::CausationViolation(cause_event_id.to_string()))?;
            if revision
                .event_filters
                .iter()
                .any(|filter| event_filter_matches(filter, &cause.envelope.event_type))
            {
                let rows = self
                    .db
                    .query(DbStatement::with_params(
                        self.target_select_sql(
                            "WHERE env = ? AND event_id = ? AND subscription_id = ? \
                             AND subscription_revision = ? AND status <> 'cancelled' \
                             AND purpose IN ('normal', 'causal_prerequisite') \
                             ORDER BY CASE purpose WHEN 'normal' THEN 0 ELSE 1 END LIMIT 1",
                        ),
                        vec![
                            DbValue::from(command.env.as_str()),
                            DbValue::from(cause_event_id),
                            DbValue::from(command.subscription_id.as_str()),
                            DbValue::from(command.subscription_revision),
                        ],
                    ))
                    .await
                    .map_err(storage_error)?;
                if let Some(target) = rows.first().map(target_from_row).transpose()? {
                    dependency = Some(target.target_id);
                } else {
                    let target_id = deterministic_target_id(
                        &command.env,
                        cause_event_id,
                        &command.subscription_id,
                        command.subscription_revision,
                        EventFanoutTargetPurpose::CausalPrerequisite,
                    );
                    dependency = Some(target_id.clone());
                    causal_insert = Some((target_id, cause_event_id.to_string()));
                }
            }
        }

        let created_at = timestamp_value_from_ms(self.flavor, command.created_at_ms)?;
        let mut steps = vec![DbTransactionStep::Query(DbStatement::with_params(
            replay_delivery_lock_sql(self.flavor),
            vec![
                DbValue::from(command.env.as_str()),
                DbValue::from(command.original_delivery_id.as_str()),
            ],
        ))];
        if let Some((target_id, cause_event_id)) = causal_insert {
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                causal_replay_insert_sql(self.flavor),
                vec![
                    DbValue::from(target_id),
                    DbValue::from(cause_event_id.as_str()),
                    DbValue::from(command.subscription_id.as_str()),
                    DbValue::from(command.subscription_revision),
                    created_at.clone(),
                    DbValue::from(command.env.as_str()),
                    DbValue::from(command.env.as_str()),
                    DbValue::from(command.original_delivery_id.as_str()),
                ],
            )));
        }
        let replay_insert_step = steps.len();
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            replay_target_insert_sql(self.flavor),
            vec![
                DbValue::from(command.target_id.as_str()),
                DbValue::from(command.subscription_revision),
                DbValue::from(command.replay_request_id.as_str()),
                DbValue::from(command.original_delivery_id.as_str()),
                DbValue::from(dependency),
                created_at.clone(),
                DbValue::from(command.env.as_str()),
                DbValue::from(command.original_delivery_id.as_str()),
                DbValue::from(command.subscription_id.as_str()),
                DbValue::from(command.subscription_revision),
                created_at.clone(),
            ],
        )));
        let replay_audit_id = uuid::Uuid::new_v4().to_string();
        let replay_details = serde_json::json!({
            "original_delivery_id": command.original_delivery_id.as_str(),
            "replay_request_id": command.replay_request_id.as_str(),
        })
        .to_string();
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            sql_with_timestamp_params(self.flavor, "INSERT INTO bcs_event_subscription_audits \
             (audit_id, subscription_id, revision, \
             action, actor_type, actor_id, reason, details_json, created_at, env) \
             SELECT ?, target.subscription_id, target.subscription_revision, \
             'delivery_replayed', ?, ?, ?, ?, __bcs_timestamp_ms__, target.env \
             FROM bcs_event_fanout_targets target WHERE target.env = ? AND target.target_id = ? \
               AND target.purpose = 'manual_replay' AND NOT EXISTS (\
                 SELECT 1 FROM bcs_event_subscription_audits audit \
                 WHERE audit.env = target.env \
                   AND audit.subscription_id = target.subscription_id \
                   AND audit.action = 'delivery_replayed' AND audit.details_json = ?\
               )"),
            vec![
                DbValue::from(replay_audit_id),
                DbValue::from(actor_type_name(command.actor.actor_type)),
                DbValue::from(command.actor.id.as_str()),
                DbValue::from(command.reason.clone()),
                DbValue::from(replay_details.as_str()),
                created_at,
                DbValue::from(command.env.as_str()),
                DbValue::from(command.target_id.as_str()),
                DbValue::from(replay_details),
            ],
        )));
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            "UPDATE bcs_events SET fanout_status = 'pending' WHERE env = ? \
             AND event_id IN (?, ?)",
            vec![
                DbValue::from(command.env.as_str()),
                DbValue::from(original.event_id.as_str()),
                DbValue::from(event.envelope.causation_event_id.clone()),
            ],
        )));
        let target_query_step = steps.len();
        steps.push(DbTransactionStep::Query(DbStatement::with_params(
            self.target_select_sql(
                "WHERE env = ? AND purpose = 'manual_replay' AND replay_of_delivery_id = ? \
                 AND (replay_request_id = ? OR status = 'pending') \
                 ORDER BY CASE WHEN replay_request_id = ? THEN 0 ELSE 1 END, created_at LIMIT 1",
            ),
            vec![
                DbValue::from(command.env.as_str()),
                DbValue::from(command.original_delivery_id.as_str()),
                DbValue::from(command.replay_request_id.as_str()),
                DbValue::from(command.replay_request_id.as_str()),
            ],
        )));
        match self.db.transaction(steps).await {
            Ok(results) => {
                let rows = transaction_rows(&results, target_query_step)?;
                if transaction_affected_rows(&results, replay_insert_step)? == 1 {
                    return rows
                        .first()
                        .map(target_from_row)
                        .transpose()?
                        .ok_or_else(|| {
                            EventRepoError::Storage("created replay target missing".into())
                        });
                }
                rows.first()
                    .map(target_from_row)
                    .transpose()?
                    .ok_or_else(|| {
                        EventRepoError::Conflict(
                            "dead-lettered Delivery is no longer replayable".into(),
                        )
                    })
            }
            Err(error) if error.is_duplicate_key() => self
                .replay_target_for_request(&command)
                .await?
                .ok_or_else(|| EventRepoError::Conflict(error.to_string())),
            Err(error) => Err(storage_error(error)),
        }
    }

    async fn skip_dead_lettered_delivery(
        &self,
        command: SkipDeadLetteredEventDelivery,
    ) -> Result<EventDeliveryRecord, EventRepoError> {
        if command.reason.trim().is_empty() || command.reason.len() > 128 {
            return Err(EventRepoError::InvalidInput(
                "skip reason must be between 1 and 128 bytes".into(),
            ));
        }
        let skipped_at = timestamp_value_from_ms(self.flavor, command.skipped_at_ms)?;
        let skip_actor = serde_json::to_string(&command.actor)
            .map_err(|error| EventRepoError::InvalidInput(format!("serialize actor: {error}")))?;
        let audit_id = uuid::Uuid::new_v4().to_string();
        let update_step = 0;
        let steps = vec![
            DbTransactionStep::Execute(DbStatement::with_params(
                sql_with_timestamp_params(self.flavor, "UPDATE bcs_event_deliveries SET \
                 status = 'skipped', skipped_at = __bcs_timestamp_ms__, \
                 skip_actor = ?, skip_reason = ?, lease_owner = NULL, lease_until = NULL \
                 WHERE env = ? AND delivery_id = ? AND status = 'dead_lettered' \
                   AND resolved_by_delivery_id IS NULL"),
                vec![
                    skipped_at.clone(),
                    DbValue::from(skip_actor),
                    DbValue::from(command.reason.as_str()),
                    DbValue::from(command.env.as_str()),
                    DbValue::from(command.delivery_id.as_str()),
                ],
            )),
            DbTransactionStep::Execute(DbStatement::with_params(
                sql_with_timestamp_params(self.flavor, "UPDATE bcs_event_deliveries SET \
                 status = 'cancelled', cancelled_at = __bcs_timestamp_ms__, \
                 lease_owner = NULL, lease_until = NULL WHERE env = ? \
                 AND replay_of_delivery_id = ? AND status IN ('pending', 'retry_wait')"),
                vec![
                    skipped_at.clone(),
                    DbValue::from(command.env.as_str()),
                    DbValue::from(command.delivery_id.as_str()),
                ],
            )),
            DbTransactionStep::Execute(DbStatement::with_params(
                sql_with_timestamp_params(self.flavor, "UPDATE bcs_event_fanout_targets SET \
                 status = 'cancelled', cancelled_at = __bcs_timestamp_ms__, \
                 lease_owner = NULL, lease_until = NULL WHERE env = ? \
                 AND replay_of_delivery_id = ? AND status = 'pending'"),
                vec![
                    skipped_at.clone(),
                    DbValue::from(command.env.as_str()),
                    DbValue::from(command.delivery_id.as_str()),
                ],
            )),
            DbTransactionStep::Execute(DbStatement::with_params(
                sql_with_timestamp_params(self.flavor, "INSERT INTO \
                 bcs_event_subscription_audits (audit_id, subscription_id, \
                 revision, action, actor_type, actor_id, reason, details_json, created_at, env) \
                 SELECT ?, subscription_id, subscription_revision, 'delivery_skipped', ?, ?, ?, \
                 NULL, __bcs_timestamp_ms__, env FROM bcs_event_deliveries \
                 WHERE env = ? AND delivery_id = ? AND status = 'skipped' \
                   AND skipped_at = __bcs_timestamp_ms__"),
                vec![
                    DbValue::from(audit_id),
                    DbValue::from(actor_type_name(command.actor.actor_type)),
                    DbValue::from(command.actor.id.as_str()),
                    DbValue::from(command.reason.as_str()),
                    skipped_at.clone(),
                    DbValue::from(command.env.as_str()),
                    DbValue::from(command.delivery_id.as_str()),
                    skipped_at.clone(),
                ],
            )),
        ];
        let results = self.db.transaction(steps).await.map_err(map_write_error)?;
        if transaction_affected_rows(&results, update_step)? != 1 {
            return Err(EventRepoError::Conflict(
                "only an unresolved dead-lettered Delivery can be skipped".into(),
            ));
        }
        self.get_delivery(&command.delivery_id, &command.env)
            .await?
            .map(|(delivery, _)| delivery)
            .ok_or_else(|| EventRepoError::Storage("skipped Delivery missing".into()))
    }

    async fn purge_expired(
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

fn claim_fanout_targets_sql(flavor: DbSqlFlavor) -> String {
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

fn claim_deliveries_sql(flavor: DbSqlFlavor) -> String {
    sql_with_timestamp_params(flavor, "UPDATE bcs_event_deliveries SET status = 'in_flight', \
     attempt_count = attempt_count + 1, lease_owner = ?, \
     lease_until = __bcs_timestamp_ms__, \
     first_attempt_at = COALESCE(first_attempt_at, __bcs_timestamp_ms__), \
     last_attempt_at = __bcs_timestamp_ms__, next_attempt_at = NULL \
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

fn causal_replay_insert_sql(flavor: DbSqlFlavor) -> String {
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

fn replay_target_insert_sql(flavor: DbSqlFlavor) -> String {
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

fn replay_delivery_lock_sql(flavor: DbSqlFlavor) -> &'static str {
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

fn transaction_rows(
    results: &[DbTransactionStepResult],
    step: usize,
) -> Result<&[DbRow], EventRepoError> {
    match results.get(step) {
        Some(DbTransactionStepResult::Rows(rows)) => Ok(rows),
        Some(DbTransactionStepResult::Executed(_)) => Err(EventRepoError::Storage(format!(
            "transaction step {step} returned an execute result"
        ))),
        None => Err(EventRepoError::Storage(format!(
            "transaction result {step} is missing"
        ))),
    }
}

fn transaction_affected_rows(
    results: &[DbTransactionStepResult],
    step: usize,
) -> Result<u64, EventRepoError> {
    match results.get(step) {
        Some(DbTransactionStepResult::Executed(result)) => Ok(result.affected_rows),
        Some(DbTransactionStepResult::Rows(_)) => Err(EventRepoError::Storage(format!(
            "transaction step {step} returned rows"
        ))),
        None => Err(EventRepoError::Storage(format!(
            "transaction result {step} is missing"
        ))),
    }
}

fn validate_claim(
    worker_id: &str,
    now_ms: u64,
    lease_until_ms: u64,
    limit: u32,
    env: &str,
) -> Result<(), EventRepoError> {
    if worker_id.is_empty() || worker_id.len() > 200 || env.is_empty() {
        return Err(EventRepoError::InvalidInput(
            "claim worker id and env must be non-empty".into(),
        ));
    }
    if limit == 0 || limit > 1_000 {
        return Err(EventRepoError::InvalidInput(
            "claim limit must be between 1 and 1000".into(),
        ));
    }
    if lease_until_ms <= now_ms {
        return Err(EventRepoError::InvalidInput(
            "claim lease must expire after now".into(),
        ));
    }
    Ok(())
}

fn validate_lease_renewal(command: &RenewEventDeliveryLease) -> Result<(), EventRepoError> {
    if command.delivery_id.is_empty()
        || command.expected_lease_owner.is_empty()
        || command.attempt_no == 0
        || command.env.is_empty()
        || command.lease_until_ms <= command.now_ms
    {
        return Err(EventRepoError::InvalidInput(
            "Delivery lease renewal is invalid".to_string(),
        ));
    }
    Ok(())
}

fn claim_owner(worker_id: &str) -> String {
    format!("{worker_id}#{}", uuid::Uuid::new_v4())
}

fn validate_materialization(command: &MaterializeFanoutTarget) -> Result<(), EventRepoError> {
    let delivery = &command.delivery;
    if command.target_id.is_empty()
        || command.expected_lease_owner.is_empty()
        || delivery.delivery_id.is_empty()
        || delivery.env.is_empty()
    {
        return Err(EventRepoError::InvalidInput(
            "materialization identifiers and env must be non-empty".into(),
        ));
    }
    let valid_initial_status = delivery.status == EventDeliveryStatus::Pending
        || (delivery.status == EventDeliveryStatus::DeadLettered
            && delivery.dead_lettered_at_ms.is_some()
            && delivery.last_error_category.is_some());
    if !valid_initial_status
        || delivery.attempt_count != 0
        || delivery.lease_owner.is_some()
        || delivery.lease_until_ms.is_some()
    {
        return Err(EventRepoError::InvalidInput(
            "new Delivery must be pending or a projection dead letter, unattempted, and unleased"
                .into(),
        ));
    }
    let actual_sha = format!("{:x}", Sha256::digest(&delivery.payload_bytes));
    if delivery.payload_sha256 != actual_sha {
        return Err(EventRepoError::InvalidInput(
            "Delivery payload SHA-256 does not match payload bytes".into(),
        ));
    }
    Ok(())
}

fn validate_completion(command: &CompleteEventDeliveryAttempt) -> Result<(), EventRepoError> {
    if command.delivery_id.is_empty()
        || command.expected_lease_owner.is_empty()
        || command.attempt_no == 0
    {
        return Err(EventRepoError::InvalidInput(
            "completion identifiers and attempt number must be non-empty".into(),
        ));
    }
    if command.completed_at_ms < command.started_at_ms {
        return Err(EventRepoError::InvalidInput(
            "attempt completion cannot precede its start".into(),
        ));
    }
    if command
        .error_summary
        .as_ref()
        .is_some_and(|summary| summary.len() > 2_048)
    {
        return Err(EventRepoError::InvalidInput(
            "error summary exceeds 2048 bytes".into(),
        ));
    }
    if command
        .error_category
        .as_ref()
        .is_some_and(|category| category.len() > 128)
    {
        return Err(EventRepoError::InvalidInput(
            "error category exceeds 128 bytes".into(),
        ));
    }
    let valid = match command.result {
        EventDeliveryAttemptRecordResult::Success => {
            command.next_status == EventDeliveryStatus::Succeeded
                && command.next_attempt_at_ms.is_none()
        }
        EventDeliveryAttemptRecordResult::Retryable => {
            (command.next_status == EventDeliveryStatus::RetryWait
                && command
                    .next_attempt_at_ms
                    .is_some_and(|next| next > command.completed_at_ms))
                || (command.next_status == EventDeliveryStatus::DeadLettered
                    && command.next_attempt_at_ms.is_none())
        }
        EventDeliveryAttemptRecordResult::Terminal => {
            command.next_status == EventDeliveryStatus::DeadLettered
                && command.next_attempt_at_ms.is_none()
        }
    };
    if !valid {
        return Err(EventRepoError::InvalidInput(
            "Attempt result and next Delivery state are inconsistent".into(),
        ));
    }
    Ok(())
}

fn validate_list_limit(limit: u32, kind: &str) -> Result<(), EventRepoError> {
    if limit == 0 || limit > 100 {
        return Err(EventRepoError::InvalidInput(format!(
            "{kind} list limit must be between 1 and 100"
        )));
    }
    Ok(())
}

fn validate_replay(command: &CreateEventReplayTarget) -> Result<(), EventRepoError> {
    if command.original_delivery_id.is_empty()
        || command.subscription_id.is_empty()
        || command.replay_request_id.is_empty()
        || command.target_id.is_empty()
        || command.actor.id.is_empty()
        || command.env.is_empty()
        || command.subscription_revision == 0
    {
        return Err(EventRepoError::InvalidInput(
            "replay identifiers, revision, and env must be non-empty".into(),
        ));
    }
    if command
        .reason
        .as_ref()
        .is_some_and(|reason| reason.len() > 128)
    {
        return Err(EventRepoError::InvalidInput(
            "replay reason exceeds 128 bytes".into(),
        ));
    }
    Ok(())
}

fn deterministic_target_id(
    env: &str,
    event_id: &str,
    subscription_id: &str,
    revision: u64,
    purpose: EventFanoutTargetPurpose,
) -> String {
    let digest = Sha256::digest(
        format!("{env}\0{event_id}\0{subscription_id}\0{revision}\0{purpose:?}").as_bytes(),
    );
    format!("evtgt_{digest:x}")
}

fn scope_lock_steps(
    flavor: DbSqlFlavor,
    env: &str,
    scope_type: &str,
    scope_id: &str,
) -> Vec<DbTransactionStep> {
    let insert = match flavor {
        DbSqlFlavor::Mysql => {
            "INSERT INTO bcs_event_scope_epochs (env, scope_type, scope_id, epoch) \
             VALUES (?, ?, ?, 0) ON DUPLICATE KEY UPDATE epoch = epoch"
        }
        DbSqlFlavor::Sqlite => {
            "INSERT INTO bcs_event_scope_epochs (env, scope_type, scope_id, epoch) \
             VALUES (?, ?, ?, 0) ON CONFLICT(env, scope_type, scope_id) DO NOTHING"
        }
    };
    let select = match flavor {
        DbSqlFlavor::Mysql => {
            "SELECT epoch FROM bcs_event_scope_epochs \
             WHERE env = ? AND scope_type = ? AND scope_id = ? FOR UPDATE"
        }
        DbSqlFlavor::Sqlite => {
            "SELECT epoch FROM bcs_event_scope_epochs \
             WHERE env = ? AND scope_type = ? AND scope_id = ?"
        }
    };
    let params = || {
        vec![
            DbValue::from(env),
            DbValue::from(scope_type),
            DbValue::from(scope_id),
        ]
    };
    vec![
        DbTransactionStep::Execute(DbStatement::with_params(insert, params())),
        DbTransactionStep::Query(DbStatement::with_params(select, params())),
    ]
}

fn scope_epoch_increment_step(env: &str, scope_type: &str, scope_id: &str) -> DbTransactionStep {
    DbTransactionStep::Execute(DbStatement::with_params(
        "UPDATE bcs_event_scope_epochs SET epoch = epoch + 1, updated_at = CURRENT_TIMESTAMP \
         WHERE env = ? AND scope_type = ? AND scope_id = ?",
        vec![
            DbValue::from(env),
            DbValue::from(scope_type),
            DbValue::from(scope_id),
        ],
    ))
}

fn subscription_lock_sql(flavor: DbSqlFlavor) -> &'static str {
    match flavor {
        DbSqlFlavor::Mysql => {
            "SELECT current_revision FROM bcs_event_subscriptions \
             WHERE env = ? AND subscription_id = ? FOR UPDATE"
        }
        DbSqlFlavor::Sqlite => {
            "SELECT current_revision FROM bcs_event_subscriptions \
             WHERE env = ? AND subscription_id = ?"
        }
    }
}

fn revision_insert_statement(
    revision: &EventSubscriptionRevisionRecord,
    env: &str,
    event_filters: &str,
    flavor: DbSqlFlavor,
    activated_at: &DbValue,
) -> Result<DbStatement, EventRepoError> {
    Ok(DbStatement::with_params(
        sql_with_timestamp_params(flavor, "INSERT INTO bcs_event_subscription_revisions \
         (subscription_id, revision, \
         event_filters_json, payload_mode, \
         endpoint_url, request_timeout_ms, activated_at, retired_at, env) \
         VALUES (?, ?, ?, ?, ?, ?, __bcs_timestamp_ms__, __bcs_timestamp_ms__, ?)"),
        vec![
            DbValue::from(revision.subscription_id.as_str()),
            DbValue::from(revision.revision),
            DbValue::from(event_filters),
            DbValue::from(payload_mode_name(revision.payload_mode)),
            DbValue::from(revision.endpoint_url.as_str()),
            DbValue::from(revision.request_timeout_ms),
            activated_at.clone(),
            optional_timestamp_value_from_ms(flavor, revision.retired_at_ms)?,
            DbValue::from(env),
        ],
    ))
}

fn revision_insert_if_subscription_exists_statement(
    revision: &EventSubscriptionRevisionRecord,
    env: &str,
    event_filters: &str,
    flavor: DbSqlFlavor,
    activated_at: &DbValue,
) -> Result<DbStatement, EventRepoError> {
    let mut params = revision_insert_statement(revision, env, event_filters, flavor, activated_at)?
        .into_params();
    params.push(DbValue::from(env));
    params.push(DbValue::from(revision.subscription_id.as_str()));
    Ok(DbStatement::with_params(
        sql_with_timestamp_params(flavor, "INSERT INTO bcs_event_subscription_revisions \
         (subscription_id, revision, \
         event_filters_json, payload_mode, endpoint_url, request_timeout_ms, \
         activated_at, retired_at, env) \
         SELECT ?, ?, ?, ?, ?, ?, __bcs_timestamp_ms__, __bcs_timestamp_ms__, ? WHERE EXISTS (\
           SELECT 1 FROM bcs_event_subscriptions WHERE env = ? AND subscription_id = ?\
         )"),
        params,
    ))
}

fn subscription_from_row(row: &DbRow) -> Result<EventSubscriptionRecord, EventRepoError> {
    Ok(EventSubscriptionRecord {
        subscription_id: column(row, "subscription_id")?,
        name: column(row, "name")?,
        scope: EventSubscriptionScope {
            scope_type: parse_scope_type(&column::<String>(row, "scope_type")?)?,
            id: column(row, "scope_id")?,
        },
        status: parse_subscription_status(&column::<String>(row, "status")?)?,
        current_revision: column(row, "current_revision")?,
        created_by: EventActor {
            actor_type: parse_actor_type(&column::<String>(row, "created_by_type")?)?,
            id: column(row, "created_by_id")?,
            display_name: None,
        },
        created_at_ms: column(row, "created_at_ms")?,
        updated_at_ms: column(row, "updated_at_ms")?,
        deleted_at_ms: optional_column(row, "deleted_at_ms")?,
        env: column(row, "env")?,
    })
}

fn revision_from_row(row: &DbRow) -> Result<EventSubscriptionRevisionRecord, EventRepoError> {
    let filters_json: String = column(row, "event_filters_json")?;
    Ok(EventSubscriptionRevisionRecord {
        subscription_id: column(row, "subscription_id")?,
        revision: column(row, "revision")?,
        event_filters: serde_json::from_str(&filters_json).map_err(|error| {
            EventRepoError::Storage(format!("parse subscription filters: {error}"))
        })?,
        payload_mode: parse_payload_mode(&column::<String>(row, "payload_mode")?)?,
        endpoint_url: column(row, "endpoint_url")?,
        request_timeout_ms: column(row, "request_timeout_ms")?,
        activated_at_ms: column(row, "activated_at_ms")?,
        retired_at_ms: optional_column(row, "retired_at_ms")?,
    })
}

fn event_from_row(row: &DbRow) -> Result<EventRecord, EventRepoError> {
    let actor_json: Option<String> = optional_column(row, "actor_json")?;
    let data_json: String = column(row, "data_json")?;
    let occurred_at_ms: u64 = column(row, "occurred_at_ms")?;
    let recorded_at_ms: u64 = column(row, "recorded_at_ms")?;
    Ok(EventRecord {
        envelope: EventEnvelope {
            spec_version: EVENT_SPEC_VERSION.to_string(),
            event_id: column(row, "event_id")?,
            event_type: column(row, "event_type")?,
            schema_version: column(row, "schema_version")?,
            source: EVENT_SOURCE.to_string(),
            occurred_at: rfc3339_from_ms(occurred_at_ms)?,
            recorded_at: rfc3339_from_ms(recorded_at_ms)?,
            subject: bcs_service_api::types::EventSubject {
                subject_type: column(row, "subject_type")?,
                id: column(row, "subject_id")?,
            },
            scope: EventScope {
                group_id: optional_column(row, "group_id")?,
                session_id: optional_column(row, "session_id")?,
                task_id: optional_column(row, "task_id")?,
                run_id: optional_column(row, "run_id")?,
            },
            stream: EventStream {
                key: column(row, "stream_key")?,
                sequence: column(row, "sequence")?,
            },
            actor: actor_json
                .map(|json| {
                    serde_json::from_str(&json).map_err(|error| {
                        EventRepoError::Storage(format!("parse Event actor: {error}"))
                    })
                })
                .transpose()?,
            correlation_id: optional_column(row, "correlation_id")?,
            causation_event_id: optional_column(row, "causation_event_id")?,
            trace_id: optional_column(row, "trace_id")?,
            data: serde_json::from_str::<BTreeMap<String, serde_json::Value>>(&data_json)
                .map_err(|error| EventRepoError::Storage(format!("parse Event data: {error}")))?,
        },
        producer: column(row, "producer")?,
        producer_key: column(row, "producer_key")?,
        fanout_status: parse_fanout_status(&column::<String>(row, "fanout_status")?)?,
        retention_until_ms: column(row, "retention_until_ms")?,
        env: column(row, "env")?,
    })
}

fn target_from_row(row: &DbRow) -> Result<EventFanoutTargetRecord, EventRepoError> {
    let replay_request_id: Option<String> = optional_column(row, "replay_request_id")?;
    Ok(EventFanoutTargetRecord {
        target_id: column(row, "target_id")?,
        event_id: column(row, "event_id")?,
        subscription_id: column(row, "subscription_id")?,
        subscription_revision: column(row, "subscription_revision")?,
        purpose: parse_target_purpose(&column::<String>(row, "purpose")?)?,
        replay_request_id: replay_request_id.filter(|value| !value.is_empty()),
        replay_of_delivery_id: optional_column(row, "replay_of_delivery_id")?,
        depends_on_target_id: optional_column(row, "depends_on_target_id")?,
        status: parse_target_status(&column::<String>(row, "status")?)?,
        created_at_ms: column(row, "created_at_ms")?,
        materialized_at_ms: optional_column(row, "materialized_at_ms")?,
        cancelled_at_ms: optional_column(row, "cancelled_at_ms")?,
        lease_owner: optional_column(row, "lease_owner")?,
        lease_until_ms: optional_column(row, "lease_until_ms")?,
        env: column(row, "env")?,
    })
}

fn delivery_from_row(row: &DbRow) -> Result<EventDeliveryRecord, EventRepoError> {
    let skip_actor_json: Option<String> = optional_column(row, "skip_actor")?;
    let http_status = optional_column::<u64>(row, "last_http_status")?
        .map(u16::try_from)
        .transpose()
        .map_err(|_| EventRepoError::Storage("HTTP status is outside u16 range".into()))?;
    Ok(EventDeliveryRecord {
        delivery_id: column(row, "delivery_id")?,
        fanout_target_id: column(row, "fanout_target_id")?,
        event_id: column(row, "event_id")?,
        event_type: column(row, "event_type")?,
        subscription_id: column(row, "subscription_id")?,
        subscription_revision: column(row, "subscription_revision")?,
        stream_key: column(row, "stream_key")?,
        sequence: column(row, "sequence")?,
        payload_bytes: column(row, "payload_bytes")?,
        payload_sha256: column(row, "payload_sha256")?,
        status: parse_delivery_status(&column::<String>(row, "status")?)?,
        attempt_count: column(row, "attempt_count")?,
        first_attempt_at_ms: optional_column(row, "first_attempt_at_ms")?,
        last_attempt_at_ms: optional_column(row, "last_attempt_at_ms")?,
        next_attempt_at_ms: optional_column(row, "next_attempt_at_ms")?,
        lease_owner: optional_column(row, "lease_owner")?,
        lease_until_ms: optional_column(row, "lease_until_ms")?,
        last_http_status: http_status,
        last_error_category: optional_column(row, "last_error_category")?,
        last_error_summary: optional_column(row, "last_error_summary")?,
        dead_lettered_at_ms: optional_column(row, "dead_lettered_at_ms")?,
        cancelled_at_ms: optional_column(row, "cancelled_at_ms")?,
        skipped_at_ms: optional_column(row, "skipped_at_ms")?,
        skip_actor: skip_actor_json
            .map(|json| {
                serde_json::from_str(&json)
                    .map_err(|error| EventRepoError::Storage(format!("parse skip actor: {error}")))
            })
            .transpose()?,
        skip_reason: optional_column(row, "skip_reason")?,
        replay_of_delivery_id: optional_column(row, "replay_of_delivery_id")?,
        resolved_by_delivery_id: optional_column(row, "resolved_by_delivery_id")?,
        resolved_at_ms: optional_column(row, "resolved_at_ms")?,
        created_at_ms: column(row, "created_at_ms")?,
        succeeded_at_ms: optional_column(row, "succeeded_at_ms")?,
        env: column(row, "env")?,
    })
}

fn attempt_from_row(row: &DbRow) -> Result<EventDeliveryAttemptRecord, EventRepoError> {
    let result = optional_column::<String>(row, "result")?
        .map(|result| parse_attempt_result(&result))
        .transpose()?;
    let http_status = optional_column::<u64>(row, "http_status")?
        .map(u16::try_from)
        .transpose()
        .map_err(|_| EventRepoError::Storage("HTTP status is outside u16 range".into()))?;
    Ok(EventDeliveryAttemptRecord {
        delivery_id: column(row, "delivery_id")?,
        attempt_no: column(row, "attempt_no")?,
        started_at_ms: column(row, "started_at_ms")?,
        completed_at_ms: optional_column(row, "completed_at_ms")?,
        latency_ms: optional_column(row, "latency_ms")?,
        result,
        http_status,
        error_category: optional_column(row, "error_category")?,
        error_summary: optional_column(row, "error_summary")?,
        response_bytes_observed: optional_column(row, "response_bytes_observed")?,
        worker_id: column(row, "worker_id")?,
    })
}

fn transaction_target_ids(
    results: &[DbTransactionStepResult],
    target_query_step: usize,
) -> Result<Vec<String>, EventRepoError> {
    let rows = match results.get(target_query_step) {
        Some(DbTransactionStepResult::Rows(rows)) => rows,
        Some(DbTransactionStepResult::Executed(_)) => {
            return Err(EventRepoError::Storage(
                "target query transaction step returned execute result".to_string(),
            ));
        }
        None => {
            return Err(EventRepoError::Storage(
                "target query transaction result missing".to_string(),
            ));
        }
    };
    rows.iter().map(|row| column(row, "target_id")).collect()
}

fn validate_new_subscription(record: &CreateEventSubscriptionRecord) -> Result<(), EventRepoError> {
    validate_scope(&record.subscription.scope).map_err(EventRepoError::InvalidInput)?;
    if record.subscription.subscription_id.is_empty() || record.subscription.env.is_empty() {
        return Err(EventRepoError::InvalidInput(
            "subscription id and env must be non-empty".to_string(),
        ));
    }
    if record.subscription.current_revision != 1
        || record.revision.revision != 1
        || record.revision.subscription_id != record.subscription.subscription_id
    {
        return Err(EventRepoError::InvalidInput(
            "new subscription must contain matching immutable revision 1".to_string(),
        ));
    }
    if record.scope_limit == 0 {
        return Err(EventRepoError::InvalidInput(
            "subscription scope limit must be non-zero".to_string(),
        ));
    }
    Ok(())
}

fn validate_replacement(command: &ReplaceEventSubscriptionRevision) -> Result<(), EventRepoError> {
    if command.expected_revision == u64::MAX
        || command.revision.revision != command.expected_revision + 1
        || command.revision.subscription_id != command.subscription_id
    {
        return Err(EventRepoError::InvalidInput(
            "replacement must contain the next immutable revision".to_string(),
        ));
    }
    Ok(())
}

fn validate_event_command(command: &AppendEventRecord) -> Result<(), EventRepoError> {
    for (name, value) in [
        ("env", command.env.as_str()),
        ("event_id", command.event.event_id.as_str()),
        ("event_type", command.event.event_type.as_str()),
        ("producer", command.event.producer.as_str()),
        ("producer_key", command.event.producer_key.as_str()),
        ("stream_key", command.event.stream_key.as_str()),
    ] {
        if value.is_empty() {
            return Err(EventRepoError::InvalidInput(format!(
                "{name} must be non-empty"
            )));
        }
    }
    Ok(())
}

fn timestamp_ms_expr(flavor: DbSqlFlavor, column: &str, alias: &str) -> String {
    match flavor {
        DbSqlFlavor::Mysql => {
            format!("CAST(UNIX_TIMESTAMP({column}) * 1000 AS UNSIGNED) AS {alias}")
        }
        DbSqlFlavor::Sqlite => {
            format!(
                "(CAST(strftime('%s', {column}) AS INTEGER) * 1000 + \
                 CAST(substr(strftime('%f', {column}), 4, 3) AS INTEGER)) AS {alias}"
            )
        }
    }
}

fn rfc3339_from_ms(timestamp_ms: u64) -> Result<String, EventRepoError> {
    let timestamp_ms = i64::try_from(timestamp_ms)
        .map_err(|_| EventRepoError::Storage("timestamp is outside supported range".to_string()))?;
    Utc.timestamp_millis_opt(timestamp_ms)
        .single()
        .map(|timestamp| timestamp.to_rfc3339_opts(SecondsFormat::Millis, true))
        .ok_or_else(|| EventRepoError::Storage("timestamp is outside supported range".to_string()))
}

fn scope_storage_id(scope: &EventSubscriptionScope) -> String {
    scope.id.clone()
}

fn scope_db_value(scope: &EventSubscriptionScope) -> DbValue {
    DbValue::from(scope.id.as_str())
}

fn scope_type_name(value: EventSubscriptionScopeType) -> &'static str {
    match value {
        EventSubscriptionScopeType::Group => "group",
    }
}

fn parse_scope_type(value: &str) -> Result<EventSubscriptionScopeType, EventRepoError> {
    match value {
        "group" => Ok(EventSubscriptionScopeType::Group),
        _ => Err(EventRepoError::Storage(format!(
            "unknown scope type {value}"
        ))),
    }
}

fn actor_type_name(value: EventActorType) -> &'static str {
    match value {
        EventActorType::Human => "human",
        EventActorType::Bot => "bot",
        EventActorType::App => "app",
        EventActorType::System => "system",
    }
}

fn parse_actor_type(value: &str) -> Result<EventActorType, EventRepoError> {
    match value {
        "human" => Ok(EventActorType::Human),
        "bot" => Ok(EventActorType::Bot),
        "app" => Ok(EventActorType::App),
        "system" => Ok(EventActorType::System),
        _ => Err(EventRepoError::Storage(format!(
            "unknown actor type {value}"
        ))),
    }
}

fn subscription_status_name(value: EventSubscriptionStatus) -> &'static str {
    match value {
        EventSubscriptionStatus::Pending => "pending",
        EventSubscriptionStatus::Active => "active",
        EventSubscriptionStatus::Disabled => "disabled",
        EventSubscriptionStatus::Deleted => "deleted",
    }
}

fn parse_subscription_status(value: &str) -> Result<EventSubscriptionStatus, EventRepoError> {
    match value {
        "pending" => Ok(EventSubscriptionStatus::Pending),
        "active" => Ok(EventSubscriptionStatus::Active),
        "disabled" => Ok(EventSubscriptionStatus::Disabled),
        "deleted" => Ok(EventSubscriptionStatus::Deleted),
        _ => Err(EventRepoError::Storage(format!(
            "unknown subscription status {value}"
        ))),
    }
}

fn payload_mode_name(value: EventPayloadMode) -> &'static str {
    match value {
        EventPayloadMode::MetadataOnly => "metadata_only",
        EventPayloadMode::Full => "full",
    }
}

fn parse_payload_mode(value: &str) -> Result<EventPayloadMode, EventRepoError> {
    match value {
        "metadata_only" => Ok(EventPayloadMode::MetadataOnly),
        "full" => Ok(EventPayloadMode::Full),
        _ => Err(EventRepoError::Storage(format!(
            "unknown payload mode {value}"
        ))),
    }
}

fn parse_fanout_status(value: &str) -> Result<EventFanoutStatus, EventRepoError> {
    match value {
        "pending" => Ok(EventFanoutStatus::Pending),
        "completed" => Ok(EventFanoutStatus::Completed),
        "failed" => Ok(EventFanoutStatus::Failed),
        _ => Err(EventRepoError::Storage(format!(
            "unknown fanout status {value}"
        ))),
    }
}

fn parse_target_purpose(value: &str) -> Result<EventFanoutTargetPurpose, EventRepoError> {
    match value {
        "normal" => Ok(EventFanoutTargetPurpose::Normal),
        "causal_prerequisite" => Ok(EventFanoutTargetPurpose::CausalPrerequisite),
        "manual_replay" => Ok(EventFanoutTargetPurpose::ManualReplay),
        _ => Err(EventRepoError::Storage(format!(
            "unknown fanout target purpose {value}"
        ))),
    }
}

fn parse_target_status(value: &str) -> Result<EventFanoutTargetStatus, EventRepoError> {
    match value {
        "pending" => Ok(EventFanoutTargetStatus::Pending),
        "materialized" => Ok(EventFanoutTargetStatus::Materialized),
        "cancelled" => Ok(EventFanoutTargetStatus::Cancelled),
        "failed" => Ok(EventFanoutTargetStatus::Failed),
        _ => Err(EventRepoError::Storage(format!(
            "unknown fanout target status {value}"
        ))),
    }
}

fn delivery_status_name(value: EventDeliveryStatus) -> &'static str {
    match value {
        EventDeliveryStatus::Pending => "pending",
        EventDeliveryStatus::InFlight => "in_flight",
        EventDeliveryStatus::RetryWait => "retry_wait",
        EventDeliveryStatus::Succeeded => "succeeded",
        EventDeliveryStatus::DeadLettered => "dead_lettered",
        EventDeliveryStatus::Cancelled => "cancelled",
        EventDeliveryStatus::Skipped => "skipped",
    }
}

fn parse_delivery_status(value: &str) -> Result<EventDeliveryStatus, EventRepoError> {
    match value {
        "pending" => Ok(EventDeliveryStatus::Pending),
        "in_flight" => Ok(EventDeliveryStatus::InFlight),
        "retry_wait" => Ok(EventDeliveryStatus::RetryWait),
        "succeeded" => Ok(EventDeliveryStatus::Succeeded),
        "dead_lettered" => Ok(EventDeliveryStatus::DeadLettered),
        "cancelled" => Ok(EventDeliveryStatus::Cancelled),
        "skipped" => Ok(EventDeliveryStatus::Skipped),
        _ => Err(EventRepoError::Storage(format!(
            "unknown Delivery status {value}"
        ))),
    }
}

fn attempt_result_name(value: EventDeliveryAttemptRecordResult) -> &'static str {
    match value {
        EventDeliveryAttemptRecordResult::Success => "success",
        EventDeliveryAttemptRecordResult::Retryable => "retryable",
        EventDeliveryAttemptRecordResult::Terminal => "terminal",
    }
}

fn parse_attempt_result(value: &str) -> Result<EventDeliveryAttemptRecordResult, EventRepoError> {
    match value {
        "success" => Ok(EventDeliveryAttemptRecordResult::Success),
        "retryable" => Ok(EventDeliveryAttemptRecordResult::Retryable),
        "terminal" => Ok(EventDeliveryAttemptRecordResult::Terminal),
        _ => Err(EventRepoError::Storage(format!(
            "unknown Delivery Attempt result {value}"
        ))),
    }
}

fn column<T: bcs_db_api::FromDbColumn>(row: &DbRow, name: &str) -> Result<T, EventRepoError> {
    db_get_column(row, name).map_err(storage_error)
}

fn optional_column<T: bcs_db_api::FromDbColumn>(
    row: &DbRow,
    name: &str,
) -> Result<Option<T>, EventRepoError> {
    db_get_column_opt(row, name).map_err(storage_error)
}

fn optional_u64_value(value: Option<u64>) -> DbValue {
    value.map(DbValue::from).unwrap_or(DbValue::Null)
}

fn storage_error(error: DbError) -> EventRepoError {
    EventRepoError::Storage(error.to_string())
}

fn map_write_error(error: DbError) -> EventRepoError {
    if error.is_duplicate_key() {
        EventRepoError::Conflict(error.to_string())
    } else {
        storage_error(error)
    }
}

#[cfg(test)]
mod tests {
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
}
