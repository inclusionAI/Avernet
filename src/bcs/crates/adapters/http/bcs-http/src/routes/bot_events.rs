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
use tracing::{Span, info, warn};
use tracing_opentelemetry::OpenTelemetrySpanExt;

use crate::gateway_trace::{record_gen_ai_output_message, record_span_content_attribute};
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
                warn!(
                    provider_id = %provider_id,
                    status = %status.as_u16(),
                    error = %body_text,
                    request_body_len = body_bytes.len(),
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
    let callback_content = bot_event_trace_content(&req);
    let requested_finish_reason = requested_finish_reason(&req);
    let provider_id = match header_required(&headers, BCN_PROVIDER_ID_HEADER) {
        Ok(provider_id) => provider_id,
        Err(error) => {
            record_bot_response_auth_failure(
                &callback_content,
                requested_finish_reason,
            );
            return Err(error);
        }
    };
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
        Span::current().set_attribute("bcn.auth.result", "unverified");
        record_gen_ai_output_message(&callback_content, "unknown", true);
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
    let credential = match credential_from_headers(&state, &headers, &provider_id).await {
        Ok(credential) => credential,
        Err(error) => {
            record_bot_response_auth_failure(
                &callback_content,
                finish_reason(&effective_state),
            );
            return Err(error);
        }
    };

    let outcome = match state
        .services
        .provider_bot_events
        .submit_event(ProviderBotEventCommand {
            provider_id: provider_id.clone(),
            credential,
            run_id: req.run_id.clone(),
            state: effective_state.clone(),
            message_text: req.message.text.clone(),
            event: req.event.clone(),
            payload: req.payload.clone(),
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
            let route_error = bot_event_error(error);
            if matches!(route_error.status, StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN) {
                record_bot_response_auth_failure(
                    &callback_content,
                    finish_reason(&effective_state),
                );
            }
            return Err(route_error);
        }
    };
    info!(
        provider_id = %provider_id,
        delivered_count = %outcome.delivered_count,
        failed_count = %outcome.failed_count,
        "provider callback: bot event processed"
    );
    let span = Span::current();
    span.set_attribute("bcn.auth.result", "success");
    span.set_attribute("bcn.operation", "bot.response");
    span.set_attribute("bcn.provider.id", provider_id.clone());
    span.set_attribute("bcn.run.id", req.run_id.clone());
    span.set_attribute("bcn.callback.state", format!("{effective_state:?}"));
    if let Some(seq) = req.seq {
        span.set_attribute("bcn.callback.seq", seq as i64);
    }
    if let Some(event) = req.event.as_deref() {
        span.set_attribute("bcn.callback.event", event.to_string());
    }
    span.set_attribute(
        "bcn.callback.delivered_count",
        outcome.delivered_count as i64,
    );
    span.set_attribute("bcn.callback.failed_count", outcome.failed_count as i64);
    record_bot_response_content(
        &callback_content,
        finish_reason(&effective_state),
        false,
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

fn record_bot_response_auth_failure(
    callback_content: &str,
    finish_reason: Option<&str>,
) {
    Span::current().set_attribute("bcn.auth.result", "failed");
    record_bot_response_content(callback_content, finish_reason, true);
}

fn record_bot_response_content(
    content: &str,
    finish_reason: Option<&str>,
    untrusted: bool,
) {
    if let Some(finish_reason) = finish_reason {
        record_gen_ai_output_message(content, finish_reason, untrusted);
    } else {
        record_span_content_attribute("bcn.bot.response.chunk", content, untrusted);
    }
}

fn finish_reason(state: &ChatEventState) -> Option<&'static str> {
    match state {
        ChatEventState::Final => Some("stop"),
        ChatEventState::Error => Some("error"),
        ChatEventState::Aborted => Some("aborted"),
        ChatEventState::Delta
        | ChatEventState::ToolCallStart
        | ChatEventState::ToolCallEnd => None,
    }
}

fn requested_finish_reason(req: &BotEventRequest) -> Option<&'static str> {
    if let Some(state) = req.state.as_ref() {
        return finish_reason(state);
    }
    match req
        .payload
        .as_ref()
        .and_then(|payload| payload.get("state"))
        .and_then(Value::as_str)
    {
        Some("final") => Some("stop"),
        Some("error") => Some("error"),
        Some("aborted") => Some("aborted"),
        Some("delta") => None,
        _ if req.event.is_some() => None,
        _ => Some("unknown"),
    }
}

fn bot_event_trace_content(req: &BotEventRequest) -> String {
    if !req.message.text.is_empty() {
        return req.message.text.clone();
    }
    let Some(payload) = req.payload.as_ref() else {
        return String::new();
    };
    if let Some(content) = payload.pointer("/message/content").and_then(Value::as_str) {
        return content.to_string();
    }
    let content = payload
        .pointer("/message/content")
        .and_then(Value::as_array)
        .map(|parts| {
            parts
                .iter()
                .filter_map(|part| part.get("text").and_then(Value::as_str))
                .collect::<String>()
        });
    match content {
        Some(content) if !content.is_empty() => content,
        _ => payload.to_string(),
    }
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
    crate::headers::extract_bearer_token(headers)
        .ok_or_else(|| BotEventRouteError::unauthorized("valid bot runtime token is required"))
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
        ProviderCoordinationEventKindDto::ToolResult => ProviderCoordinationEventKind::ToolResult,
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
        ProviderBotEventError::Unauthorized(message) => BotEventRouteError::unauthorized(message),
        ProviderBotEventError::Forbidden(message) if message == "provider_id_mismatch" => {
            BotEventRouteError::provider_id_mismatch()
        }
        ProviderBotEventError::Forbidden(message) => BotEventRouteError::forbidden(message),
        ProviderBotEventError::InvalidRequest(message) => BotEventRouteError::bad_request(message),
        ProviderBotEventError::RunNotFound(message) => BotEventRouteError::not_found(message),
        ProviderBotEventError::RunTerminated(message) => BotEventRouteError::gone(message),
        ProviderBotEventError::BotNotFound(bot_id) => {
            BotEventRouteError::bot_not_found(format!("bot not found: {bot_id}"))
        }
        ProviderBotEventError::Internal(message) => {
            BotEventRouteError::new(StatusCode::INTERNAL_SERVER_ERROR, "internal_error", message)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn trace_content_preserves_agent_payload_when_message_text_is_absent() {
        let request = BotEventRequest {
            run_id: "run-1".to_string(),
            seq: Some(1),
            state: None,
            message: BotEventMessage::default(),
            event: Some("agent".to_string()),
            payload: Some(json!({
                "type": "tool_result",
                "tool_name": "search",
                "result": "found"
            })),
        };

        let content = bot_event_trace_content(&request);
        let Ok(payload): Result<Value, _> = serde_json::from_str(&content) else {
            panic!("expected preserved payload JSON");
        };

        assert_eq!(payload["type"], "tool_result");
        assert_eq!(payload["tool_name"], "search");
        assert_eq!(payload["result"], "found");
    }
}
