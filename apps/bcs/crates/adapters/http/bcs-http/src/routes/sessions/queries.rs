use axum::{
    Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode, Uri},
    response::{IntoResponse, Response},
};
use serde::Deserialize;
use serde_json::Value;

use bcs_domain::ActorKind;

use crate::routes::group_messages::{GroupChatCaller, resolve_group_chat_caller};
use crate::state::HttpAppState;

use super::{
    human_controls_bot_response, resolve_collector_bot, session_error_to_response, session_to_json,
};

// ---------------------------------------------------------------
// GET /groups/{id}/sessions
// ---------------------------------------------------------------

#[derive(Debug, Deserialize)]
pub struct ListSessionsQuery {
    #[serde(default)]
    pub status: Option<bcs_service_api::SessionStatus>,
    /// Optional search query: filters by session_title substring (case-insensitive).
    #[serde(default)]
    pub q: Option<String>,
    /// Filter sessions by participant (bot_uuid or human actor_id).
    #[serde(default)]
    pub participant: Option<String>,
    /// When true, return only sessions collected by `participant` (requires participant).
    #[serde(default)]
    pub collected: Option<bool>,
    #[serde(default)]
    pub offset: u64,
    #[serde(default = "default_limit")]
    pub limit: u64,
}

fn default_limit() -> u64 {
    20
}

pub async fn list_sessions_for_group(
    State(state): State<HttpAppState>,
    Path(group_id): Path<String>,
    Query(params): Query<ListSessionsQuery>,
    headers: HeaderMap,
    uri: Uri,
) -> Response {
    // collected filter: requires participant, returns only sessions the bot
    // has collected in this group.
    if params.collected == Some(true) {
        let bot_uuid = match params.participant.as_deref() {
            Some(p) => p,
            None => {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(serde_json::json!({
                        "error": "invalid_params",
                        "message": "collected filter requires participant"
                    })),
                )
                    .into_response();
            }
        };
        // Authorization: the caller must be authorized to act as (and therefore
        // view the collections of) the requested `participant` bot. A bot token
        // resolves to itself; a human caller must own the named bot. Without
        // this check any caller could enumerate any bot's collected sessions
        // in any group (BOLA).
        match resolve_collector_bot(&state, &headers, &uri, Some(bot_uuid)).await {
            Ok(authorized_bot) => {
                if authorized_bot != bot_uuid {
                    return (
                        StatusCode::FORBIDDEN,
                        Json(serde_json::json!({
                            "error": "forbidden",
                            "message": format!(
                                "caller is not authorized to view collections for bot {}",
                                bot_uuid
                            )
                        })),
                    )
                        .into_response();
                }
            }
            Err(resp) => return resp,
        }
        let collected_sessions = match state
            .services
            .session_management
            .list_collected_by_group(
                &group_id,
                bot_uuid,
                params.status,
                params.q.as_deref(),
                params.offset,
                params.limit,
            )
            .await
        {
            Ok(v) => v,
            Err(e) => return session_error_to_response(&e),
        };
        let items: Vec<Value> = collected_sessions
            .iter()
            .map(|s| {
                let mut v = session_to_json(s);
                if let Some(obj) = v.as_object_mut() {
                    // This branch only returns collected sessions, so every item
                    // is collected=true. collected_at was populated by the store.
                    obj.insert("collected".into(), Value::Bool(true));
                }
                v
            })
            .collect();
        return Json(serde_json::json!({
            "items": items,
            "group_id": group_id,
        }))
        .into_response();
    }

    let sessions = match state
        .services
        .session_management
        .list_by_group(
            &group_id,
            params.status,
            params.offset,
            params.limit,
            params.q.as_deref(),
            params.participant.as_deref(),
        )
        .await
    {
        Ok(v) => v,
        Err(e) => return session_error_to_response(&e),
    };

    // Resolve caller for visibility filtering. Temp participants (caller
    // added only to a session, not to group.participants) can only see
    // sessions they themselves are in. Formal group members see all.
    //
    // Bug fix #11: Human caller must be expanded to {actor_id, ...owned bots}
    // so a Human who owns a driver-bot in the group is treated as formal
    // (legacy server.rs:12767-12782).
    let caller_actor = resolve_group_chat_caller(&state, &headers, &uri).await.ok();
    let caller_ids: Vec<String> = match &caller_actor {
        Some(GroupChatCaller::Bot { bot_uuid }) => vec![bot_uuid.clone()],
        Some(GroupChatCaller::Human(h)) => {
            // Human caller expands to {actor_id, controlled
            // Bots} (bot-authority plan Task 6.5): the live
            // authority hook decides per candidate Bot; the
            // legacy `list_bots_by_creator` expansion is gone.
            let mut controlled: Vec<String> = Vec::new();
            let mut candidates: Vec<String> = Vec::new();
            if let Some(g) = state.services.group.get(&group_id).await {
                candidates.extend(
                    g.participants
                        .iter()
                        .filter(|p| p.actor_kind == ActorKind::Bot)
                        .map(|p| p.bot_uuid.clone()),
                );
            }
            for session in &sessions {
                candidates.extend(
                    session
                        .participants
                        .iter()
                        .filter(|p| p.actor_kind == ActorKind::Bot)
                        .map(|p| p.bot_uuid.clone()),
                );
            }
            candidates.sort();
            candidates.dedup();
            for bot_id in candidates {
                match human_controls_bot_response(&state, &h.staff_no, &bot_id).await {
                    Ok(true) => controlled.push(bot_id),
                    Ok(false) => {}
                    Err(response) => return response,
                }
            }
            let mut ids = vec![h.actor_id.clone()];
            ids.extend(controlled);
            ids
        }
        None => Vec::new(),
    };
    let is_formal_member = if caller_ids.is_empty() {
        false
    } else if let Some(g) = state.services.group.get(&group_id).await {
        caller_ids
            .iter()
            .any(|id| g.participants.iter().any(|p| p.bot_uuid == *id))
    } else {
        false
    };

    // When participant filter is already applied by the service layer,
    // visibility is implicitly scoped. Otherwise, temp participants
    // only see sessions they belong to.
    let visible: Vec<_> = if is_formal_member || params.participant.is_some() {
        sessions
    } else if !caller_ids.is_empty() {
        sessions
            .into_iter()
            .filter(|s| {
                caller_ids
                    .iter()
                    .any(|id| s.participants.iter().any(|p| p.bot_uuid == *id))
            })
            .collect()
    } else {
        sessions
    };

    let items: Vec<Value> = visible.iter().map(session_to_json).collect();

    // When the request explicitly specifies a participant, surface that
    // participant's per-session collected state on each item: collected (bool)
    // for every item, and collected_at (epoch ms) for the collected ones. When
    // no participant is given, neither field is added.
    let items: Vec<Value> = if let Some(p) = params.participant.as_deref() {
        let ids: Vec<&str> = items
            .iter()
            .filter_map(|v| v.get("id").and_then(|i| i.as_str()))
            .collect();
        let collected_map: std::collections::HashMap<String, u64> = state
            .services
            .session_management
            .collected_at_map(&ids, p)
            .await
            .unwrap_or_default()
            .into_iter()
            .collect();
        items
            .into_iter()
            .map(|mut v| {
                if let Some(obj) = v.as_object_mut() {
                    // Query the map by &str directly (String: Borrow<str>),
                    // avoiding a per-item String allocation.
                    if let Some(sid) = obj.get("id").and_then(|i| i.as_str()) {
                        if let Some(ts) = collected_map.get(sid) {
                            obj.insert("collected".into(), Value::Bool(true));
                            obj.insert("collected_at".into(), Value::from(*ts));
                        } else {
                            obj.insert("collected".into(), Value::Bool(false));
                        }
                    }
                }
                v
            })
            .collect()
    } else {
        items
    };

    Json(serde_json::json!({
        "items": items,
        "group_id": group_id,
    }))
    .into_response()
}

// ---------------------------------------------------------------
// GET /sessions/{sid}
// ---------------------------------------------------------------

pub async fn get_session_by_id(
    State(state): State<HttpAppState>,
    Path(sid): Path<String>,
) -> impl IntoResponse {
    match state.services.session_management.get(&sid).await {
        Ok(Some(mut s)) => {
            bcs_service_api::backfill_participant_names(
                state.services.registry.as_ref(),
                &mut s.participants,
            )
            .await;
            Json(session_to_json(&s)).into_response()
        }
        Ok(None) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"error": "session not found"})),
        )
            .into_response(),
        Err(e) => session_error_to_response(&e),
    }
}
