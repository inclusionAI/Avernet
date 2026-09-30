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
use tracing::warn;

use crate::transaction_plan::EventAppendTransactionPlan;
use crate::timestamp::{
    optional_timestamp_value_from_ms, sql_with_timestamp_params, timestamp_value_from_ms,
};
use crate::{event_filter_matches, validate_scope};

mod subscriptions;
mod events;
mod deliveries;
mod replay;
mod retention;
mod claim_sql;
mod validation;
mod subscription_sql;
mod rows;
mod results;

use claim_sql::*;
use validation::*;
use subscription_sql::*;
use rows::*;
use results::*;


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
        self.sql_create_subscription(record).await
    }

    async fn cancel_pending_subscriptions(
        &self,
        command: CancelPendingEventSubscriptions,
    ) -> Result<u64, EventRepoError> {
        self.sql_cancel_pending_subscriptions(command).await
    }

    async fn get_subscription(
        &self,
        subscription_id: &str,
        env: &str,
    ) -> Result<Option<(EventSubscriptionRecord, EventSubscriptionRevisionRecord)>, EventRepoError>
    {
        self.sql_get_subscription(subscription_id, env).await
    }

    async fn get_subscription_revision(
        &self,
        subscription_id: &str,
        revision: u64,
        env: &str,
    ) -> Result<Option<EventSubscriptionRevisionRecord>, EventRepoError> {
        self.sql_get_subscription_revision(subscription_id, revision, env).await
    }

    async fn list_subscriptions(
        &self,
        query: ListEventSubscriptionRecords,
    ) -> Result<Vec<EventSubscriptionRecord>, EventRepoError> {
        self.sql_list_subscriptions(query).await
    }

    async fn replace_subscription_revision(
        &self,
        command: ReplaceEventSubscriptionRevision,
    ) -> Result<EventSubscriptionRecord, EventRepoError> {
        self.sql_replace_subscription_revision(command).await
    }

    async fn append_event(
        &self,
        command: AppendEventRecord,
    ) -> Result<AppendEventRecordResult, EventRepoError> {
        self.sql_append_event(command).await
    }

    async fn get_event(
        &self,
        event_id: &str,
        env: &str,
    ) -> Result<Option<EventRecord>, EventRepoError> {
        self.sql_get_event(event_id, env).await
    }

    async fn claim_fanout_targets(
        &self,
        command: ClaimFanoutTargets,
    ) -> Result<Vec<EventFanoutTargetRecord>, EventRepoError> {
        self.sql_claim_fanout_targets(command).await
    }

    async fn materialize_fanout_target(
        &self,
        command: MaterializeFanoutTarget,
    ) -> Result<EventDeliveryRecord, EventRepoError> {
        self.sql_materialize_fanout_target(command).await
    }

    async fn claim_deliveries(
        &self,
        command: ClaimEventDeliveries,
    ) -> Result<Vec<EventDeliveryRecord>, EventRepoError> {
        self.sql_claim_deliveries(command).await
    }

    async fn renew_delivery_lease(
        &self,
        command: RenewEventDeliveryLease,
    ) -> Result<EventDeliveryRecord, EventRepoError> {
        self.sql_renew_delivery_lease(command).await
    }

    async fn complete_delivery_attempt(
        &self,
        command: CompleteEventDeliveryAttempt,
    ) -> Result<EventDeliveryRecord, EventRepoError> {
        self.sql_complete_delivery_attempt(command).await
    }

    async fn get_delivery(
        &self,
        delivery_id: &str,
        env: &str,
    ) -> Result<Option<(EventDeliveryRecord, Vec<EventDeliveryAttemptRecord>)>, EventRepoError>
    {
        self.sql_get_delivery(delivery_id, env).await
    }

    async fn list_deliveries(
        &self,
        query: ListEventDeliveryRecords,
    ) -> Result<Vec<EventDeliveryRecord>, EventRepoError> {
        self.sql_list_deliveries(query).await
    }

    async fn create_replay_target(
        &self,
        command: CreateEventReplayTarget,
    ) -> Result<EventFanoutTargetRecord, EventRepoError> {
        self.sql_create_replay_target(command).await
    }

    async fn skip_dead_lettered_delivery(
        &self,
        command: SkipDeadLetteredEventDelivery,
    ) -> Result<EventDeliveryRecord, EventRepoError> {
        self.sql_skip_dead_lettered_delivery(command).await
    }

    async fn purge_expired(
        &self,
        command: EventRetentionRequest,
    ) -> Result<EventRetentionResult, EventRepoError> {
        self.sql_purge_expired(command).await
    }
}

#[cfg(test)]
#[path = "db/tests.rs"]
mod tests;
