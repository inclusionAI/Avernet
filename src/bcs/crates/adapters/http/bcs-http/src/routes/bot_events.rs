use axum::{
    Json,
    body::to_bytes,
    extract::{FromRequest, Request, State},
    http::{HeaderMap, StatusCode, header},
    response::{IntoResponse, Response},
};
use bcs_auth_api::is_jwt_format;
use bcs_protocol::{
    BCN_PROVIDER_BOT_REF_HEADER, BCN_PROVIDER_ID_HEADER, ProviderCoordinationEventKindDto,
    ProviderCoordinationEventRequest,
};
use bcs_service_api::{
    ChatEventState, ProviderBotCoordinationCommand, ProviderBotEventCommand,
    ProviderBotEventCredential, ProviderBotEventError, ProviderCoordinationEventKind,
    ProviderCoordinationIntent,
};
use serde::Deserialize;
use serde_json::{Value, json};
use tracing::{info, warn};

use crate::state::HttpAppState;

const BOT_EVENT_REQUEST_BODY_LIMIT: usize = 2 * 1024 * 1024;

#[derive(Debug, Deserialize)]
pub struct BotEventRequest {
    pub run_id: String,
    #[serde(default)]
    pub seq: Option<u64>,
    /// 1.0 terminal-only field. Optional now: 2.0 callback-streaming carries
    /// the chat state inside `payload` instead. When `event`/`payload` are
    /// absent this MUST be present (legacy terminal-only contract).
    #[serde(default)]
    pub state: Option<ChatEventState>,
    #[serde(default)]
    pub message: BotEventMessage,
    /// 2.0 callback-streaming (spec §11.2): event class ("agent" | "chat").
    /// When present with `payload`, BCS parses the full event (§3 schema)
    /// instead of the legacy `state`/`message.text` shape.
    #[serde(default)]
    pub event: Option<String>,
    /// 2.0 callback-streaming (spec §11.2): full §3 event payload.
    #[serde(default)]
    pub payload: Option<Value>,
}

#[derive(Debug, Default, Deserialize)]
pub struct BotEventMessage {
    #[serde(default)]
    pub text: String,
}

pub struct LoggedBotEventRequest(BotEventRequest);

impl<S> FromRequest<S> for LoggedBotEventRequest
where
    S: Send + Sync,
{
    type Rejection = Response;

    async fn from_request(req: Request, _state: &S) -> Result<Self, Self::Rejection> {
        let provider_id = req
            .headers()
            .get(BCN_PROVIDER_ID_HEADER)
            .and_then(|value| value.to_str().ok())
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .unwrap_or("<missing>")
            .to_string();

        if !json_content_type(req.headers()) {
            let status = StatusCode::UNSUPPORTED_MEDIA_TYPE;
            let body_text = "Expected request with `Content-Type: application/json`";
            warn!(
                provider_id = %provider_id,
                status = %status.as_u16(),
                error = %body_text,
                "provider callback: invalid bot event request"
            );
            return Err((status, body_text).into_response());
        }

        let body_bytes = match to_bytes(req.into_body(), BOT_EVENT_REQUEST_BODY_LIMIT).await {
            Ok(body_bytes) => body_bytes,
            Err(error) => {
                let status = StatusCode::BAD_REQUEST;
                let body_text = format!("Failed to read request body: {error}");
                warn!(
                    provider_id = %provider_id,
                    status = %status.as_u16(),
                    error = %body_text,
                    "provider callback: invalid bot event request"
                );
                return Err((status, body_text).into_response());
            }
        };

        match Json::<BotEventRequest>::from_bytes(&body_bytes) {
            Ok(Json(req)) => Ok(Self(req)),
            Err(rejection) => {
                let status = rejection.status();
                let body_text = rejection.body_text();
                let request_body = String::from_utf8_lossy(&body_bytes);
                warn!(
                    provider_id = %provider_id,
                    status = %status.as_u16(),
                    error = %body_text,
                    request_body = %request_body,
                    "provider callback: invalid bot event request"
                );
                Err(rejection.into_response())
            }
        }
    }
}

fn json_content_type(headers: &HeaderMap) -> bool {
    let Some(content_type) = headers.get(header::CONTENT_TYPE) else {
        return false;
    };
    let Ok(content_type) = content_type.to_str() else {
        return false;
    };
    let mime_type = content_type
        .split(';')
        .next()
        .unwrap_or("")
        .trim()
        .to_ascii_lowercase();
    mime_type == "application/json"
        || (mime_type.starts_with("application/") && mime_type.ends_with("+json"))
}

#[derive(Debug)]
pub struct BotEventRouteError {
    status: StatusCode,
    code: &'static str,
    message: String,
}

impl BotEventRouteError {
    fn new(status: StatusCode, code: &'static str, message: impl Into<String>) -> Self {
        Self {
            status,
            code,
            message: message.into(),
        }
    }

    fn bad_request(message: impl Into<String>) -> Self {
        Self::new(StatusCode::BAD_REQUEST, "invalid_request", message)
    }

    fn unauthorized(message: impl Into<String>) -> Self {
        Self::new(StatusCode::UNAUTHORIZED, "unauthorized", message)
    }

    fn forbidden(message: impl Into<String>) -> Self {
        Self::new(StatusCode::FORBIDDEN, "forbidden", message)
    }

    fn provider_id_mismatch() -> Self {
        Self::new(
            StatusCode::FORBIDDEN,
            "provider_id_mismatch",
            "provider_id_mismatch",
        )
    }

    fn auth_mode_mismatch(message: impl Into<String>) -> Self {
        Self::new(StatusCode::UNAUTHORIZED, "auth_mode_mismatch", message)
    }

    fn not_found(message: impl Into<String>) -> Self {
        Self::new(StatusCode::NOT_FOUND, "run_not_found", message)
    }

    fn bot_not_found(message: impl Into<String>) -> Self {
        Self::new(StatusCode::NOT_FOUND, "bot_not_found", message)
    }

    fn gone(message: impl Into<String>) -> Self {
        Self::new(StatusCode::GONE, "run_terminated", message)
    }
}

impl IntoResponse for BotEventRouteError {
    fn into_response(self) -> Response {
        let status = self.status;
        (
            status,
            Json(json!({
                "error": self.code,
                "message": self.message,
                "status": status.as_u16(),
            })),
        )
            .into_response()
    }
}

pub async fn post_bot_event(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    LoggedBotEventRequest(req): LoggedBotEventRequest,
) -> Result<Json<Value>, BotEventRouteError> {
    let provider_id = header_required(&headers, BCN_PROVIDER_ID_HEADER)?;
    // Derive state: prefer the explicit `state` field (1.0); fall back to
    // extracting from `payload.state` for chat events (2.0 callback-streaming);
    // for agent events default to Delta (non-terminal, goes through pipeline).
    let effective_state = if let Some(state) = req.state.clone() {
        state
    } else if req.event.as_deref() == Some("chat") {
        // Try to extract state from the payload for chat events.
        req.payload
            .as_ref()
            .and_then(|p| p.get("state"))
            .and_then(|s| s.as_str())
            .and_then(|s| match s {
                "final" => Some(ChatEventState::Final),
                "error" => Some(ChatEventState::Error),
                "aborted" => Some(ChatEventState::Aborted),
                "delta" => Some(ChatEventState::Delta),
                _ => None,
            })
            .unwrap_or(ChatEventState::Delta)
    } else if req.event.is_some() {
        // agent events (tool/thinking/lifecycle) — non-terminal pipeline.
        ChatEventState::Delta
    } else {
        return Err(BotEventRouteError::bad_request(
            "state is required when event/payload are absent (1.0 contract)",
        ));
    };

    info!(
        provider_id = %provider_id,
        run_id = %req.run_id,
        seq = ?req.seq,
        state = ?effective_state,
        event = ?req.event,
        message_text = %req.message.text,
        "provider callback: received bot event"
    );
    let credential = credential_from_headers(&state, &headers, &provider_id).await?;

    let outcome = match state
        .services
        .provider_bot_events
        .submit_event(ProviderBotEventCommand {
            provider_id: provider_id.clone(),
            credential,
            run_id: req.run_id,
            state: effective_state,
            message_text: req.message.text,
            event: req.event,
            payload: req.payload,
        })
        .await
    {
        Ok(outcome) => outcome,
        Err(error) => {
            warn!(
                provider_id = %provider_id,
                error = %error,
                "provider callback: bot event rejected"
            );
            return Err(bot_event_error(error));
        }
    };
    info!(
        provider_id = %provider_id,
        delivered_count = %outcome.delivered_count,
        failed_count = %outcome.failed_count,
        "provider callback: bot event processed"
    );

    Ok(Json(json!({
        "ok": true,
        "delivered_count": outcome.delivered_count,
        "failed_count": outcome.failed_count,
    })))
}

pub async fn post_coordination_event(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    Json(req): Json<ProviderCoordinationEventRequest>,
) -> Result<Json<Value>, BotEventRouteError> {
    let provider_id = header_required(&headers, BCN_PROVIDER_ID_HEADER)?;
    let credential = credential_from_headers(&state, &headers, &provider_id).await?;
    info!(
        provider_id = %provider_id,
        run_id = %req.run_id,
        tool_call_id = %req.tool_call_id,
        kind = ?req.kind,
        tool_name = ?req.tool_name,
        mcp_server = ?req.mcp_server,
        "provider callback: received coordination event"
    );

    let outcome = state
        .services
        .provider_bot_events
        .submit_coordination(ProviderBotCoordinationCommand {
            provider_id: provider_id.clone(),
            credential,
            run_id: req.run_id,
            tool_call_id: req.tool_call_id,
            kind: coordination_kind_from_wire(req.kind),
            tool_name: req.tool_name,
            result_text: req.result_text,
            mcp_server: req.mcp_server,
            intent: req.intent.map(|intent| ProviderCoordinationIntent {
                v: intent.v,
                tool: intent.tool,
                arguments: intent.arguments,
            }),
        })
        .await
        .map_err(bot_event_error)?;

    Ok(Json(json!({
        "ok": true,
        "processed": outcome.processed,
        "duplicate": outcome.duplicate,
    })))
}

fn header_required(headers: &HeaderMap, name: &'static str) -> Result<String, BotEventRouteError> {
    headers
        .get(name)
        .and_then(|value| value.to_str().ok())
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .ok_or_else(|| BotEventRouteError::bad_request(format!("{name} header is required")))
}

fn bearer_token(headers: &HeaderMap) -> Result<String, BotEventRouteError> {
    crate::headers::extract_bearer_token(headers).ok_or_else(|| {
        BotEventRouteError::unauthorized("valid bot runtime token is required")
    })
}

async fn credential_from_headers(
    state: &HttpAppState,
    headers: &HeaderMap,
    provider_id: &str,
) -> Result<ProviderBotEventCredential, BotEventRouteError> {
    let token = bearer_token(headers)?;
    if is_jwt_format(&token) {
        let agent_code = state
            .bot_runtime_token_resolver
            .resolve_agentpass_agent_code(&token)
            .await
            .ok_or_else(|| BotEventRouteError::unauthorized("unauthorized"))?;
        return Ok(ProviderBotEventCredential::AgentPass { agent_code });
    }

    if let Some(resolved_provider_id) = state
        .bot_runtime_token_resolver
        .try_provider_admin(&token)
        .await
    {
        if resolved_provider_id != provider_id {
            return Err(BotEventRouteError::provider_id_mismatch());
        }
        let provider_bot_ref = header_required(headers, BCN_PROVIDER_BOT_REF_HEADER)?;
        return Ok(ProviderBotEventCredential::ProviderAdmin {
            provider_admin_token: token,
            provider_bot_ref,
        });
    }

    Ok(ProviderBotEventCredential::StaticBearer(token))
}

fn coordination_kind_from_wire(
    kind: ProviderCoordinationEventKindDto,
) -> ProviderCoordinationEventKind {
    match kind {
        ProviderCoordinationEventKindDto::ToolResult => {
            ProviderCoordinationEventKind::ToolResult
        }
        ProviderCoordinationEventKindDto::CoordinationIntent => {
            ProviderCoordinationEventKind::CoordinationIntent
        }
    }
}

fn bot_event_error(error: ProviderBotEventError) -> BotEventRouteError {
    match error {
        ProviderBotEventError::Unauthorized(message) if message == "auth_mode_mismatch" => {
            BotEventRouteError::auth_mode_mismatch(message)
        }
        ProviderBotEventError::Unauthorized(message) => {
            BotEventRouteError::unauthorized(message)
        }
        ProviderBotEventError::Forbidden(message) if message == "provider_id_mismatch" => {
            BotEventRouteError::provider_id_mismatch()
        }
        ProviderBotEventError::Forbidden(message) => BotEventRouteError::forbidden(message),
        ProviderBotEventError::InvalidRequest(message) => {
            BotEventRouteError::bad_request(message)
        }
        ProviderBotEventError::RunNotFound(message) => BotEventRouteError::not_found(message),
        ProviderBotEventError::RunTerminated(message) => BotEventRouteError::gone(message),
        ProviderBotEventError::BotNotFound(bot_id) => {
            BotEventRouteError::bot_not_found(format!("bot not found: {bot_id}"))
        }
        ProviderBotEventError::Internal(message) => BotEventRouteError::new(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal_error",
            message,
        ),
    }
}
