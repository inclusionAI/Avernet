//! Frame dispatcher for BCS WebSocket protocol.
//!
//! Dispatches incoming frames to appropriate handlers based on frame type and method.

mod connect;
mod error;
mod event;
mod response;
mod routing;
#[cfg(test)]
mod tests;
mod tasks;
mod trace;

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_protocol::{BcsFrame, RequestFrame};
use bcs_service_api::{
    BotRunContextPort, BotRuntimeConnectionService, CollaborationRuntimeService,
    GroupDispatchContextPort, MessageFlowService,
    SessionManagementService, SystemMessageService,
};
use serde::Deserialize;
use serde_json::Value;
use tokio::sync::{Mutex, mpsc};
use tracing::debug;

pub use error::{BotWsDispatchError, Result};
pub(super) use event::validate_v3_run_scope;

use connect::{handle_bot_connect, handle_bot_status};
use error::{map_session_error, send_error, send_ok};
use event::handle_event_frame;
use response::handle_response_frame;
use routing::handle_route_resolve;
use tasks::{handle_task_complete, handle_task_dispatch, handle_task_message};

use crate::bot::BotConnectionRegistry;
use crate::shared::RunChannelManager;

fn unwrap_outbound_group_id(
    wire_group_id: &str,
    explicit_session_id: Option<&str>,
) -> (String, Option<String>) {
    if let Some(session_id) = explicit_session_id {
        if let Some((group_id, _)) = session_id.split_once(':') {
            return (group_id.to_string(), Some(session_id.to_string()));
        }
        if let Some((group_id, _)) = wire_group_id.split_once(':') {
            // Old BCN plugins pass the real group id as `session_id` while
            // carrying the session-shaped id in `group_id`.
            if session_id == group_id {
                return (group_id.to_string(), Some(wire_group_id.to_string()));
            }
        }
        return (wire_group_id.to_string(), Some(session_id.to_string()));
    }

    match wire_group_id.split_once(':') {
        Some((group_id, _)) => (group_id.to_string(), Some(wire_group_id.to_string())),
        None => (wire_group_id.to_string(), None),
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BotDispatchOutcome {
    Dispatched,
    BotConnect {
        registered: bool,
        bot_uuid: Option<String>,
    },
}

#[async_trait]
pub trait TaskCallbackHook: Send + Sync {
    async fn execute_callback(&self, group_id: String);
}

#[derive(Clone)]
pub struct BotDispatchState {
    pub expected_actor_id: Option<String>,
    pub bot_runtime: Arc<dyn BotRuntimeConnectionService>,
    pub message_flow: Arc<dyn MessageFlowService>,
    pub collaboration_runtime: Arc<dyn CollaborationRuntimeService>,
    pub bot_run_context: Arc<dyn BotRunContextPort>,
    pub bot_connections: Arc<BotConnectionRegistry>,
    pub run_channels: Arc<RunChannelManager>,
    pub task_callback: Option<Arc<dyn TaskCallbackHook>>,
    pub session_management: Arc<dyn SessionManagementService>,
    pub group_dispatch: Arc<dyn GroupDispatchContextPort>,
    pub system_message: Option<Arc<dyn SystemMessageService>>,
    pub coordination_processed: Arc<Mutex<HashMap<String, u64>>>,
    pub agent_credential_backfill: Option<Arc<dyn super::AgentCredentialBackfillPort>>,
    pub interactions: Arc<dyn bcs_service_api::InteractionService>,
}

/// Dispatch an incoming frame to the appropriate handler.
///
/// Returns an error if the frame is invalid or processing fails.
/// The registered_bot_id is updated when a bot.connect succeeds.
pub async fn dispatch_frame(
    state: &Arc<BotDispatchState>,
    text: &str,
    tx: &mpsc::Sender<String>,
    registered_bot_id: &mut Option<String>,
) -> Result<BotDispatchOutcome> {
    // Parse the frame
    let frame: BcsFrame = serde_json::from_str(text)
        .map_err(|e| BotWsDispatchError::InvalidFrameFormat(e.to_string()))?;

    match frame {
        BcsFrame::Request(req) => {
            let is_initial_connect = req.method == "bot.connect" && registered_bot_id.is_none();
            let requested_bot_uuid = if is_initial_connect {
                req.params
                    .as_ref()
                    .and_then(|params| params.get("bot_id"))
                    .and_then(Value::as_str)
                    .map(str::to_owned)
            } else {
                None
            };
            if let Err(error) = handle_request_frame(state, &req, tx, registered_bot_id).await {
                if is_initial_connect {
                    return Err(BotWsDispatchError::BotConnectError {
                        bot_uuid: requested_bot_uuid,
                        source: Box::new(error),
                    });
                }
                return Err(error);
            }
            if is_initial_connect {
                return Ok(BotDispatchOutcome::BotConnect {
                    registered: registered_bot_id.is_some(),
                    bot_uuid: registered_bot_id.clone().or(requested_bot_uuid),
                });
            }
        }
        BcsFrame::Response(res) => {
            handle_response_frame(state, &res, registered_bot_id).await?;
        }
        BcsFrame::Event(event) => {
            handle_event_frame(state, &event, registered_bot_id).await?;
        }
    }

    Ok(BotDispatchOutcome::Dispatched)
}

/// Handle session.complete from a bot: mark an active session as completed.
async fn handle_session_complete(
    state: &Arc<BotDispatchState>,
    req_id: &str,
    params: Value,
    tx: &mpsc::Sender<String>,
) -> Result<()> {
    #[derive(Deserialize)]
    struct SessionCompleteParams {
        session_id: String,
        #[serde(default)]
        output: Option<Value>,
        #[serde(default)]
        error: Option<String>,
    }

    let params: SessionCompleteParams = serde_json::from_value(params).map_err(|e| {
        BotWsDispatchError::InvalidFrameFormat(format!("invalid session.complete params: {e}"))
    })?;

    match state
        .session_management
        .complete_if_running(&params.session_id, params.output, params.error)
        .await
    {
        Ok(Some(session)) => {
            send_ok(
                tx,
                req_id,
                serde_json::to_value(&session).unwrap_or(Value::Null),
            )
            .await?;
        }
        Ok(None) => {
            send_ok(tx, req_id, serde_json::json!({"already_completed": true})).await?;
        }
        Err(e) => {
            let (code, message) = map_session_error(e);
            send_error(tx, req_id, &code, &message).await?;
        }
    }
    Ok(())
}

/// Handle a RequestFrame.
async fn handle_request_frame(
    state: &Arc<BotDispatchState>,
    req: &RequestFrame,
    tx: &mpsc::Sender<String>,
    registered_bot_id: &mut Option<String>,
) -> Result<()> {
    debug!(id = %req.id, method = %req.method, "Handling RequestFrame");

    match req.method.as_str() {
        "bot.connect" => {
            let params = req.params.clone().unwrap_or(Value::Null);
            handle_bot_connect(state, &req.id, params, tx, registered_bot_id).await?;
        }
        "bot.status" => {
            let params = req.params.clone().unwrap_or(Value::Null);
            handle_bot_status(state, &req.id, params, tx, registered_bot_id).await?;
        }
        "chat.send" | "chat.abort" => {
            // Phase 2
            send_error(
                tx,
                &req.id,
                "not_implemented",
                "Chat methods not yet implemented",
            )
            .await?;
        }
        "task.status" | "task.cancel" => {
            tasks::handle_task_control(state, &req.id, &req.method, req.params.clone().unwrap_or(Value::Null), tx, registered_bot_id).await?;
        }
        "task.dispatch" => {
            let params = req.params.clone().unwrap_or(Value::Null);
            handle_task_dispatch(state, &req.id, params, tx, registered_bot_id).await?;
        }
        "task.message" => {
            let params = req.params.clone().unwrap_or(Value::Null);
            handle_task_message(state, &req.id, params, tx, registered_bot_id).await?;
        }
        "route.resolve" => {
            let params = req.params.clone().unwrap_or(Value::Null);
            handle_route_resolve(state, &req.id, params, tx, registered_bot_id).await?;
        }
        "task.complete" => {
            let params = req.params.clone().unwrap_or(Value::Null);
            handle_task_complete(state, &req.id, params, tx, registered_bot_id).await?;
        }
        "session.complete" => {
            let params = req.params.clone().unwrap_or(Value::Null);
            handle_session_complete(state, &req.id, params, tx).await?;
        }
        _ => {
            send_error(
                tx,
                &req.id,
                "unknown_method",
                &format!("Unknown method: {}", req.method),
            )
            .await?;
        }
    }

    Ok(())
}
