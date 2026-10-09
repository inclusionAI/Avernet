use axum::{
    Json,
    extract::State,
    http::{HeaderMap, StatusCode, Uri},
    response::{IntoResponse, Response},
};
use bcs_service_api::{CurrentHumanActorCommand, EnsureCurrentHumanActorError};
use serde_json::Value;

use crate::state::HttpAppState;

pub async fn me_identity(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
) -> Json<Value> {
    let user = state.user_identity.extract(&headers, &uri).await;
    let staff_no = user
        .as_ref()
        .and_then(|u| u.staff_no.as_deref())
        .map(str::to_string);
    let nick_name = user
        .as_ref()
        .and_then(|u| u.nick_name.as_deref())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string);
    let actor_uuid = staff_no.as_deref().map(|s| format!("human_{}", s));

    Json(serde_json::json!({
        "user_id": staff_no.clone(),
        "staff_no": staff_no,
        "nick_name": nick_name,
        "actor_uuid": actor_uuid,
    }))
}

pub async fn me_repair_info(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
) -> Response {
    let command = current_human_actor_command(&state, &headers, &uri).await;
    Json(
        state
            .services
            .human_actors
            .repair_human_actor_info(command)
            .await,
    )
    .into_response()
}

pub async fn ensure_mine(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
) -> Response {
    let command = current_human_actor_command(&state, &headers, &uri).await;
    match state
        .services
        .human_actors
        .ensure_current_human_actor(command)
        .await
    {
        Ok(result) => Json(result).into_response(),
        Err(error) => ensure_current_human_actor_error_response(error),
    }
}

async fn current_human_actor_command(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
) -> CurrentHumanActorCommand {
    let user = state.user_identity.extract(headers, uri).await;
    CurrentHumanActorCommand {
        staff_no: user
            .as_ref()
            .and_then(|identity| identity.staff_no.as_deref())
            .map(str::to_string),
        nick_name: user
            .as_ref()
            .and_then(|identity| identity.nick_name.as_deref())
            .map(str::to_string),
    }
}

fn ensure_current_human_actor_error_response(error: EnsureCurrentHumanActorError) -> Response {
    let (status, body) = match error {
        EnsureCurrentHumanActorError::LoginRequired => (
            StatusCode::UNAUTHORIZED,
            serde_json::json!({
                "error": "需要登录态，请携带有效的 Buservice cookie 后重试",
                "status": StatusCode::UNAUTHORIZED.as_u16(),
            }),
        ),
        EnsureCurrentHumanActorError::InvalidStaffNo => (
            StatusCode::BAD_REQUEST,
            serde_json::json!({
                "error": "staff_no 格式错误：仅允许 ASCII 字母和数字",
                "status": StatusCode::BAD_REQUEST.as_u16(),
            }),
        ),
        EnsureCurrentHumanActorError::EnsureHumanActorFailed(error) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            serde_json::json!({
                "error": format!("ensure_human_actor failed: {}", error),
                "status": StatusCode::INTERNAL_SERVER_ERROR.as_u16(),
            }),
        ),
        EnsureCurrentHumanActorError::ListLegacyBotsForOwnerFailed(error) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            serde_json::json!({
                "error": format!("list_legacy_bots_for_owner failed: {}", error),
                "status": StatusCode::INTERNAL_SERVER_ERROR.as_u16(),
            }),
        ),
        EnsureCurrentHumanActorError::AllMatchedBotsFailed {
            failed_bots,
            errors,
        } => (
            StatusCode::INTERNAL_SERVER_ERROR,
            serde_json::json!({
                "error": "all matched bots failed edge creation",
                "status": StatusCode::INTERNAL_SERVER_ERROR.as_u16(),
                "failed_bots": failed_bots,
                "errors": errors,
            }),
        ),
    };
    (status, Json(body)).into_response()
}
