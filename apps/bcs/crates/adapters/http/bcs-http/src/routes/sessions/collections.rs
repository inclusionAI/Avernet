use axum::{
    Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode, Uri},
    response::{IntoResponse, Response},
};
use serde::Deserialize;

use crate::routes::group_messages::{GroupChatCaller, resolve_group_chat_caller};
use crate::state::HttpAppState;

use super::{human_controls_bot_response, session_error_to_response};

// ---------------------------------------------------------------
// POST /sessions/{sid}/collect
// DELETE /sessions/{sid}/collect
//
// Mark / unmark a session as collected by a bot. Caller resolves via
// resolve_group_chat_caller (bot token -> that bot; human cookie -> must
// supply an owned bot via the `participant` body/query field, ownership
// checked via registry.list_bots_by_creator).
// ---------------------------------------------------------------

#[derive(Debug, Deserialize, Default)]
pub struct CollectSessionRequest {
    /// Target bot that performs the collect. Required for human callers;
    /// ignored (defaults to the calling bot) when a bot token is used.
    #[serde(default)]
    pub participant: Option<String>,
}

pub(super) async fn resolve_collector_bot(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
    participant: Option<&str>,
) -> Result<String, Response> {
    let caller = match resolve_group_chat_caller(state, headers, uri).await {
        Ok(c) => c,
        Err(_) => {
            return Err((
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"error": "unauthorized"})),
            )
                .into_response());
        }
    };
    match caller {
        GroupChatCaller::Bot { bot_uuid } => Ok(bot_uuid),
        GroupChatCaller::Human(h) => {
            let bot_uuid = participant.ok_or_else(|| {
                (
                    StatusCode::BAD_REQUEST,
                    Json(serde_json::json!({
                        "error": "invalid_params",
                        "message": "human caller must supply participant"
                    })),
                )
                    .into_response()
            })?;
            let owns = if bot_uuid == h.actor_id {
                // §3.3 Human self right: the caller's
                // OWN `human_{id}` actor (collect /
                // collect-listing) passes through its
                // self identity — there are NO
                // Human→Human authority edges, so the
                // control-hook leg must not gate it.
                // This mirrors the V1 collect seam
                // (`load_session_for_collection`), and
                // a different Human actor still falls
                // through to the control edge below.
                true
            } else if bot_uuid.starts_with("human_") {
                false
            } else {
                match human_controls_bot_response(&state, &h.staff_no, bot_uuid).await {
                    Ok(v) => v,
                    Err(response) => return Err(response),
                }
            };
            if !owns {
                return Err((
                    StatusCode::FORBIDDEN,
                    Json(serde_json::json!({
                        "error": "forbidden",
                        "message": format!("caller does not own bot {}", bot_uuid)
                    })),
                )
                    .into_response());
            }
            Ok(bot_uuid.to_string())
        }
    }
}

pub async fn collect_session(
    State(state): State<HttpAppState>,
    Path(sid): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Json(body): Json<CollectSessionRequest>,
) -> impl IntoResponse {
    let collector =
        match resolve_collector_bot(&state, &headers, &uri, body.participant.as_deref()).await {
            Ok(b) => b,
            Err(resp) => return resp,
        };
    match state
        .services
        .session_management
        .collect(&sid, &collector)
        .await
    {
        Ok(()) => Json(serde_json::json!({"collected": true, "session_id": sid})).into_response(),
        Err(e) => session_error_to_response(&e),
    }
}

pub async fn uncollect_session(
    State(state): State<HttpAppState>,
    Path(sid): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Query(body): Query<CollectSessionRequest>,
) -> impl IntoResponse {
    let collector =
        match resolve_collector_bot(&state, &headers, &uri, body.participant.as_deref()).await {
            Ok(b) => b,
            Err(resp) => return resp,
        };
    match state
        .services
        .session_management
        .uncollect(&sid, &collector)
        .await
    {
        Ok(()) => Json(serde_json::json!({"collected": false, "session_id": sid})).into_response(),
        Err(e) => session_error_to_response(&e),
    }
}
