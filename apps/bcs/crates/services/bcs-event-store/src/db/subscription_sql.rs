//! Event store subscription sql helpers.

use super::*;

pub(super) fn scope_lock_steps(
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

pub(super) fn scope_epoch_increment_step(env: &str, scope_type: &str, scope_id: &str) -> DbTransactionStep {
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

pub(super) fn subscription_lock_sql(flavor: DbSqlFlavor) -> &'static str {
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

pub(super) fn revision_insert_statement(
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

pub(super) fn revision_insert_if_subscription_exists_statement(
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
