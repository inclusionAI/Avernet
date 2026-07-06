//! Frame dispatcher for BCS WebSocket protocol.
//!
//! Dispatches incoming frames to appropriate handlers based on frame type and method.

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_protocol::{
    AgentEventPayload, AgentStream, BCS_MIN_SUPPORTED_VERSION, BCS_PROTOCOL_VERSION, BcsFrame,
    BotConnectParams as WireBotConnectParams, BotConnectResponse, BotStatus as WsBotStatus,
    BotStatusParams, ChatEventPayload, ChatEventState, ErrorShape, EventFrame,
    RequestFrame, ResponseFrame, RouteSelectorWire,
};
use bcs_service_api::{
    BotEventCommand, BotRuntimeConnectCommand, BotRuntimeConnectionService,
    BotRunContextPort, BotRuntimeDisconnectCommand, BotRuntimeStatusCommand, BotUseCaseError,
    ChatEventState as AppChatEventState,
    CollaborationRuntimeService, ConnectError, GroupDispatchContextPort, MessageFlowService,
    MessageLogEventType, MessageLogMode, MessageLogStatus, MESSAGE_LOG_SCHEMA_VERSION,
    MSG_LOG_TARGET, ServiceError, SessionCallbackDispatchPort,
    Participant, ParticipantRole, SessionManagementService, SessionUseCaseError,
    SystemMessageService, TaskCompleteCommand, TaskDispatchCommand,
    TaskMessageCommand, TaskRunAliasRegistration,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::sync::{Mutex, mpsc};
use tracing::{debug, info, warn};

use crate::bot::BotConnectionRegistry;
use crate::shared::RunChannelManager;

pub type Result<T> = std::result::Result<T, BotWsDispatchError>;

const BOT_DELIVERY_IS_PROVIDER_CODE: &str = "bot_delivery_is_provider";
const BOT_DELIVERY_IS_PROVIDER_MESSAGE: &str =
    "Bot delivery is configured for HttpProvider; WebSocket uplink is no longer accepted for this bot";

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

fn legacy_default_session_id(group_id: &str) -> Option<String> {
    if group_id.is_empty() || group_id.contains(':') {
        None
    } else {
        Some(format!("{group_id}:00000000"))
    }
}

#[derive(Debug, thiserror::Error)]
pub enum BotWsDispatchError {
    #[error("Invalid frame format: {0}")]
    InvalidFrameFormat(String),
    #[error("Bot '{0}' already has an active WebSocket connection")]
    BotAlreadyConnected(String),
    #[error("Conflict: {0}")]
    Conflict(String),
    #[error("Invalid or expired session token")]
    InvalidSessionToken,
    #[error("WebSocket protocol error: {0}")]
    WsProtocolError(String),
    #[error("JSON error: {0}")]
    JsonError(#[from] serde_json::Error),
    #[error("Service error: {0}")]
    ServiceError(#[from] ServiceError),
    #[error("bot.connect failed: {0}")]
    BotConnectError(Box<BotWsDispatchError>),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BotDispatchOutcome {
    Dispatched,
    BotConnect { registered: bool },
}

#[async_trait]
pub trait TaskCallbackHook: Send + Sync {
    async fn execute_callback(&self, group_id: String);
}

pub struct BotDispatchState {
    pub bot_runtime: Arc<dyn BotRuntimeConnectionService>,
    pub message_flow: Arc<dyn MessageFlowService>,
    pub collaboration_runtime: Arc<dyn CollaborationRuntimeService>,
    pub bot_run_context: Arc<dyn BotRunContextPort>,
    pub bot_connections: Arc<BotConnectionRegistry>,
    pub run_channels: Arc<RunChannelManager>,
    pub task_callback: Option<Arc<dyn TaskCallbackHook>>,
    pub session_management: Arc<dyn SessionManagementService>,
    pub group_dispatch: Arc<dyn GroupDispatchContextPort>,
    pub callback_dispatch: Arc<dyn SessionCallbackDispatchPort>,
    pub system_message: Option<Arc<dyn SystemMessageService>>,
    pub coordination_processed: Arc<Mutex<HashMap<String, u64>>>,
    pub agent_credential_backfill: Option<Arc<dyn super::AgentCredentialBackfillPort>>,
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
            let is_initial_connect =
                req.method == "bot.connect" && registered_bot_id.is_none();
            if let Err(error) = handle_request_frame(state, &req, tx, registered_bot_id).await {
                if is_initial_connect {
                    return Err(BotWsDispatchError::BotConnectError(Box::new(error)));
                }
                return Err(error);
            }
            if is_initial_connect {
                return Ok(BotDispatchOutcome::BotConnect {
                    registered: registered_bot_id.is_some(),
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

    let params: SessionCompleteParams = serde_json::from_value(params)
        .map_err(|e| BotWsDispatchError::InvalidFrameFormat(format!("invalid session.complete params: {e}")))?;

    match state.session_management.complete_if_running(&params.session_id, params.output, params.error).await {
        Ok(Some(session)) => {
            state
                .callback_dispatch
                .maybe_dispatch(session.clone(), state.session_management.clone())
                .await;
            send_ok(tx, req_id, serde_json::to_value(&session).unwrap_or(Value::Null)).await?;
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

/// Map a SessionUseCaseError to a WS error code and message.
fn map_session_error(err: SessionUseCaseError) -> (String, String) {
    match err {
        SessionUseCaseError::NotFound(s) => ("session_not_found".to_string(), s),
        SessionUseCaseError::InvalidParams(s) => ("invalid_params".to_string(), s),
        SessionUseCaseError::CallbackPending(s) => ("callback_pending".to_string(), s),
        SessionUseCaseError::Conflict(s) => ("conflict".to_string(), s),
        SessionUseCaseError::Internal(e) => ("internal_error".to_string(), e.to_string()),
    }
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

/// Handle bot.connect request (initial connection handshake).
///
/// This method is used for the initial connection handshake:
/// - If token is valid and bot exists → return is_new: false with bot_id (reconnection)
/// - If token is empty/invalid → return is_new: true with new token (onboarding needed)
/// - If bot_id is provided (preconfigured) → use that bot_id instead of generating one
///
/// For new bots, BCS will send a chat.send event telling the bot to onboard.
/// For existing bots, capabilities are loaded from storage.
async fn handle_bot_connect(
    state: &Arc<BotDispatchState>,
    req_id: &str,
    params: Value,
    tx: &mpsc::Sender<String>,
    registered_bot_id: &mut Option<String>,
) -> Result<()> {
    // Parse params
    let params: WireBotConnectParams = serde_json::from_value(params).map_err(|e| {
        BotWsDispatchError::InvalidFrameFormat(format!("Invalid connect params: {}", e))
    })?;

    // Protocol version negotiation
    let requested_version = params.protocol_version.unwrap_or(BCS_PROTOCOL_VERSION);
    if requested_version < BCS_MIN_SUPPORTED_VERSION || requested_version > BCS_PROTOCOL_VERSION {
        send_error(
            tx,
            req_id,
            "unsupported_protocol_version",
            &format!(
                "Protocol version {} is not supported. Supported range: {}-{}",
                requested_version, BCS_MIN_SUPPORTED_VERSION, BCS_PROTOCOL_VERSION
            ),
        )
        .await?;
        return Ok(());
    }

    if let Some(bot_id) = params.bot_id.as_deref() {
        if reject_provider_delivery_websocket(state, tx, req_id, bot_id).await? {
            return Ok(());
        }
    }

    let result = state
        .bot_runtime
        .connect_streaming(BotRuntimeConnectCommand {
            caller_actor_id: None,
            token: params.token,
            bot_id: params.bot_id,
            protocol_version: Some(requested_version),
            client_kind: params.client_kind,
        })
        .await
        .map_err(map_bot_use_case_error)?;

    if reject_provider_delivery_websocket(state, tx, req_id, &result.bot_uuid).await? {
        state
            .bot_runtime
            .disconnect_streaming(BotRuntimeDisconnectCommand {
                bot_id: result.bot_uuid,
            })
            .await
            .map_err(map_bot_use_case_error)?;
        return Ok(());
    }

    // Update registered_bot_id
    *registered_bot_id = Some(result.bot_uuid.clone());

    state
        .bot_connections
        .connect(result.bot_uuid.clone(), tx.clone())
        .await;

    // Send response with env for child processes
    let mut env_map = HashMap::new();
    env_map.insert("BCN_BOT_UUID".to_string(), result.bot_uuid.clone());
    env_map.insert("BCN_BOT_TOKEN".to_string(), result.token.clone());

    let response = BotConnectResponse {
        is_new: result.is_new,
        token: result.token,
        bot_uuid: result.bot_uuid,
        protocol_version: requested_version,
        min_supported_version: BCS_MIN_SUPPORTED_VERSION,
        deprecation: None,
        env: Some(env_map),
    };
    send_ok(tx, req_id, serde_json::to_value(response)?).await?;

    Ok(())
}

async fn reject_provider_delivery_websocket(
    state: &Arc<BotDispatchState>,
    tx: &mpsc::Sender<String>,
    req_id: &str,
    bot_id: &str,
) -> Result<bool> {
    match state
        .bot_runtime
        .is_provider_downlink_bot(bot_id)
        .await
    {
        Ok(true) => {
            send_error(
                tx,
                req_id,
                BOT_DELIVERY_IS_PROVIDER_CODE,
                BOT_DELIVERY_IS_PROVIDER_MESSAGE,
            )
            .await?;
            Ok(true)
        }
        Ok(false) | Err(ServiceError::BotNotFound(_)) => Ok(false),
        Err(err) => {
            warn!(
                bot_id = %bot_id,
                error = %err,
                "provider delivery guard failed; allowing WebSocket connect"
            );
            Ok(false)
        }
    }
}

/// Handle bot.status request.
async fn handle_bot_status(
    state: &Arc<BotDispatchState>,
    req_id: &str,
    params: Value,
    tx: &mpsc::Sender<String>,
    registered_bot_id: &Option<String>,
) -> Result<()> {
    // Must be registered first
    let bot_id = match registered_bot_id {
        Some(id) => id.clone(),
        None => {
            send_error(
                tx,
                req_id,
                "not_registered",
                "Bot must register before sending status",
            )
            .await?;
            return Ok(());
        }
    };

    // Parse params
    let params: BotStatusParams = serde_json::from_value(params).map_err(|e| {
        BotWsDispatchError::InvalidFrameFormat(format!("Invalid status params: {}", e))
    })?;

    debug!(
        bot_id = %bot_id,
        status = ?params.status,
        dynamic_summary = ?params.dynamic_summary,
        load = ?params.load,
        "Processing bot.status"
    );

    // Convert WS status to internal format
    let status_str = match params.status.unwrap_or(WsBotStatus::Idle) {
        WsBotStatus::Idle => "idle",
        WsBotStatus::Busy => "busy",
        WsBotStatus::Error => "error",
    };

    let status = bcs_service_api::BotDynamicStatus {
        status: status_str.to_string(),
        dynamic_summary: params.dynamic_summary,
        load: params.load,
        updated_at: Some(
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_millis() as u64)
                .unwrap_or(0),
        ),
    };

    let outcome = state
        .bot_runtime
        .update_runtime_status(BotRuntimeStatusCommand {
            caller_actor_id: Some(bot_id.clone()),
            bot_id: bot_id.clone(),
            status,
        })
        .await
        .map_err(map_bot_use_case_error)?;

    if !outcome.updated {
        warn!(bot_id = %bot_id, "Failed to update status - bot not found in registry");
        send_error(tx, req_id, "not_registered", "Bot not found in registry").await?;
        return Ok(());
    }

    info!(
        bot_id = %bot_id,
        status = status_str,
        "Bot status updated via WebSocket"
    );

    // Send success response
    send_ok(tx, req_id, serde_json::json!({"updated": true})).await?;

    Ok(())
}

fn map_bot_use_case_error(err: BotUseCaseError) -> BotWsDispatchError {
    match err {
        BotUseCaseError::Connect(ConnectError::AlreadyConnected(id)) => {
            BotWsDispatchError::BotAlreadyConnected(id)
        }
        BotUseCaseError::Connect(ConnectError::AlreadyRegistered(id)) => {
            BotWsDispatchError::Conflict(format!("Bot '{}' is already registered", id))
        }
        BotUseCaseError::Connect(ConnectError::InvalidBotId) => {
            BotWsDispatchError::InvalidFrameFormat("bot_id cannot be empty".to_string())
        }
        BotUseCaseError::Connect(ConnectError::InvalidToken) => {
            BotWsDispatchError::InvalidSessionToken
        }
        BotUseCaseError::Connect(ConnectError::InternalError(msg)) => {
            BotWsDispatchError::WsProtocolError(msg)
        }
        BotUseCaseError::InvalidBotId(message) => BotWsDispatchError::InvalidFrameFormat(message),
        BotUseCaseError::Service(service_error) => BotWsDispatchError::ServiceError(service_error),
        other => BotWsDispatchError::WsProtocolError(other.to_string()),
    }
}

/// Handle a ResponseFrame from a bot.
///
/// ResponseFrames are responses to requests initiated by BCS (e.g., chat.send, chat.abort).
/// The id in the response corresponds to the run_id for the request.
async fn handle_response_frame(
    state: &Arc<BotDispatchState>,
    res: &ResponseFrame,
    registered_bot_id: &Option<String>,
) -> Result<()> {
    let run_id = &res.id;

    // Check if this is a one-shot pending request (e.g., chat.history)
    let payload = if res.ok {
        res.payload.clone().unwrap_or(Value::Null)
    } else {
        serde_json::json!({
            "error": res.error.as_ref().map(|e| format!("{}: {}", e.code, e.message))
        })
    };
    state
        .bot_connections
        .resolve_pending_request(run_id, payload)
        .await;

    // Check if this is a response to a known run
    let is_known_run = state.run_channels.is_registered(run_id).await;
    log_bot_response_frame(state, run_id, res, registered_bot_id).await;

    if res.ok {
        // Success response - bot acknowledged the request
        if is_known_run {
            info!(
                run_id = %run_id,
                "Bot acknowledged request, expecting streaming events"
            );
        } else {
            // This could be a response to chat.abort or another request that was already cleaned up
            debug!(
                run_id = %run_id,
                ok = res.ok,
                "Received success ResponseFrame for request"
            );
        }

        // If this is a task dispatch ACK, delegate alias registration to master_slave strategy
        handle_master_slave_response(state, run_id, res, registered_bot_id).await;
        handle_state_machine_response(state, run_id, res).await;
    } else {
        // Error response - request was rejected
        let error = res
            .error
            .as_ref()
            .map(|e| format!("{}: {}", e.code, e.message))
            .unwrap_or_else(|| "Unknown error".to_string());

        if is_known_run {
            // Forward error to client and clean up
            warn!(
                run_id = %run_id,
                error = %error,
                "Bot rejected request, cleaning up run channel"
            );

            // Build error event frame to send to client
            let error_event = EventFrame::new(
                "chat",
                Some(serde_json::json!({
                    "run_id": run_id,
                    "bcs_group_id": "",
                    "state": "error",
                    "error": {
                        "code": res.error.as_ref().map(|e| e.code.clone()).unwrap_or_default(),
                        "message": res.error.as_ref().map(|e| e.message.clone()).unwrap_or_default(),
                    }
                })),
                Some(0),
            );

            // Send error to client
            let event_json = serde_json::to_string(&BcsFrame::Event(error_event))?;
            state.run_channels.send_event(run_id, event_json).await;

            // Clean up the run channel
            state.run_channels.unregister(run_id).await;
        } else {
            warn!(
                run_id = %run_id,
                error = %error,
                "Received error ResponseFrame for unknown run"
            );
        }
    }

    Ok(())
}

async fn log_bot_response_frame(
    state: &Arc<BotDispatchState>,
    run_id: &str,
    res: &ResponseFrame,
    registered_bot_id: &Option<String>,
) {
    let run_context = state.bot_run_context.get_context(run_id).await;
    let correlation = match state.collaboration_runtime.lookup_delivery_correlation(run_id).await {
        Ok(correlation) => correlation,
        Err(error) => {
            debug!(
                run_id = %run_id,
                error = %error,
                "message_log: failed to lookup state-machine delivery correlation"
            );
            None
        }
    };
    if run_context.is_none() && correlation.is_none() {
        return;
    }

    let state_machine_run = match correlation.as_ref() {
        Some(correlation) => match state
            .collaboration_runtime
            .get_state_machine_run(&correlation.state_machine_run_id)
            .await
        {
            Ok(run) => run,
            Err(error) => {
                debug!(
                    run_id = %run_id,
                    state_machine_run_id = %correlation.state_machine_run_id,
                    error = %error,
                    "message_log: failed to load state-machine run for response log"
                );
                None
            }
        },
        None => None,
    };

    let group_id = state_machine_run
        .as_ref()
        .map(|view| view.run.group_id.clone())
        .or_else(|| run_context.as_ref().map(|ctx| ctx.group_id.clone()))
        .unwrap_or_default();
    let session_id = state_machine_run
        .as_ref()
        .map(|view| view.run.session_id.clone())
        .or_else(|| run_context.as_ref().and_then(|ctx| ctx.bcs_session_id.clone()))
        .or_else(|| run_context.as_ref().map(|ctx| ctx.group_id.clone()))
        .unwrap_or_else(|| group_id.clone());
    let bot_id = correlation
        .as_ref()
        .map(|corr| corr.assignee_bot_id.clone())
        .or_else(|| run_context.as_ref().map(|ctx| ctx.bot_id.clone()))
        .or_else(|| registered_bot_id.clone())
        .unwrap_or_default();
    let accepted_run_id = response_payload_run_id(res).unwrap_or("");
    let mode = if correlation.is_some() {
        MessageLogMode::StateMachine
    } else {
        MessageLogMode::FreeChat
    };
    let event_type = if res.ok {
        MessageLogEventType::BotAccept
    } else {
        MessageLogEventType::BotReject
    };
    let status = if res.ok {
        MessageLogStatus::Accepted
    } else {
        MessageLogStatus::Rejected
    };
    let error_code = res.error.as_ref().map(|error| error.code.as_str()).unwrap_or("");
    let error_message = res.error.as_ref().map(|error| error.message.as_str()).unwrap_or("");
    let state_machine_run_id = correlation
        .as_ref()
        .map(|corr| corr.state_machine_run_id.as_str())
        .unwrap_or("");
    let node_id = correlation
        .as_ref()
        .map(|corr| corr.node_id.as_str())
        .unwrap_or("");
    let attempt = correlation.as_ref().map(|corr| corr.attempt).unwrap_or(0);
    let delivery_request_id = correlation
        .as_ref()
        .map(|corr| corr.delivery_request_id.as_str())
        .unwrap_or("");

    if res.ok {
        info!(
            target: MSG_LOG_TARGET,
            schema_version = MESSAGE_LOG_SCHEMA_VERSION,
            event_type = event_type.as_str(),
            status = status.as_str(),
            mode = mode.as_str(),
            session_id = %session_id,
            group_id = %group_id,
            run_id = %run_id,
            bot_id = %bot_id,
            accepted_run_id = %accepted_run_id,
            state_machine_run_id = %state_machine_run_id,
            node_id = %node_id,
            attempt = attempt,
            delivery_request_id = %delivery_request_id,
            error_code = %error_code,
            error = %error_message,
            "bot_accept"
        );
    } else {
        warn!(
            target: MSG_LOG_TARGET,
            schema_version = MESSAGE_LOG_SCHEMA_VERSION,
            event_type = event_type.as_str(),
            status = status.as_str(),
            mode = mode.as_str(),
            session_id = %session_id,
            group_id = %group_id,
            run_id = %run_id,
            bot_id = %bot_id,
            accepted_run_id = %accepted_run_id,
            state_machine_run_id = %state_machine_run_id,
            node_id = %node_id,
            attempt = attempt,
            delivery_request_id = %delivery_request_id,
            error_code = %error_code,
            error = %error_message,
            "bot_reject"
        );
    }
}

fn response_payload_run_id(res: &ResponseFrame) -> Option<&str> {
    res.payload
        .as_ref()
        .and_then(|payload| payload.get("run_id"))
        .and_then(|value| value.as_str())
        .filter(|payload_run_id| *payload_run_id != res.id)
}

/// Handle an EventFrame from a bot.
///
/// EventFrames are streaming events from bots during chat sessions.
/// Dispatches to the appropriate handler based on the group's service_mode.
async fn handle_event_frame(
    state: &Arc<BotDispatchState>,
    event: &EventFrame,
    registered_bot_id: &Option<String>,
) -> Result<()> {
    let bot_id = match registered_bot_id {
        Some(id) => id.clone(),
        None => {
            log_bot_event("unknown", event);
            warn!(event = %event.event, "Received EventFrame from unregistered bot");
            return Ok(());
        }
    };

    log_bot_event(&bot_id, event);

    // Extract run_id and determine event type
    let (run_id, bcs_group_id, is_final) = match event.event.as_str() {
        "agent" => match parse_agent_event(&event.payload) {
            Some((run_id, group_id, stream)) => {
                let is_final = matches!(stream, AgentStream::Error);
                (run_id, group_id, is_final)
            }
            None => {
                warn!(bot_id = %bot_id, "Failed to parse agent event payload");
                return Ok(());
            }
        },
        "chat.event" => match parse_chat_event(&event.payload) {
            Some((run_id, group_id, state)) => {
                let is_final = matches!(
                    state,
                    ChatEventState::Final | ChatEventState::Aborted | ChatEventState::Error
                );
                (run_id, group_id, is_final)
            }
            None => {
                warn!(bot_id = %bot_id, "Failed to parse chat event payload");
                return Ok(());
            }
        },
        _ => {
            warn!(bot_id = %bot_id, event = %event.event, "Unknown event type");
            return Ok(());
        }
    };

    let chat_event_state = match event.event.as_str() {
        "chat.event" => match parse_chat_event(&event.payload) {
            Some((_, _, state)) => Some(state),
            None => None,
        },
        _ => None,
    };

    let event_payload = event.payload.clone().unwrap_or(Value::Null);
    let event_state = chat_event_state.unwrap_or(ChatEventState::Delta);
    let explicit_session_id = event_payload
        .get("bcs_session_id")
        .and_then(|v| v.as_str());
    let (real_group_id, mut bcs_session_id) =
        unwrap_outbound_group_id(&bcs_group_id, explicit_session_id);
    if bcs_session_id.is_none() {
        if let Some(mapped_session_id) = state.run_channels.session_for_run(&run_id).await {
            let restored_session_id = if mapped_session_id.contains(':') {
                Some(mapped_session_id)
            } else {
                legacy_default_session_id(&mapped_session_id)
            };
            if let Some(restored_session_id) = restored_session_id {
                info!(
                    bot_id = %bot_id,
                    run_id = %run_id,
                    bcs_group_id = %bcs_group_id,
                    bcs_session_id = %restored_session_id,
                    restore_source = "run_mapping",
                    "Bot event session restored"
                );
                bcs_session_id = Some(restored_session_id);
            }
        }
    }
    if bcs_session_id.is_none() {
        if let Some(fallback_session_id) = legacy_default_session_id(&real_group_id) {
            info!(
                bot_id = %bot_id,
                run_id = %run_id,
                bcs_group_id = %bcs_group_id,
                bcs_session_id = %fallback_session_id,
                restore_source = "legacy_default",
                "Bot event session restored"
            );
            bcs_session_id = Some(fallback_session_id);
        }
    }

    if state
        .collaboration_runtime
        .lookup_delivery_correlation(&run_id)
        .await
        .map_err(|error| BotWsDispatchError::ServiceError(ServiceError::InternalError(error.to_string())))?
        .is_some()
    {
        let terminal = matches!(
            event_state,
            ChatEventState::Final | ChatEventState::Aborted | ChatEventState::Error
        ) || matches!(event.event.as_str(), "agent") && is_final;
        state
            .collaboration_runtime
            .handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
                bot_id,
                run_id: run_id.clone(),
                event_type: event.event.clone(),
                event_payload: event_payload.clone(),
                state: to_app_chat_event_state(&event_state),
                bcs_session_id: bcs_session_id.clone(),
            })
            .await
            .map_err(|error| {
                BotWsDispatchError::ServiceError(ServiceError::InternalError(error.to_string()))
            })?;
        if terminal {
            state.run_channels.unregister(&run_id).await;
        }
        return Ok(());
    }

    handle_default_group_event(
        state,
        &bot_id,
        &run_id,
        &real_group_id,
        bcs_session_id.as_deref(),
        event,
        &event_payload,
        &event_state,
        is_final,
    )
    .await?;

    Ok(())
}

// ---------------------------------------------------------------------------
// Event routing strategies by service_mode
// ---------------------------------------------------------------------------

/// Default group event routing: broadcast via OutboundDispatcher.
async fn handle_default_group_event(
    state: &Arc<BotDispatchState>,
    bot_id: &str,
    run_id: &str,
    bcs_group_id: &str,
    bcs_session_id: Option<&str>,
    event: &EventFrame,
    event_payload: &Value,
    event_state: &ChatEventState,
    is_final: bool,
) -> Result<()> {
    info!(
        bot_id = %bot_id,
        run_id = %run_id,
        bcs_group_id = %bcs_group_id,
        bcs_session_id = ?bcs_session_id,
        event_type = %event.event,
        event_state = ?event_state,
        is_final = is_final,
        ">>> dispatch_bot_event entry: bot reply received"
    );

    state
        .message_flow
        .handle_bot_event(BotEventCommand {
            bot_id: bot_id.to_string(),
            run_id: run_id.to_string(),
            group_id: bcs_group_id.to_string(),
            event_type: event.event.clone(),
            event_payload: event_payload.clone(),
            state: to_app_chat_event_state(event_state),
            bcs_session_id: bcs_session_id.map(str::to_string),
        })
        .await?;

    if is_final {
        state.run_channels.unregister(run_id).await;
        info!(
            bot_id = %bot_id,
            run_id = %run_id,
            bcs_group_id = %bcs_group_id,
            event = %event.event,
            "Run completed, channel unregistered"
        );
    }

    Ok(())
}

fn to_app_chat_event_state(state: &ChatEventState) -> AppChatEventState {
    match state {
        ChatEventState::Delta => AppChatEventState::Delta,
        ChatEventState::Final => AppChatEventState::Final,
        ChatEventState::Aborted => AppChatEventState::Aborted,
        ChatEventState::Error => AppChatEventState::Error,
        ChatEventState::ToolCallStart => AppChatEventState::ToolCallStart,
        ChatEventState::ToolCallEnd => AppChatEventState::ToolCallEnd,
    }
}

/// master_slave mode: register sub bot's run_id as alias for the task_id.
async fn handle_master_slave_response(
    state: &Arc<BotDispatchState>,
    run_id: &str,
    res: &ResponseFrame,
    registered_bot_id: &Option<String>,
) {
    if let Some(sub_run_id) = res
        .payload
        .as_ref()
        .and_then(|p| p.get("run_id"))
        .and_then(|v| v.as_str())
    {
        if sub_run_id != run_id {
            let task_alias_registration = match registered_bot_id.as_deref() {
                Some(bot_id) => match state
                    .message_flow
                    .register_task_run_alias(run_id, sub_run_id, bot_id)
                    .await
                {
                    Ok(registration) => registration,
                    Err(error) => {
                        warn!(
                            task_id = %run_id,
                            sub_bot_run_id = %sub_run_id,
                            bot_id = %bot_id,
                            error = %error,
                            "master_slave: failed to register sub bot run_id alias"
                        );
                        TaskRunAliasRegistration::Rejected
                    }
                },
                None => TaskRunAliasRegistration::Rejected,
            };
            let run_channel_alias_registered = match task_alias_registration {
                TaskRunAliasRegistration::Registered | TaskRunAliasRegistration::NotTask => {
                    state
                        .run_channels
                        .register_alias(sub_run_id.to_string(), run_id.to_string())
                        .await
                }
                TaskRunAliasRegistration::Rejected => false,
            };
            info!(
                task_id = %run_id,
                sub_bot_run_id = %sub_run_id,
                task_alias_registration = ?task_alias_registration,
                run_channel_alias_registered = run_channel_alias_registered,
                "master_slave: registered sub bot run_id alias"
            );
            log_master_slave_bot_accept(
                state,
                run_id,
                sub_run_id,
                registered_bot_id.as_deref().unwrap_or(""),
                &task_alias_registration,
                run_channel_alias_registered,
            )
            .await;
        }
    }
}

async fn log_master_slave_bot_accept(
    state: &Arc<BotDispatchState>,
    task_id: &str,
    accepted_run_id: &str,
    bot_id: &str,
    task_alias_registration: &TaskRunAliasRegistration,
    run_channel_alias_registered: bool,
) {
    let session_id = state
        .run_channels
        .session_for_run(task_id)
        .await
        .unwrap_or_default();
    let group_id = session_id
        .split_once(':')
        .map(|(group_id, _)| group_id.to_string())
        .unwrap_or_else(|| session_id.clone());
    info!(
        target: MSG_LOG_TARGET,
        schema_version = MESSAGE_LOG_SCHEMA_VERSION,
        event_type = MessageLogEventType::BotAccept.as_str(),
        status = MessageLogStatus::Accepted.as_str(),
        mode = MessageLogMode::ManagerWorker.as_str(),
        session_id = %session_id,
        group_id = %group_id,
        task_id = %task_id,
        run_id = %task_id,
        bot_id = %bot_id,
        accepted_run_id = %accepted_run_id,
        task_alias_registration = ?task_alias_registration,
        run_channel_alias_registered = run_channel_alias_registered,
        "bot_accept"
    );
}

async fn handle_state_machine_response(
    state: &Arc<BotDispatchState>,
    run_id: &str,
    res: &ResponseFrame,
) {
    let correlation = match state.collaboration_runtime.lookup_delivery_correlation(run_id).await {
        Ok(Some(correlation)) => correlation,
        Ok(None) => return,
        Err(error) => {
            warn!(
                run_id = %run_id,
                error = %error,
                "state_machine: failed to lookup delivery correlation for response"
            );
            return;
        }
    };
    let Some(bot_delivery_run_id) = res
        .payload
        .as_ref()
        .and_then(|p| p.get("run_id"))
        .and_then(|v| v.as_str())
        .filter(|bot_run_id| *bot_run_id != run_id)
        .map(str::to_string)
    else {
        return;
    };
    if let Err(error) = state
        .collaboration_runtime
        .register_delivery_alias(&correlation.delivery_request_id, bot_delivery_run_id.clone())
        .await
    {
        warn!(
            delivery_request_id = %correlation.delivery_request_id,
            bot_delivery_run_id = %bot_delivery_run_id,
            error = %error,
            "state_machine: failed to register bot delivery run alias"
        );
    } else {
        info!(
            delivery_request_id = %correlation.delivery_request_id,
            bot_delivery_run_id = %bot_delivery_run_id,
            "state_machine: registered bot delivery run alias"
        );
    }
}

/// Log bot event at INFO level with key payload information.
fn log_bot_event(bot_id: &str, event: &EventFrame) {
    match event.event.as_str() {
        "agent" => {
            // Try to parse agent event and extract key info
            if let Some((run_id, bcs_group_id, stream)) = parse_agent_event(&event.payload) {
                // Extract text content from data if available
                let text = event
                    .payload
                    .as_ref()
                    .and_then(|p| p.get("data"))
                    .and_then(|d| d.get("text"))
                    .or_else(|| {
                        event
                            .payload
                            .as_ref()
                            .and_then(|p| p.get("data"))
                            .and_then(|d| d.get("delta"))
                    })
                    .and_then(|t| t.as_str())
                    .unwrap_or("");

                // Truncate text for readability (use chars() to handle UTF-8 properly)
                let truncated_text = if text.chars().count() > 100 {
                    format!("{}...", text.chars().take(100).collect::<String>())
                } else {
                    text.to_string()
                };

                info!(
                    bot_id = %bot_id,
                    run_id = %run_id,
                    bcs_group_id = %bcs_group_id,
                    stream = ?stream,
                    text = %truncated_text,
                    "📥 [BCS] 收到 Bot 事件"
                );
            } else {
                info!(
                    bot_id = %bot_id,
                    event = %event.event,
                    payload = ?event.payload,
                    "📥 [BCS] 收到 Bot 事件 (解析失败)"
                );
            }
        }
        "chat.event" => {
            // Try to parse chat event and extract key info
            if let Some((run_id, bcs_group_id, state)) = parse_chat_event(&event.payload) {
                info!(
                    bot_id = %bot_id,
                    run_id = %run_id,
                    bcs_group_id = %bcs_group_id,
                    state = ?state,
                    "📥 [BCS] 收到 Bot 事件"
                );
            } else {
                info!(
                    bot_id = %bot_id,
                    event = %event.event,
                    payload = ?event.payload,
                    "📥 [BCS] 收到 Bot 事件 (解析失败)"
                );
            }
        }
        _ => {
            info!(
                bot_id = %bot_id,
                event = %event.event,
                payload = ?event.payload,
                "📥 [BCS] 收到 Bot 事件 (未知类型)"
            );
        }
    }
}

/// Parse an agent event payload to extract run_id, bcs_group_id, and stream type.
fn parse_agent_event(payload: &Option<Value>) -> Option<(String, String, AgentStream)> {
    let payload = payload.as_ref()?;
    let agent_event: AgentEventPayload = serde_json::from_value(payload.clone()).ok()?;
    Some((
        agent_event.run_id,
        agent_event.bcs_group_id,
        agent_event.stream,
    ))
}

/// Parse a chat event payload to extract run_id, bcs_group_id, and state.
fn parse_chat_event(payload: &Option<Value>) -> Option<(String, String, ChatEventState)> {
    let payload = payload.as_ref()?;
    let chat_event: ChatEventPayload = serde_json::from_value(payload.clone()).ok()?;
    Some((chat_event.run_id, chat_event.bcs_group_id, chat_event.state))
}

/// Send a success response frame.
async fn send_ok(tx: &mpsc::Sender<String>, req_id: &str, payload: Value) -> Result<()> {
    let response = ResponseFrame::ok(req_id, payload);
    let frame = BcsFrame::Response(response);
    let json = serde_json::to_string(&frame)?;
    tx.send(json).await.map_err(|e| {
        BotWsDispatchError::WsProtocolError(format!("Failed to send response: {}", e))
    })?;
    Ok(())
}

/// Send an error response frame.
async fn send_error(
    tx: &mpsc::Sender<String>,
    req_id: &str,
    code: &str,
    message: &str,
) -> Result<()> {
    let response = ResponseFrame::err(
        req_id,
        ErrorShape {
            code: code.to_string(),
            message: message.to_string(),
            details: None,
            retryable: false,
            retry_after_ms: None,
        },
    );
    let frame = BcsFrame::Response(response);
    let json = serde_json::to_string(&frame)?;
    tx.send(json).await.map_err(|e| {
        BotWsDispatchError::WsProtocolError(format!("Failed to send error response: {}", e))
    })?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Task dispatch types
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Deserialize)]
struct TaskDispatchParams {
    group_id: String,
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
    message: String,
}

#[derive(Debug, Clone, Deserialize)]
struct TaskCompleteParams {
    group_id: String,
    summary: String,
    #[serde(default = "default_complete_status")]
    status: String,
}

fn default_complete_status() -> String {
    "completed".to_string()
}

#[derive(Debug, Clone, Deserialize)]
struct RouteResolveParams {
    group_id: String,
    #[serde(default)]
    session_id: Option<String>,
    selectors: Vec<RouteSelectorWire>,
}

#[derive(Debug, Clone, Serialize)]
struct RouteResolveTarget {
    #[serde(rename = "type")]
    selector_type: String,
    value: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    bot_name: Option<String>,
    role: String,
}

#[derive(Debug, Clone, Serialize)]
struct RouteResolveCandidate {
    bot_uuid: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    bot_name: Option<String>,
    role: String,
}

#[derive(Debug, Clone, Serialize)]
struct RouteResolveResult {
    ok: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    error: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    message: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    resolved: Vec<RouteResolveTarget>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    candidates: Vec<RouteResolveCandidate>,
}

fn resolve_route_selectors(
    participants: &[Participant],
    selectors: &[RouteSelectorWire],
) -> RouteResolveResult {
    if selectors.is_empty() {
        return RouteResolveResult {
            ok: false,
            error: Some("INVALID_ROUTE_SELECTOR".to_string()),
            message: Some("'selectors' must be a non-empty array".to_string()),
            resolved: Vec::new(),
            candidates: route_candidates(participants, None),
        };
    }

    let mut resolved = Vec::new();
    for selector in selectors {
        let value = selector.value.as_deref().unwrap_or_default().trim();
        match selector.selector_type.as_str() {
            "bot" => {
                if value.is_empty() {
                    return invalid_route_selector(participants, "'bot' selector requires a non-empty value");
                }
                match participants
                    .iter()
                    .find(|participant| participant.is_bot() && participant.bot_uuid == value)
                {
                    Some(participant) => push_resolved_target(&mut resolved, participant),
                    None => {
                        return unknown_route_target(
                            participants,
                            value,
                            format!("No participant matched bot \"{value}\""),
                        );
                    }
                }
            }
            "name" => {
                if value.is_empty() {
                    return invalid_route_selector(participants, "'name' selector requires a non-empty value");
                }
                let matches: Vec<_> = participants
                    .iter()
                    .filter(|participant| {
                        participant.is_bot()
                            && participant
                                .bot_name
                                .as_deref()
                                .map_or(false, |name| name == value)
                    })
                    .collect();
                match matches.as_slice() {
                    [participant] => push_resolved_target(&mut resolved, participant),
                    [] => {
                        return unknown_route_target(
                            participants,
                            value,
                            format!("No participant matched name \"{value}\""),
                        );
                    }
                    _ => {
                        return RouteResolveResult {
                            ok: false,
                            error: Some("AMBIGUOUS_ROUTE_TARGET".to_string()),
                            message: Some(format!("Multiple participants matched name \"{value}\"")),
                            resolved: Vec::new(),
                            candidates: matches.into_iter().map(route_candidate).collect(),
                        };
                    }
                }
            }
            other => {
                return invalid_route_selector(
                    participants,
                    &format!("unsupported route selector type \"{other}\""),
                );
            }
        }
    }

    RouteResolveResult {
        ok: true,
        error: None,
        message: None,
        resolved,
        candidates: Vec::new(),
    }
}

fn push_resolved_target(resolved: &mut Vec<RouteResolveTarget>, participant: &Participant) {
    if resolved
        .iter()
        .any(|target| target.value == participant.bot_uuid)
    {
        return;
    }
    resolved.push(RouteResolveTarget {
        selector_type: "bot".to_string(),
        value: participant.bot_uuid.clone(),
        bot_name: participant.bot_name.clone(),
        role: participant_role_slug(participant.role).to_string(),
    });
}

fn invalid_route_selector(participants: &[Participant], message: impl Into<String>) -> RouteResolveResult {
    RouteResolveResult {
        ok: false,
        error: Some("INVALID_ROUTE_SELECTOR".to_string()),
        message: Some(message.into()),
        resolved: Vec::new(),
        candidates: route_candidates(participants, None),
    }
}

fn unknown_route_target(participants: &[Participant], value: &str, message: String) -> RouteResolveResult {
    RouteResolveResult {
        ok: false,
        error: Some("UNKNOWN_ROUTE_TARGET".to_string()),
        message: Some(message),
        resolved: Vec::new(),
        candidates: route_candidates(participants, Some(value)),
    }
}

fn route_candidates(participants: &[Participant], query: Option<&str>) -> Vec<RouteResolveCandidate> {
    let query = query.map(|value| value.to_lowercase());
    let mut candidates: Vec<_> = participants
        .iter()
        .filter(|participant| participant.is_bot())
        .filter(|participant| match query.as_deref() {
            Some(query) if !query.is_empty() => {
                participant.bot_uuid.to_lowercase().contains(query)
                    || participant
                        .bot_name
                        .as_deref()
                        .map(|name| name.to_lowercase().contains(query) || query.contains(&name.to_lowercase()))
                        .unwrap_or(false)
            }
            _ => true,
        })
        .map(route_candidate)
        .collect();
    if candidates.is_empty() && query.is_some() {
        candidates = participants
            .iter()
            .filter(|participant| participant.is_bot())
            .map(route_candidate)
            .collect();
    }
    candidates
}

fn route_candidate(participant: &Participant) -> RouteResolveCandidate {
    RouteResolveCandidate {
        bot_uuid: participant.bot_uuid.clone(),
        bot_name: participant.bot_name.clone(),
        role: participant_role_slug(participant.role).to_string(),
    }
}

fn participant_role_slug(role: ParticipantRole) -> &'static str {
    match role {
        ParticipantRole::Driver => "driver",
        ParticipantRole::Consultant => "consultant",
        ParticipantRole::Manager => "manager",
        ParticipantRole::Worker => "worker",
        ParticipantRole::Observer => "observer",
    }
}

async fn send_service_error(
    tx: &mpsc::Sender<String>,
    req_id: &str,
    err: ServiceError,
) -> Result<()> {
    let (code, message) = match err {
        ServiceError::GroupNotFound(group_id) => {
            ("group_not_found", format!("Group not found: {group_id}"))
        }
        ServiceError::BotNotFound(bot_id) => (
            "target_not_found",
            format!("Target bot not found in group: {bot_id}"),
        ),
        ServiceError::Unauthorized(message) => {
            let code = if message.contains("driver bot") || message.contains("driver") {
                "not_driver"
            } else {
                "unauthorized"
            };
            (code, message)
        }
        ServiceError::InvalidOperation { message, .. }
            if message.contains("service_mode=master_slave") =>
        {
            ("unsupported_service_mode", message)
        }
        ServiceError::InvalidOperation { message, .. }
            if message.contains("invalid task complete status") =>
        {
            ("invalid_status", message)
        }
        ServiceError::InvalidOperation { message, .. }
            if message.contains("target bot is not connected") =>
        {
            ("bot_not_connected", message)
        }
        ServiceError::InvalidOperation { message, .. }
            if message.contains("target bot is muted") =>
        {
            ("target_bot_muted", message)
        }
        other => ("internal_error", other.to_string()),
    };
    send_error(tx, req_id, code, &message).await
}

async fn handle_route_resolve(
    state: &Arc<BotDispatchState>,
    req_id: &str,
    params: Value,
    tx: &mpsc::Sender<String>,
    registered_bot_id: &Option<String>,
) -> Result<()> {
    if registered_bot_id.is_none() {
        send_error(
            tx,
            req_id,
            "not_registered",
            "Bot must register before resolving route targets",
        )
        .await?;
        return Ok(());
    }

    let params: RouteResolveParams = serde_json::from_value(params).map_err(|e| {
        BotWsDispatchError::InvalidFrameFormat(format!("Invalid route.resolve params: {}", e))
    })?;
    let (real_group_id, wire_session_id) =
        unwrap_outbound_group_id(&params.group_id, params.session_id.as_deref());
    let mut participants = match state.group_dispatch.participants(&real_group_id).await {
        Some(participants) => participants,
        None => {
            send_error(
                tx,
                req_id,
                "group_not_found",
                &format!("Group not found: {real_group_id}"),
            )
            .await?;
            return Ok(());
        }
    };

    if let Some(session_id) = wire_session_id {
        match state.session_management.get(&session_id).await {
            Ok(Some(session)) => {
                if session.group_id != real_group_id {
                    send_error(
                        tx,
                        req_id,
                        "invalid_session",
                        &format!(
                            "Session {session_id} does not belong to group {real_group_id}"
                        ),
                    )
                    .await?;
                    return Ok(());
                }
                if !session.participants.is_empty() {
                    participants = session.participants;
                }
            }
            Ok(None) => {
                send_error(
                    tx,
                    req_id,
                    "session_not_found",
                    &format!("Session not found: {session_id}"),
                )
                .await?;
                return Ok(());
            }
            Err(error) => {
                let (code, message) = map_session_error(error);
                send_error(tx, req_id, &code, &message).await?;
                return Ok(());
            }
        }
    }

    send_ok(
        tx,
        req_id,
        serde_json::to_value(resolve_route_selectors(&participants, &params.selectors))?,
    )
    .await
}

// ---------------------------------------------------------------------------
// task.dispatch handler
// ---------------------------------------------------------------------------

async fn handle_task_dispatch(
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
    let (real_group_id, manager_session_id) = unwrap_outbound_group_id(&params.group_id, None);
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

async fn handle_task_message(
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
    let (real_group_id, manager_session_id) = unwrap_outbound_group_id(&params.group_id, None);
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

async fn handle_task_complete(
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
    let (real_group_id, wire_session_id) = unwrap_outbound_group_id(&params.group_id, None);
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
            if let Some(session) = outcome.completed_session {
                state
                    .callback_dispatch
                    .maybe_dispatch(session, state.session_management.clone())
                    .await;
            } else if outcome.callback_requested {
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

#[cfg(test)]
mod tests {
    use bcs_protocol::RouteSelectorWire;
    use bcs_service_api::{Group, Participant, ParticipantRole};

    use super::*;

    fn participant(id: &str, name: &str, role: ParticipantRole) -> Participant {
        let mut participant = Participant::bot(id, role);
        participant.bot_name = Some(name.to_string());
        participant
    }

    fn poker_group() -> Group {
        Group::new(
            "group-1",
            "20260512_1fl0038o:437240",
            vec![
                participant(
                    "20260512_1fl0038o:437240",
                    "poker_发牌师傅",
                    ParticipantRole::Driver,
                ),
                participant(
                    "20260604_m869qsxf:146836",
                    "qz的德州钻石玩家bot_v1",
                    ParticipantRole::Consultant,
                ),
            ],
        )
    }

    #[test]
    fn unwrap_outbound_group_id_accepts_legacy_plain_group_session_param() {
        let (group_id, session_id) =
            unwrap_outbound_group_id("group-1:abcdef12", Some("group-1"));

        assert_eq!(group_id, "group-1");
        assert_eq!(session_id.as_deref(), Some("group-1:abcdef12"));
    }

    #[test]
    fn route_resolve_unknown_name_returns_candidates() {
        let group = poker_group();
        let result = resolve_route_selectors(
            &group.participants,
            &[RouteSelectorWire {
                selector_type: "name".to_string(),
                value: Some("钻石玩家".to_string()),
            }],
        );

        assert!(!result.ok);
        assert_eq!(result.error.as_deref(), Some("UNKNOWN_ROUTE_TARGET"));
        assert_eq!(result.candidates.len(), 1);
        assert_eq!(result.candidates[0].bot_uuid, "20260604_m869qsxf:146836");
        assert_eq!(
            result.candidates[0].bot_name.as_deref(),
            Some("qz的德州钻石玩家bot_v1")
        );
    }

    #[test]
    fn route_resolve_exact_name_returns_canonical_bot_selector() {
        let group = poker_group();
        let result = resolve_route_selectors(
            &group.participants,
            &[RouteSelectorWire {
                selector_type: "name".to_string(),
                value: Some("qz的德州钻石玩家bot_v1".to_string()),
            }],
        );

        assert!(result.ok);
        assert_eq!(result.resolved.len(), 1);
        assert_eq!(result.resolved[0].selector_type, "bot");
        assert_eq!(result.resolved[0].value, "20260604_m869qsxf:146836");
        assert_eq!(
            result.resolved[0].bot_name.as_deref(),
            Some("qz的德州钻石玩家bot_v1")
        );
    }
}
