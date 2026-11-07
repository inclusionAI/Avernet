use axum::{
    Json,
    extract::rejection::QueryRejection,
    extract::{Query, State},
    http::{HeaderMap, StatusCode, Uri},
    response::{IntoResponse, Response},
};
use bcs_domain::{RegisterTokenPayload, RegisterTokenError, register_token_encode, register_token_decode_and_verify};
use bcs_service_api::application::v1::{ApplicationError, RegisterBot};
use serde::Deserialize;

use crate::state::HttpAppState;

mod scoped;

const REGISTER_TOKEN_TTL_SECONDS: u64 = 21600; // 6 hours

// ---------------------------------------------------------------
// GET /register/token
// ---------------------------------------------------------------

#[derive(Debug, Deserialize)]
pub struct RegisterTokenQuery {
    pub provider_id: Option<String>,
}

pub async fn get_register_token(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    query: Result<Query<RegisterTokenQuery>, QueryRejection>,
) -> Response {
    let identity = match state.user_identity.extract(&headers, &uri).await {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"error": "unauthorized", "message": "human login required"})),
            ).into_response();
        }
    };

    let staff_no = match identity.staff_no.filter(|s| !s.is_empty()) {
        Some(s) => s,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"error": "unauthorized", "message": "cannot resolve human identity"})),
            ).into_response();
        }
    };

    let Query(query) = match query {
        Ok(query) => query,
        Err(_) => return scoped::error_response(
            ApplicationError::invalid(
                "invalid_request", "invalid registration query",
            ),
        ),
    };
    if let Some(provider_id) = query.provider_id {
        return scoped::issue_token(&state, staff_no, identity.nick_name, provider_id).await;
    }

    let human_id = format!("human_{}", staff_no);
    let now_secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let exp = now_secs + REGISTER_TOKEN_TTL_SECONDS;

    let payload = RegisterTokenPayload { v: 1, id: human_id, exp };
    let token = register_token_encode(&payload, &state.invite_token_secret);

    scoped::success_response(serde_json::json!({
        "token": token,
        "expires_at": exp * 1000,
        "note": "Use this token for bot registration within 6 hours"
    }))
}

// ---------------------------------------------------------------
// POST /register?token=xxx&bot-name=yyy
// ---------------------------------------------------------------

#[derive(Debug, Deserialize)]
pub struct RegisterQuery {
    pub token: Option<String>,
    #[serde(alias = "bot-name")]
    pub bot_name: Option<String>,
    pub agent_card_url: Option<String>,
}

#[derive(Default,serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct RegisterBody {
    agent_card:Option<serde_json::Value>,
    agent_card_url:Option<String>,
    #[serde(alias="bot-name")]
    bot_name:Option<String>,
}

pub async fn register_bot(
    State(state): State<HttpAppState>,
    uri: Uri,
    Query(query): Query<RegisterQuery>,
    body:axum::body::Bytes,
) -> Response {
    let body=if body.is_empty() {RegisterBody::default()} else {
        match serde_json::from_slice::<RegisterBody>(&body) {
            Ok(body)=>body,
            Err(_)=>return scoped::error_response(ApplicationError::invalid("invalid_request","invalid registration JSON body")),
        }
    };
    if (query.agent_card_url.is_some() && body.agent_card_url.is_some()) || (query.bot_name.is_some() && body.bot_name.is_some()) {
        return scoped::error_response(ApplicationError::invalid("invalid_request","duplicate registration fields in query and body"));
    }
    let source=match bcs_service_api::types::a2a_bot::A2aCardSource::from_parts(query.agent_card_url.clone().or(body.agent_card_url),body.agent_card) {
        Ok(source)=>source,Err(error)=>return scoped::error_response(ApplicationError::invalid("invalid_request",error.to_string())),
    };
    let supplied_name=query.bot_name.clone().or(body.bot_name);
    // 1. Validate required params
    let token_str = match query.token.as_deref().filter(|s| !s.is_empty()) {
        Some(t) => t,
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({"error": "bad_request", "message": "missing required parameter: token"})),
            ).into_response();
        }
    };

    let bot_name = match supplied_name.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        Some(n) => n.to_string(),
        None if source.is_some() && supplied_name.is_none()=>String::new(),
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({"error": "bad_request", "message": "missing required parameter: bot-name"})),
            ).into_response();
        }
    };

    // 2. Validate bot-name length
    let name_len = bot_name.chars().count();
    if (!bot_name.is_empty() || source.is_none()) && (name_len < 2 || name_len > 64) {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "error": "bad_request",
                "message": format!("bot-name must be 2-64 characters, got {}", name_len)
            })),
        ).into_response();
    }

    // 3. Decode and verify token
    let _payload = match register_token_decode_and_verify(token_str, &state.invite_token_secret) {
        Ok(p) => p,
        Err(RegisterTokenError::UnsupportedVersion)
            if bcs_domain::provider_registration_token::decode_and_verify(
                token_str, &state.invite_token_secret,
            ).is_ok() =>
        {
            return scoped::register_bot(&state, &uri, token_str.to_string(), bot_name,source).await;
        }
        Err(e) => {
            let message = match &e {
                RegisterTokenError::Expired => "register token has expired",
                RegisterTokenError::InvalidSignature => "invalid register token signature",
                RegisterTokenError::InvalidEncoding => "invalid register token encoding",
                RegisterTokenError::UnsupportedVersion => "unsupported register token version",
                RegisterTokenError::NotHumanToken => "token is not a human registration token",
                RegisterTokenError::MalformedPayload(_) => "malformed register token",
            };
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"error": "unauthorized", "message": message})),
            ).into_response();
        }
    };

    if source.is_some() {
        return scoped::error_response(ApplicationError::invalid(
            "invalid_request", "A2A registration requires a Provider-scoped v2 token",
        ));
    }

    // 4. Delegate the v1 trusted registration to the shared
    //    facade. The legacy adapter strips the historically
    //    unknown mode/provider_bot_ref/webhook_url fields
    //    BEFORE the facade (they are never parsed here), the
    //    facade verifies the token expiry itself, and the
    //    register application commits the trusted chain. There
    //    is no connect→onboard orchestration and no
    //    warn-and-succeed any more (bot authority spec §10.1).
    let Some(service) = state.register_application.as_deref() else {
        return scoped::error_response(ApplicationError::internal(
            "registration service is not configured",
        ));
    };
    match service
        .register_bot(RegisterBot {
            // Legacy v1 tolerance: mode/provider_bot_ref/
            // webhook_url are ignored and never forwarded,
            // even when they cannot parse as the Provider
            // mode enum.
            token: token_str.to_string(),
            bot_name,
            mode: None,
            provider_bot_ref: None,
            webhook_url: None,
        })
        .await
    {
        Ok(bot) => scoped::success_response(serde_json::json!({
            "bot_name": bot.bot_name,
            "bot_uuid": bot.bot_uuid,
            "bot_token": bot.bot_token,
        })),
        Err(error) => scoped::error_response(error),
    }
}
