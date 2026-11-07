use super::*;
pub(super) fn snapshot_column(row: &DbRow, column: &str) -> ServiceResult<Option<String>> {
    db_get_column_opt(row, column)
        .map_err(|error| ServiceError::InternalError(format!("snapshot {column}: {error}")))
}

pub(super) fn current_millis() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}

pub(super) fn optional_u64_value(value: Option<u64>) -> DbValue {
    value.map(DbValue::from).unwrap_or(DbValue::Null)
}

pub(super) fn run_insert_params(env: &str, run: &StateMachineRun) -> ServiceResult<Vec<DbValue>> {
    let opening_message_override_json = run
        .opening_message_override
        .as_ref()
        .map(serde_json::to_string)
        .transpose()
        .map_err(|error| {
            ServiceError::InternalError(format!(
                "serialize state-machine opening_message_override: {error}"
            ))
        })?;
    Ok(vec![
        DbValue::from(env),
        DbValue::from(run.run_id.as_str()),
        DbValue::from(run.root_run_id.as_deref()),
        DbValue::from(run.rerun_of.as_deref()),
        DbValue::from(run.definition_id.as_str()),
        DbValue::from(run.definition_version),
        DbValue::from(run.group_id.as_str()),
        DbValue::from(run.group_version),
        DbValue::from(run.session_id.as_str()),
        run.session_activation_count
            .map(DbValue::from)
            .unwrap_or(DbValue::Null),
        DbValue::from(run.created_by.as_deref()),
        DbValue::from(run_status_to_str(run.status)),
        DbValue::from(run.input.to_string()),
        DbValue::from(opening_message_override_json.as_deref()),
        DbValue::from(run.output.as_deref()),
        DbValue::from(run.error.as_deref()),
        DbValue::from(run.created_at),
        DbValue::from(run.updated_at),
        optional_u64_value(run.completed_at),
    ])
}

pub(super) fn build_guarded_node_runs_inserts(
    env: &str,
    run_id: &str,
    nodes: &[StateMachineNodeRun],
) -> Vec<DbTransactionStep> {
    // 48 * 17 + 2 bind parameters stays below SQLite's legacy 999 limit.
    // Both dialects accept this derived UNION ALL table; the outer guard is
    // evaluated once per chunk inside the existing Session-locked transaction.
    const COLUMNS: &str = "env, run_id, node_id, status, attempt, node_timeout_ms, \
        timeout_deadline_ms, max_attempts, assignee_bot_id, outcome, responded_by, \
        delivery_request_id, bot_delivery_run_id, artifact_text, error_message, \
        started_at_ms, completed_at_ms, record_status";
    let first_row = COLUMNS.split(", ").enumerate().map(|(index, column)| {
        format!("{} AS {}", if index == 17 { "'active'" } else { "?" }, column)
    }).collect::<Vec<_>>().join(", ");
    nodes.chunks(48).map(|chunk| {
        let mut rows = vec![format!("SELECT {first_row}")];
        rows.extend((1..chunk.len()).map(|_| "SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active'".into()));
        let sql = format!("INSERT INTO bcs_state_machine_node_runs ({COLUMNS}) \
            SELECT batch.* FROM ({}) AS batch WHERE EXISTS ( \
                SELECT 1 FROM bcs_state_machine_runs \
                WHERE env = ? AND run_id = ? AND record_status = 'active')", rows.join(" UNION ALL "));
        let mut params = Vec::with_capacity(chunk.len() * 17 + 2);
        for node in chunk { params.extend(node_insert_params(env, run_id, node)); }
        params.extend([DbValue::from(env), DbValue::from(run_id)]);
        DbTransactionStep::Execute(DbStatement::with_params(sql, params))
    }).collect()
}

pub(super) fn build_node_runs_insert(
    env: &str,
    run_id: &str,
    nodes: &[StateMachineNodeRun],
) -> Option<(String, Vec<DbValue>)> {
    if nodes.is_empty() {
        return None;
    }
    let placeholders = nodes
        .iter()
        .map(|_| "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')")
        .collect::<Vec<_>>()
        .join(", ");
    let sql = format!(
        "INSERT INTO bcs_state_machine_node_runs \
         (env, run_id, node_id, status, attempt, node_timeout_ms, \
          timeout_deadline_ms, max_attempts, assignee_bot_id, outcome, responded_by, \
          delivery_request_id, bot_delivery_run_id, artifact_text, error_message, \
          started_at_ms, completed_at_ms, record_status) \
         VALUES {placeholders}"
    );
    let mut params = Vec::with_capacity(nodes.len() * 17);
    for node in nodes {
        params.extend(node_insert_params(env, run_id, node));
    }
    Some((sql, params))
}

fn node_insert_params(
    env: &str,
    run_id: &str,
    node: &StateMachineNodeRun,
) -> Vec<DbValue> {
    vec![
        DbValue::from(env),
        DbValue::from(run_id),
        DbValue::from(node.node_id.as_str()),
        DbValue::from(node_status_to_str(node.status)),
        DbValue::from(node.attempt),
        optional_u64_value(node.node_timeout_ms),
        optional_u64_value(node.timeout_deadline_ms),
        DbValue::from(node.max_attempts),
        DbValue::from(node.assignee_bot_id.as_deref().unwrap_or("")),
        DbValue::from(node.outcome.as_deref()),
        DbValue::from(node.responded_by.as_deref()),
        DbValue::from(node.delivery_request_id.as_deref()),
        DbValue::from(node.bot_delivery_run_id.as_deref()),
        DbValue::from(node.artifact_text.as_deref()),
        DbValue::from(node.error.as_deref()),
        optional_u64_value(node.started_at),
        optional_u64_value(node.completed_at),
    ]
}

pub(super) fn row_to_state_machine_run(row: DbRow) -> ServiceResult<StateMachineRun> {
    let status_raw: String = db_get_column(&row, "status")
        .map_err(|error| ServiceError::InternalError(format!("run status: {error}")))?;
    Ok(StateMachineRun {
        run_id: db_string(&row, "run_id")?,
        root_run_id: db_optional_string(&row, "root_run_id")?,
        rerun_of: db_optional_string(&row, "rerun_of")?,
        definition_id: db_string(&row, "definition_id")?,
        definition_version: db_i32(&row, "definition_version")?,
        group_id: db_string(&row, "group_id")?,
        group_version: db_i32(&row, "group_version")?,
        session_id: db_string(&row, "session_id")?,
        session_activation_count: db_optional_i32(&row, "session_activation_count")?,
        created_by: db_optional_string(&row, "created_by")?,
        status: parse_run_status(&status_raw)?,
        input: db_json(&row, "input_json")?.unwrap_or(serde_json::Value::Null),
        opening_message_override: db_opening_message_override(&row)?,
        output: db_optional_string(&row, "output_text")?,
        error: db_optional_string(&row, "error_message")?,
        created_at: db_u64(&row, "created_at_ms")?,
        updated_at: db_u64(&row, "updated_at_ms")?,
        completed_at: db_optional_u64(&row, "completed_at_ms")?,
    })
}

pub(super) fn row_to_state_machine_node_run(row: DbRow) -> ServiceResult<StateMachineNodeRun> {
    let status_raw: String = db_get_column(&row, "status")
        .map_err(|error| ServiceError::InternalError(format!("node status: {error}")))?;
    Ok(StateMachineNodeRun {
        run_id: db_string(&row, "run_id")?,
        node_id: db_string(&row, "node_id")?,
        status: parse_node_status(&status_raw)?,
        attempt: db_i32(&row, "attempt")?,
        node_timeout_ms: db_optional_u64(&row, "node_timeout_ms")?,
        timeout_deadline_ms: db_optional_u64(&row, "timeout_deadline_ms")?,
        max_attempts: db_i32(&row, "max_attempts")?,
        assignee_bot_id: db_assignee_bot_id(&row)?,
        outcome: db_optional_string(&row, "outcome")?,
        responded_by: db_optional_string(&row, "responded_by")?,
        delivery_request_id: db_optional_string(&row, "delivery_request_id")?,
        bot_delivery_run_id: db_optional_string(&row, "bot_delivery_run_id")?,
        artifact_text: db_optional_string(&row, "artifact_text")?,
        error: db_optional_string(&row, "error_message")?,
        started_at: db_optional_u64(&row, "started_at_ms")?,
        completed_at: db_optional_u64(&row, "completed_at_ms")?,
    })
}

pub(super) fn row_to_delivery_correlation(row: DbRow) -> ServiceResult<StateMachineDeliveryCorrelation> {
    Ok(StateMachineDeliveryCorrelation {
        state_machine_run_id: db_string(&row, "state_machine_run_id")?,
        node_id: db_string(&row, "node_id")?,
        attempt: db_i32(&row, "attempt")?,
        assignee_bot_id: db_string(&row, "assignee_bot_id")?,
        delivery_request_id: db_string(&row, "delivery_request_id")?,
        bot_delivery_run_id: db_optional_string(&row, "bot_delivery_run_id")?,
    })
}

pub(super) fn row_to_collaboration_event(row: DbRow) -> ServiceResult<CollaborationEventRecord> {
    Ok(CollaborationEventRecord {
        state_machine_run_id: db_string(&row, "state_machine_run_id")?,
        node_id: db_optional_string(&row, "node_id")?,
        attempt: db_optional_i32(&row, "attempt")?,
        event_type: db_string(&row, "event_type")?,
        payload: db_json(&row, "payload_json")?.unwrap_or(serde_json::Value::Null),
        created_at: db_u64(&row, "created_at_ms")?,
    })
}

pub(super) fn db_plugin_error(action: &str, error: DbError) -> ServiceError {
    ServiceError::InternalError(format!("template database {action}: {error}"))
}

pub(super) fn db_string(row: &DbRow, column: &str) -> ServiceResult<String> {
    db_get_column(row, column)
        .map_err(|error| ServiceError::InternalError(format!("{column}: {error}")))
}

fn db_assignee_bot_id(row: &DbRow) -> ServiceResult<Option<String>> {
    let value = db_string(row, "assignee_bot_id")?;
    Ok((!value.is_empty()).then_some(value))
}

fn db_optional_string(row: &DbRow, column: &str) -> ServiceResult<Option<String>> {
    db_get_column_opt(row, column)
        .map_err(|error| ServiceError::InternalError(format!("{column}: {error}")))
}

pub(super) fn db_i32(row: &DbRow, column: &str) -> ServiceResult<i32> {
    db_get_column(row, column)
        .map_err(|error| ServiceError::InternalError(format!("{column}: {error}")))
}

fn db_optional_i32(row: &DbRow, column: &str) -> ServiceResult<Option<i32>> {
    db_get_column_opt(row, column)
        .map_err(|error| ServiceError::InternalError(format!("{column}: {error}")))
}

pub(super) fn db_u64(row: &DbRow, column: &str) -> ServiceResult<u64> {
    db_optional_u64(row, column)?
        .ok_or_else(|| ServiceError::InternalError(format!("{column}: column is missing or NULL")))
}

fn db_optional_u64(row: &DbRow, column: &str) -> ServiceResult<Option<u64>> {
    match row.get(column) {
        None | Some(DbValue::Null) => Ok(None),
        Some(value) => value.as_u64().map(Some).ok_or_else(|| {
            ServiceError::InternalError(format!("{column}: not an unsigned integer: {value:?}"))
        }),
    }
}

fn db_json(row: &DbRow, column: &str) -> ServiceResult<Option<serde_json::Value>> {
    let raw = db_optional_string(row, column)?;
    match raw {
        None => Ok(None),
        Some(value) if value.is_empty() => Ok(None),
        Some(value) => serde_json::from_str(&value)
            .map(Some)
            .map_err(|error| ServiceError::InternalError(format!("{column}: json parse: {error}"))),
    }
}

fn db_opening_message_override(row: &DbRow) -> ServiceResult<Option<OpeningMessage>> {
    match db_optional_string(row, "opening_message_override_json")? {
        None => Ok(None),
        Some(value) if value.is_empty() => Err(ServiceError::InternalError(
            "opening_message_override_json: persisted value must not be empty".to_string(),
        )),
        Some(value) => serde_json::from_str(&value).map(Some).map_err(|error| {
            ServiceError::InternalError(format!(
                "opening_message_override_json: json parse: {error}"
            ))
        }),
    }
}

pub(super) fn run_status_to_str(status: StateMachineRunStatus) -> &'static str {
    match status {
        StateMachineRunStatus::Pending => "pending",
        StateMachineRunStatus::Running => "running",
        StateMachineRunStatus::Completed => "completed",
        StateMachineRunStatus::Failed => "failed",
        StateMachineRunStatus::Aborted => "aborted",
    }
}

fn parse_run_status(value: &str) -> ServiceResult<StateMachineRunStatus> {
    match value {
        "pending" => Ok(StateMachineRunStatus::Pending),
        "running" => Ok(StateMachineRunStatus::Running),
        "completed" => Ok(StateMachineRunStatus::Completed),
        "failed" => Ok(StateMachineRunStatus::Failed),
        "aborted" => Ok(StateMachineRunStatus::Aborted),
        other => Err(ServiceError::InternalError(format!(
            "unknown state machine run status: {other}"
        ))),
    }
}

fn node_status_to_str(status: StateMachineNodeStatus) -> &'static str {
    match status {
        StateMachineNodeStatus::Pending => "pending",
        StateMachineNodeStatus::Ready => "ready",
        StateMachineNodeStatus::Running => "running",
        StateMachineNodeStatus::Completed => "completed",
        StateMachineNodeStatus::Failed => "failed",
        StateMachineNodeStatus::RetryScheduled => "retry_scheduled",
        StateMachineNodeStatus::Skipped => "skipped",
    }
}

pub(super) fn parse_node_status(value: &str) -> ServiceResult<StateMachineNodeStatus> {
    match value {
        "pending" => Ok(StateMachineNodeStatus::Pending),
        "ready" => Ok(StateMachineNodeStatus::Ready),
        "running" => Ok(StateMachineNodeStatus::Running),
        "completed" => Ok(StateMachineNodeStatus::Completed),
        "failed" => Ok(StateMachineNodeStatus::Failed),
        "retry_scheduled" => Ok(StateMachineNodeStatus::RetryScheduled),
        "skipped" => Ok(StateMachineNodeStatus::Skipped),
        other => Err(ServiceError::InternalError(format!(
            "unknown state machine node status: {other}"
        ))),
    }
}
