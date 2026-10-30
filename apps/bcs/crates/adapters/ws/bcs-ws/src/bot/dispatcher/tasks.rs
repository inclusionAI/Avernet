//! task.dispatch / task.message / task.complete request handling.

use std::sync::Arc;

use bcs_service_api::{TaskCompleteCommand, TaskDispatchCommand, TaskMessageCommand};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::sync::mpsc;
use tracing::info;

use super::{BotDispatchState, unwrap_outbound_group_id};
use super::error::{BotWsDispatchError, Result, send_error, send_ok, send_service_error};

fn resolve_task_scope(
    wire_group_id: &str,
    explicit_session_id: Option<&str>,
) -> Result<(String, Option<String>)> {
    if explicit_session_id.is_some_and(|session_id| session_id.trim().is_empty()) {
        return Err(BotWsDispatchError::InvalidFrameFormat(
            "task Session ID must not be empty".to_string(),
        ));
    }
    let (expected_group_id, _) = unwrap_outbound_group_id(wire_group_id, None);
    let (group_id, session_id) = unwrap_outbound_group_id(wire_group_id, explicit_session_id);
    if group_id != expected_group_id {
        return Err(BotWsDispatchError::InvalidFrameFormat(
            "task Session ID does not belong to the requested Group".to_string(),
        ));
    }
    Ok((group_id, session_id))
}

// ---------------------------------------------------------------------------
// Task dispatch types
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Deserialize)]
struct TaskDispatchParams {
    group_id: String,
    #[serde(default, alias = "bcs_session_id", alias = "sessionId")]
    session_id: Option<String>,
    target_bot: String,
    message: String,
}

#[derive(Debug, Clone, Serialize)]
struct TaskDispatchResult {
    task_id: String,
    status: String,
}

#[derive(Debug, Clone, Deserialize)]
struct TaskMessageParams {
    group_id: String,
    #[serde(default, alias = "bcs_session_id", alias = "sessionId")]
    session_id: Option<String>,
    message: String,
}

#[derive(Debug, Clone, Deserialize)]
struct TaskCompleteParams {
    group_id: String,
    #[serde(default, alias = "bcs_session_id", alias = "sessionId")]
    session_id: Option<String>,
    summary: String,
    #[serde(default = "default_complete_status")]
    status: String,
}

fn default_complete_status() -> String {
    "completed".to_string()
}

// ---------------------------------------------------------------------------
// task.dispatch handler
// ---------------------------------------------------------------------------

pub(super) async fn handle_task_dispatch(
    state: &Arc<BotDispatchState>,
    req_id: &str,
    params: Value,
    tx: &mpsc::Sender<String>,
    registered_bot_id: &Option<String>,
) -> Result<()> {
    let driver_bot = match registered_bot_id {
        Some(id) => id.clone(),
        None => {
            send_error(
                tx,
                req_id,
                "not_registered",
                "Bot must register before dispatching tasks",
            )
            .await?;
            return Ok(());
        }
    };

    let params: TaskDispatchParams = serde_json::from_value(params).map_err(|e| {
        BotWsDispatchError::InvalidFrameFormat(format!("Invalid task.dispatch params: {}", e))
    })?;
    let (real_group_id, manager_session_id) =
        resolve_task_scope(&params.group_id, params.session_id.as_deref())?;
    let mut payload = serde_json::json!({
        "message": params.message,
    });
    if let Some(session_id) = manager_session_id.as_deref() {
        payload["bcs_session_id"] = Value::String(session_id.to_string());
    }

    match state
        .message_flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: driver_bot.clone(),
            group_id: real_group_id.clone(),
            target_bot_id: params.target_bot.clone(),
            target_bot_name: None,
            payload,
            caller_delegation: None,
        })
        .await
    {
        Ok(outcome) => {
            info!(
                task_id = %outcome.task_id,
                driver = %driver_bot,
                group_id = %real_group_id,
                "Task dispatched to sub bot"
            );
            send_ok(
                tx,
                req_id,
                serde_json::to_value(TaskDispatchResult {
                    task_id: outcome.task_id,
                    status: outcome.status,
                })?,
            )
            .await?;
        }
        Err(err) => {
            send_service_error(tx, req_id, err).await?;
        }
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// task.message handler
// ---------------------------------------------------------------------------

pub(super) async fn handle_task_message(
    state: &Arc<BotDispatchState>,
    req_id: &str,
    params: Value,
    tx: &mpsc::Sender<String>,
    registered_bot_id: &Option<String>,
) -> Result<()> {
    let worker_bot = match registered_bot_id {
        Some(id) => id.clone(),
        None => {
            send_error(
                tx,
                req_id,
                "not_registered",
                "Bot must register before sending task messages",
            )
            .await?;
            return Ok(());
        }
    };

    let params: TaskMessageParams = serde_json::from_value(params).map_err(|e| {
        BotWsDispatchError::InvalidFrameFormat(format!("Invalid task.message params: {}", e))
    })?;
    let (real_group_id, manager_session_id) =
        resolve_task_scope(&params.group_id, params.session_id.as_deref())?;
    let mut payload = serde_json::json!({
        "message": params.message,
    });
    if let Some(session_id) = manager_session_id.as_deref() {
        payload["bcs_session_id"] = Value::String(session_id.to_string());
    }

    match state
        .message_flow
        .handle_task_message(TaskMessageCommand {
            worker_bot_id: worker_bot.clone(),
            group_id: real_group_id.clone(),
            payload,
        })
        .await
    {
        Ok(outcome) => {
            info!(
                group_id = %real_group_id,
                worker = %worker_bot,
                status = %outcome.status,
                "Task message sent to manager"
            );
            send_ok(tx, req_id, serde_json::json!({"status": outcome.status})).await?;
        }
        Err(err) => {
            send_service_error(tx, req_id, err).await?;
        }
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// task.complete handler
// ---------------------------------------------------------------------------

pub(super) async fn handle_task_complete(
    state: &Arc<BotDispatchState>,
    req_id: &str,
    params: Value,
    tx: &mpsc::Sender<String>,
    registered_bot_id: &Option<String>,
) -> Result<()> {
    let caller_bot = match registered_bot_id {
        Some(id) => id.clone(),
        None => {
            send_error(
                tx,
                req_id,
                "not_registered",
                "Bot must register before completing tasks",
            )
            .await?;
            return Ok(());
        }
    };

    let params: TaskCompleteParams = serde_json::from_value(params).map_err(|e| {
        BotWsDispatchError::InvalidFrameFormat(format!("Invalid task.complete params: {}", e))
    })?;
    let (real_group_id, wire_session_id) =
        resolve_task_scope(&params.group_id, params.session_id.as_deref())?;
    let mut payload = serde_json::json!({
        "group_id": real_group_id.clone(),
        "summary": params.summary,
        "status": params.status,
    });
    if let Some(session_id) = wire_session_id.as_deref() {
        payload["bcs_session_id"] = Value::String(session_id.to_string());
    }

    match state
        .message_flow
        .handle_task_complete(TaskCompleteCommand {
            task_id: real_group_id.clone(),
            bot_id: caller_bot.clone(),
            via_echo: false,
            payload,
        })
        .await
    {
        Ok(outcome) => {
            info!(
                group_id = %real_group_id,
                driver = %caller_bot,
                status = %outcome.status,
                "Task group completed"
            );
            if outcome.completed_session.is_none() && outcome.callback_requested {
                if let Some(callback) = state.task_callback.clone() {
                    let group_id = real_group_id.clone();
                    tokio::spawn(async move {
                        callback.execute_callback(group_id).await;
                    });
                }
            }
            send_ok(tx, req_id, serde_json::json!({"ok": true})).await?;
        }
        Err(err) => {
            send_service_error(tx, req_id, err).await?;
        }
    }

    Ok(())
}
