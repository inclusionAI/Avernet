//! Edge-permission `/collaboration/friend-connections/*` route handlers.
//!
//! These handlers call the Task-2 application traits (`ConnectService`) on
//! `HttpAppState.connect`. Caller resolution reuses `routes::caller`
//! (`caller_actor_id_from_headers`): Bearer bot token first, then human
//! identity (`human_<staff_no>`). The edge-permission service layer owns the
//! final relationship policy and this adapter owns caller impersonation guards.
//!
//! # Wire-shape note
//! Responses are wrapped in the legacy `{success,data}` envelope
//! ([`FriendApiResponse`]) so the existing `bcs-cli` client
//! (`crates/tools/bcs-cli/src/client.rs`, deserializing `FriendApiResponse`)
//! keeps working. The envelope is retired in Phase 5 alongside the old friend
//! graph.
use axum::{
    Json,
    extract::{Path, Query, State},
    http::{HeaderMap, Uri},
};
use bcs_protocol::http::friends::{
    AcceptFriendRequestResponse, CreateFriendRequestBody, CreateFriendRequestResponse,
    DecisionBody, FriendApiResponse, FriendListByActorQuery, FriendListResponse, ListRequestsQuery,
    RevokeFriendResponse, StatusResponse, envelope,
};
use bcs_service_api::application::{ConnectStatus, RequestDirection};

use crate::error::HttpAdapterError;
use crate::state::HttpAppState;

use super::{bots::bot_use_case_error_to_http, caller::caller_actor_id_from_headers};

/// `POST /collaboration/friend-connections/requests` — create a friend connection request.
pub async fn create_friend_request(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Json(body): Json<CreateFriendRequestBody>,
) -> Result<Json<FriendApiResponse>, HttpAdapterError> {
    let from = resolve_caller(&state, &headers, &uri, body.from_actor.as_deref(), body.actor_kind.as_deref()).await?;
    let res = state
        .connect
        .create_connect(&from, &body.to_bot, body.message.clone())
        .await?;
    let status = match res.status {
        ConnectStatus::Pending => "pending",
        ConnectStatus::Approved => "approved",
        ConnectStatus::PublicNoEdge => "public_no_edge",
    };
    Ok(Json(envelope(&CreateFriendRequestResponse {
        request_ids: res.request_ids,
        status: status.into(),
        edge_ids: res.edge_ids,
        auto_accepted: res.auto_accepted,
    })))
}

/// `POST /collaboration/friend-connections/requests/{id}/accept` — approve a pending request.
pub async fn accept_friend_request(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Path(id): Path<u64>,
) -> Result<Json<FriendApiResponse>, HttpAdapterError> {
    let caller = resolve_caller(&state, &headers, &uri, None, None).await?;
    let edge_ids = state.connect.approve(id, &caller).await?;
    Ok(Json(envelope(&AcceptFriendRequestResponse { edge_ids })))
}

/// `POST /collaboration/friend-connections/requests/{id}/reject` — reject a pending request.
pub async fn reject_friend_request(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Path(id): Path<u64>,
    body: Option<Json<DecisionBody>>,
) -> Result<Json<FriendApiResponse>, HttpAdapterError> {
    let caller = resolve_caller(&state, &headers, &uri, None, None).await?;
    // Body is optional: bcs-cli POSTs reject with no body / no content-type.
    let reason = body.and_then(|Json(b)| b.reason);
    state.connect.reject(id, &caller, reason).await?;
    Ok(Json(envelope(&StatusResponse {
        status: "rejected".into(),
    })))
}

/// `POST /collaboration/friend-connections/requests/{id}/cancel` — caller withdraws a pending request.
pub async fn cancel_friend_request(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Path(id): Path<u64>,
) -> Result<Json<FriendApiResponse>, HttpAdapterError> {
    // Caller identity is resolved for auth-area consistency; the caller may
    // only cancel requests they originally created.
    let caller = resolve_caller(&state, &headers, &uri, None, None).await?;
    let req = state.connect.get_request(id).await?;
    if req.created_by != caller {
        return Err(HttpAdapterError::Forbidden(format!(
            "not authorized to cancel request '{}'",
            id
        )));
    }
    state.connect.cancel(id).await?;
    Ok(Json(envelope(&StatusResponse {
        status: "cancelled".into(),
    })))
}

/// `GET /collaboration/friend-connections/requests` — paginated inbox/sent list.
pub async fn list_friend_requests(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Query(q): Query<ListRequestsQuery>,
) -> Result<Json<FriendApiResponse>, HttpAdapterError> {
    let caller = resolve_caller(&state, &headers, &uri, None, None).await?;
    let direction = match q.direction.as_str() {
        "sent" => RequestDirection::Sent,
        "all" => RequestDirection::All,
        _ => RequestDirection::Received,
    };
    let status = q.status.as_deref().and_then(parse_status_filter);
    let page = state
        .connect
        .list_requests(&caller, direction, status, q.page, q.page_size)
        .await?;
    let payload = serde_json::json!({
        "items": page.items,
        "total": page.total,
        "page": page.page,
        "page_size": page.page_size,
    });
    Ok(Json(envelope(&payload)))
}

/// `DELETE /collaboration/friend-connections/{actor}` — unfriend (revoke friend edges only).
pub async fn revoke_friend(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Path(actor): Path<String>,
    _body: Option<Json<DecisionBody>>,
) -> Result<Json<FriendApiResponse>, HttpAdapterError> {
    let caller = resolve_caller(&state, &headers, &uri, None, None).await?;
    // Body optional (bcs-cli sends empty POSTs). The service now returns the
    // actual revoked edge_ids (B4c) rather than a count.
    let revoked_edges = state.connect.revoke_friend(&caller, &actor).await?;
    Ok(Json(envelope(&RevokeFriendResponse { revoked_edges })))
}

/// Friend list for a bot. Kept as a handler helper only; the canonical route is `GET /collaboration/friend-connections?actor=...&actor_kind=bot`.
pub async fn list_friends(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Path(bot_id): Path<String>,
) -> Result<Json<FriendApiResponse>, HttpAdapterError> {
    // Caller identity is resolved for auth-area consistency; the service layer
    // enforces any visibility/ownership rules on listing.
    let _caller = resolve_caller(&state, &headers, &uri, None, None).await?;
    let items = state.connect.list_friends(&bot_id).await?;
    let total = items.len() as u32;
    Ok(Json(envelope(&FriendListResponse { items, total })))
}

/// `GET /collaboration/friend-connections?actor=` — friend list for any actor, human or bot.
pub async fn list_friends_by_actor(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Query(q): Query<FriendListByActorQuery>,
) -> Result<Json<FriendApiResponse>, HttpAdapterError> {
    // Caller identity resolved for auth-area consistency; the service layer
    // enforces visibility/ownership on listing.
    let _caller = resolve_caller(&state, &headers, &uri, None, None).await?;
    let actor = match q.actor_kind.as_deref() {
        Some("human") => format!("human_{}", q.actor),
        _ => q.actor.clone(),
    };
    let items = state.connect.list_friends(&actor).await?;
    let total = items.len() as u32;
    Ok(Json(envelope(&FriendListResponse { items, total })))
}

/// Resolve the caller actor id from request context for friend-connections endpoints.
///
/// friend-connections security model (stricter than old `/friends/*`):
/// 1. **Bearer** (primary): token resolves to a human (`human_<staff>`) or bot
///    identity → authenticated, safe.
/// 2. **from_actor fallback**: only when Bearer resolves AND the caller wants
///    to "act as" a different actor they are allowed to represent. If NO
///    Bearer → reject (401).
///    This closes the unauthenticated-self-declaration hole that the old
///    `/friends/*` Strategy-A fallback allowed.
///
/// Ownership rule: a human bearer may only act as a bot they own
/// (`bot_query.list_bots_by_creator(staff_no)`), and a bot bearer may only act
/// as itself. Any other `from_actor` is rejected with 403.
async fn resolve_caller(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
    from_actor: Option<&str>,
    actor_kind: Option<&str>,
) -> Result<String, HttpAdapterError> {
    let bearer_id = caller_actor_id_from_headers(state, headers, uri).await;

    let Some(bearer) = bearer_id else {
        return Err(HttpAdapterError::Unauthorized(
            "friend-connections endpoints require Bearer authentication; from_actor fallback without Bearer is not allowed".to_string(),
        ));
    };

    let Some(actor_id) = from_actor.filter(|id| !id.is_empty()) else {
        return Ok(bearer);
    };

    let canonical = match actor_kind {
        Some("human") => format!("human_{}", actor_id),
        _ => actor_id.to_string(),
    };

    if canonical == bearer {
        return Ok(canonical);
    }

    if matches!(actor_kind, Some("bot")) && bearer.starts_with("human_") {
        let staff_no = bearer.strip_prefix("human_").unwrap_or(&bearer);
        let owned_bots = state
            .services
            .bot_query
            .list_bots_by_creator(staff_no)
            .await
            .map_err(bot_use_case_error_to_http)?;
        if owned_bots.iter().any(|bot| bot.bot_uuid == canonical) {
            return Ok(canonical);
        }
    }

    Err(HttpAdapterError::Forbidden(format!(
        "not authorized to act as actor '{}'",
        canonical
    )))
}

fn parse_status_filter(s: &str) -> Option<bcs_domain::edge_permission::RequestStatus> {
    use bcs_domain::edge_permission::RequestStatus;
    match s {
        "pending" => Some(RequestStatus::Pending),
        "approved" => Some(RequestStatus::Approved),
        "rejected" => Some(RequestStatus::Rejected),
        "cancelled" => Some(RequestStatus::Cancelled),
        // Legacy "accepted" alias used by older clients.
        "accepted" => Some(RequestStatus::Approved),
        _ => None,
    }
}
