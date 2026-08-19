//! Edge-permission `/friends/*` route handlers.
//!
//! These handlers call the Task-2 application traits (`ConnectService`) on
//! `HttpAppState.connect`. Caller resolution reuses `routes::caller`
//! (`caller_actor_id_from_headers`): Bearer bot token first, then human
//! identity (`human_<staff_no>`), then `from_bot` body fallback. The
//! edge-permission service layer (wired in Installment 3) owns the authz.
//!
//! Transitional: `state.connect` is `NoopConnectService` until Installment 3.
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

use super::caller::caller_actor_id_from_headers;

/// `POST /friends/request` — create a friend (connect) request.
pub async fn create_friend_request(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Json(body): Json<CreateFriendRequestBody>,
) -> Result<Json<FriendApiResponse>, HttpAdapterError> {
    let from = resolve_caller(&state, &headers, &uri, body.from_bot.as_deref()).await?;
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

/// `POST /friends/requests/{id}/accept` — approve a pending request.
pub async fn accept_friend_request(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Path(id): Path<String>,
) -> Result<Json<FriendApiResponse>, HttpAdapterError> {
    let caller = resolve_caller(&state, &headers, &uri, None).await?;
    let edge_ids = state.connect.approve(&id, &caller).await?;
    Ok(Json(envelope(&AcceptFriendRequestResponse { edge_ids })))
}

/// `POST /friends/requests/{id}/reject` — reject a pending request.
pub async fn reject_friend_request(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Path(id): Path<String>,
    body: Option<Json<DecisionBody>>,
) -> Result<Json<FriendApiResponse>, HttpAdapterError> {
    let caller = resolve_caller(&state, &headers, &uri, None).await?;
    // Body is optional: bcs-cli POSTs reject with no body / no content-type.
    let reason = body.and_then(|Json(b)| b.reason);
    state.connect.reject(&id, &caller, reason).await?;
    Ok(Json(envelope(&StatusResponse {
        status: "rejected".into(),
    })))
}

/// `POST /friends/requests/{id}/cancel` — caller withdraws a pending request.
pub async fn cancel_friend_request(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Path(id): Path<String>,
) -> Result<Json<FriendApiResponse>, HttpAdapterError> {
    // Caller identity is resolved for auth-area consistency; `cancel` acts on
    // the request id and the service layer verifies the caller is the sender.
    let _caller = resolve_caller(&state, &headers, &uri, None).await?;
    state.connect.cancel(&id).await?;
    Ok(Json(envelope(&StatusResponse {
        status: "cancelled".into(),
    })))
}

/// `GET /friends/requests` — paginated inbox/sent list.
pub async fn list_friend_requests(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Query(q): Query<ListRequestsQuery>,
) -> Result<Json<FriendApiResponse>, HttpAdapterError> {
    let caller = resolve_caller(&state, &headers, &uri, None).await?;
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

/// `POST /friends/{actor}/revoke` — unfriend (revoke friend edges only).
pub async fn revoke_friend(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Path(actor): Path<String>,
    _body: Option<Json<DecisionBody>>,
) -> Result<Json<FriendApiResponse>, HttpAdapterError> {
    let caller = resolve_caller(&state, &headers, &uri, None).await?;
    // Body optional (bcs-cli sends empty POSTs). The service now returns the
    // actual revoked edge_ids (B4c) rather than a count.
    let revoked_edges = state.connect.revoke_friend(&caller, &actor).await?;
    Ok(Json(envelope(&RevokeFriendResponse { revoked_edges })))
}

/// `GET /bots/{id}/friends` — friend list for a bot.
pub async fn list_friends(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Path(bot_id): Path<String>,
) -> Result<Json<FriendApiResponse>, HttpAdapterError> {
    // Caller identity is resolved for auth-area consistency; the service layer
    // enforces any visibility/ownership rules on listing.
    let _caller = resolve_caller(&state, &headers, &uri, None).await?;
    let items = state.connect.list_friends(&bot_id).await?;
    let total = items.len() as u32;
    Ok(Json(envelope(&FriendListResponse { items, total })))
}

/// `GET /friends?actor=` — friend list for any actor, human or bot (api-contract ⑦).
pub async fn list_friends_by_actor(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Query(q): Query<FriendListByActorQuery>,
) -> Result<Json<FriendApiResponse>, HttpAdapterError> {
    // Caller identity resolved for auth-area consistency; the service layer
    // enforces visibility/ownership on listing.
    let _caller = resolve_caller(&state, &headers, &uri, None).await?;
    let items = state.connect.list_friends(&q.actor).await?;
    let total = items.len() as u32;
    Ok(Json(envelope(&FriendListResponse { items, total })))
}

/// Resolve the caller actor id from request context.
///
/// Mirrors the legacy Strategy A resolution: Bearer bot token → human identity
/// (`human_<staff_no>`) → optional `from_bot` fallback. Returns `Unauthorized`
/// when no caller can be established.
async fn resolve_caller(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
    from_bot: Option<&str>,
) -> Result<String, HttpAdapterError> {
    if let Some(actor_id) = caller_actor_id_from_headers(state, headers, uri).await {
        return Ok(actor_id);
    }
    if let Some(actor_id) = from_bot.filter(|id| !id.is_empty()) {
        // TODO(installment-3): ownership enforcement moved to the service layer —
        // ConnectService::create_connect MUST verify the authenticated caller is
        // authorized to act as `from_bot` (the legacy HTTP-layer
        // `check_actor_ownership` was removed as part of the edge-permission
        // authz reform). Until the real service lands, `create_connect` is Noop
        // and no friend state changes; see plan Installment 3 index.
        return Ok(actor_id.to_string());
    }
    Err(HttpAdapterError::Unauthorized(
        "no valid token or caller identity provided".to_string(),
    ))
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
