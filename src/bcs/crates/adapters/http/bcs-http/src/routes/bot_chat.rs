use axum::{
    Json,
    extract::{Path, State},
    http::{HeaderMap, StatusCode, Uri},
    response::{IntoResponse, Response},
};
use bcs_service_api::{
    AsyncA2aChatCommand, BlockingA2aChatCommand, BotActor, CallerContext, ChatResponseMode,
    ServiceError,
};
use serde::Deserialize;
use serde_json::Value;
use std::time::Instant;

use crate::chat_digest::{ChatDigestRecord, log_chat_digest};
use crate::state::HttpAppState;

use super::{bot_id_from_headers, container_header_matches};

#[derive(Debug, Deserialize)]
pub struct ChatRequest {
    pub message: String,
    #[serde(default)]
    pub from: Option<String>,
    #[serde(default, alias = "timeoutMs")]
    pub timeout_ms: Option<u64>,
    #[serde(default, alias = "sessionId")]
    pub session_id: Option<String>,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default, alias = "responseMode")]
    pub response_mode: ChatResponseMode,
    #[serde(default, alias = "callerWaitMode")]
    pub caller_wait_mode: Option<String>,
    #[serde(default)]
    pub organization_code: Option<String>,
}

#[derive(Debug)]
pub struct LegacyChatError {
    status: StatusCode,
    message: String,
    error_kind: &'static str,
    digest_success: bool,
}

impl LegacyChatError {
    fn new(
        status: StatusCode,
        message: impl Into<String>,
        error_kind: &'static str,
    ) -> Self {
        Self {
            status,
            message: message.into(),
            error_kind,
            digest_success: false,
        }
    }

    fn business_rejection(
        status: StatusCode,
        message: impl Into<String>,
        error_kind: &'static str,
    ) -> Self {
        Self {
            status,
            message: message.into(),
            error_kind,
            digest_success: true,
        }
    }

    fn invalid_session_token() -> Self {
        Self::new(
            StatusCode::UNAUTHORIZED,
            "Invalid or expired session token",
            "InvalidSessionToken",
        )
    }

    fn bot_not_connected(bot_uuid: &str) -> Self {
        Self::new(
            StatusCode::NOT_FOUND,
            format!("Bot '{}' is not connected via WebSocket", bot_uuid),
            "BotNotConnected",
        )
    }

    fn invalid_request(message: impl Into<String>) -> Self {
        Self::new(
            StatusCode::BAD_REQUEST,
            format!("Invalid request: {}", message.into()),
            "InvalidRequest",
        )
    }
}

impl IntoResponse for LegacyChatError {
    fn into_response(self) -> Response {
        let status = self.status;
        let body = Json(serde_json::json!({
            "error": self.message,
            "status": status.as_u16(),
        }));
        (status, body).into_response()
    }
}

pub async fn bot_chat(
    State(state): State<HttpAppState>,
    Path(bot_uuid): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Json(req): Json<ChatRequest>,
) -> Result<Json<Value>, LegacyChatError> {
    let started = Instant::now();
    let message_len = req.message.len();
    let client_identity = headers
        .get("x-bcs-client")
        .and_then(|v| v.to_str().ok())
        .map(str::to_string);
    let effective_timeout_ms = effective_legacy_chat_timeout_ms(req.timeout_ms);

    let from_bot_id = match resolve_bot_caller(&state, &headers).await {
        Ok(from_bot_id) => from_bot_id,
        Err(err) => {
            log_bot_chat_digest(ChatDigestArgs {
                endpoint: "bot_chat",
                from_bot_id: None,
                target_bot_id: &bot_uuid,
                run_id: None,
                session_id: req.session_id.as_deref(),
                client: client_identity.as_deref(),
                async_mode: false,
                timeout_ms: Some(effective_timeout_ms),
                message_len,
                started,
                success: err.digest_success,
                status_code: err.status,
                error_kind: Some(err.error_kind),
            });
            return Err(err);
        }
    };
    let authenticated_staff_id = authenticated_staff_id(&state, &headers, &uri).await;

    if !container_header_matches(&state, &headers, &from_bot_id) {
        let err = LegacyChatError::invalid_session_token();
        log_bot_chat_digest(ChatDigestArgs {
            endpoint: "bot_chat",
            from_bot_id: Some(&from_bot_id),
            target_bot_id: &bot_uuid,
            run_id: None,
            session_id: req.session_id.as_deref(),
            client: client_identity.as_deref(),
            async_mode: false,
            timeout_ms: Some(effective_timeout_ms),
            message_len,
            started,
            success: err.digest_success,
            status_code: err.status,
            error_kind: Some(err.error_kind),
        });
        return Err(err);
    }

    let run_id = uuid::Uuid::new_v4().to_string();
    let session_key = resolve_session_key(
        req.session_id.as_deref(),
        &run_id,
        client_identity.as_deref(),
        &from_bot_id,
    )
    .map_err(LegacyChatError::invalid_request)
    .map_err(|err| {
        log_bot_chat_digest(ChatDigestArgs {
            endpoint: "bot_chat",
            from_bot_id: Some(&from_bot_id),
            target_bot_id: &bot_uuid,
            run_id: Some(&run_id),
            session_id: req.session_id.as_deref(),
            client: client_identity.as_deref(),
            async_mode: false,
            timeout_ms: Some(effective_timeout_ms),
            message_len,
            started,
            success: err.digest_success,
            status_code: err.status,
            error_kind: Some(err.error_kind),
        });
        err
    })?;
    let run_channel_from = req.from.clone();
    let organization_code = normalize_optional_string(req.organization_code);
    let chat_from_actor_id = Some(req.from.unwrap_or_else(|| "user".to_string()));
    let tags = normalize_tags(req.tags);
    let digest_client = client_identity.clone();

    let outcome = state
        .services
        .a2a_chat_runs
        .run_blocking_chat(BlockingA2aChatCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: from_bot_id.clone(),
            }),
            target_bot_id: bot_uuid.clone(),
            message: req.message,
            from_actor_id: chat_from_actor_id,
            run_channel_from,
            authenticated_staff_id,
            run_id: run_id.clone(),
            session_key: session_key.clone(),
            timeout_ms: effective_timeout_ms,
            client: client_identity,
            tags,
            response_mode: req.response_mode,
            organization_code,
        })
        .await
        .map_err(map_service_error)
        .map_err(|err| {
            log_bot_chat_digest(ChatDigestArgs {
                endpoint: "bot_chat",
                from_bot_id: Some(&from_bot_id),
                target_bot_id: &bot_uuid,
                run_id: Some(&run_id),
                session_id: Some(&session_key),
                client: digest_client.as_deref(),
                async_mode: false,
                timeout_ms: Some(effective_timeout_ms),
                message_len,
                started,
                success: err.digest_success,
                status_code: err.status,
                error_kind: Some(err.error_kind),
            });
            err
        })?;

    log_bot_chat_digest(ChatDigestArgs {
        endpoint: "bot_chat",
        from_bot_id: Some(&from_bot_id),
        target_bot_id: &bot_uuid,
        run_id: Some(&run_id),
        session_id: Some(&outcome.session_id),
        client: digest_client.as_deref(),
        async_mode: false,
        timeout_ms: Some(effective_timeout_ms),
        message_len,
        started,
        success: true,
        status_code: StatusCode::OK,
        error_kind: None,
    });

    Ok(Json(serde_json::json!({
        "delivered": outcome.delivered,
        "bot_uuid": outcome.bot_uuid,
        "session_id": outcome.session_id,
        "response": {
            "content": outcome.content,
        },
    })))
}

pub async fn bot_chat_async(
    State(state): State<HttpAppState>,
    Path(bot_uuid): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Json(req): Json<ChatRequest>,
) -> Result<(StatusCode, Json<Value>), LegacyChatError> {
    let started = Instant::now();
    let message_len = req.message.len();
    let client_identity = headers
        .get("x-bcs-client")
        .and_then(|v| v.to_str().ok())
        .map(str::to_string);
    let timeout_ms = req
        .timeout_ms
        .unwrap_or(state.async_chat_run_timeout_ms)
        .min(24 * 60 * 60 * 1_000);

    let from_bot_id = match resolve_bot_caller(&state, &headers).await {
        Ok(from_bot_id) => from_bot_id,
        Err(err) => {
            log_bot_chat_digest(ChatDigestArgs {
                endpoint: "bot_chat_async",
                from_bot_id: None,
                target_bot_id: &bot_uuid,
                run_id: None,
                session_id: req.session_id.as_deref(),
                client: client_identity.as_deref(),
                async_mode: true,
                timeout_ms: Some(timeout_ms),
                message_len,
                started,
                success: err.digest_success,
                status_code: err.status,
                error_kind: Some(err.error_kind),
            });
            return Err(err);
        }
    };
    let authenticated_staff_id = authenticated_staff_id(&state, &headers, &uri).await;

    let run_id = uuid::Uuid::new_v4().to_string();
    let session_key = resolve_session_key(
        req.session_id.as_deref(),
        &run_id,
        client_identity.as_deref(),
        &from_bot_id,
    )
    .map_err(LegacyChatError::invalid_request)
    .map_err(|err| {
        log_bot_chat_digest(ChatDigestArgs {
            endpoint: "bot_chat_async",
            from_bot_id: Some(&from_bot_id),
            target_bot_id: &bot_uuid,
            run_id: Some(&run_id),
            session_id: req.session_id.as_deref(),
            client: client_identity.as_deref(),
            async_mode: true,
            timeout_ms: Some(timeout_ms),
            message_len,
            started,
            success: false,
            status_code: err.status,
            error_kind: Some(err.error_kind),
        });
        err
    })?;
    let run_channel_from = req.from.clone();
    let organization_code = normalize_optional_string(req.organization_code);
    let chat_from_actor_id = Some(req.from.unwrap_or_else(|| "user".to_string()));
    let tags = normalize_tags(req.tags);
    let caller_wait_mode = normalize_optional_string(req.caller_wait_mode);
    let digest_client = client_identity.clone();
    let accepted = state
        .services
        .a2a_chat_runs
        .start_async_chat(AsyncA2aChatCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: from_bot_id.clone(),
            }),
            target_bot_id: bot_uuid.clone(),
            message: req.message,
            from_actor_id: chat_from_actor_id,
            run_channel_from,
            authenticated_staff_id,
            run_id: run_id.clone(),
            session_key: session_key.clone(),
            timeout_ms,
            client: client_identity,
            tags,
            response_mode: req.response_mode,
            caller_wait_mode,
            organization_code,
        })
        .await
        .map_err(map_service_error)
        .map_err(|err| {
            log_bot_chat_digest(ChatDigestArgs {
                endpoint: "bot_chat_async",
                from_bot_id: Some(&from_bot_id),
                target_bot_id: &bot_uuid,
                run_id: Some(&run_id),
                session_id: Some(&session_key),
                client: digest_client.as_deref(),
                async_mode: true,
                timeout_ms: Some(timeout_ms),
                message_len,
                started,
                success: err.digest_success,
                status_code: err.status,
                error_kind: Some(err.error_kind),
            });
            err
        })?;

    log_bot_chat_digest(ChatDigestArgs {
        endpoint: "bot_chat_async",
        from_bot_id: Some(&from_bot_id),
        target_bot_id: &bot_uuid,
        run_id: Some(&accepted.run_id),
        session_id: Some(&accepted.session_id),
        client: digest_client.as_deref(),
        async_mode: true,
        timeout_ms: Some(timeout_ms),
        message_len,
        started,
        success: true,
        status_code: StatusCode::ACCEPTED,
        error_kind: None,
    });

    Ok((
        StatusCode::ACCEPTED,
        Json(serde_json::json!({
            "run_id": accepted.run_id,
            "bot_uuid": accepted.bot_uuid,
            "session_id": accepted.session_id,
            "status": accepted.status,
            "expires_at_ms": accepted.expires_at_ms,
        })),
    ))
}

fn effective_legacy_chat_timeout_ms(timeout_ms: Option<u64>) -> u64 {
    timeout_ms.unwrap_or(300_000).min(300_000)
}

fn normalize_tags(tags: Vec<String>) -> Vec<String> {
    tags.into_iter()
        .map(|tag| tag.trim().to_string())
        .filter(|tag| !tag.is_empty())
        .collect()
}

fn normalize_optional_string(value: Option<String>) -> Option<String> {
    value
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
}

struct ChatDigestArgs<'a> {
    endpoint: &'a str,
    from_bot_id: Option<&'a str>,
    target_bot_id: &'a str,
    run_id: Option<&'a str>,
    session_id: Option<&'a str>,
    client: Option<&'a str>,
    async_mode: bool,
    timeout_ms: Option<u64>,
    message_len: usize,
    started: Instant,
    success: bool,
    status_code: StatusCode,
    error_kind: Option<&'a str>,
}

fn log_bot_chat_digest(args: ChatDigestArgs<'_>) {
    log_chat_digest(&ChatDigestRecord {
        endpoint: args.endpoint,
        from_bot_id: args.from_bot_id,
        target_bot_id: args.target_bot_id,
        run_id: args.run_id,
        session_id: args.session_id,
        client: args.client,
        async_mode: args.async_mode,
        timeout_ms: args.timeout_ms,
        message_len: args.message_len,
        duration_ms: args.started.elapsed().as_millis(),
        success: args.success,
        status_code: args.status_code,
        error_kind: args.error_kind,
    });
}

fn resolve_session_key(
    session_id: Option<&str>,
    run_id: &str,
    client_identity: Option<&str>,
    from_bot_id: &str,
) -> Result<String, String> {
    if let Some(sid) = session_id {
        let trimmed = sid.trim();
        if trimmed.is_empty() {
            return Err("session_id must not be blank".to_string());
        }
        if trimmed.len() > 128 {
            return Err("session_id too long (max 128 chars)".to_string());
        }
        if !trimmed
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_' || c == ':' || c == '.')
        {
            return Err("session_id may only contain ASCII alphanumerics or - _ : .".to_string());
        }
        return Ok(trimmed.to_string());
    }

    let suffix = &run_id[..8];
    let is_cli = client_identity
        .map(|client| client.starts_with("bcs-cli"))
        .unwrap_or(false);
    if is_cli && !from_bot_id.is_empty() {
        Ok(format!("bcs-cli:{}:{}", from_bot_id, suffix))
    } else if is_cli {
        Ok(format!("bcs-cli:{}", suffix))
    } else {
        Ok(format!("chat:{}", suffix))
    }
}

async fn resolve_bot_caller(
    state: &HttpAppState,
    headers: &HeaderMap,
) -> Result<String, LegacyChatError> {
    bot_id_from_headers(state, headers)
        .await
        .ok_or_else(LegacyChatError::invalid_session_token)
}

async fn authenticated_staff_id(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
) -> Option<String> {
    state
        .user_identity
        .extract(headers, uri)
        .await
        .and_then(|u| u.staff_no)
        .filter(|staff_no| !staff_no.is_empty())
}

fn map_service_error(err: ServiceError) -> LegacyChatError {
    match err {
        ServiceError::BotNotFound(id) => {
            LegacyChatError::new(
                StatusCode::NOT_FOUND,
                format!("Bot not found: {}", id),
                "BotNotFound",
            )
        }
        ServiceError::BotNotRegistered(id) => LegacyChatError::new(
            StatusCode::NOT_FOUND,
            format!("Bot '{}' is not registered", id),
            "BotNotRegistered",
        ),
        ServiceError::BotNotConnected(id) => LegacyChatError::bot_not_connected(&id),
        ServiceError::BotHidden(id) => LegacyChatError::new(
            StatusCode::FORBIDDEN,
            format!("Bot '{}' is not collaborative", id),
            "BotHidden",
        ),
        ServiceError::NotFriends(bot_ids) => LegacyChatError::business_rejection(
            StatusCode::FORBIDDEN,
            not_friends_message(&bot_ids),
            "NotFriends",
        ),
        ServiceError::Unauthorized(message) => {
            LegacyChatError::new(StatusCode::FORBIDDEN, message, "Unauthorized")
        }
        ServiceError::Forbidden(message) => {
            LegacyChatError::new(StatusCode::FORBIDDEN, message, "Forbidden")
        }
        ServiceError::InvalidOperation { message, .. } => {
            LegacyChatError::new(StatusCode::BAD_REQUEST, message, "InvalidOperation")
        }
        ServiceError::InternalError(message) => {
            LegacyChatError::new(
                StatusCode::INTERNAL_SERVER_ERROR,
                message,
                "InternalError",
            )
        }
        other => LegacyChatError::new(
            StatusCode::INTERNAL_SERVER_ERROR,
            other.to_string(),
            "ServiceError",
        ),
    }
}

fn not_friends_message(bot_ids: &[String]) -> String {
    if bot_ids.is_empty() {
        "Bot friendship required".to_string()
    } else {
        format!("Bot friendship required: {}", bot_ids.join(","))
    }
}
