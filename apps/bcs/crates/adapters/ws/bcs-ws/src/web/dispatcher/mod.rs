use std::collections::HashMap;
use std::sync::Arc;

use bcs_domain::HumanMessageView;
use bcs_protocol::{BcsFrame, ErrorShape, RequestFrame, ResponseFrame};
use bcs_service_api::application::v1::{
    AuthorizeGroupSessionConnection, GroupSessionConnectionBinding, GroupSessionConnectionService,
    ParticipantRole,
};
use bcs_service_api::{
    CallerContext, ChatAbortCommand, CollaborationRuntimeError, CollaborationRuntimeService,
    HandleSessionHumanInputCommand, HandleSessionHumanInputOutcome, HumanActor,
    HumanResponseSource, MessageFlowService, ParticipantKind, ServiceError, WebSendCommand,
    WorkbenchChatAbortAuthorizationCommand, WorkbenchChatAuthorizationCommand,
    WorkbenchConnectCommand, WorkbenchConnectOutcome, WorkbenchParticipantView,
    WorkbenchSessionService,
};
use bcs_service_api::{
    InteractionFrontendEvent, InteractionService, InteractionServiceError, InteractionStatus,
    ResolveInteractionCommand,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;
use tracing::{debug, info, warn};

use crate::shared::RunChannelManager;
use crate::web::frontend_delivery::interaction_event_json;
use crate::web::protected_delivery::{
    ProtectedDeliveryBinding, WorkbenchOutbound, enqueue_single_protected,
};
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
    pub interactions: Arc<dyn InteractionService>,
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
            .field("interactions", &"<InteractionService>")
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
    pub subscribed_sessions: Vec<(String, u64, Option<HumanMessageView>)>,
    pub phase: WebConnectionPhase,
    pub shutdown: CancellationToken,
}

pub async fn dispatch_client_frame(
    state: &Arc<WebDispatchState>,
    text: &str,
    tx: &mpsc::Sender<WorkbenchOutbound>,
    connection_state: &mut WebClientConnectionState,
    auth: &WorkbenchConnectionAuth,
) -> Result<WebDispatchOutcome> {
    let frame: BcsFrame = serde_json::from_str(text)
        .map_err(|e| WebWsDispatchError::InvalidFrameFormat(e.to_string()))?;

    match frame {
        BcsFrame::Request(req) => {
            let is_connect = req.method == "connect";
            let subscribed_before = connection_state.subscribed_sessions.len();
            if let Err(error) = handle_client_request(state, &req, tx, connection_state, auth).await
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
                let subscribed = connection_state.subscribed_sessions.len() > subscribed_before;
                if matches!(auth, WorkbenchConnectionAuth::SessionBound { .. }) && !subscribed {
                    return Ok(WebDispatchOutcome::Dispatched);
                }
                return Ok(WebDispatchOutcome::ClientConnect { subscribed });
            }
        }
        BcsFrame::Response(res) => {
            warn!(request_id = %bcs_observability::CurrentRequestId, id = %res.id, ok = res.ok, "Unexpected ResponseFrame from frontend client");
        }
        BcsFrame::Event(event) => {
            warn!(request_id = %bcs_observability::CurrentRequestId, event = %event.event, "Unexpected EventFrame from frontend client");
        }
    }

    Ok(WebDispatchOutcome::Dispatched)
}

async fn handle_client_request(
    state: &Arc<WebDispatchState>,
    req: &RequestFrame,
    tx: &mpsc::Sender<WorkbenchOutbound>,
    connection_state: &mut WebClientConnectionState,
    auth: &WorkbenchConnectionAuth,
) -> Result<()> {
    debug!(id = %req.id, method = %req.method, "Handling client RequestFrame");
    info!(method = %req.method, "Client request received");

    let requires_connect =
        matches!(auth, WorkbenchConnectionAuth::SessionBound { .. }) || req.method == "chat.abort";
    if requires_connect
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
        "interaction.resolve" => {
            handle_interaction_resolve(state, req, tx, connection_state, auth).await?;
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

// Responsibility split (Task 16 review round): the frame routing stays here;
// connect / interaction / chat / reply building live in sibling submodules.
// Public API is unchanged — everything re-exported from `web::dispatcher`
// comes from this module tree's original path.
mod chat;
mod connect;
mod interaction;
mod replies;

use self::chat::{handle_chat_abort, handle_chat_send};
use self::connect::handle_connect;
use self::interaction::handle_interaction_resolve;
use self::replies::send_error;
