//! Atomic local acceptance and bounded replay of immutable history messages.
use super::*;
use bcs_domain::{MessageVisibilityDomain, SenderType};
use bcs_service_api::port::repo::collaboration_history::*;

fn invalid(message: impl std::fmt::Display) -> ServiceError {
    ServiceError::InternalError(format!("history checkpoint: {message}"))
}
pub(super) fn identity(
    payload: &StateMachineHistoryPayload,
) -> ServiceResult<StateMachineHistoryIdentity> {
    let m = &payload.message;
    let meta = &m.content["metadata"]["state_machine"];
    let node = meta["node_id"]
        .as_str()
        .filter(|s| !s.is_empty())
        .ok_or_else(|| invalid("missing node"))?;
    let attempt = meta["attempt"]
        .as_i64()
        .and_then(|a| i32::try_from(a).ok())
        .filter(|a| *a >= 0)
        .ok_or_else(|| invalid("invalid attempt"))?;
    let event = meta["event"]
        .as_str()
        .ok_or_else(|| invalid("missing event"))?;
    let kind = match event {
        "output" => bcs_domain::STATE_MACHINE_OUTPUT_MESSAGE_TYPE,
        "human_input_prompt" => bcs_domain::STATE_MACHINE_HUMAN_INPUT_PROMPT_MESSAGE_TYPE,
        _ => return Err(invalid("unsupported event")),
    };
    let key = if event == "output" {
        bcs_domain::state_machine_history::output_message_key(&m.run_id, node, attempt)
    } else {
        format!("{}:{node}:{attempt}:human-input-prompt", m.run_id)
    };
    if payload.schema_version != 1
        || meta["history_schema_version"].as_u64() != Some(1)
        || m.message_type != kind
        || m.run_id.is_empty()
        || m.session_id.is_empty()
        || m.group_id.is_empty()
        || m.visibility_domain != MessageVisibilityDomain::StateMachine
        || m.audience.is_none()
        || m.content["text"].as_str().is_none()
        || meta["run_id"].as_str() != Some(&m.run_id)
        || m.client_msg_id.as_deref() != Some(&key)
        || payload.message_id != bcs_domain::state_machine_history::physical_message_id(&key)
    {
        return Err(invalid("invalid payload identity or schema"));
    }
    Ok(StateMachineHistoryIdentity {
        session_id: m.session_id.clone(),
        run_id: m.run_id.clone(),
        node_id: node.into(),
        attempt,
        event: event.into(),
    })
}
fn key(env: &str, id: &StateMachineHistoryIdentity) -> ServiceResult<String> {
    Ok(format!(
        "history-message:{:x}",
        Sha256::digest(serde_json::to_vec(&(env, id)).map_err(invalid)?)
    ))
}
pub(super) fn checkpoint(
    env: &str,
    payload: StateMachineHistoryPayload,
) -> ServiceResult<StateMachineHistoryCheckpoint> {
    Ok(StateMachineHistoryCheckpoint {
        operation_key: key(env, &identity(&payload)?)?,
        payload,
        delivered_at_ms: None,
    })
}
fn same(a: &StateMachineHistoryPayload, b: &StateMachineHistoryPayload) -> ServiceResult<bool> {
    Ok(serde_json::to_value(a).map_err(invalid)? == serde_json::to_value(b).map_err(invalid)?)
}
fn validate_event(c: &AcceptStateMachineHistory) -> ServiceResult<()> {
    if c.event.as_ref().is_some_and(|e| {
        e.event.scope.session_id.as_deref() != Some(&c.payload.message.session_id)
            || e.event.scope.group_id.as_deref() != Some(&c.payload.message.group_id)
    }) {
        return Err(invalid("event scope mismatch"));
    }
    match &c.mutation {
        StateMachineHistoryMutation::ActivateHuman(command)
            if c.payload.message.created_at != command.started_at_ms =>
        {
            return Err(invalid("activation timestamp mismatch"));
        }
        StateMachineHistoryMutation::Preserve(node) => {
            let id = identity(&c.payload)?;
            if node.run_id != id.run_id
                || node.node_id != id.node_id
                || node.attempt != id.attempt
                || (id.event == "output"
                    && (node.artifact_text.as_deref()
                        != c.payload.message.content["text"].as_str()
                        || node.responded_by.as_ref().or(node.assignee_bot_id.as_ref())
                            != Some(&c.payload.message.sender_id)))
                || (id.event == "human_input_prompt"
                    && node.started_at != Some(c.payload.message.created_at))
            {
                return Err(invalid("preserved fact differs from Node evidence"));
            }
        }
        _ => {}
    }
    Ok(())
}

pub(super) fn accept_memory(
    inner: &mut StoreInner,
    c: &AcceptStateMachineHistory,
) -> ServiceResult<bool> {
    let id = identity(&c.payload)?;
    validate_event(c)?;
    let k = key("memory", &id)?;
    if let Some(saved) = inner.history_messages.get(&k) {
        if !same(&saved.payload, &c.payload)? {
            return Err(ServiceError::Conflict("history payload differs".into()));
        }
        return Ok(true);
    }
    let run = inner
        .runs
        .get(&id.run_id)
        .ok_or_else(|| invalid("run missing"))?;
    if run.session_id != id.session_id || run.group_id != c.payload.message.group_id {
        return Err(invalid("run scope mismatch"));
    }
    if !matches!(c.mutation, StateMachineHistoryMutation::Preserve(_))
        && run.status != StateMachineRunStatus::Running
    {
        return Ok(false);
    }
    let n = node_mut(inner, &id.run_id, &id.node_id)?;
    if n.attempt != id.attempt {
        return Ok(false);
    }
    match &c.mutation {
        StateMachineHistoryMutation::ActivateHuman(command) => {
            if id.event != "human_input_prompt"
                || command.run_id != id.run_id
                || command.node_id != id.node_id
                || command.attempt != id.attempt
                || n.assignee_bot_id.is_some()
                || !matches!(
                    n.status,
                    StateMachineNodeStatus::Pending | StateMachineNodeStatus::Ready
                )
            {
                return Ok(false);
            }
            n.status = StateMachineNodeStatus::Running;
            n.started_at = Some(command.started_at_ms);
            n.timeout_deadline_ms = Some(command.timeout_deadline_ms);
            n.assignee_bot_id = None;
            n.delivery_request_id = None;
            n.bot_delivery_run_id = None;
            n.outcome = None;
            n.responded_by = None;
            n.artifact_text = None;
            n.error = None;
            n.completed_at = None;
        }
        StateMachineHistoryMutation::AcceptOutput { judging } => {
            if id.event != "output"
                || n.status != StateMachineNodeStatus::Running
                || n.artifact_text.is_some()
            {
                return Ok(false);
            }
            if c.payload.message.sender_type == SenderType::Bot
                && n.assignee_bot_id.as_ref() != Some(&c.payload.message.sender_id)
            {
                return Ok(false);
            }
            n.artifact_text = Some(c.payload.message.content["text"].as_str().unwrap().into());
            n.responded_by = (c.payload.message.sender_type == SenderType::Human)
                .then(|| c.payload.message.sender_id.clone());
            n.error = None;
            if *judging {
                judge::accept_history_input(inner, &id.run_id, &id.node_id, id.attempt);
            }
        }
        StateMachineHistoryMutation::Preserve(expected) => {
            if serde_json::to_value(&*n).map_err(invalid)?
                != serde_json::to_value(expected).map_err(invalid)?
            {
                return Ok(false);
            }
        }
    }
    inner.history_messages.insert(
        k.clone(),
        StateMachineHistoryCheckpoint {
            operation_key: k,
            payload: c.payload.clone(),
            delivered_at_ms: None,
        },
    );
    Ok(true)
}

pub(super) fn accept_sql(
    store: &MySqlCollaborationStore,
    c: &AcceptStateMachineHistory,
) -> ServiceResult<Vec<DbTransactionStep>> {
    let id = identity(&c.payload)?;
    validate_event(c)?;
    let suffix = if store.flavor == DbSqlFlavor::Mysql {
        " FOR UPDATE"
    } else {
        ""
    };
    let active = if matches!(c.mutation, StateMachineHistoryMutation::Preserve(_)) {
        ""
    } else {
        " AND status = 'running'"
    };
    let mut steps = vec![DbTransactionStep::Query(DbStatement::with_params(
        format!(
            "SELECT run_id FROM bcs_state_machine_runs WHERE env = ? AND run_id = ? AND session_id = ? AND group_id = ? AND record_status = 'active'{active}{suffix}"
        ),
        vec![
            store.env.as_str().into(),
            id.run_id.as_str().into(),
            id.session_id.as_str().into(),
            c.payload.message.group_id.as_str().into(),
        ],
    ))];
    let mut params: Vec<DbTransactionParam> = Vec::new();
    let (set, filter) = match &c.mutation {
        StateMachineHistoryMutation::ActivateHuman(command) => {
            if id.event != "human_input_prompt"
                || command.run_id != id.run_id
                || command.node_id != id.node_id
                || command.attempt != id.attempt
            {
                return Err(invalid("activation identity mismatch"));
            }
            params.extend([
                DbTransactionParam::value(command.started_at_ms),
                DbTransactionParam::value(command.timeout_deadline_ms),
            ]);
            ("status = 'running', started_at_ms = ?, timeout_deadline_ms = ?, assignee_bot_id = '', delivery_request_id = NULL, bot_delivery_run_id = NULL, outcome = NULL, responded_by = NULL, artifact_text = NULL, error_message = NULL, completed_at_ms = NULL".to_string(), "status IN ('pending', 'ready') AND assignee_bot_id = ''".to_string())
        }
        StateMachineHistoryMutation::AcceptOutput { judging } => {
            if id.event != "output" {
                return Err(invalid("output identity mismatch"));
            }
            params.extend([
                DbTransactionParam::value(c.payload.message.content["text"].as_str().unwrap()),
                DbTransactionParam::value(DbValue::from(
                    (c.payload.message.sender_type == SenderType::Human)
                        .then_some(c.payload.message.sender_id.as_str()),
                )),
            ]);
            (format!("artifact_text = ?, responded_by = ?, error_message = NULL{}", if *judging { ", runtime_phase = 'judging', recovery_lease_owner = NULL, recovery_lease_until_ms = NULL" } else { "" }),
                "status = 'running' AND artifact_text IS NULL AND (runtime_phase IS NULL OR runtime_phase IN ('dispatch_pending', 'waiting_provider'))".to_string())
        }
        StateMachineHistoryMutation::Preserve(_) => (String::new(), String::new()),
    };
    params.extend([
        DbTransactionParam::value(store.env.as_str()),
        DbTransactionParam::query_result(0, 0, "run_id"),
        DbTransactionParam::value(id.node_id.as_str()),
        DbTransactionParam::value(id.attempt),
    ]);
    if let StateMachineHistoryMutation::Preserve(expected) = &c.mutation {
        let binary = if store.flavor == DbSqlFlavor::Mysql {
            "BINARY "
        } else {
            ""
        };
        let mut filter = "status = ? AND (started_at_ms = ? OR (started_at_ms IS NULL AND ? IS NULL)) AND (completed_at_ms = ? OR (completed_at_ms IS NULL AND ? IS NULL))".to_string();
        params.push(DbTransactionParam::value(status_string(expected.status)?));
        for value in [
            expected.started_at,
            expected.started_at,
            expected.completed_at,
            expected.completed_at,
        ] {
            params.push(DbTransactionParam::value(
                value.map(DbValue::from).unwrap_or(DbValue::Null),
            ));
        }
        for (column, value) in [
            ("artifact_text", expected.artifact_text.as_deref()),
            ("responded_by", expected.responded_by.as_deref()),
            (
                "assignee_bot_id",
                Some(expected.assignee_bot_id.as_deref().unwrap_or("")),
            ),
        ] {
            filter.push_str(&format!(
                " AND ({binary}{column} = {binary}? OR ({column} IS NULL AND ? IS NULL))"
            ));
            params.extend([
                DbTransactionParam::value(DbValue::from(value)),
                DbTransactionParam::value(DbValue::from(value)),
            ]);
        }
        steps.push(DbTransactionStep::Query(DbStatement::with_transaction_params(format!("SELECT run_id FROM bcs_state_machine_node_runs WHERE env = ? AND run_id = ? AND node_id = ? AND attempt = ? AND record_status = 'active' AND {filter}{suffix}"), params)));
    } else {
        let sender_fence = if matches!(c.mutation, StateMachineHistoryMutation::AcceptOutput { .. })
            && c.payload.message.sender_type == SenderType::Bot
        {
            params.push(DbTransactionParam::value(
                c.payload.message.sender_id.as_str(),
            ));
            " AND assignee_bot_id = ?"
        } else {
            ""
        };
        steps.push(DbTransactionStep::ExecuteChecked { statement: DbStatement::with_transaction_params(format!(
            "UPDATE bcs_state_machine_node_runs SET {set}, {} WHERE env = ? AND run_id = ? AND node_id = ? AND attempt = ? AND record_status = 'active' AND {filter}{sender_fence}", store.flavor.set_modified_now()), params), expected_affected_rows: 1 });
    }
    let aggregate = if matches!(c.mutation, StateMachineHistoryMutation::Preserve(_)) {
        1
    } else {
        0
    };
    steps.push(DbTransactionStep::Execute(DbStatement::with_transaction_params(
        "INSERT INTO bcs_collaboration_delivery_checkpoints (env, operation_key, aggregate_kind, aggregate_id, operation_kind, node_id, aggregate_attempt, payload_json, status, created_at_ms) VALUES (?, ?, 'state_machine_node', ?, 'history_message', ?, ?, ?, 'pending', ?)",
        vec![DbTransactionParam::value(store.env.as_str()), DbTransactionParam::value(key(&store.env, &id)?), DbTransactionParam::query_result(aggregate, 0, "run_id"), DbTransactionParam::value(id.node_id), DbTransactionParam::value(id.attempt), DbTransactionParam::value(serde_json::to_string(&c.payload).map_err(invalid)?), DbTransactionParam::value(c.payload.message.created_at)])));
    Ok(steps)
}
fn status_string(status: StateMachineNodeStatus) -> ServiceResult<String> {
    serde_json::to_value(status)
        .map_err(invalid)?
        .as_str()
        .map(str::to_owned)
        .ok_or_else(|| invalid("invalid status"))
}

impl MemoryCollaborationStore {
    pub(super) async fn history_get(
        &self,
        id: &StateMachineHistoryIdentity,
    ) -> ServiceResult<Option<StateMachineHistoryCheckpoint>> {
        Ok(self
            .inner
            .read()
            .await
            .history_messages
            .get(&key("memory", id)?)
            .cloned())
    }
    pub(super) async fn history_confirm(
        &self,
        c: &StateMachineHistoryCheckpoint,
        at: u64,
    ) -> ServiceResult<()> {
        let mut inner = self.inner.write().await;
        let k = key("memory", &identity(&c.payload)?)?;
        let saved = inner
            .history_messages
            .get_mut(&k)
            .ok_or_else(|| invalid("checkpoint missing"))?;
        if c.operation_key != k || !same(&saved.payload, &c.payload)? {
            return Err(invalid("confirmation mismatch"));
        }
        saved.delivered_at_ms.get_or_insert(at);
        Ok(())
    }
    pub(super) async fn history_page(
        &self,
        after: Option<&StateMachineHistoryCursor>,
        limit: usize,
    ) -> ServiceResult<StateMachineHistoryPage> {
        let inner = self.inner.read().await;
        let pending: Vec<_> = inner
            .history_messages
            .values()
            .filter(|c| c.delivered_at_ms.is_none())
            .collect();
        let mut rows: Vec<_> = pending
            .into_iter()
            .filter(|c| {
                after.is_none_or(|a| {
                    (&c.payload.message.run_id, &c.operation_key) > (&a.run_id, &a.operation_key)
                })
            })
            .cloned()
            .collect();
        rows.sort_by(|a, b| {
            (&a.payload.message.run_id, &a.operation_key)
                .cmp(&(&b.payload.message.run_id, &b.operation_key))
        });
        rows.truncate(limit.min(100));
        let count = rows.len() as u64;
        let oldest = rows.iter().map(|c| c.payload.message.created_at).min();
        let next = rows.last().map(|c| StateMachineHistoryCursor {
            run_id: c.payload.message.run_id.clone(),
            operation_key: c.operation_key.clone(),
        });
        Ok(StateMachineHistoryPage {
            checkpoints: rows,
            failures: Vec::new(),
            next,
            pending_count: count,
            oldest_pending_at_ms: oldest,
        })
    }
}

const COLUMNS: &str = "operation_key, aggregate_id, node_id, aggregate_attempt, payload_json, status, created_at_ms, delivered_at_ms";
fn decode(env: &str, row: &DbRow) -> ServiceResult<StateMachineHistoryCheckpoint> {
    let payload: StateMachineHistoryPayload =
        serde_json::from_str(&db_get_column::<String>(row, "payload_json").map_err(invalid)?)
            .map_err(|_| invalid("invalid payload JSON"))?;
    let id = identity(&payload)?;
    let operation_key: String = db_get_column(row, "operation_key").map_err(invalid)?;
    let status: String = db_get_column(row, "status").map_err(invalid)?;
    if operation_key != key(env, &id)?
        || db_get_column::<String>(row, "aggregate_id").map_err(invalid)? != id.run_id
        || db_get_column::<String>(row, "node_id").map_err(invalid)? != id.node_id
        || db_get_column::<i32>(row, "aggregate_attempt").map_err(invalid)? != id.attempt
        || db_get_column::<u64>(row, "created_at_ms").map_err(invalid)?
            != payload.message.created_at
        || !matches!(status.as_str(), "pending" | "delivered")
    {
        return Err(invalid("corrupt identity or status"));
    }
    let delivered_at_ms = db_get_column_opt(row, "delivered_at_ms").map_err(invalid)?;
    if (status == "delivered") != delivered_at_ms.is_some() {
        return Err(invalid("inconsistent delivery status"));
    }
    Ok(StateMachineHistoryCheckpoint {
        operation_key,
        payload,
        delivered_at_ms,
    })
}
impl MySqlCollaborationStore {
    pub(super) async fn history_get(
        &self,
        id: &StateMachineHistoryIdentity,
    ) -> ServiceResult<Option<StateMachineHistoryCheckpoint>> {
        let rows = self.db.query(DbStatement::with_params(format!("SELECT {COLUMNS} FROM bcs_collaboration_delivery_checkpoints WHERE env = ? AND operation_kind = 'history_message' AND operation_key = ?"), vec![self.env.as_str().into(), key(&self.env,id)?.into()])).await.map_err(invalid)?;
        rows.first().map(|r| decode(&self.env, r)).transpose()
    }
    pub(super) async fn history_confirm(
        &self,
        c: &StateMachineHistoryCheckpoint,
        at: u64,
    ) -> ServiceResult<()> {
        let id = identity(&c.payload)?;
        if c.operation_key != key(&self.env, &id)? {
            return Err(invalid("confirmation identity mismatch"));
        }
        // Native MySQL JSON canonicalizes whitespace and object key order.
        // Compare JSON values, not the textual serialization of that column.
        let payload_match = if self.flavor == DbSqlFlavor::Mysql {
            "payload_json = CAST(? AS JSON)"
        } else {
            "payload_json = ?"
        };
        let changed = self.db.execute(DbStatement::with_params(format!("UPDATE bcs_collaboration_delivery_checkpoints SET status = 'delivered', delivered_at_ms = ? WHERE env = ? AND operation_key = ? AND operation_kind = 'history_message' AND status = 'pending' AND delivered_at_ms IS NULL AND {payload_match}"), vec![at.into(),self.env.as_str().into(),c.operation_key.as_str().into(),serde_json::to_string(&c.payload).map_err(invalid)?.into()])).await.map_err(invalid)?;
        if changed.affected_rows == 0 {
            let saved = self
                .history_get(&id)
                .await?
                .ok_or_else(|| invalid("checkpoint missing"))?;
            if saved.delivered_at_ms.is_none() || !same(&saved.payload, &c.payload)? {
                return Err(invalid("confirmation payload or status mismatch"));
            }
        }
        Ok(())
    }
    pub(super) async fn history_page(
        &self,
        after: Option<&StateMachineHistoryCursor>,
        limit: usize,
    ) -> ServiceResult<StateMachineHistoryPage> {
        let rows = self.db.query(DbStatement::with_params(format!("SELECT {COLUMNS} FROM bcs_collaboration_delivery_checkpoints WHERE env = ? AND operation_kind = 'history_message' AND status = 'pending' AND (aggregate_id > ? OR (aggregate_id = ? AND operation_key > ?)) ORDER BY aggregate_id, operation_key LIMIT ?"), vec![self.env.as_str().into(),after.map_or("",|a|a.run_id.as_str()).into(),after.map_or("",|a|a.run_id.as_str()).into(),after.map_or("",|a|a.operation_key.as_str()).into(),(limit.min(100) as u64).into()])).await.map_err(invalid)?;
        let cursor = |row: &DbRow| -> ServiceResult<StateMachineHistoryCursor> {
            Ok(StateMachineHistoryCursor {
                run_id: db_get_column(row, "aggregate_id").map_err(invalid)?,
                operation_key: db_get_column(row, "operation_key").map_err(invalid)?,
            })
        };
        let next = rows.last().map(cursor).transpose()?;
        let mut checkpoints = Vec::new();
        let mut failures = Vec::new();
        for row in &rows {
            match decode(&self.env, row) {
                Ok(checkpoint) => checkpoints.push(checkpoint),
                Err(_) => failures.push(cursor(row)?),
            }
        }
        // Page-local metrics avoid an ever-growing backlog COUNT on each tick.
        let oldest = rows
            .iter()
            .filter_map(|r| db_get_column::<u64>(r, "created_at_ms").ok())
            .min();
        Ok(StateMachineHistoryPage {
            pending_count: rows.len() as u64,
            oldest_pending_at_ms: oldest,
            checkpoints,
            failures,
            next,
        })
    }
}
