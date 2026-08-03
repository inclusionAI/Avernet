use std::sync::Arc;

use bcs_protocol::{BcsFrame, ErrorShape, RequestFrame, ResponseFrame};
use bcs_service_api::application::v1::{
    AuthorizeGroupSessionConnection, GroupSessionConnectionBinding,
    GroupSessionConnectionService, ParticipantRole,
};
use bcs_service_api::{
    CallerContext, ChatAbortCommand, CollaborationRuntimeError, CollaborationRuntimeService,
    HandleSessionHumanInputCommand, HandleSessionHumanInputOutcome, HumanActor,
    HumanResponseSource, MessageFlowService, ServiceError, WebSendCommand,
    ParticipantKind, WorkbenchChatAuthorizationCommand, WorkbenchConnectCommand,
    WorkbenchConnectOutcome, WorkbenchParticipantView, WorkbenchSessionService,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::sync::mpsc;
use tracing::{debug, info, warn};

use crate::shared::RunChannelManager;
use crate::web::{WorkbenchConnectionAuth, WorkbenchConnectionRegistry};

const STATE_MACHINE_EVENT_BOT_UUID: &str = "bcs_state_machine";

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
    Close,
}

pub struct WebDispatchState {
    pub message_flow: Arc<dyn MessageFlowService>,
    pub collaboration_runtime: Arc<dyn CollaborationRuntimeService>,
    pub workbench_sessions: Arc<dyn WorkbenchSessionService>,
    pub group_session_connections: Option<Arc<dyn GroupSessionConnectionService>>,
    pub frontend_connections: Arc<WorkbenchConnectionRegistry>,
    pub run_channels: Arc<RunChannelManager>,
}

impl std::fmt::Debug for WebDispatchState {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("WebDispatchState")
            .field("message_flow", &"<MessageFlowService>")
            .field("collaboration_runtime", &"<CollaborationRuntimeService>")
            .field("workbench_sessions", &"<WorkbenchSessionService>")
            .field(
                "group_session_connections",
                &self
                    .group_session_connections
                    .as_ref()
                    .map(|_| "<GroupSessionConnectionService>"),
            )
            .field("frontend_connections", &"<WorkbenchConnectionRegistry>")
            .field("run_channels", &"<RunChannelManager>")
            .finish()
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum WebConnectionPhase {
    #[default]
    AwaitingConnect,
    Connected,
    Closed,
}

#[derive(Debug, Default)]
pub struct WebClientConnectionState {
    pub active_run_ids: Vec<String>,
    pub subscribed_sessions: Vec<(String, u64)>,
    pub phase: WebConnectionPhase,
}

pub async fn dispatch_client_frame(
    state: &Arc<WebDispatchState>,
    text: &str,
    tx: &mpsc::Sender<String>,
    connection_state: &mut WebClientConnectionState,
    auth: &WorkbenchConnectionAuth,
) -> Result<WebDispatchOutcome> {
    let frame: BcsFrame = serde_json::from_str(text)
        .map_err(|e| WebWsDispatchError::InvalidFrameFormat(e.to_string()))?;

    match frame {
        BcsFrame::Request(req) => {
            let is_connect = req.method == "connect";
            let subscribed_before = connection_state.subscribed_sessions.len();
            if let Err(error) =
                handle_client_request(state, &req, tx, connection_state, auth).await
            {
                if is_connect {
                    return Err(WebWsDispatchError::ClientConnectError(Box::new(error)));
                }
                return Err(error);
            }
            if connection_state.phase == WebConnectionPhase::Closed {
                return Ok(WebDispatchOutcome::Close);
            }
            if is_connect {
                let subscribed =
                    connection_state.subscribed_sessions.len() > subscribed_before;
                if matches!(auth, WorkbenchConnectionAuth::SessionBound { .. }) && !subscribed {
                    return Ok(WebDispatchOutcome::Dispatched);
                }
                return Ok(WebDispatchOutcome::ClientConnect {
                    subscribed,
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
    auth: &WorkbenchConnectionAuth,
) -> Result<()> {
    debug!(id = %req.id, method = %req.method, "Handling client RequestFrame");
    info!(method = %req.method, "Client request received");

    if matches!(auth, WorkbenchConnectionAuth::SessionBound { .. })
        && connection_state.phase == WebConnectionPhase::AwaitingConnect
        && req.method != "connect"
    {
        send_error(
            tx,
            &req.id,
            "connect_required",
            "A successful connect request is required before this method",
        )
        .await?;
        return Ok(());
    }

    match req.method.as_str() {
        "connect" => {
            handle_connect(state, req, tx, connection_state, auth).await?;
        }
        "chat.send" => {
            handle_chat_send(state, req, tx, connection_state, auth).await?;
        }
        "chat.abort" => {
            handle_chat_abort(state, req, tx, connection_state, auth).await?;
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
    auth: &WorkbenchConnectionAuth,
) -> Result<()> {
    let bound_actor_id = auth.actor_id();
    let params: ConnectParams = serde_json::from_value(req.params.clone().unwrap_or(Value::Null))
        .map_err(|e| {
        WebWsDispatchError::InvalidFrameFormat(format!("Invalid connect params: {}", e))
    })?;

    let (group_id, session_id) = match auth {
        WorkbenchConnectionAuth::UserBound { .. } => {
            (params.group_id.clone(), params.session_id.clone())
        }
        WorkbenchConnectionAuth::SessionBound {
            group_id,
            session_id,
            ..
        } => {
            if connection_state.phase == WebConnectionPhase::Connected {
                send_error(
                    tx,
                    &req.id,
                    "already_connected",
                    "This WebSocket is already connected",
                )
                .await?;
                return Ok(());
            }
            if params.group_id != *group_id || params.session_id.as_deref() != Some(session_id) {
                send_error(
                    tx,
                    &req.id,
                    "token_scope_mismatch",
                    "Connect scope does not match the connection token",
                )
                .await?;
                connection_state.phase = WebConnectionPhase::Closed;
                return Ok(());
            }
            (group_id.clone(), Some(session_id.clone()))
        }
    };

    debug!(
        group_id = %group_id,
        session_id = ?session_id,
        bound_actor_id = ?bound_actor_id,
        "Processing connect request"
    );

    let outcome = match auth {
        WorkbenchConnectionAuth::UserBound { .. } => match state
            .workbench_sessions
            .connect(WorkbenchConnectCommand {
                bound_actor_id: bound_actor_id.map(str::to_string),
                group_id: group_id.clone(),
                session_id: session_id.clone(),
            })
            .await
        {
            Ok(outcome) => outcome,
            Err(err) => {
                warn!(
                    group_id = %group_id,
                    session_id = ?session_id,
                    bound_actor_id = ?bound_actor_id,
                    error = ?err,
                    "connect rejected by Workbench WS authorization"
                );
                let message = err.message();
                send_error(tx, &req.id, err.code(), &message).await?;
                return Ok(());
            }
        },
        WorkbenchConnectionAuth::SessionBound {
            tenant,
            actor_id,
            group_id,
            session_id,
        } => {
            let user_id = actor_id
                .strip_prefix("human_")
                .filter(|user_id| !user_id.is_empty());
            let service = state.group_session_connections.as_ref();
            let authorized = match (service, user_id) {
                (Some(service), Some(user_id)) => service
                    .authorize_connect(AuthorizeGroupSessionConnection {
                        binding: GroupSessionConnectionBinding {
                            tenant: tenant.clone(),
                            user_id: user_id.to_string(),
                            group_id: group_id.clone(),
                            session_id: session_id.clone(),
                        },
                    })
                    .await,
                _ => {
                    warn!("session-bound connect is missing a valid V1 authorization context");
                    send_session_access_revoked(tx, &req.id, connection_state).await?;
                    return Ok(());
                }
            };
            match authorized {
                Ok(authorized) => WorkbenchConnectOutcome {
                    group_id: group_id.clone(),
                    participants: authorized
                        .participants
                        .into_iter()
                        .map(|participant| WorkbenchParticipantView {
                            bot_uuid: participant.actor_id,
                            role: participant_role_to_wire(participant.role).to_string(),
                            kind: ParticipantKind::Bot,
                            mode: Some(participant.mode),
                        })
                        .collect(),
                },
                Err(err) => {
                    warn!(
                        error = ?err,
                        "connect rejected by V1 group-session authorization"
                    );
                    send_session_access_revoked(tx, &req.id, connection_state).await?;
                    return Ok(());
                }
            }
        }
    };

    let subscription_key = session_id.clone().unwrap_or_else(|| group_id.clone());
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
    if matches!(auth, WorkbenchConnectionAuth::SessionBound { .. }) {
        connection_state.phase = WebConnectionPhase::Connected;
    }

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

async fn send_session_access_revoked(
    tx: &mpsc::Sender<String>,
    request_id: &str,
    connection_state: &mut WebClientConnectionState,
) -> Result<()> {
    send_error(
        tx,
        request_id,
        "session_access_revoked",
        "Session access is no longer authorized",
    )
    .await?;
    connection_state.phase = WebConnectionPhase::Closed;
    Ok(())
}

fn participant_role_to_wire(role: ParticipantRole) -> &'static str {
    match role {
        ParticipantRole::Driver => "driver",
        ParticipantRole::Consultant => "consultant",
        ParticipantRole::Manager => "manager",
        ParticipantRole::Worker => "worker",
        ParticipantRole::Observer => "observer",
    }
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
    auth: &WorkbenchConnectionAuth,
) -> Result<()> {
    let bound_actor_id = auth.actor_id();
    let params: ChatSendParams = serde_json::from_value(req.params.clone().unwrap_or(Value::Null))
        .map_err(|e| {
            WebWsDispatchError::InvalidFrameFormat(format!("Invalid chat.send params: {}", e))
        })?;

    let mut from_id = params
        .bot_id
        .clone()
        .or_else(|| params.bot_uuid.clone())
        .unwrap_or_else(|| "unknown".to_string());
    let mut session_id = resolve_bcs_session_id(&params);
    let mut group_id = params.group_id.clone();
    if let WorkbenchConnectionAuth::SessionBound {
        actor_id,
        group_id: bound_group_id,
        session_id: bound_session_id,
        ..
    } = auth
    {
        if group_id != *bound_group_id || session_id.as_deref() != Some(bound_session_id) {
            send_error(
                tx,
                &req.id,
                "token_scope_mismatch",
                "Request scope does not match the connection token",
            )
            .await?;
            connection_state.phase = WebConnectionPhase::Closed;
            return Ok(());
        }
        group_id = bound_group_id.clone();
        session_id = Some(bound_session_id.clone());
        from_id = actor_id.clone();
    }

    info!(
        group_id = %group_id,
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
            group_id: group_id.clone(),
            from_actor_id: from_id.clone(),
            session_id: session_id.clone(),
        })
        .await
    {
        warn!(
            from = %from_id,
            group_id = %group_id,
            bound_actor_id = ?bound_actor_id,
            error = ?err,
            "chat.send rejected by Workbench WS authorization"
        );
        let message = err.message();
        send_error(tx, &req.id, err.code(), &message).await?;
        return Ok(());
    }

    // COSEC: Human identity comes only from the authenticated WebSocket
    // connection. Never trust bot_id/bot_uuid from the chat.send payload as
    // the responder identity for a HumanInput node.
    match state
        .collaboration_runtime
        .handle_session_human_input(HandleSessionHumanInputCommand {
            group_id: group_id.clone(),
            session_id: session_id.clone(),
            caller_actor_id: bound_actor_id.unwrap_or_default().to_string(),
            content: params.message.clone(),
            source: HumanResponseSource::Http,
        })
        .await
    {
        Ok(HandleSessionHumanInputOutcome::NotStateMachine) => {}
        Ok(HandleSessionHumanInputOutcome::Consumed { response }) => {
            let run_id = response.run.run_id;
            let response = ChatSendResponse {
                run_id: run_id.clone(),
                status: "accepted".to_string(),
            };
            send_ok(tx, &req.id, serde_json::to_value(response)?).await?;
            send_empty_human_input_final(
                tx,
                &group_id,
                session_id.as_deref(),
                &run_id,
            )
            .await?;
            return Ok(());
        }
        Err(error) => {
            warn!(
                group_id = %group_id,
                session_id = ?session_id,
                error = %error,
                "chat.send rejected by state-machine HumanInput routing"
            );
            let error_code = collaboration_error_code(&error);
            let error_message = error.to_string();
            send_error(tx, &req.id, error_code, &error_message).await?;
            send_human_input_error_event(
                tx,
                &group_id,
                session_id.as_deref(),
                error_code,
                &error_message,
            )
            .await?;
            return Ok(());
        }
    }

    let sender_subscription_key = session_id.as_deref().unwrap_or(&group_id);
    let sender_conn_id = connection_state
        .subscribed_sessions
        .iter()
        .find(|(key, _)| key.as_str() == sender_subscription_key)
        .or_else(|| {
            connection_state
                .subscribed_sessions
                .iter()
                .find(|(key, _)| key == &group_id)
        })
        .map(|(_, id)| *id);

    let caller = caller_context_from_bound_actor(bound_actor_id, &from_id);

    info!("chat.send: calling message_flow.handle_web_send");
    let outcome = state
        .message_flow
        .handle_web_send(WebSendCommand {
            caller,
            group_id: group_id.clone(),
            session_id: session_id.clone(),
            from_actor_id: from_id,
            from_name: params.bot_name.clone(),
            message: params.message,
            mentions: params.mentions,
            attachments: params.attachments.map(|attachments| {
                attachments
                    .into_iter()
                    .map(bcs_domain::Attachment::from)
                    .collect()
            }),
            thinking: params.thinking,
            idempotency_key: params.idempotency_key,
            source_im_message_id: None,
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
    let run_session_key = session_id.unwrap_or_else(|| group_id.clone());
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

fn collaboration_error_code(error: &CollaborationRuntimeError) -> &'static str {
    match error {
        CollaborationRuntimeError::RunNotFound(_)
        | CollaborationRuntimeError::NodeNotFound { .. }
        | CollaborationRuntimeError::DefinitionNotFound(_, _) => "not_found",
        CollaborationRuntimeError::InvalidDefinition(_) => "invalid_definition",
        CollaborationRuntimeError::InvalidParticipantBinding(_)
        | CollaborationRuntimeError::InvalidRequest(_) => "invalid_request",
        CollaborationRuntimeError::Unauthenticated => "unauthorized",
        CollaborationRuntimeError::Forbidden(_) => "forbidden",
        CollaborationRuntimeError::JudgeUnavailable(_) => "judge_unavailable",
        CollaborationRuntimeError::Conflict(_) => "conflict",
        CollaborationRuntimeError::Internal(_) => "internal_error",
    }
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
    connection_state: &mut WebClientConnectionState,
    auth: &WorkbenchConnectionAuth,
) -> Result<()> {
    let params: ClientChatAbortParams =
        serde_json::from_value(req.params.clone().unwrap_or(Value::Null)).map_err(|e| {
            WebWsDispatchError::InvalidFrameFormat(format!("Invalid chat.abort params: {}", e))
        })?;

    let mut group_id = params.group_id;
    let run_id = params.run_id;
    let (caller, session_id) = match auth {
        WorkbenchConnectionAuth::UserBound { .. } => {
            (caller_context_from_bound_actor(None, "unknown"), None)
        }
        WorkbenchConnectionAuth::SessionBound {
            actor_id,
            group_id: bound_group_id,
            session_id,
            ..
        } => {
            if group_id != *bound_group_id {
                send_error(
                    tx,
                    &req.id,
                    "token_scope_mismatch",
                    "Request scope does not match the connection token",
                )
                .await?;
                connection_state.phase = WebConnectionPhase::Closed;
                return Ok(());
            }
            group_id = bound_group_id.clone();
            (
                caller_context_from_bound_actor(Some(actor_id), actor_id),
                Some(session_id.clone()),
            )
        }
    };

    info!(
        group_id = %group_id,
        run_id = ?run_id,
        "Processing chat.abort"
    );

    let outcome = state
        .message_flow
        .handle_chat_abort(ChatAbortCommand {
            caller,
            group_id: group_id.clone(),
            session_id,
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

async fn send_empty_human_input_final(
    tx: &mpsc::Sender<String>,
    group_id: &str,
    session_id: Option<&str>,
    run_id: &str,
) -> Result<()> {
    // HumanInput consumes this chat.send without dispatching a Bot run. Emit a
    // terminal chat event on the same connection so the current frontend can
    // close its pending request without rendering an assistant message.
    let event = serde_json::json!({
        "type": "event",
        "event": "chat",
        "group_id": group_id,
        "bot_uuid": STATE_MACHINE_EVENT_BOT_UUID,
        "payload": {
            "run_id": run_id,
            "bcs_group_id": group_id,
            "bcs_session_id": session_id,
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [],
            },
        },
    });
    let json = serde_json::to_string(&event)?;
    tx.send(json).await.map_err(|error| {
        WebWsDispatchError::WsProtocolError(format!(
            "Failed to send HumanInput completion event: {}",
            error
        ))
    })?;
    Ok(())
}

async fn send_human_input_error_event(
    tx: &mpsc::Sender<String>,
    group_id: &str,
    session_id: Option<&str>,
    error_code: &str,
    error_message: &str,
) -> Result<()> {
    // The current group-chat SDK does not render ResponseFrame errors because
    // they have no bot_uuid. Keep the protocol response and add a chat error
    // event so the frontend can render the rejection and close its request.
    let event = serde_json::json!({
        "type": "event",
        "event": "chat",
        "group_id": group_id,
        "bot_uuid": STATE_MACHINE_EVENT_BOT_UUID,
        "payload": {
            "bcs_group_id": group_id,
            "bcs_session_id": session_id,
            "state": "error",
            "errorCode": error_code,
            "errorMessage": error_message,
            "message": {
                "role": "assistant",
                "content": [{
                    "type": "text",
                    "text": error_message,
                }],
            },
        },
    });
    let json = serde_json::to_string(&event)?;
    tx.send(json).await.map_err(|error| {
        WebWsDispatchError::WsProtocolError(format!(
            "Failed to send HumanInput error event: {}",
            error
        ))
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
