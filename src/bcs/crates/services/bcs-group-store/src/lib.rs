//! MySQL-based Group Session Storage.
//!
//! This module provides a persistent group session store backed by MySQL.
//!
//! # Architecture
//!
//! ```text
//! MySQL: Session metadata + Participants (persistent)
//! ```
//!
//! Messages and workspace are NOT persisted - they are lost on server restart.

use async_trait::async_trait;
use bcs_db_api::{
    DbError, DbPlugin, DbResult, DbRow, DbSqlFlavor, DbStatement, DbTransactionParam,
    DbTransactionStep, DbTransactionStepResult, DbValue as Value, db_get_column, db_get_column_opt,
};
use bcs_event_store::{
    EventAppendTransactionPlan, GroupDeletionEventTransactionPlan,
    GroupProvisioningEventTransactionPlan,
};
use bcs_domain::MessageViewScope;
use chrono::{TimeZone, Utc};
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;
use tracing::{debug, error, info, warn};

use bcs_service_api::port::repo::{
    CommitGroupEventfulMutation, FinalizeGroupProvisioning, GroupEventfulMutation,
};
use bcs_service_api::types::OpeningMessage;
use bcs_service_api::{
    ActorKind, DefaultDelivery, Group, GroupHumanNotifyPolicy, GroupMessage, GroupMetricCount,
    GroupMetricsSnapshotPort,
    GroupMutableFieldsPatch, GroupStatus, GroupStrategy, HumanMentionNotifyMode, Participant,
    ParticipantKind, ParticipantMode, ParticipantRole, RoutingPolicy, ServiceError, ServiceResult,
    Workspace,
};

pub mod memory;

pub use bcs_service_api::port::repo::GroupRepoPort;
pub use memory::{GroupBuilder, MemoryGroupRepo};
mod store_dm;
mod store_eventful;
mod store_finds;
mod store_lists;
#[cfg(test)]
mod store_tests;
mod store_mapping;
mod store_reads;
mod store_repo;
mod store_writes;

/// MySQL-backed group repository.
pub type MysqlGroupRepo = MySqlGroupStore;

#[derive(Clone)]
struct DbPluginCompat {
    db: Arc<dyn DbPlugin>,
}

impl DbPluginCompat {
    fn new(db: Arc<dyn DbPlugin>) -> Self {
        Self { db }
    }

    fn plugin(&self) -> Arc<dyn DbPlugin> {
        self.db.clone()
    }

    async fn query_with(
        &self,
        logical_db: &str,
        sql: &str,
        params: Vec<Value>,
    ) -> DbResult<Vec<DbRow>> {
        assert_empty_logical_db(logical_db)?;
        self.db.query(DbStatement::with_params(sql, params)).await
    }

    async fn execute_with(&self, logical_db: &str, sql: &str, params: Vec<Value>) -> DbResult<u64> {
        assert_empty_logical_db(logical_db)?;
        self.db
            .execute(DbStatement::with_params(sql, params))
            .await
            .map(|result| result.affected_rows)
    }
}

fn assert_empty_logical_db(logical_db: &str) -> DbResult<()> {
    if logical_db.is_empty() {
        Ok(())
    } else {
        Err(DbError::InvalidInput(
            "DbPlugin is bound to a single datasource by bootstrap; service code must not pass logical_db routing keys"
                .to_string(),
        ))
    }
}

fn transaction_lock_row_is_missing(error: &DbError) -> bool {
    matches!(
        error,
        DbError::InvalidInput(message)
            if message.contains("references missing row 0 from step 0")
    )
}

fn db_timestamp_from_millis(timestamp_ms: u64) -> ServiceResult<String> {
    let timestamp_ms = i64::try_from(timestamp_ms)
        .map_err(|_| ServiceError::InternalError("Group timestamp is out of range".to_string()))?;
    // `YYYY-MM-DD HH:MM:SS.mmm` is accepted by both MySQL `timestamp` columns
    // (RFC3339 `T`/`Z` forms fail with ERROR 1292) and SQLite `strftime('%s')`
    // reads.
    Utc.timestamp_millis_opt(timestamp_ms)
        .single()
        .map(|timestamp| timestamp.format("%Y-%m-%d %H:%M:%S%.3f").to_string())
        .ok_or_else(|| ServiceError::InternalError("Group timestamp is invalid".to_string()))
}

fn routing_policy_json(policy: Option<&RoutingPolicy>, delivery: DefaultDelivery) -> String {
    let mut policy = policy.cloned().unwrap_or_default();
    policy.default_bot_final_delivery = delivery;
    serde_json::to_string(&policy).expect("RoutingPolicy contains only JSON-compatible values")
}

fn parse_stored_routing_policy_json(
    stored_json: Option<&str>,
) -> ServiceResult<Option<RoutingPolicy>> {
    Ok(match stored_json {
        Some(json) => Some(
            serde_json::from_str::<RoutingPolicy>(json).map_err(|error| {
                ServiceError::InternalError(format!(
                    "deserialize routing_policy_json before patch: {error}"
                ))
            })?,
        ),
        _ => None,
    })
}

fn patch_stored_routing_policy_json(
    stored_json: Option<&str>,
    delivery: DefaultDelivery,
) -> ServiceResult<String> {
    let policy = parse_stored_routing_policy_json(stored_json)?;
    Ok(routing_policy_json(policy.as_ref(), delivery))
}

fn group_version_update_step(
    env: &str,
    group_id: DbTransactionParam,
    mutated_at: &str,
) -> DbTransactionStep {
    DbTransactionStep::Execute(DbStatement::with_transaction_params(
        "UPDATE bcs_groups SET version = version + 1, gmt_modified = ? \
         WHERE env = ? AND group_id = ?",
        vec![
            DbTransactionParam::value(mutated_at),
            DbTransactionParam::value(env),
            group_id,
        ],
    ))
}

fn apply_db_group_mutation_candidate(
    group: &mut Group,
    mutation: &GroupEventfulMutation,
    mutated_at_ms: u64,
) -> ServiceResult<()> {
    let changed = match mutation {
        GroupEventfulMutation::PatchMutableFields(patch) => {
            let mut changed = false;
            if let Some(label) = &patch.label
                && group.label.as_ref() != Some(label)
            {
                group.label = Some(label.clone());
                changed = true;
            }
            if let Some(context) = &patch.context
                && group.context.as_ref() != Some(context)
            {
                group.context = Some(context.clone());
                changed = true;
            }
            if let Some(opening_message) = &patch.opening_message
                && group.opening_message != *opening_message
            {
                group.opening_message = opening_message.clone();
                changed = true;
            }
            if let Some(visibility) = &patch.visibility
                && group.visibility != *visibility
            {
                group.visibility = visibility.clone();
                changed = true;
            }
            if let Some(delivery) = patch.default_bot_final_delivery {
                let current = group
                    .routing_policy
                    .as_ref()
                    .map(|policy| policy.default_bot_final_delivery)
                    .unwrap_or_default();
                if current != delivery {
                    group
                        .routing_policy
                        .get_or_insert_with(Default::default)
                        .default_bot_final_delivery = delivery;
                    changed = true;
                }
            }
            if let Some(mode) = patch.human_mention_notify_mode
                && group.human_mention_notify_mode != mode
            {
                group.human_mention_notify_mode = mode;
                changed = true;
            }
            changed
        }
        GroupEventfulMutation::UpdateStatus(status) => {
            if group.status == *status {
                false
            } else {
                group.status = *status;
                true
            }
        }
        GroupEventfulMutation::AddParticipant {
            participant,
            actor_is_public,
        } => {
            if group
                .participants
                .iter()
                .any(|existing| existing.bot_uuid == participant.bot_uuid)
            {
                false
            } else {
                if participant.is_bot() && group.visibility == "public" && !actor_is_public {
                    return Err(ServiceError::ExistNonPublicBots {
                        bots: vec![(participant.bot_uuid.clone(), participant.bot_name.clone())],
                    });
                }
                group.participants.push(participant.clone());
                true
            }
        }
        GroupEventfulMutation::RemoveParticipant { actor_id } => {
            let initial_len = group.participants.len();
            group
                .participants
                .retain(|participant| participant.bot_uuid != *actor_id);
            group.participants.len() != initial_len
        }
        GroupEventfulMutation::UpdateParticipantMode { actor_id, mode } => {
            let participant = group
                .participants
                .iter_mut()
                .find(|participant| participant.bot_uuid == *actor_id)
                .ok_or_else(|| ServiceError::ParticipantNotFound(actor_id.clone()))?;
            if participant.effective_mode() == *mode {
                false
            } else {
                participant.mode = Some(*mode);
                true
            }
        }
        GroupEventfulMutation::UpdateParticipantMessageViewScope {
            actor_id,
            message_view_scope,
            mode,
        } => {
            let participant = group
                .participants
                .iter_mut()
                .find(|participant| participant.bot_uuid == *actor_id)
                .ok_or_else(|| ServiceError::ParticipantNotFound(actor_id.clone()))?;
            if !message_view_scope.is_valid_for(participant.actor_kind) {
                return Err(ServiceError::InvalidOperation {
                    message: "Bot participants must use full message_view_scope".to_string(),
                    request_id: None,
                });
            }
            let scope_changed = participant.message_view_scope != *message_view_scope;
            let mode_changed = mode.is_some_and(|mode| participant.effective_mode() != mode);
            participant.message_view_scope = *message_view_scope;
            if let Some(mode) = mode {
                participant.mode = Some(*mode);
            }
            scope_changed || mode_changed
        }
        GroupEventfulMutation::UpdateRoutingPolicy(policy) => {
            let current = serde_json::to_value(&group.routing_policy)
                .map_err(|error| ServiceError::InternalError(error.to_string()))?;
            let requested = serde_json::to_value(Some(policy))
                .map_err(|error| ServiceError::InternalError(error.to_string()))?;
            if current == requested {
                false
            } else {
                group.routing_policy = Some(policy.clone());
                true
            }
        }
        GroupEventfulMutation::UpdateServiceSpec(service_spec) => {
            let current = serde_json::to_value(&group.service_spec)
                .map_err(|error| ServiceError::InternalError(error.to_string()))?;
            let requested = serde_json::to_value(service_spec)
                .map_err(|error| ServiceError::InternalError(error.to_string()))?;
            if current == requested {
                false
            } else {
                group.service_spec = service_spec.clone();
                true
            }
        }
        GroupEventfulMutation::Delete => true,
    };
    if !changed {
        return Err(ServiceError::Conflict(format!(
            "Group '{}' mutation is already applied",
            group.id
        )));
    }
    group.version = group
        .version
        .checked_add(1)
        .ok_or_else(|| ServiceError::Conflict(format!("Group '{}' version overflow", group.id)))?;
    group.updated_at = mutated_at_ms;
    Ok(())
}

/// MySQL-backed group session store.
///
/// Uses MySQL for persistent storage of session metadata and participants.
///
/// Messages and workspace are NEVER persisted - they are lost on server restart.
pub struct MySqlGroupStore {
    /// Database plugin selected by the composition root.
    db: DbPluginCompat,
    /// TODO: remove with DbPluginCompat once legacy helper signatures stop threading logical_db.
    /// Retained as an always-empty logical label for legacy helper signatures.
    logical_db: String,
    /// Environment for multi-tenancy.
    env: String,
    /// SQL dialect (MySQL vs SQLite).
    flavor: DbSqlFlavor,
    /// In-memory message counts per group (not persisted, lost on restart).
    message_counts: RwLock<HashMap<String, usize>>,
    /// In-memory group cache (master mode only, avoids repeated DB queries).
    cache: RwLock<HashMap<String, Group>>,
}

impl MySqlGroupStore {
    /// Create a new MySqlGroupStore.
    pub fn new(db: Arc<dyn DbPlugin>, env: String) -> Self {
        Self {
            db: DbPluginCompat::new(db),
            logical_db: String::new(),
            env,
            flavor: DbSqlFlavor::Mysql,
            message_counts: RwLock::new(HashMap::new()),
            cache: RwLock::new(HashMap::new()),
        }
    }

    /// Create a new MySqlGroupStore with SQLite dialect.
    pub fn sqlite(db: Arc<dyn DbPlugin>, env: String) -> Self {
        Self {
            db: DbPluginCompat::new(db),
            logical_db: String::new(),
            env,
            flavor: DbSqlFlavor::Sqlite,
            message_counts: RwLock::new(HashMap::new()),
            cache: RwLock::new(HashMap::new()),
        }
    }
}

fn sql_metric_service_mode_to_option(raw: &str) -> Option<String> {
    match raw {
        "none" => None,
        "master_slave" => Some("master_slave".to_string()),
        _ => Some("other".to_string()),
    }
}

#[async_trait]
impl GroupMetricsSnapshotPort for MySqlGroupStore {
    async fn group_counts(&self) -> ServiceResult<Vec<GroupMetricCount>> {
        let rows = self
            .db
            .query_with(
                &self.logical_db,
                "SELECT status, group_kind, group_strategy, service_mode, COUNT(*) AS group_count \
             FROM ( \
                 SELECT status, \
                        COALESCE(group_kind, 'normal') AS group_kind, \
                        CASE \
                            WHEN group_strategy = 'manager_worker' THEN 'manager_worker' \
                            WHEN group_strategy = 'state_machine' THEN 'state_machine' \
                            ELSE 'chat' \
                        END AS group_strategy, \
                        CASE \
                            WHEN service_mode IS NULL OR TRIM(service_mode) = '' THEN 'none' \
                            WHEN service_mode = 'master_slave' THEN 'master_slave' \
                            ELSE 'other' \
                        END AS service_mode \
                 FROM bcs_groups \
                 WHERE env = ? \
             ) metric_groups \
             GROUP BY status, group_kind, group_strategy, service_mode",
                vec![Value::from(self.env.as_str())],
            )
            .await
            .map_err(|e| {
                warn!(env = %self.env, error = %e, "group metrics snapshot query failed");
                ServiceError::InternalError(format!("group metrics snapshot query failed: {}", e))
            })?;

        let mut counts = Vec::with_capacity(rows.len());
        for row in rows {
            let status_raw: String = db_get_column(&row, "status").map_err(|e| {
                ServiceError::InternalError(format!(
                    "group metrics status conversion failed: {}",
                    e
                ))
            })?;
            let group_kind_raw: String = db_get_column(&row, "group_kind").map_err(|e| {
                ServiceError::InternalError(format!("group metrics kind conversion failed: {}", e))
            })?;
            let service_mode_raw: String = db_get_column(&row, "service_mode").map_err(|e| {
                ServiceError::InternalError(format!(
                    "group metrics service_mode conversion failed: {}",
                    e
                ))
            })?;
            let group_strategy_raw: String =
                db_get_column(&row, "group_strategy").map_err(|e| {
                    ServiceError::InternalError(format!(
                        "group metrics group_strategy conversion failed: {}",
                        e
                    ))
                })?;
            let group_count: i64 = db_get_column(&row, "group_count").map_err(|e| {
                ServiceError::InternalError(format!("group metrics count conversion failed: {}", e))
            })?;
            let count = u64::try_from(group_count).map_err(|e| {
                ServiceError::InternalError(format!("group metrics count is invalid: {}", e))
            })?;
            if count == 0 {
                continue;
            }

            counts.push(GroupMetricCount {
                status: Self::str_to_status(&status_raw),
                kind: Self::parse_group_kind(Some(group_kind_raw.as_str())),
                group_strategy: Self::parse_group_strategy(Some(group_strategy_raw.as_str())),
                service_mode: sql_metric_service_mode_to_option(&service_mode_raw),
                count,
            });
        }

        Ok(counts)
    }
}
