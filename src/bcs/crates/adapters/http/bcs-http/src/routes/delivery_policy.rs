//! Human-only environment policy management through the configured auth boundary.
use crate::state::HttpAppState;
use axum::{Json, extract::State, http::{HeaderMap, StatusCode, Uri}, response::{IntoResponse, Response}};
use bcs_config_api::message_delivery::DeliveryPolicy;
use bcs_service_api::{HumanActor, CallerContext, ServiceError};
use serde::Deserialize;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplacePolicy {
    expected_version: u64,
    policy: DeliveryPolicy,
}

async fn caller(state: &HttpAppState, headers: &HeaderMap, uri: &Uri) -> Result<CallerContext, StatusCode> {
    // Explicit machine credentials take precedence over local mock identity.
    // Resolving them is only to distinguish authenticated non-Human (403)
    // from invalid credentials (401), never to authorize policy management.
    for name in ["X-BCS-Service-Key", "X-Service-Credential"] {
        if let Some(key) = headers.get(name) {
            return Err(if key.to_str().ok().and_then(|key| state.service_api_keys.resolve(key)).is_some() {
                StatusCode::FORBIDDEN
            } else { StatusCode::UNAUTHORIZED });
        }
    }
    if state.bot_uuid_from_headers(headers).await.is_some() { return Err(StatusCode::FORBIDDEN); }
    if headers.contains_key("X-BCS-Bot-Token") { return Err(StatusCode::UNAUTHORIZED); }
    if let Some(token) = crate::headers::extract_bearer_token(headers) {
        if state.bot_runtime_token_resolver.try_provider_admin(&token).await.is_some() {
            return Err(StatusCode::FORBIDDEN);
        }
    }
    let user_id = if let Some(chain) = &state.auth_chain {
        let principal = chain.authenticate(headers).await.map_err(|_| StatusCode::UNAUTHORIZED)?
            .principal.ok_or(StatusCode::UNAUTHORIZED)?;
        if principal.bot_uuid.is_some() || matches!(principal.source_name.as_deref(), Some("AgentPass" | "SessionToken")) {
            return Err(StatusCode::FORBIDDEN);
        }
        if headers.contains_key("Authorization") && principal.source_name.as_deref() == Some("Local") {
            return Err(StatusCode::UNAUTHORIZED);
        }
        principal.user_id
    } else {
        // The injected Human identity adapter is the same boundary used by
        // group messages. Without a chain, no Bearer may fall back to mock.
        if headers.contains_key("Authorization") { return Err(StatusCode::UNAUTHORIZED); }
        state.user_identity.extract(headers, uri).await.and_then(|identity| identity.staff_no)
    };
    let staff_no = user_id.filter(|id| !id.trim().is_empty()).ok_or(StatusCode::UNAUTHORIZED)?;
    Ok(CallerContext::Human(HumanActor { actor_id: format!("human_{staff_no}"), staff_no }))
}

fn error(error: ServiceError) -> Response {
    let (status, message) = match error {
        ServiceError::Unauthorized(_) => (StatusCode::UNAUTHORIZED, "unauthorized".into()),
        ServiceError::Forbidden(_) => (StatusCode::FORBIDDEN, "forbidden".into()),
        ServiceError::InvalidOperation { message, .. } => {
            let code = if message == "delivery_policy_version_conflict" { StatusCode::CONFLICT } else { StatusCode::BAD_REQUEST };
            (code, message)
        }
        _ => (StatusCode::SERVICE_UNAVAILABLE, "delivery policy unavailable".into()),
    };
    (status, Json(serde_json::json!({"error": message}))).into_response()
}

pub async fn get(State(state): State<HttpAppState>, headers: HeaderMap, uri: Uri) -> Response {
    let caller = match caller(&state, &headers, &uri).await { Ok(c) => c, Err(status) => return status.into_response() };
    match state.services.message_flow.get_delivery_policy(caller).await {
        Ok(value) => Json(value).into_response(), Err(e) => error(e),
    }
}

pub async fn put(State(state): State<HttpAppState>, headers: HeaderMap, uri: Uri, body: Result<Json<ReplacePolicy>, axum::extract::rejection::JsonRejection>) -> Response {
    let caller = match caller(&state, &headers, &uri).await { Ok(c) => c, Err(status) => {
        tracing::warn!(actor_id = "non_human_or_unauthenticated", attempted_at_ms = audit_time_ms(),
            before_version = "unknown", after_version = "unchanged", changed_fields = "unknown", outcome = "authentication_denied", status = status.as_u16(), "delivery policy update audited");
        return status.into_response();
    } };
    let Json(body) = match body { Ok(body) => body, Err(error) => {
        let actor = match &caller { CallerContext::Human(human) => human.actor_id.as_str(), _ => "non_human" };
        tracing::warn!(actor_id = actor, attempted_at_ms = audit_time_ms(), before_version = "unknown", after_version = "unchanged",
            changed_fields = "unknown", outcome = "invalid_payload", "delivery policy update audited");
        return error.into_response();
    } };
    match state.services.message_flow.replace_delivery_policy(caller, body.expected_version, body.policy).await {
        Ok(value) => Json(value).into_response(), Err(e) => error(e),
    }
}

fn audit_time_ms() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_millis() as u64
}
