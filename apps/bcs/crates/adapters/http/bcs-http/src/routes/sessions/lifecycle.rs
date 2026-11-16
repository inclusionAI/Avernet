use axum::{
    Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode, Uri},
    response::IntoResponse,
};
use serde::Deserialize;
use serde_json::Value;

use crate::routes::group_messages::{GroupChatCaller, resolve_group_chat_caller};
use crate::state::HttpAppState;

use super::{human_controls_bot_response, session_error_to_response, session_to_json};

// ---------------------------------------------------------------
// PATCH /sessions/{sid}
// ---------------------------------------------------------------

#[derive(Debug, Deserialize)]
pub struct PatchSessionRequest {
    #[serde(default)]
    pub session_title: Option<String>,
}

pub async fn patch_session(
    State(state): State<HttpAppState>,
    Path(sid): Path<String>,
    Json(body): Json<PatchSessionRequest>,
) -> impl IntoResponse {
    if let Some(title) = body.session_title {
        match state
            .services
            .session_management
            .update_title(&sid, Some(title))
            .await
        {
            Ok(s) => return Json(s).into_response(),
            Err(e) => return session_error_to_response(&e),
        }
    }
    // No fields to patch — return current session
    match state.services.session_management.get(&sid).await {
        Ok(Some(s)) => Json(session_to_json(&s)).into_response(),
        Ok(None) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"error": "session not found"})),
        )
            .into_response(),
        Err(e) => session_error_to_response(&e),
    }
}

// ---------------------------------------------------------------
// POST /sessions/{sid}/complete
//
// Auth (legacy server.rs:13115-13157):
//   - Caller must be authenticated (Bot token or Human cookie); 401 otherwise.
//   - ServiceInvocation sessions are rejected here; 403 with hint to use the
//     /services/* endpoint.
//   - Caller must be the parent group's lead/driver (driver bot itself or
//     Human owner of the driver bot); 403 otherwise.
// ---------------------------------------------------------------

pub async fn complete_session(
    State(state): State<HttpAppState>,
    Path(sid): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    // 1. Authenticate
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(c) => c,
        Err(_) => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"error": "unauthorized"})),
            )
                .into_response();
        }
    };

    // 2. Look up session
    let sess = match state.services.session_management.get(&sid).await {
        Ok(Some(s)) => s,
        Ok(None) => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({
                    "error": "not_found",
                    "message": format!("session {} not found", sid)
                })),
            )
                .into_response();
        }
        Err(e) => return session_error_to_response(&e),
    };

    // 3. Service-invocation sessions must use /services/*
    if matches!(
        sess.session_kind,
        bcs_service_api::SessionKind::ServiceInvocation
    ) {
        return (
            StatusCode::FORBIDDEN,
            Json(serde_json::json!({
                "error": "forbidden",
                "message": "service sessions cannot be completed via this endpoint"
            })),
        )
            .into_response();
    }

    // 4. Look up parent group for driver check
    let group = match state.services.group.get(&sess.group_id).await {
        Some(g) => g,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({
                    "error": "not_found",
                    "message": "parent group not found"
                })),
            )
                .into_response();
        }
    };

    // 5. Caller must be driver (bot itself, or Human who controls the
    //    driver bot through the live authority edge)
    let is_driver = match &caller {
        GroupChatCaller::Bot { bot_uuid } => group.driver_bot == *bot_uuid,
        GroupChatCaller::Human(h) => {
            let controlled = match human_controls_bot_response(&state, &h.staff_no, &group.driver_bot).await {
                Ok(v) => v,
                Err(response) => return response,
            };
            h.actor_id == group.driver_bot || controlled
        }
    };
    if !is_driver {
        return (
            StatusCode::FORBIDDEN,
            Json(serde_json::json!({
                "error": "forbidden",
                "message": "only driver can complete a chat session"
            })),
        )
            .into_response();
    }

    // 6. Complete via CAS
    let output = body.get("output").cloned();
    let error = body.get("error").and_then(|v| v.as_str().map(String::from));

    match state
        .services
        .session_management
        .complete_if_running(&sid, output, error)
        .await
    {
        Ok(Some(session)) => {
            Json(session).into_response()
        }
        Ok(None) => Json(serde_json::json!({"already_completed": true})).into_response(),
        Err(e) => session_error_to_response(&e),
    }
}

// ---------------------------------------------------------------
// DELETE /sessions/{sid}
//
// Auth: bot_id query param must be the session creator, the group's driver
// bot, or — for a human caller — a bot they own that is the session creator
// or the group's driver bot.
// ---------------------------------------------------------------

pub async fn delete_session(
    State(state): State<HttpAppState>,
    Path(sid): Path<String>,
    Query(query): Query<crate::routes::groups::DeleteSessionQuery>,
) -> impl IntoResponse {
    let sess = match state.services.session_management.get(&sid).await {
        Ok(Some(s)) => s,
        Ok(None) => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({"error": "not_found", "message": format!("session {} not found", sid)})),
            ).into_response();
        }
        Err(e) => return session_error_to_response(&e),
    };

    let caller_id = &query.bot_id;
    let session_creator = sess.created_by.as_deref().unwrap_or("");
    let driver_bot = state
        .services
        .group
        .get(&sess.group_id)
        .await
        .map(|g| g.driver_bot);
    let driver_bot = driver_bot.as_deref().unwrap_or("");

    // The session creator, the driver bot, or a human who CONTROLS
    // the creator or driver bot (live authority hook; bot-authority
    // plan Task 6.5) may delete the session.
    let authorized = if caller_id == session_creator || caller_id == driver_bot {
        true
    } else if caller_id.starts_with("human_") {
        let staff_no = caller_id.trim_start_matches("human_");
        if !session_creator.is_empty() {
            match human_controls_bot_response(&state, staff_no, session_creator).await {
                Ok(true) => true,
                Ok(false) => !driver_bot.is_empty()
                    && match human_controls_bot_response(&state, staff_no, driver_bot).await {
                        Ok(v) => v,
                        Err(response) => return response,
                    },
                Err(response) => return response,
            }
        } else {
            !driver_bot.is_empty()
                && match human_controls_bot_response(&state, staff_no, driver_bot).await {
                    Ok(v) => v,
                    Err(response) => return response,
                }
        }
    } else {
        false
    };

    if !authorized {
        return (
            StatusCode::FORBIDDEN,
            Json(serde_json::json!({
                "error": "forbidden",
                "message": format!("caller {} is not authorized to delete this session", caller_id)
            })),
        )
            .into_response();
    }

    match state.services.session_management.delete(&sid).await {
        Ok(true) => {
            // Best-effort session-file cleanup: count logged on success, error
            // logged but not fatal — orphan sweep reconciles later. MUST NOT
            // fail the session-delete response.
            match state
                .services
                .session_files
                .delete_all_for_session(&sid)
                .await
            {
                Ok(n) => tracing::info!(
                    target: "bcs_http::routes::sessions",
                    session_id = %sid,
                    deleted = n,
                    "cleaned up session files after session delete"
                ),
                Err(e) => tracing::warn!(
                    target: "bcs_http::routes::sessions",
                    request_id = %bcs_observability::CurrentRequestId,
                    error = ?e,
                    session_id = %sid,
                    "session file cleanup partial failure (orphan sweep will reconcile)"
                ),
            }
            Json(serde_json::json!({"deleted": true, "session_id": sid})).into_response()
        }
        Ok(false) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"error": "not_found", "message": "session not found"})),
        )
            .into_response(),
        Err(e) => session_error_to_response(&e),
    }
}
