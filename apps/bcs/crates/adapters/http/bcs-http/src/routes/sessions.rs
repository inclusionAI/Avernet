//! Session HTTP handlers.
//!
//! Phase 2a: 9 endpoints mounted at /groups/{id}/sessions and /sessions/{sid}/...
//! Full auth (caller resolution + 6eb4b6384 permission check) for create/complete
//! is deferred to Task 9.

use axum::{
    Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode, Uri},
    response::{IntoResponse, Response},
};
use serde::Deserialize;
use serde_json::Value;

use bcs_domain::{ActorKind, DeliveryType, HumanMessageView, MessageViewScope, SystemMessageEvent};
use bcs_service_api::{
    CreateSessionLaunch, GroupChatCommand, GroupMessage, ParticipantRole as DomainParticipantRole,
    ReactivateSessionLaunch, RequestedSessionRole, SessionCaller, SessionKind as DomainSessionKind,
    SessionLaunchError, SessionLaunchRequest, StateMachineRunView,
};

use crate::routes::collaboration_runs::collaboration_error_to_response;
use crate::routes::group_messages::{
    GroupChatCaller, application_caller, delivery_results_json, group_chat_caller_context,
    resolve_group_chat_caller,
};
use crate::state::HttpAppState;

// ---------------------------------------------------------------
// helpers
// ---------------------------------------------------------------

/// Serialize a Session with both `id` and `session_id` keys for backward
/// compatibility with test scripts and legacy clients that expect `session_id`.
fn session_to_json(sess: &bcs_service_api::Session) -> Value {
    let mut v = serde_json::to_value(sess).unwrap_or(Value::Null);
    if let Some(obj) = v.as_object_mut() {
        let sid = obj.get("id").and_then(|v| v.as_str().map(str::to_string));
        if let Some(sid) = sid {
            obj.insert("session_id".into(), Value::String(sid));
        }
    }
    v
}

/// Live Human→Bot control judgment for the Session routes
/// (bot-authority plan Task 6.5, spec §8): the legacy
/// `list_bots_by_creator` / `created_by` probes are gone — the
/// routes consult the container-injected authority hook. A
/// failing authority read is a 500, never a deny-as-forbidden.
pub(crate) async fn human_controls_bot(
    state: &HttpAppState,
    staff_no: &str,
    bot_id: &str,
) -> Result<bool, crate::error::HttpAdapterError> {
    match state.services.bot_authority_hook.can_manage(staff_no, bot_id).await {
        Ok(controlled) => Ok(controlled),
        Err(error) => Err(crate::error::HttpAdapterError::Service(
            bcs_service_api::ServiceError::InternalError(format!(
                "bot authority lookup failed: {error}"
            )),
        )),
    }
}

/// `human_controls_bot` for routes that return bare `Response`s.
pub(crate) async fn human_controls_bot_response(
    state: &HttpAppState,
    staff_no: &str,
    bot_id: &str,
) -> Result<bool, Response> {
    human_controls_bot(state, staff_no, bot_id)
        .await
        .map_err(IntoResponse::into_response)
}



/// Check whether the Human identified by `actor_id` / `staff_no` has access
/// to the given session. The Human has access if any of:
///   1. The Human's actor_id is a participant in the session.
///   2. The Human controls at least one bot that is a participant in the
///      session (live authority edge).
///
/// Session participants are the authoritative set for session-scoped access
/// (seeded from the group at creation, then evolving independently); this
/// mirrors `human_has_group_access` but judges membership against
/// `session.participants` rather than `group.participants`.
pub(crate) async fn human_has_session_access(
    state: &HttpAppState,
    session: &bcs_service_api::Session,
    actor_id: &str,
    staff_no: &str,
) -> Result<bool, crate::error::HttpAdapterError> {
    if session.participants.iter().any(|p| p.bot_uuid == actor_id) {
        return Ok(true);
    }
    for participant in session
        .participants
        .iter()
        .filter(|p| p.actor_kind == ActorKind::Bot)
    {
        if human_controls_bot(state, staff_no, &participant.bot_uuid).await? {
            return Ok(true);
        }
    }
    Ok(false)
}

pub fn session_error_to_response(err: &bcs_service_api::SessionUseCaseError) -> Response {
    let (code, msg) = match err {
        bcs_service_api::SessionUseCaseError::InvalidParams(s) => (StatusCode::BAD_REQUEST, s.clone()),
        bcs_service_api::SessionUseCaseError::NotFound(s) => (StatusCode::NOT_FOUND, s.clone()),
        bcs_service_api::SessionUseCaseError::Conflict(s) => (StatusCode::CONFLICT, s.clone()),
        bcs_service_api::SessionUseCaseError::Internal(bcs_service_api::ServiceError::Forbidden(message)) => {
            (StatusCode::FORBIDDEN, message.clone())
        }
        bcs_service_api::SessionUseCaseError::Internal(e) => {
            (StatusCode::INTERNAL_SERVER_ERROR, e.to_string())
        }
    };
    (code, Json(serde_json::json!({"error": msg}))).into_response()
}

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
    Query(query): Query<super::groups::DeleteSessionQuery>,
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
                    session_id = %sid,
                    deleted = n,
                    "cleaned up session files after session delete"
                ),
                Err(e) => tracing::warn!(
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

async fn resolve_collector_bot(
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

#[cfg(test)]
mod tests {
    // Session message history tests are in bcs-message-flow/src/group_history.rs
    // alongside the session_history_request_params and resolve_session_history_source_bots
    // functions that power the service layer.
}

mod launch;
pub use launch::{CreateSessionRequest, create_session_for_group};
mod participants;
pub use participants::{
    AddParticipantRequest, UpdateParticipantModeRequest, add_session_participant,
    remove_session_participant, update_session_participant_mode,
};
mod conversation;
pub use conversation::{GetSessionMessagesQuery, SessionChatRequest, get_session_messages, session_chat};
