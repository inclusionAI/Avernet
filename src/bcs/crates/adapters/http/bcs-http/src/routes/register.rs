use axum::{
    Json,
    extract::{Query, State},
    http::{HeaderMap, StatusCode, Uri},
    response::{IntoResponse, Response},
};
use bcs_domain::{RegisterTokenPayload, RegisterTokenError, register_token_encode, register_token_decode_and_verify};
use bcs_service_api::{
    AdminBotOnboardCommand, BotConnectCommand, OnboardActorIdentity,
};
use serde::Deserialize;

use crate::state::HttpAppState;

const REGISTER_TOKEN_TTL_SECONDS: u64 = 21600; // 6 hours

// ---------------------------------------------------------------
// GET /register/token
// ---------------------------------------------------------------

pub async fn get_register_token(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
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

    let human_id = format!("human_{}", staff_no);
    let now_secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let exp = now_secs + REGISTER_TOKEN_TTL_SECONDS;

    let payload = RegisterTokenPayload { v: 1, id: human_id, exp };
    let token = register_token_encode(&payload, &state.invite_token_secret);

    Json(serde_json::json!({
        "token": token,
        "expires_at": exp * 1000,
        "note": "Use this token for bot registration within 6 hours"
    })).into_response()
}

// ---------------------------------------------------------------
// POST /register?token=xxx&bot-name=yyy
// ---------------------------------------------------------------

#[derive(Debug, Deserialize)]
pub struct RegisterQuery {
    pub token: Option<String>,
    #[serde(alias = "bot-name")]
    pub bot_name: Option<String>,
}

pub async fn register_bot(
    State(state): State<HttpAppState>,
    Query(query): Query<RegisterQuery>,
) -> Response {
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

    let bot_name = match query.bot_name.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        Some(n) => n.to_string(),
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({"error": "bad_request", "message": "missing required parameter: bot-name"})),
            ).into_response();
        }
    };

    // 2. Validate bot-name length
    let name_len = bot_name.chars().count();
    if name_len < 2 || name_len > 64 {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "error": "bad_request",
                "message": format!("bot-name must be 2-64 characters, got {}", name_len)
            })),
        ).into_response();
    }

    // 3. Decode and verify token
    let payload = match register_token_decode_and_verify(token_str, &state.invite_token_secret) {
        Ok(p) => p,
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

    // 4. Extract staff_no from payload.id ("human_xxx" -> "xxx")
    let staff_no = payload.id.trim_start_matches("human_").to_string();

    // 5. Connect bot (creates new bot_uuid + bot_token)
    let connect_result = match state
        .services
        .bot_management
        .connect_bot(BotConnectCommand {
            caller_actor_id: None,
            token: None,
            bot_id: None,
            protocol_version: None,
        })
        .await
    {
        Ok(r) => r,
        Err(e) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"error": "internal", "message": format!("bot connect failed: {}", e)})),
            ).into_response();
        }
    };

    // 6. Onboard bot (set name + establish ownership)
    let onboard_result = state
        .services
        .bot_onboarding
        .admin_onboard_bot(AdminBotOnboardCommand {
            bot_uuid: connect_result.bot_uuid.clone(),
            name: Some(bot_name.clone()),
            summary: None,
            domains: vec![],
            skills: vec![],
            scopes: vec![],
            binding_channels: None,
            actor_identity: Some(OnboardActorIdentity {
                staff_no,
                nick_name: None,
            }),
        })
        .await;

    if let Err(e) = onboard_result {
        tracing::warn!(
            bot_uuid = %connect_result.bot_uuid,
            error = %e,
            "register: admin_onboard_bot failed after connect"
        );
    }

    // 7. Return credentials
    Json(serde_json::json!({
        "bot_name": bot_name,
        "bot_uuid": connect_result.bot_uuid,
        "bot_token": connect_result.token,
    })).into_response()
}
