//! Provider-admin Card configuration and explicit refresh.
use axum::{Json, extract::{Path, State}, http::{HeaderMap, StatusCode}, response::{IntoResponse, Response}};
use bcs_service_api::application::a2a_bot::ConfigureA2aBotCommand;
use serde::Deserialize;
use serde_json::json;
use crate::state::HttpAppState;
use bcs_service_api::ServiceError;

fn bearer_token(headers: &HeaderMap) -> Result<String, Response> {
    crate::headers::extract_bearer_token(headers).ok_or_else(|| {
        (StatusCode::UNAUTHORIZED, Json(json!({"error":"valid provider admin token is required"}))).into_response()
    })
}

fn provider_error(error: ServiceError) -> Response {
    let (status, message) = match error {
        ServiceError::Unauthorized(message) => (StatusCode::UNAUTHORIZED, message),
        ServiceError::Forbidden(message) => (StatusCode::FORBIDDEN, message),
        ServiceError::Conflict(message) => (StatusCode::CONFLICT, message),
        ServiceError::InvalidOperation { message, .. } => (StatusCode::BAD_REQUEST, message),
        ServiceError::BotNotFound(_) | ServiceError::BotNotRegistered(_) => (StatusCode::NOT_FOUND, "Bot not found".into()),
        _ => (StatusCode::INTERNAL_SERVER_ERROR, "A2A Card management failed".into()),
    };
    (status, Json(json!({"error":message,"status":status.as_u16()}))).into_response()
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ConfigureRequest {
    pub agent_card_url: Option<String>,
    pub agent_card: Option<serde_json::Value>,
    pub expected_revision: u64,
}

fn now_ms() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
        .map(|v| i64::try_from(v.as_millis()).unwrap_or(i64::MAX)).unwrap_or(0)
}

fn unavailable() -> Response {
    (StatusCode::SERVICE_UNAVAILABLE, Json(json!({"error":"A2A Card management is not configured"}))).into_response()
}

pub async fn configure(
    State(state): State<HttpAppState>, Path((provider_id, bot_uuid)): Path<(String, String)>,
    headers: HeaderMap, Json(request): Json<ConfigureRequest>,
) -> Response {
    let token = match bearer_token(&headers) { Ok(v) => v, Err(e) => return e.into_response() };
    let Some(service) = &state.services.a2a_bot_management else { return unavailable(); };
    let source=match bcs_service_api::types::a2a_bot::A2aCardSource::from_parts(request.agent_card_url,request.agent_card) {
        Ok(Some(source))=>source,
        Ok(None)=>return provider_error(ServiceError::InvalidOperation {message:"agent_card_url or agent_card is required".into(),request_id:None}),
        Err(error)=>return provider_error(error),
    };
    let command = ConfigureA2aBotCommand { provider_id, provider_admin_token: token, bot_uuid,
        source, expected_revision: request.expected_revision, now_ms: now_ms() };
    match service.configure(command).await {
        Ok(record) => Json(record).into_response(),
        Err(error) => provider_error(error).into_response(),
    }
}

pub async fn get(
    State(state): State<HttpAppState>, Path((provider_id, bot_uuid)): Path<(String, String)>, headers: HeaderMap,
) -> Response {
    let token = match bearer_token(&headers) { Ok(v) => v, Err(e) => return e };
    let Some(service) = &state.services.a2a_bot_management else { return unavailable(); };
    match service.get(&provider_id, &token, &bot_uuid).await {
        Ok(record) => Json(record).into_response(),
        Err(error) => provider_error(error),
    }
}

pub async fn refresh(
    State(state): State<HttpAppState>, Path((provider_id, bot_uuid)): Path<(String, String)>, headers: HeaderMap,
) -> Response {
    let token = match bearer_token(&headers) { Ok(v) => v, Err(e) => return e.into_response() };
    let Some(service) = &state.services.a2a_bot_management else { return unavailable(); };
    match service.refresh(&provider_id, &token, &bot_uuid, now_ms()).await {
        Ok(record) => Json(record).into_response(),
        Err(error) => provider_error(error).into_response(),
    }
}
