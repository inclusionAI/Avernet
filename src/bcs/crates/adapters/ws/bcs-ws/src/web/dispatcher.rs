use std::sync::Arc;

use bcs_protocol::{BcsFrame, ErrorShape, RequestFrame, ResponseFrame};
use bcs_service_api::{
    CallerContext, ChatAbortCommand, HumanActor, MessageFlowService, ServiceError, WebSendCommand,
    WorkbenchChatAuthorizationCommand, WorkbenchConnectCommand, WorkbenchSessionService,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::sync::mpsc;
use tracing::{debug, info, warn};

use crate::shared::RunChannelManager;
use crate::web::WorkbenchConnectionRegistry;

pub type Result<T> = std::result::Result<T, WebWsDispatchError>;

#[derive(Debug, thiserror::Error)]
pub enum WebWsDispatchError {
    #[error("invalid frame format: {0}")]
    InvalidFrameFormat(String),
    #[error("websocket protocol error: {0}")]
    WsProtocolError(String),
    #[error("client connect failed: {0}")]
    ClientConnectError(Box<WebWsDispatchError>),
    #[error(transparent)]
    JsonError(#[from] serde_json::Error),
    #[error(transparent)]
    ServiceError(#[from] ServiceError),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WebDispatchOutcome {
    Dispatched,
    ClientConnect { subscribed: bool },
}

pub struct WebDispatchState {
    pub message_flow: Arc<dyn MessageFlowService>,
    pub workbench_sessions: Arc<dyn WorkbenchSessionService>,
    pub frontend_connections: Arc<WorkbenchConnectionRegistry>,
    pub run_channels: Arc<RunChannelManager>,
}

impl std::fmt::Debug for WebDispatchState {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("WebDispatchState")
            .field("message_flow", &"<MessageFlowService>")
            .field("workbench_sessions", &"<WorkbenchSessionService>")
            .field("frontend_connections", &"<WorkbenchConnectionRegistry>")
            .field("run_channels", &"<RunChannelManager>")
            .finish()
    }
}

#[derive(Debug, Default)]
pub struct WebClientConnectionState {
    pub active_run_ids: Vec<String>,
    pub subscribed_sessions: Vec<(String, u64)>,
}

pub async fn dispatch_client_frame(
    state: &Arc<WebDispatchState>,
    text: &str,
    tx: &mpsc::Sender<String>,
    connection_state: &mut WebClientConnectionState,
    bound_actor_id: Option<&str>,
) -> Result<WebDispatchOutcome> {
    let frame: BcsFrame = serde_json::from_str(text)
        .map_err(|e| WebWsDispatchError::InvalidFrameFormat(e.to_string()))?;

    match frame {
        BcsFrame::Request(req) => {
            let is_connect = req.method == "connect";
            let subscribed_before = connection_state.subscribed_sessions.len();
            if let Err(error) =
                handle_client_request(state, &req, tx, connection_state, bound_actor_id).await
            {
                if is_connect {
                    return Err(WebWsDispatchError::ClientConnectError(Box::new(error)));
                }
                return Err(error);
            }
            if is_connect {
                return Ok(WebDispatchOutcome::ClientConnect {
                    subscribed: connection_state.subscribed_sessions.len() > subscribed_before,
                });
            }
        }
        BcsFrame::Response(res) => {
            warn!(id = %res.id, ok = res.ok, "Unexpected ResponseFrame from frontend client");
        }
        BcsFrame::Event(event) => {
            warn!(event = %event.event, "Unexpected EventFrame from frontend client");
        }
    }

    Ok(WebDispatchOutcome::Dispatched)
}

async fn handle_client_request(
    state: &Arc<WebDispatchState>,
    req: &RequestFrame,
    tx: &mpsc::Sender<String>,
    connection_state: &mut WebClientConnectionState,
    bound_actor_id: Option<&str>,
) -> Result<()> {
    debug!(id = %req.id, method = %req.method, "Handling client RequestFrame");
    info!(method = %req.method, "Client request received");

    match req.method.as_str() {
        "connect" => {
            handle_connect(state, req, tx, connection_state, bound_actor_id).await?;
        }
        "chat.send" => {
            handle_chat_send(state, req, tx, connection_state, bound_actor_id).await?;
        }
        "chat.abort" => {
            handle_chat_abort(state, req, tx).await?;
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

#[derive(Debug, Deserialize)]
struct ConnectParams {
    group_id: String,
    #[serde(default, alias = "bcs_session_id", alias = "sessionId")]
    session_id: Option<String>,
}

#[derive(Debug, Serialize)]
struct ConnectResponse {
    group_id: String,
    participants: Vec<Value>,
}

async fn handle_connect(
    state: &Arc<WebDispatchState>,
    req: &RequestFrame,
    tx: &mpsc::Sender<String>,
    connection_state: &mut WebClientConnectionState,
    bound_actor_id: Option<&str>,
) -> Result<()> {
    let params: ConnectParams = serde_json::from_value(req.params.clone().unwrap_or(Value::Null))
        .map_err(|e| {
        WebWsDispatchError::InvalidFrameFormat(format!("Invalid connect params: {}", e))
    })?;

    debug!(
        group_id = %params.group_id,
        session_id = ?params.session_id,
        bound_actor_id = ?bound_actor_id,
        "Processing connect request"
    );

    let outcome = match state
        .workbench_sessions
        .connect(WorkbenchConnectCommand {
            bound_actor_id: bound_actor_id.map(str::to_string),
            group_id: params.group_id.clone(),
            session_id: params.session_id.clone(),
        })
        .await
    {
        Ok(outcome) => outcome,
        Err(err) => {
            warn!(
                group_id = %params.group_id,
                session_id = ?params.session_id,
                bound_actor_id = ?bound_actor_id,
                error = ?err,
                "connect rejected by Workbench WS authorization"
            );
            let message = err.message();
            send_error(tx, &req.id, err.code(), &message).await?;
            return Ok(());
        }
    };

    let subscription_key = params
        .session_id
        .clone()
        .unwrap_or_else(|| params.group_id.clone());
    let conn_id = state
        .frontend_connections
        .subscribe(
            subscription_key.clone(),
            tx.clone(),
            bound_actor_id.map(str::to_string),
        )
        .await;
    connection_state
        .subscribed_sessions
        .push((subscription_key, conn_id));

    let participants: Vec<Value> = outcome
        .participants
        .into_iter()
        .map(|participant| serde_json::to_value(participant).unwrap_or(Value::Null))
        .collect();

    let response = ConnectResponse {
        group_id: outcome.group_id,
        participants,
    };

    send_ok(tx, &req.id, serde_json::to_value(response)?).await?;
    Ok(())
}

#[derive(Debug, Deserialize)]
struct ChatSendParams {
    #[serde(alias = "sessionKey")]
    session_key: Option<String>,
    #[serde(default, alias = "bcs_session_id", alias = "sessionId")]
    session_id: Option<String>,
    message: String,
    group_id: String,
    bot_uuid: Option<String>,
    bot_id: Option<String>,
    bot_name: Option<String>,
    #[serde(default)]
    mentions: Vec<String>,
    thinking: Option<String>,
    #[serde(alias = "idempotencyKey")]
    idempotency_key: Option<String>,
    attachments: Option<Vec<bcs_protocol::Attachment>>,
}

#[derive(Debug, Serialize)]
struct ChatSendResponse {
    #[serde(rename = "runId")]
    run_id: String,
    status: String,
}

async fn handle_chat_send(
    state: &Arc<WebDispatchState>,
    req: &RequestFrame,
    tx: &mpsc::Sender<String>,
    connection_state: &mut WebClientConnectionState,
    bound_actor_id: Option<&str>,
) -> Result<()> {
    let params: ChatSendParams = serde_json::from_value(req.params.clone().unwrap_or(Value::Null))
        .map_err(|e| {
            WebWsDispatchError::InvalidFrameFormat(format!("Invalid chat.send params: {}", e))
        })?;

    let from_id = params
        .bot_id
        .clone()
        .or_else(|| params.bot_uuid.clone())
        .unwrap_or_else(|| "unknown".to_string());
    let session_id = resolve_bcs_session_id(&params);

    info!(
        group_id = %params.group_id,
        session_id = ?session_id,
        bot_id = ?params.bot_id,
        bot_uuid = ?params.bot_uuid,
        bound_actor_id = ?bound_actor_id,
        "Processing chat.send for group"
    );

    if let Err(err) = state
        .workbench_sessions
        .authorize_chat_send(WorkbenchChatAuthorizationCommand {
            bound_actor_id: bound_actor_id.map(str::to_string),
            group_id: params.group_id.clone(),
            from_actor_id: from_id.clone(),
            session_id: session_id.clone(),
        })
        .await
    {
        warn!(
            from = %from_id,
            group_id = %params.group_id,
            bound_actor_id = ?bound_actor_id,
            error = ?err,
            "chat.send rejected by Workbench WS authorization"
        );
        let message = err.message();
        send_error(tx, &req.id, err.code(), &message).await?;
        return Ok(());
    }

    let sender_subscription_key = session_id.as_deref().unwrap_or(&params.group_id);
    let sender_conn_id = connection_state
        .subscribed_sessions
        .iter()
        .find(|(key, _)| key.as_str() == sender_subscription_key)
        .or_else(|| {
            connection_state
                .subscribed_sessions
                .iter()
                .find(|(key, _)| key == &params.group_id)
        })
        .map(|(_, id)| *id);

    let caller = caller_context_from_bound_actor(bound_actor_id, &from_id);

    info!("chat.send: calling message_flow.handle_web_send");
    let outcome = state
        .message_flow
        .handle_web_send(WebSendCommand {
            caller,
            group_id: params.group_id.clone(),
            session_id: session_id.clone(),
            from_actor_id: from_id,
            from_name: params.bot_name.clone(),
            message: params.message,
            mentions: params.mentions,
            attachments: params.attachments,
            thinking: params.thinking,
            idempotency_key: params.idempotency_key,
            sender_conn_id,
        })
        .await?;
    info!(
        run_ids = ?outcome.active_run_ids.len(),
        delivered = outcome.bot_deliveries.iter().filter(|d| d.delivered).count(),
        failed = outcome.bot_deliveries.iter().filter(|d| !d.delivered).count(),
        "chat.send: message_flow processing complete"
    );

    connection_state
        .active_run_ids
        .extend(outcome.active_run_ids.iter().cloned());
    let run_session_key = session_id.unwrap_or_else(|| params.group_id.clone());
    for run_id in &outcome.active_run_ids {
        state
            .run_channels
            .register(
                run_id.clone(),
                run_session_key.clone(),
                tx.clone(),
                Some("workbench-ws".to_string()),
                bound_actor_id.map(str::to_string),
            )
            .await;
    }

    let response = ChatSendResponse {
        run_id: outcome.primary_run_id,
        status: outcome.status,
    };

    send_ok(tx, &req.id, serde_json::to_value(response)?).await?;
    Ok(())
}

fn resolve_bcs_session_id(params: &ChatSendParams) -> Option<String> {
    params.session_id.clone().or_else(|| {
        params
            .session_key
            .as_deref()
            .filter(|session_key| {
                session_key
                    .strip_prefix(params.group_id.as_str())
                    .is_some_and(|suffix| suffix.starts_with(':'))
            })
            .map(str::to_string)
    })
}

#[derive(Debug, Serialize)]
struct ChatAbortResult {
    ok: bool,
    aborted: bool,
    run_ids: Vec<String>,
}

#[derive(Debug, Deserialize)]
struct ClientChatAbortParams {
    group_id: String,
    run_id: Option<String>,
}

async fn handle_chat_abort(
    state: &Arc<WebDispatchState>,
    req: &RequestFrame,
    tx: &mpsc::Sender<String>,
) -> Result<()> {
    let params: ClientChatAbortParams =
        serde_json::from_value(req.params.clone().unwrap_or(Value::Null)).map_err(|e| {
            WebWsDispatchError::InvalidFrameFormat(format!("Invalid chat.abort params: {}", e))
        })?;

    let group_id = params.group_id;
    let run_id = params.run_id;

    info!(
        group_id = %group_id,
        run_id = ?run_id,
        "Processing chat.abort"
    );

    let outcome = state
        .message_flow
        .handle_chat_abort(ChatAbortCommand {
            caller: caller_context_from_bound_actor(None, "unknown"),
            group_id: group_id.clone(),
            run_id,
        })
        .await?;

    let result = ChatAbortResult {
        ok: true,
        aborted: outcome.aborted,
        run_ids: outcome.aborted_run_ids,
    };

    info!(
        group_id = %group_id,
        aborted = result.aborted,
        "Chat abort completed"
    );

    send_ok(tx, &req.id, serde_json::to_value(result)?).await?;
    Ok(())
}

fn caller_context_from_bound_actor(
    bound_actor_id: Option<&str>,
    fallback_actor_id: &str,
) -> CallerContext {
    let actor_id = bound_actor_id.unwrap_or(fallback_actor_id).to_string();
    let staff_no = actor_id
        .strip_prefix("human_")
        .unwrap_or(actor_id.as_str())
        .to_string();
    CallerContext::Human(HumanActor { actor_id, staff_no })
}

async fn send_ok(tx: &mpsc::Sender<String>, req_id: &str, payload: Value) -> Result<()> {
    let response = ResponseFrame::ok(req_id, payload);
    let frame = BcsFrame::Response(response);
    let json = serde_json::to_string(&frame)?;
    tx.send(json).await.map_err(|e| {
        WebWsDispatchError::WsProtocolError(format!("Failed to send response: {}", e))
    })?;
    Ok(())
}

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
        WebWsDispatchError::WsProtocolError(format!("Failed to send error response: {}", e))
    })?;
    Ok(())
}
