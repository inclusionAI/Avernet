//! WebSocket upgrade + bot ws handlers + IntoResponse for BcsError + lifecycle hook factory.
//!
//! Split out from server.rs as part of the V1 API auth plugin chain (Task 1) refactor. Behavior preserved exactly.

use super::*;

pub(super) fn ws_lifecycle_hook(_state: &Arc<BcsServerState>) -> Arc<dyn WsLifecycleInstrumentationHook> {
    #[cfg(feature = "prometheus-metrics")]
    {
        if let Some(metrics) = &_state.metrics {
            return metrics.clone();
        }
    }

    Arc::new(NoopWsLifecycleInstrumentationHook)
}

pub(super) fn state_machine_loop_instrumentation(
    _metrics: Option<&Arc<crate::metrics::MetricsRuntime>>,
) -> Option<Arc<dyn bcs_service_api::StateMachineLoopInstrumentationHook>> {
    #[cfg(feature = "prometheus-metrics")]
    if let Some(metrics) = _metrics {
        return Some(Arc::new(crate::metrics::MetricsStateMachineLoopHook::new(metrics.env.clone())));
    }
    None
}

pub(super) fn direct_chat_run_lifecycle_hook(
    _metrics: Option<&Arc<crate::metrics::MetricsRuntime>>,
) -> Arc<dyn DirectChatRunLifecycleHook> {
    #[cfg(feature = "prometheus-metrics")]
    {
        if let Some(metrics) = _metrics {
            return Arc::new(crate::metrics::MetricsDirectChatRunLifecycleHook::new(
                metrics.env.clone(),
            ));
        }
    }

    Arc::new(NoopDirectChatRunLifecycleHook)
}

/// WebSocket upgrade handler for frontend clients (AI Workbench).
///
/// Bind the calling Human's actor id (`human_{staff_no}`) into the WS session
/// at the HTTP upgrade boundary. The bound id is computed once here from the
/// configured auth chain and then immutable for the lifetime of the session;
/// clients cannot rewrite their identity by sending a different
/// sender in subsequent frames.
///
/// If the cookie is missing / invalid / staff_no is empty, the session has
/// `bound_actor_id = None`; Workbench `connect` and `chat.send` then reject
/// request frames with `unauthorized`.
pub(super) async fn ws_upgrade_handler(
    ws: WebSocketUpgrade,
    headers: axum::http::HeaderMap,
    State(state): State<Arc<BcsServerState>>,
) -> Response {
    // Resolve identity BEFORE on_upgrade: once the connection switches to
    // WebSocket frames the original HTTP headers are gone, so cookie
    // extraction must happen here in the request scope.
    let bound_actor_id = match state.auth_chain.authenticate(&headers).await {
        Ok(result) => result
            .principal
            .and_then(|p| p.user_id)
            .filter(|s| !s.is_empty())
            .map(|staff_no| format!("human_{}", staff_no)),
        Err(_) => None,
    };

    if let Some(ref actor_id) = bound_actor_id {
        info!(actor_id = %actor_id, "WS upgrade: bound human actor id");
    } else {
        debug!("WS upgrade: anonymous session (no staff_no in cookie)");
    }

    let request_id = bcs_observability::current_request_id();
    ws.on_upgrade(move |socket| {
        let ws_state = web_ws_dispatch_state(&state, None);
        let metrics_hook = ws_lifecycle_hook(&state);
        bcs_observability::with_request_id(request_id, bcs_ws::web::handle_client_connection(
            socket,
            ws_state,
            bcs_ws::web::WorkbenchConnectionAuth::UserBound {
                actor_id: bound_actor_id,
            },
            metrics_hook,
        ))
    })
}

/// WebSocket handler for bot connections.
///
/// Token validation is handled by the bot.connect frame after upgrade:
/// - Valid token: reconnect to existing bot
/// - Invalid/missing token: treated as new bot, assigned new bot_id + token
///
/// The Authorization header and x-agentclaw-agent-code are captured before
/// the upgrade so they can be backfilled into bot_info after a successful
/// bot.connect handshake.
pub(super) async fn bot_ws_handler(
    State(state): State<Arc<BcsServerState>>,
    headers: axum::http::HeaderMap,
    ws: WsUpgrade,
) -> Response {
    let agent_token = headers
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .map(|s| s.to_string());
    let agent_code_header = headers
        .get("x-agentclaw-agent-code")
        .and_then(|v| v.to_str().ok())
        .map(|s| s.to_string());

    let request_id = bcs_observability::current_request_id();
    ws.on_upgrade(move |socket| {
        let ws_state = bot_ws_dispatch_state(&state);
        let metrics_hook = ws_lifecycle_hook(&state);
        bcs_observability::with_request_id(request_id, bcs_ws::bot::handle_connection(
            socket,
            ws_state,
            metrics_hook,
            agent_token,
            agent_code_header,
        ))
    })
}

impl IntoResponse for crate::BcsError {
    fn into_response(self) -> axum::response::Response {
        let (status, message) = match &self {
            Self::SessionNotFound(id) => {
                (StatusCode::NOT_FOUND, format!("Session not found: {}", id))
            }
            // Issue #4 (E.6 regression 2026-04-29): GroupNotFound previously
            // fell through to the catch-all `_ => 500` arm, so `GET /groups/{id}`
            // for any non-existent (or just-deleted) group returned HTTP 500
            // instead of 404. The error body already says "Group not found",
            // so the only fix needed is the status-code mapping.
            Self::GroupNotFound(id) => (StatusCode::NOT_FOUND, format!("Group not found: {}", id)),
            Self::BotNotFound(id) => (StatusCode::NOT_FOUND, format!("Bot not found: {}", id)),
            Self::BotNotRegistered(id) => (
                StatusCode::NOT_FOUND,
                format!("Bot '{}' is not registered", id),
            ),
            Self::BotAlreadyConnected(id) => (
                StatusCode::CONFLICT,
                format!("Bot '{}' already has an active WebSocket connection", id),
            ),
            Self::BotNotConnected(id) => (
                StatusCode::NOT_FOUND,
                format!("Bot '{}' is not connected via WebSocket", id),
            ),
            Self::InvalidRequest(msg) => {
                (StatusCode::BAD_REQUEST, format!("Invalid request: {}", msg))
            }
            Self::NotFriends { bot, driver } => (
                StatusCode::FORBIDDEN,
                format!(
                    "Bot '{}' is protected and not a friend of '{}'",
                    bot, driver
                ),
            ),
            Self::BotPrivate(id) => (
                StatusCode::FORBIDDEN,
                format!(
                    "Bot '{}' is in private mode and cannot participate in collaboration network",
                    id
                ),
            ),
            Self::InvalidSessionToken => (
                StatusCode::UNAUTHORIZED,
                "Invalid or expired session token".to_string(),
            ),
            Self::Forbidden(msg) => (StatusCode::FORBIDDEN, msg.clone()),
            Self::Unauthorized(msg) => (StatusCode::UNAUTHORIZED, msg.clone()),
            Self::WsProtocolError(msg) => (
                StatusCode::BAD_REQUEST,
                format!("WebSocket protocol error: {}", msg),
            ),
            Self::InvalidFrameFormat(msg) => (
                StatusCode::BAD_REQUEST,
                format!("Invalid frame format: {}", msg),
            ),
            Self::ProposalNotFound(id) => (
                StatusCode::NOT_FOUND,
                format!("Proposal not found or expired: {}", id),
            ),
            Self::BotDirectoryNotFound(path) => (
                StatusCode::NOT_FOUND,
                format!("Bot directory not found: {}", path),
            ),
            Self::InvalidConfig(msg) => (StatusCode::BAD_REQUEST, msg.clone()),
            Self::InvalidOperation(msg) => (StatusCode::BAD_REQUEST, msg.clone()),
            Self::TooManyGroups(msg) => (StatusCode::BAD_REQUEST, msg.clone()),
            Self::TooManyMembers(msg) => (StatusCode::BAD_REQUEST, msg.clone()),
            Self::TooManyMessages(msg) => (StatusCode::BAD_REQUEST, msg.clone()),
            _ => (StatusCode::INTERNAL_SERVER_ERROR, self.to_string()),
        };

        let variant = self.variant_name();
        if status.is_server_error() {
            tracing::error!(status = %status.as_u16(), error_type = %variant, backtrace = %std::backtrace::Backtrace::force_capture(), "{message}");
        } else if status.is_client_error() && status != StatusCode::NOT_FOUND {
            tracing::warn!(status = %status.as_u16(), error_type = %variant, "{message}");
        }

        let body = Json(serde_json::json!({
            "error": message,
            "status": status.as_u16()
        }));

        (status, body).into_response()
    }
}
