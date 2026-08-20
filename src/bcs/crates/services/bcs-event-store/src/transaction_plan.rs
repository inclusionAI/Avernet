//! Store-owned Event append transaction fragment.
//!
//! Persistent business repositories may prepend their mutation steps and then
//! append this fragment to the same `DbPlugin::transaction` call. The fragment
//! type intentionally lives in this implementation crate rather than the
//! application-facing Service API.

use bcs_db_api::{DbSqlFlavor, DbStatement, DbTransactionParam, DbTransactionStep, DbValue};
use bcs_service_api::port::repo::{AppendEventRecord, EventRepoError};
use bcs_service_api::types::{EventActor, EventActorType};
use chrono::{DateTime, SecondsFormat, TimeZone, Utc};

/// A composable set of Event append steps plus the result positions needed to
/// interpret the committed Event.
#[derive(Debug)]
pub struct EventAppendTransactionPlan {
    pub steps: Vec<DbTransactionStep>,
    pub sequence_query_step: usize,
    pub event_insert_step: usize,
    pub target_query_step: usize,
}

/// Event-store-owned half of Group provisioning finalization.
///
/// The Group repository prepends its locked availability transition, then
/// appends these steps to activate the pending inline subscriptions and write
/// the ordered creation Events in the same database transaction.
#[derive(Debug)]
pub struct GroupProvisioningEventTransactionPlan {
    pub steps: Vec<DbTransactionStep>,
}

/// Event-store-owned cleanup performed in the same transaction that deletes
/// a Group. It disables direct Group subscriptions and cancels work that has
/// not started, while leaving in-flight HTTP requests to finish normally.
#[derive(Debug)]
pub struct GroupDeletionEventTransactionPlan {
    pub steps: Vec<DbTransactionStep>,
}

impl GroupDeletionEventTransactionPlan {
    pub fn build(
        group_id: &str,
        env: &str,
        deleted_at_ms: u64,
        flavor: DbSqlFlavor,
    ) -> Result<Self, EventRepoError> {
        if group_id.is_empty() || env.is_empty() {
            return Err(EventRepoError::InvalidInput(
                "Group deletion group id and env must be non-empty".to_string(),
            ));
        }
        let deleted_at = db_timestamp_from_ms(deleted_at_ms)?;
        let audit_id_expression = match flavor {
            DbSqlFlavor::Mysql => "UUID()",
            DbSqlFlavor::Sqlite => "lower(hex(randomblob(16)))",
        };
        let mut steps = vec![
            DbTransactionStep::Execute(DbStatement::with_params(
                scope_epoch_insert_sql(flavor),
                vec![
                    DbValue::from(env),
                    DbValue::from("group"),
                    DbValue::from(group_id),
                ],
            )),
            DbTransactionStep::Query(DbStatement::with_params(
                scope_epoch_lock_sql(flavor),
                vec![
                    DbValue::from(env),
                    DbValue::from("group"),
                    DbValue::from(group_id),
                ],
            )),
        ];
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            format!(
                "INSERT INTO bcs_event_subscription_audits \
                 (audit_id, subscription_id, revision, action, actor_type, actor_id, reason, \
                  details_json, created_at, env) \
                 SELECT {audit_id_expression}, subscription_id, current_revision, 'disabled', \
                        'system', 'group-deletion', 'scope_deleted', NULL, ?, env \
                 FROM bcs_event_subscriptions \
                 WHERE env = ? AND scope_type = 'group' AND scope_id = ? AND status = 'active'"
            ),
            vec![
                DbValue::from(deleted_at.as_str()),
                DbValue::from(env),
                DbValue::from(group_id),
            ],
        )));
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            "UPDATE bcs_event_deliveries SET status = 'cancelled', cancelled_at = ?, \
             next_attempt_at = NULL, lease_owner = NULL, lease_until = NULL \
             WHERE env = ? AND status IN ('pending', 'retry_wait') AND subscription_id IN (\
               SELECT subscription_id FROM bcs_event_subscriptions \
               WHERE env = ? AND scope_type = 'group' AND scope_id = ? AND status = 'active'\
             )",
            vec![
                DbValue::from(deleted_at.as_str()),
                DbValue::from(env),
                DbValue::from(env),
                DbValue::from(group_id),
            ],
        )));
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            "UPDATE bcs_event_fanout_targets SET status = 'cancelled', cancelled_at = ?, \
             lease_owner = NULL, lease_until = NULL \
             WHERE env = ? AND status = 'pending' AND subscription_id IN (\
               SELECT subscription_id FROM bcs_event_subscriptions \
               WHERE env = ? AND scope_type = 'group' AND scope_id = ? AND status = 'active'\
             )",
            vec![
                DbValue::from(deleted_at.as_str()),
                DbValue::from(env),
                DbValue::from(env),
                DbValue::from(group_id),
            ],
        )));
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            "UPDATE bcs_events SET fanout_status = 'completed' WHERE env = ? \
             AND fanout_status = 'pending' AND NOT EXISTS (\
               SELECT 1 FROM bcs_event_fanout_targets target \
               WHERE target.env = bcs_events.env AND target.event_id = bcs_events.event_id \
                 AND target.status = 'pending'\
             )",
            vec![DbValue::from(env)],
        )));
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            "UPDATE bcs_event_subscriptions SET status = 'disabled', updated_at = ? \
             WHERE env = ? AND scope_type = 'group' AND scope_id = ? AND status = 'active'",
            vec![
                DbValue::from(deleted_at.as_str()),
                DbValue::from(env),
                DbValue::from(group_id),
            ],
        )));
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            "UPDATE bcs_event_scope_epochs SET epoch = epoch + 1 \
             WHERE env = ? AND scope_type = 'group' AND scope_id = ?",
            vec![DbValue::from(env), DbValue::from(group_id)],
        )));
        Ok(Self { steps })
    }
}

impl GroupProvisioningEventTransactionPlan {
    #[allow(clippy::too_many_arguments)]
    pub fn build(
        group_id: &str,
        subscription_ids: &[String],
        actor: &EventActor,
        finalized_at_ms: u64,
        env: &str,
        events: &[AppendEventRecord],
        flavor: DbSqlFlavor,
        preceding_step_count: usize,
    ) -> Result<Self, EventRepoError> {
        if group_id.is_empty() || env.is_empty() {
            return Err(EventRepoError::InvalidInput(
                "Group provisioning group id and env must be non-empty".to_string(),
            ));
        }
        if events.iter().any(|event| {
            event.env != env || event.event.scope.group_id.as_deref() != Some(group_id)
        }) {
            return Err(EventRepoError::InvalidInput(
                "Group provisioning Events must use the finalized Group scope and environment"
                    .to_string(),
            ));
        }
        let finalized_at = db_timestamp_from_ms(finalized_at_ms)?;
        let mut steps = vec![
            DbTransactionStep::Execute(DbStatement::with_params(
                scope_epoch_insert_sql(flavor),
                vec![
                    DbValue::from(env),
                    DbValue::from("group"),
                    DbValue::from(group_id),
                ],
            )),
            DbTransactionStep::Query(DbStatement::with_params(
                scope_epoch_lock_sql(flavor),
                vec![
                    DbValue::from(env),
                    DbValue::from("group"),
                    DbValue::from(group_id),
                ],
            )),
        ];

        for subscription_id in subscription_ids {
            let query_step = preceding_step_count + steps.len();
            steps.push(DbTransactionStep::Query(DbStatement::with_params(
                pending_subscription_lock_sql(flavor),
                vec![
                    DbValue::from(env),
                    DbValue::from(subscription_id.as_str()),
                    DbValue::from(group_id),
                ],
            )));
            let subscription_binding =
                DbTransactionParam::query_result(query_step, 0, "subscription_id");
            steps.push(DbTransactionStep::Execute(
                DbStatement::with_transaction_params(
                    "UPDATE bcs_event_subscriptions SET status = 'active', updated_at = ? \
                     WHERE env = ? AND subscription_id = ? AND status = 'pending'",
                    vec![
                        value(finalized_at.as_str()),
                        value(env),
                        subscription_binding.clone(),
                    ],
                ),
            ));
            steps.push(DbTransactionStep::Execute(
                DbStatement::with_transaction_params(
                    "UPDATE bcs_event_subscription_revisions SET activated_at = ? \
                     WHERE env = ? AND subscription_id = ? AND revision = 1",
                    vec![
                        value(finalized_at.as_str()),
                        value(env),
                        subscription_binding.clone(),
                    ],
                ),
            ));
            steps.push(DbTransactionStep::Execute(
                DbStatement::with_transaction_params(
                    "INSERT INTO bcs_event_subscription_audits \
                     (audit_id, subscription_id, revision, action, actor_type, actor_id, reason, \
                      details_json, created_at, env) \
                     VALUES (?, ?, 1, 'provisioning_activated', ?, ?, \
                             'group_provisioning_finalized', NULL, ?, ?)",
                    vec![
                        value(uuid::Uuid::new_v4().to_string()),
                        subscription_binding,
                        value(event_actor_type_name(actor.actor_type)),
                        value(actor.id.as_str()),
                        value(finalized_at.as_str()),
                        value(env),
                    ],
                ),
            ));
        }
        if !subscription_ids.is_empty() {
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                "UPDATE bcs_event_scope_epochs SET epoch = epoch + 1 \
                 WHERE env = ? AND scope_type = 'group' AND scope_id = ?",
                vec![DbValue::from(env), DbValue::from(group_id)],
            )));
        }
        for event in events {
            let plan = EventAppendTransactionPlan::build(
                event,
                flavor,
                preceding_step_count + steps.len(),
            )?;
            steps.extend(plan.steps);
        }
        Ok(Self { steps })
    }
}

fn pending_subscription_lock_sql(flavor: DbSqlFlavor) -> &'static str {
    match flavor {
        DbSqlFlavor::Mysql => {
            "SELECT subscription_id FROM bcs_event_subscriptions \
             WHERE env = ? AND subscription_id = ? AND scope_type = 'group' \
               AND scope_id = ? AND status = 'pending' FOR UPDATE"
        }
        DbSqlFlavor::Sqlite => {
            "SELECT subscription_id FROM bcs_event_subscriptions \
             WHERE env = ? AND subscription_id = ? AND scope_type = 'group' \
               AND scope_id = ? AND status = 'pending'"
        }
    }
}

fn event_actor_type_name(actor_type: EventActorType) -> &'static str {
    match actor_type {
        EventActorType::Human => "human",
        EventActorType::Bot => "bot",
        EventActorType::App => "app",
        EventActorType::System => "system",
    }
}

impl EventAppendTransactionPlan {
    pub fn build(
        command: &AppendEventRecord,
        flavor: DbSqlFlavor,
        preceding_step_count: usize,
    ) -> Result<Self, EventRepoError> {
        if command.event.causation_event_id.as_deref() == Some(command.event.event_id.as_str()) {
            return Err(EventRepoError::CausationViolation(
                "event cannot cause itself".to_string(),
            ));
        }
        let occurred_at = db_timestamp_from_rfc3339(&command.event.occurred_at)?;
        let recorded_at = db_timestamp_from_rfc3339(&command.recorded_at)?;
        let retention_until = db_timestamp_from_ms(command.retention_until_ms)?;
        let actor_json = command
            .event
            .actor
            .as_ref()
            .map(serde_json::to_string)
            .transpose()
            .map_err(|error| EventRepoError::InvalidInput(format!("serialize actor: {error}")))?;
        let data_json = serde_json::to_string(&command.event.data).map_err(|error| {
            EventRepoError::InvalidInput(format!("serialize Event data: {error}"))
        })?;

        let mut steps = scope_epoch_lock_steps(command, flavor);
        let causation_param = if let Some(cause_id) = command.event.causation_event_id.as_ref() {
            let cause_query_step = preceding_step_count + steps.len();
            steps.push(DbTransactionStep::Query(DbStatement::with_params(
                "SELECT event_id FROM bcs_events WHERE env = ? AND event_id = ?",
                vec![
                    DbValue::from(command.env.as_str()),
                    DbValue::from(cause_id.as_str()),
                ],
            )));
            DbTransactionParam::query_result(cause_query_step, 0, "event_id")
        } else {
            value(DbValue::Null)
        };
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            stream_insert_sql(flavor),
            vec![
                DbValue::from(command.env.as_str()),
                DbValue::from(command.event.stream_key.as_str()),
            ],
        )));
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            "UPDATE bcs_event_streams SET last_sequence = last_sequence + 1, \
             updated_at = CURRENT_TIMESTAMP WHERE env = ? AND stream_key = ?",
            vec![
                DbValue::from(command.env.as_str()),
                DbValue::from(command.event.stream_key.as_str()),
            ],
        )));
        let sequence_query_step = preceding_step_count + steps.len();
        steps.push(DbTransactionStep::Query(DbStatement::with_params(
            stream_sequence_sql(flavor),
            vec![
                DbValue::from(command.env.as_str()),
                DbValue::from(command.event.stream_key.as_str()),
            ],
        )));

        let event_insert_step = preceding_step_count + steps.len();
        steps.push(DbTransactionStep::Execute(
            DbStatement::with_transaction_params(
                "INSERT INTO bcs_events (event_id, event_type, schema_version, producer, \
                 producer_key, subject_type, subject_id, group_id, \
                 session_id, task_id, run_id, stream_key, sequence, actor_json, correlation_id, \
                 causation_event_id, trace_id, data_json, occurred_at, recorded_at, fanout_status, \
                 retention_until, env) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, \
                 ?, ?, ?, ?, ?, ?, ?)",
                vec![
                    value(command.event.event_id.as_str()),
                    value(command.event.event_type.as_str()),
                    value(command.event.schema_version.as_str()),
                    value(command.event.producer.as_str()),
                    value(command.event.producer_key.as_str()),
                    value(command.event.subject.subject_type.as_str()),
                    value(command.event.subject.id.as_str()),
                    optional_value(command.event.scope.group_id.as_deref()),
                    optional_value(command.event.scope.session_id.as_deref()),
                    optional_value(command.event.scope.task_id.as_deref()),
                    optional_value(command.event.scope.run_id.as_deref()),
                    value(command.event.stream_key.as_str()),
                    DbTransactionParam::query_result(sequence_query_step, 0, "sequence"),
                    optional_owned_value(actor_json),
                    optional_value(command.event.correlation_id.as_deref()),
                    causation_param,
                    optional_value(command.event.trace_id.as_deref()),
                    value(data_json),
                    value(occurred_at),
                    value(recorded_at.as_str()),
                    value("pending"),
                    value(retention_until),
                    value(command.env.as_str()),
                ],
            ),
        ));

        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            target_snapshot_sql(flavor),
            target_snapshot_params(command, &recorded_at),
        )));
        if let Some(cause_event_id) = command.event.causation_event_id.as_deref() {
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                causal_target_snapshot_sql(flavor),
                vec![
                    DbValue::from(recorded_at.as_str()),
                    DbValue::from(cause_event_id),
                    DbValue::from(command.env.as_str()),
                    DbValue::from(command.event.event_id.as_str()),
                ],
            )));
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                causal_dependency_update_sql(),
                vec![
                    DbValue::from(cause_event_id),
                    DbValue::from(command.env.as_str()),
                    DbValue::from(command.event.event_id.as_str()),
                ],
            )));
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                "UPDATE bcs_events SET fanout_status = 'pending' WHERE env = ? AND event_id = ? \
                 AND EXISTS (SELECT 1 FROM bcs_event_fanout_targets \
                   WHERE env = ? AND event_id = ? AND purpose = 'causal_prerequisite' \
                     AND status = 'pending')",
                vec![
                    DbValue::from(command.env.as_str()),
                    DbValue::from(cause_event_id),
                    DbValue::from(command.env.as_str()),
                    DbValue::from(cause_event_id),
                ],
            )));
        }
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            "UPDATE bcs_events SET fanout_status = 'completed' \
             WHERE env = ? AND event_id = ? AND NOT EXISTS (\
               SELECT 1 FROM bcs_event_fanout_targets \
               WHERE env = ? AND event_id = ?\
             )",
            vec![
                DbValue::from(command.env.as_str()),
                DbValue::from(command.event.event_id.as_str()),
                DbValue::from(command.env.as_str()),
                DbValue::from(command.event.event_id.as_str()),
            ],
        )));
        let target_query_step = preceding_step_count + steps.len();
        steps.push(DbTransactionStep::Query(DbStatement::with_params(
            "SELECT target_id FROM bcs_event_fanout_targets \
             WHERE env = ? AND event_id = ? ORDER BY target_id",
            vec![
                DbValue::from(command.env.as_str()),
                DbValue::from(command.event.event_id.as_str()),
            ],
        )));

        Ok(Self {
            steps,
            sequence_query_step,
            event_insert_step,
            target_query_step,
        })
    }
}

fn scope_epoch_lock_steps(
    command: &AppendEventRecord,
    flavor: DbSqlFlavor,
) -> Vec<DbTransactionStep> {
    let mut steps = Vec::new();
    if let Some(scope_id) = command.event.scope.group_id.as_deref() {
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            scope_epoch_insert_sql(flavor),
            vec![
                DbValue::from(command.env.as_str()),
                DbValue::from("group"),
                DbValue::from(scope_id),
            ],
        )));
        steps.push(DbTransactionStep::Query(DbStatement::with_params(
            scope_epoch_lock_sql(flavor),
            vec![
                DbValue::from(command.env.as_str()),
                DbValue::from("group"),
                DbValue::from(scope_id),
            ],
        )));
    }
    steps
}

fn scope_epoch_insert_sql(flavor: DbSqlFlavor) -> &'static str {
    match flavor {
        DbSqlFlavor::Mysql => {
            "INSERT INTO bcs_event_scope_epochs (env, scope_type, scope_id, epoch) \
             VALUES (?, ?, ?, 0) ON DUPLICATE KEY UPDATE epoch = epoch"
        }
        DbSqlFlavor::Sqlite => {
            "INSERT INTO bcs_event_scope_epochs (env, scope_type, scope_id, epoch) \
             VALUES (?, ?, ?, 0) ON CONFLICT(env, scope_type, scope_id) DO NOTHING"
        }
    }
}

fn scope_epoch_lock_sql(flavor: DbSqlFlavor) -> &'static str {
    match flavor {
        DbSqlFlavor::Mysql => {
            "SELECT epoch FROM bcs_event_scope_epochs \
             WHERE env = ? AND scope_type = ? AND scope_id = ? FOR UPDATE"
        }
        DbSqlFlavor::Sqlite => {
            "SELECT epoch FROM bcs_event_scope_epochs \
             WHERE env = ? AND scope_type = ? AND scope_id = ?"
        }
    }
}

fn stream_insert_sql(flavor: DbSqlFlavor) -> &'static str {
    match flavor {
        DbSqlFlavor::Mysql => {
            "INSERT INTO bcs_event_streams (env, stream_key, last_sequence) VALUES (?, ?, 0) \
             ON DUPLICATE KEY UPDATE stream_key = stream_key"
        }
        DbSqlFlavor::Sqlite => {
            "INSERT INTO bcs_event_streams (env, stream_key, last_sequence) VALUES (?, ?, 0) \
             ON CONFLICT(env, stream_key) DO NOTHING"
        }
    }
}

fn stream_sequence_sql(flavor: DbSqlFlavor) -> &'static str {
    match flavor {
        DbSqlFlavor::Mysql => {
            "SELECT last_sequence AS sequence FROM bcs_event_streams \
             WHERE env = ? AND stream_key = ? FOR UPDATE"
        }
        DbSqlFlavor::Sqlite => {
            "SELECT last_sequence AS sequence FROM bcs_event_streams \
             WHERE env = ? AND stream_key = ?"
        }
    }
}

fn target_snapshot_sql(flavor: DbSqlFlavor) -> &'static str {
    match flavor {
        DbSqlFlavor::Mysql => {
            "INSERT INTO bcs_event_fanout_targets (target_id, event_id, subscription_id, \
             subscription_revision, purpose, replay_request_id, replay_of_delivery_id, \
             depends_on_target_id, status, lease_owner, lease_until, created_at, materialized_at, \
             cancelled_at, env) SELECT UUID(), ?, s.subscription_id, s.current_revision, 'normal', \
             '', NULL, NULL, 'pending', NULL, NULL, ?, NULL, NULL, ? \
             FROM bcs_event_subscriptions s JOIN bcs_event_subscription_revisions r \
               ON r.subscription_id = s.subscription_id AND r.revision = s.current_revision \
              AND r.env = s.env \
             WHERE s.env = ? AND s.status = 'active' \
               AND (JSON_CONTAINS(r.event_filters_json, JSON_QUOTE(?), '$') \
                    OR EXISTS (SELECT 1 FROM JSON_TABLE(r.event_filters_json, '$[*]' \
                       COLUMNS(filter_value VARCHAR(128) PATH '$')) filters \
                       WHERE RIGHT(filters.filter_value, 2) = '.*' \
                         AND ? LIKE CONCAT(LEFT(filters.filter_value, \
                             CHAR_LENGTH(filters.filter_value) - 1), '%'))) \
               AND s.scope_type = 'group' AND s.scope_id = ?"
        }
        DbSqlFlavor::Sqlite => {
            "INSERT INTO bcs_event_fanout_targets (target_id, event_id, subscription_id, \
             subscription_revision, purpose, replay_request_id, replay_of_delivery_id, \
             depends_on_target_id, status, lease_owner, lease_until, created_at, materialized_at, \
             cancelled_at, env) SELECT lower(hex(randomblob(16))), ?, s.subscription_id, \
             s.current_revision, 'normal', '', NULL, NULL, 'pending', NULL, NULL, ?, NULL, NULL, ? \
             FROM bcs_event_subscriptions s JOIN bcs_event_subscription_revisions r \
               ON r.subscription_id = s.subscription_id AND r.revision = s.current_revision \
              AND r.env = s.env \
             WHERE s.env = ? AND s.status = 'active' \
               AND EXISTS (SELECT 1 FROM json_each(r.event_filters_json) filters \
                   WHERE filters.value = ? OR (substr(filters.value, -2) = '.*' \
                     AND ? LIKE substr(filters.value, 1, length(filters.value) - 1) || '%')) \
               AND s.scope_type = 'group' AND s.scope_id = ?"
        }
    }
}

fn target_snapshot_params(command: &AppendEventRecord, recorded_at: &str) -> Vec<DbValue> {
    vec![
        DbValue::from(command.event.event_id.as_str()),
        DbValue::from(recorded_at),
        DbValue::from(command.env.as_str()),
        DbValue::from(command.env.as_str()),
        DbValue::from(command.event.event_type.as_str()),
        DbValue::from(command.event.event_type.as_str()),
        DbValue::from(command.event.scope.group_id.as_deref()),
    ]
}

fn causal_target_snapshot_sql(flavor: DbSqlFlavor) -> &'static str {
    match flavor {
        DbSqlFlavor::Mysql => {
            "INSERT IGNORE INTO bcs_event_fanout_targets (target_id, event_id, subscription_id, \
             subscription_revision, purpose, replay_request_id, replay_of_delivery_id, \
             depends_on_target_id, status, lease_owner, lease_until, created_at, materialized_at, \
             cancelled_at, env) SELECT UUID(), cause.event_id, effect.subscription_id, \
             effect.subscription_revision, 'causal_prerequisite', '', NULL, NULL, 'pending', \
             NULL, NULL, ?, NULL, NULL, effect.env \
             FROM bcs_event_fanout_targets effect JOIN bcs_events cause \
               ON cause.env = effect.env AND cause.event_id = ? \
             JOIN bcs_event_subscription_revisions revision \
               ON revision.env = effect.env \
              AND revision.subscription_id = effect.subscription_id \
              AND revision.revision = effect.subscription_revision \
             WHERE effect.env = ? AND effect.event_id = ? AND effect.purpose = 'normal' \
               AND effect.status <> 'cancelled' \
               AND (JSON_CONTAINS(revision.event_filters_json, JSON_QUOTE(cause.event_type), '$') \
                    OR EXISTS (SELECT 1 FROM JSON_TABLE(revision.event_filters_json, '$[*]' \
                       COLUMNS(filter_value VARCHAR(128) PATH '$')) filters \
                       WHERE RIGHT(filters.filter_value, 2) = '.*' \
                         AND cause.event_type LIKE CONCAT(LEFT(filters.filter_value, \
                             CHAR_LENGTH(filters.filter_value) - 1), '%'))) \
               AND NOT EXISTS (SELECT 1 FROM bcs_event_fanout_targets existing \
                 WHERE existing.env = effect.env AND existing.event_id = cause.event_id \
                   AND existing.subscription_id = effect.subscription_id \
                   AND existing.subscription_revision = effect.subscription_revision \
                   AND existing.purpose IN ('normal', 'causal_prerequisite') \
                   AND existing.status <> 'cancelled')"
        }
        DbSqlFlavor::Sqlite => {
            "INSERT OR IGNORE INTO bcs_event_fanout_targets (target_id, event_id, subscription_id, \
             subscription_revision, purpose, replay_request_id, replay_of_delivery_id, \
             depends_on_target_id, status, lease_owner, lease_until, created_at, materialized_at, \
             cancelled_at, env) SELECT lower(hex(randomblob(16))), cause.event_id, \
             effect.subscription_id, effect.subscription_revision, 'causal_prerequisite', '', \
             NULL, NULL, 'pending', NULL, NULL, ?, NULL, NULL, effect.env \
             FROM bcs_event_fanout_targets effect JOIN bcs_events cause \
               ON cause.env = effect.env AND cause.event_id = ? \
             JOIN bcs_event_subscription_revisions revision \
               ON revision.env = effect.env \
              AND revision.subscription_id = effect.subscription_id \
              AND revision.revision = effect.subscription_revision \
             WHERE effect.env = ? AND effect.event_id = ? AND effect.purpose = 'normal' \
               AND effect.status <> 'cancelled' \
               AND EXISTS (SELECT 1 FROM json_each(revision.event_filters_json) filters \
                 WHERE filters.value = cause.event_type \
                    OR (substr(filters.value, -2) = '.*' \
                      AND cause.event_type LIKE \
                        substr(filters.value, 1, length(filters.value) - 1) || '%')) \
               AND NOT EXISTS (SELECT 1 FROM bcs_event_fanout_targets existing \
                 WHERE existing.env = effect.env AND existing.event_id = cause.event_id \
                   AND existing.subscription_id = effect.subscription_id \
                   AND existing.subscription_revision = effect.subscription_revision \
                   AND existing.purpose IN ('normal', 'causal_prerequisite') \
                   AND existing.status <> 'cancelled')"
        }
    }
}

fn causal_dependency_update_sql() -> &'static str {
    "UPDATE bcs_event_fanout_targets AS effect SET depends_on_target_id = (\
       SELECT cause_target.target_id FROM bcs_event_fanout_targets cause_target \
       WHERE cause_target.env = effect.env AND cause_target.event_id = ? \
         AND cause_target.subscription_id = effect.subscription_id \
         AND cause_target.subscription_revision = effect.subscription_revision \
         AND cause_target.purpose IN ('normal', 'causal_prerequisite') \
         AND cause_target.status <> 'cancelled' \
       ORDER BY CASE cause_target.purpose WHEN 'normal' THEN 0 ELSE 1 END LIMIT 1\
     ) WHERE effect.env = ? AND effect.event_id = ? AND effect.purpose = 'normal'"
}

fn db_timestamp_from_rfc3339(timestamp: &str) -> Result<String, EventRepoError> {
    let parsed = DateTime::parse_from_rfc3339(timestamp).map_err(|error| {
        EventRepoError::InvalidInput(format!("invalid RFC3339 timestamp {timestamp:?}: {error}"))
    })?;
    Ok(parsed
        .with_timezone(&Utc)
        .format("%Y-%m-%d %H:%M:%S%.3f")
        .to_string())
}

fn db_timestamp_from_ms(timestamp_ms: u64) -> Result<String, EventRepoError> {
    let timestamp_ms = i64::try_from(timestamp_ms).map_err(|_| {
        EventRepoError::InvalidInput("timestamp is outside supported range".to_string())
    })?;
    Utc.timestamp_millis_opt(timestamp_ms)
        .single()
        .map(|timestamp| {
            timestamp
                .to_rfc3339_opts(SecondsFormat::Millis, true)
                .replace('T', " ")
                .trim_end_matches('Z')
                .to_string()
        })
        .ok_or_else(|| {
            EventRepoError::InvalidInput("timestamp is outside supported range".to_string())
        })
}

fn value(value: impl Into<DbValue>) -> DbTransactionParam {
    DbTransactionParam::Value(value.into())
}

fn optional_value(value: Option<&str>) -> DbTransactionParam {
    DbTransactionParam::Value(DbValue::from(value))
}

fn optional_owned_value(value: Option<String>) -> DbTransactionParam {
    DbTransactionParam::Value(DbValue::from(value))
}

#[cfg(test)]
#[allow(clippy::expect_used)]
mod tests {
    use std::collections::BTreeMap;

    use bcs_service_api::port::NewEvent;
    use bcs_service_api::types::{EVENT_SCHEMA_VERSION_V1, EventScope, EventSubject};

    use super::*;

    #[test]
    fn mysql_and_sqlite_plans_bind_every_placeholder_without_global_scope_row() {
        for flavor in [DbSqlFlavor::Mysql, DbSqlFlavor::Sqlite] {
            let plan = EventAppendTransactionPlan::build(&command(), flavor, 3)
                .expect("build Event transaction plan");
            let mut locked_scope_ids = Vec::new();
            for step in &plan.steps {
                let statement = match step {
                    DbTransactionStep::Query(statement) | DbTransactionStep::Execute(statement) => {
                        statement
                    }
                };
                assert_eq!(
                    statement.sql().matches('?').count(),
                    statement.params().len(),
                    "placeholder mismatch in {}",
                    statement.sql()
                );
                if statement.sql().contains("bcs_event_scope_epochs") {
                    locked_scope_ids.extend(
                        statement
                            .params()
                            .iter()
                            .filter_map(DbValue::as_str)
                            .map(str::to_string),
                    );
                }
            }
            assert!(
                locked_scope_ids
                    .iter()
                    .any(|value| value == "group-contract")
            );
            assert!(!locked_scope_ids.iter().any(|value| value == "__system__"));
            assert!(plan.sequence_query_step >= 3);
            assert!(plan.event_insert_step > plan.sequence_query_step);
            assert!(plan.target_query_step > plan.event_insert_step);
        }
    }

    #[test]
    fn plan_rejects_self_causation_before_building_steps() {
        let mut command = command();
        command.event.causation_event_id = Some(command.event.event_id.clone());
        assert!(matches!(
            EventAppendTransactionPlan::build(&command, DbSqlFlavor::Sqlite, 0),
            Err(EventRepoError::CausationViolation(_))
        ));
    }

    fn command() -> AppendEventRecord {
        AppendEventRecord {
            event: NewEvent {
                event_id: "evt-plan".to_string(),
                event_type: "group.created".to_string(),
                schema_version: EVENT_SCHEMA_VERSION_V1.to_string(),
                producer: "plan-test".to_string(),
                producer_key: "group:created".to_string(),
                occurred_at: "2026-08-19T00:00:00.000Z".to_string(),
                subject: EventSubject {
                    subject_type: "group".to_string(),
                    id: "group-contract".to_string(),
                },
                scope: EventScope {
                    group_id: Some("group-contract".to_string()),
                    ..EventScope::default()
                },
                stream_key: "group:group-contract".to_string(),
                actor: None,
                correlation_id: None,
                causation_event_id: None,
                trace_id: None,
                data: BTreeMap::new(),
            },
            recorded_at: "2026-08-19T00:00:01.000Z".to_string(),
            retention_until_ms: 2_000_000_000_000,
            env: "contract".to_string(),
        }
    }
}
