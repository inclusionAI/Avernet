use axum::{
    Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode, Uri},
    response::{IntoResponse, Response},
};
use bcs_protocol::{
    BotConnectParams, BotSearchEntry, BotSearchQuery, QueryBotsRequest, SetVisibilityRequest,
    UpdateStatusRequest,
};
use bcs_service_api::{
    ActorKind, ActorStatus, BotConnectCommand, BotDetailCommand, BotDetailResult, BotLeaveCommand,
    BotListCommand, BotListEntry, BotPagedListCommand, BotQueryByIdsCommand, BotQueryEntry,
    BotStatusUpdateCommand, BotUseCaseError, BotVisibilityCommand, BotVisibilityQueryCommand,
    BotVisibilityQueryResult, ConnectError, MyBotsCommand, SearchBotsCommand, ServiceError,
};
use serde::Deserialize;
use serde_json::Value;

use crate::error::HttpAdapterError;
use crate::mapping::capabilities::{
    to_core_dynamic_status, to_wire_capabilities, to_wire_dynamic_status,
    to_wire_dynamic_status_response,
};
use crate::state::{HttpAppState, VisibilitySyncRequest};

use super::{
    bot_token_from_headers, caller_actor_id_from_headers, require_bot_id_from_headers,
    require_caller_actor_id_from_headers,
    validate_container_header,
};

#[derive(Debug, Deserialize)]
pub struct ListBotsQuery {
    #[serde(default)]
    pub onboarded: Option<bool>,
    #[serde(default)]
    pub offset: Option<u64>,
    #[serde(default)]
    pub limit: Option<u64>,
}

#[derive(Debug, Deserialize)]
pub struct ListBotsPagedQuery {
    #[serde(default)]
    pub user_id: Option<String>,
    #[serde(default)]
    pub offset: Option<u64>,
    #[serde(default)]
    pub limit: Option<u64>,
}

#[derive(Debug, Deserialize)]
pub struct MyBotsQuery {
    #[serde(default)]
    pub offset: Option<u64>,
    #[serde(default)]
    pub limit: Option<u64>,
    #[serde(default)]
    pub active_only: Option<bool>,
}

pub async fn list_bots(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Query(query): Query<ListBotsQuery>,
) -> Result<Json<Value>, HttpAdapterError> {
    let offset = query.offset.unwrap_or(0);
    let limit = query.limit.unwrap_or(200);
    let caller_actor_id = require_caller_actor_id_from_headers(&state, &headers, &uri).await?;

    let result = state
        .services
        .bot_query
        .list_bots(BotListCommand {
            caller_actor_id: Some(caller_actor_id),
            offset,
            limit,
            onboarded: query.onboarded,
        })
        .await
        .map_err(bot_use_case_error_to_http)?;

    let items: Vec<Value> = result
        .bots
        .into_iter()
        .map(list_bot_entry_to_json)
        .collect();

    Ok(Json(Value::Array(items)))
}

pub async fn update_bot_status(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    _uri: Uri,
    Json(req): Json<UpdateStatusRequest>,
) -> Result<Json<Value>, HttpAdapterError> {
    let bot_id = resolve_bot_caller(&state, &headers).await?;
    validate_container_header(&state, &headers, &bot_id)?;
    let target_bot_id: String = if req.bot_uuid.trim().is_empty() {
        bot_id.clone()
    } else if req.bot_uuid == bot_id {
        req.bot_uuid
    } else {
        return Err(HttpAdapterError::Forbidden(format!(
            "bot token for '{}' cannot update status for '{}'",
            bot_id, req.bot_uuid
        )));
    };

    let result = state
        .services
        .bot_management
        .update_status(BotStatusUpdateCommand {
            caller_actor_id: Some(bot_id),
            bot_id: target_bot_id,
            status: to_core_dynamic_status(req.status),
        })
        .await
        .map_err(bot_use_case_error_to_http)?;

    Ok(Json(serde_json::json!({
        "updated": result.updated,
        "bot_uuid": result.bot_uuid,
        "status": to_wire_dynamic_status(result.status),
    })))
}

pub async fn connect_bot(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Json(params): Json<BotConnectParams>,
) -> Result<Json<bcs_service_api::BotConnectResult>, HttpAdapterError> {
    let caller_actor_id = extract_caller_actor_id(&state, &headers, &uri).await;
    let result = state
        .services
        .bot_management
        .connect_bot(BotConnectCommand {
            caller_actor_id,
            token: params.token,
            bot_id: params.bot_id,
            protocol_version: params.protocol_version,
        })
        .await
        .map_err(bot_use_case_error_to_http)?;

    Ok(Json(result))
}

pub async fn list_bots_paged(
    State(state): State<HttpAppState>,
    Query(query): Query<ListBotsPagedQuery>,
) -> Result<Json<Value>, HttpAdapterError> {
    let offset = query.offset.unwrap_or(0);
    let limit = query.limit.unwrap_or(20);

    let result = state
        .services
        .bot_query
        .list_bots_paged(BotPagedListCommand {
            user_id: query.user_id,
            offset,
            limit,
        })
        .await
        .map_err(bot_use_case_error_to_http)?;
    let items: Vec<Value> = result
        .items
        .into_iter()
        .map(bot_query_entry_to_paged_json)
        .collect();

    Ok(Json(serde_json::json!({
        "items": items,
        "total": result.total,
        "offset": result.offset,
        "limit": result.limit,
    })))
}

pub async fn list_my_bots(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Query(query): Query<MyBotsQuery>,
) -> Result<Json<Value>, HttpAdapterError> {
    let user = state.user_identity.extract(&headers, &uri).await;
    let staff_no = user
        .and_then(|u| u.staff_no)
        .filter(|s| !s.is_empty())
        .ok_or_else(|| {
            HttpAdapterError::Unauthorized("Login required to query your bots".to_string())
        })?;

    let offset = query.offset.unwrap_or(0);
    let limit = query.limit.unwrap_or(20);
    let active_only = query.active_only.unwrap_or(false);

    let result = state
        .services
        .bot_query
        .list_my_bots(MyBotsCommand {
            staff_no,
            offset,
            limit,
            active_only,
        })
        .await
        .map_err(bot_use_case_error_to_http)?;
    let items: Vec<Value> = result
        .items
        .into_iter()
        .map(bot_query_entry_to_my_json)
        .collect();

    Ok(Json(serde_json::json!({
        "items": items,
        "total": result.total,
        "offset": result.offset,
        "limit": result.limit,
    })))
}

pub async fn get_bot(
    State(state): State<HttpAppState>,
    Path(bot_uuid): Path<String>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Json<Value>, HttpAdapterError> {
    let caller_actor_id = require_caller_actor_id_from_headers(&state, &headers, &uri).await?;
    let detail = state
        .services
        .bot_query
        .get_bot(BotDetailCommand {
            caller_actor_id: Some(caller_actor_id),
            bot_id: bot_uuid,
        })
        .await
        .map_err(bot_use_case_error_to_http)?;

    Ok(Json(bot_detail_to_json(detail)))
}

pub async fn leave_bot(
    State(state): State<HttpAppState>,
    Path(bot_uuid): Path<String>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Json<Value>, HttpAdapterError> {
    let human_actor_id = extract_human_actor_id(&state, &headers, &uri)
        .await
        .ok_or_else(|| {
            HttpAdapterError::Unauthorized("valid human identity is required".to_string())
        })?;

    let result = state
        .services
        .bot_management
        .leave_bot(BotLeaveCommand {
            caller_actor_id: Some(human_actor_id.clone()),
            human_actor_id: Some(human_actor_id),
            bot_id: bot_uuid,
        })
        .await
        .map_err(bot_use_case_error_to_http)?;

    Ok(Json(serde_json::json!({
        "left": result.left,
        "bot_uuid": result.bot_uuid
    })))
}

pub async fn get_visibility(
    State(state): State<HttpAppState>,
    Path(bot_uuid): Path<String>,
    headers: HeaderMap,
    uri: Uri,
) -> Response {
    let caller_actor_id = match extract_visibility_caller_actor_id(&state, &headers, &uri).await {
        Ok(caller_actor_id) => caller_actor_id,
        Err(response) => return response,
    };
    match state
        .services
        .bot_query
        .get_visibility(BotVisibilityQueryCommand {
            caller_actor_id,
            bot_id: bot_uuid.clone(),
        })
        .await
    {
        Ok(result) => (StatusCode::OK, Json(bot_visibility_to_json(result))).into_response(),
        Err(error) => bot_use_case_error_to_visibility_response(error, &bot_uuid),
    }
}

pub async fn set_visibility(
    State(state): State<HttpAppState>,
    Path(bot_uuid): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Json(req): Json<SetVisibilityRequest>,
) -> Response {
    let caller_actor_id = extract_caller_actor_id(&state, &headers, &uri).await;
    match state
        .services
        .bot_management
        .set_visibility(BotVisibilityCommand {
            caller_actor_id,
            bot_id: bot_uuid.clone(),
            visibility: req.visibility,
        })
        .await
    {
        Ok(result) => {
            dispatch_visibility_sync_after_update(
                &state,
                &result.bot_uuid,
                &result.visibility,
            )
            .await;
            (
                StatusCode::OK,
                Json(serde_json::json!({
                    "success": true,
                    "data": {
                        "bot_uuid": result.bot_uuid,
                        "visibility": result.visibility
                    }
                })),
            )
                .into_response()
        }
        Err(error) => bot_use_case_error_to_visibility_response(error, &bot_uuid),
    }
}

pub async fn query_bots(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Json(body): Json<QueryBotsRequest>,
) -> Result<Json<Value>, HttpAdapterError> {
    let _caller_actor_id =
        require_caller_actor_id_from_headers(&state, &headers, &uri).await?;
    let result = state
        .services
        .bot_query
        .query_bots_by_ids(BotQueryByIdsCommand {
            bot_ids: body.bot_uuids,
        })
        .await
        .map_err(bot_use_case_error_to_http)?;
    let entries = result
        .bots
        .into_iter()
        .map(bot_query_entry_to_query_json)
        .collect();

    Ok(Json(Value::Array(entries)))
}

fn list_bot_entry_to_json(bot: BotListEntry) -> Value {
    let wire_capabilities = to_wire_capabilities(bot.capabilities);
    let skill_names: Vec<String> = wire_capabilities
        .skills
        .iter()
        .map(|s| s.name.clone())
        .collect();
    let mut caps =
        serde_json::to_value(&wire_capabilities).unwrap_or_else(|_| serde_json::json!({}));
    if let Some(obj) = caps.as_object_mut() {
        obj.insert("skills".to_string(), serde_json::json!(skill_names));
    }

    serde_json::json!({
        "bot_uuid": bot.bot_uuid,
        "capabilities": caps,
        "created_by": bot.created_by
    })
}

fn bot_detail_to_json(bot: BotDetailResult) -> Value {
    serde_json::json!({
        "bot_uuid": bot.bot_uuid,
        "capabilities": to_wire_capabilities(bot.capabilities),
        "status": actor_status_to_wire(bot.status),
        "created_by": bot.created_by,
        "actor_kind": bot.actor_kind,
        "env": bot.env,
        "dynamic_status": to_wire_dynamic_status_response(bot.dynamic_status),
    })
}

fn bot_query_entry_to_paged_json(bot: BotQueryEntry) -> Value {
    serde_json::json!({
        "bot_uuid": bot.bot_uuid,
        "capabilities": to_wire_capabilities(bot.capabilities),
        "created_by": bot.created_by
    })
}

fn bot_query_entry_to_my_json(bot: BotQueryEntry) -> Value {
    let wire_capabilities = to_wire_capabilities(bot.capabilities);
    let skill_names: Vec<String> = wire_capabilities
        .skills
        .iter()
        .map(|s| s.name.clone())
        .collect();
    let mut caps =
        serde_json::to_value(&wire_capabilities).unwrap_or_else(|_| serde_json::json!({}));
    if let Some(obj) = caps.as_object_mut() {
        obj.insert("skills".to_string(), serde_json::json!(skill_names));
    }

    serde_json::json!({
        "bot_uuid": bot.bot_uuid,
        "capabilities": caps,
        "visibility": bot.visibility,
        "created_by": bot.created_by,
        "actor_kind": bot.actor_kind,
        "env": bot.env,
        "status": actor_status_to_wire(bot.status),
        "dynamic_status": to_wire_dynamic_status_response(bot.dynamic_status),
    })
}

fn bot_query_entry_to_query_json(bot: BotQueryEntry) -> Value {
    serde_json::json!({
        "bot_uuid": bot.bot_uuid,
        "capabilities": to_wire_capabilities(bot.capabilities),
        "visibility": bot.visibility,
        "status": actor_status_to_wire(bot.status),
        "actor_kind": bot.actor_kind,
        "dynamic_status": to_wire_dynamic_status_response(bot.dynamic_status),
    })
}

fn bot_visibility_to_json(result: BotVisibilityQueryResult) -> Value {
    serde_json::json!({
        "success": true,
        "data": {
            "bot_uuid": result.bot_uuid,
            "visibility": result.visibility
        }
    })
}

/// `GET /bots/search` — bot search with name fuzzy + keyword filtering + is_friend.
/// Spec: docs/superpowers/specs/2026-08-20-bot-search-endpoint-design.md
pub async fn search_bots(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Query(q): Query<BotSearchQuery>,
) -> Result<Json<Value>, HttpAdapterError> {
    // ── Validate pagination + filter params (spec §3.5) ────────────────────────
    let offset = q.offset.unwrap_or(0);
    let limit = match q.limit.unwrap_or(20) {
        limit if (1..=100).contains(&limit) => limit,
        limit => {
            return Err(HttpAdapterError::BadRequest(format!(
                "limit must be between 1 and 100, got {limit}"
            )))
        }
    };
    let visibility = match q.visibility.as_deref() {
        Some(v) if matches!(v, "public" | "protected" | "private") => Some(v.to_string()),
        Some(v) => {
            return Err(HttpAdapterError::BadRequest(format!(
                "visibility must be public|protected|private, got '{v}'"
            )))
        }
        None => None,
    };
    let status = match q.status.as_deref() {
        Some("online") => Some(ActorStatus::Online),
        Some("hidden") => Some(ActorStatus::Hidden),
        Some(v) => {
            return Err(HttpAdapterError::BadRequest(format!(
                "status must be online|hidden, got '{v}'"
            )))
        }
        None => None,
    };

    // ── Resolve caller (optional Bearer) ───────────────────────────────────────
    let caller_id = caller_actor_id_from_headers(&state, &headers, &uri).await;

    // ── Effective visibility scope (spec §3.6.2) ───────────────────────────────
    // No Bearer → force public (ignore any client-provided visibility). Bearer +
    // explicit visibility → respect it. Bearer + no visibility → None (service
    // yields the default public+protected scope).
    let effective_visibility = match (&caller_id, &visibility) {
        (None, _) => Some("public".to_string()),
        (Some(_), v) => v.clone(),
    };

    // ── Query the registry via the application service (carries status/online) ─
    let result = state
        .services
        .bot_query
        .search_bots(SearchBotsCommand {
            q: q.q.clone(),
            visibility: effective_visibility,
            status,
            requester_actor_id: caller_id.clone(),
            tc_bot: q.tc_bot,
        })
        .await
        .map_err(bot_use_case_error_to_http)?;

    // ── Friend set from the caller's edge_grants (spec §3.7) ───────────────────
    // Anonymous callers are friends with nobody (empty set), so the is_friend
    // filter naturally resolves is_friend=true → none, is_friend=false → all.
    let friend_ids: std::collections::HashSet<String> = match &caller_id {
        Some(caller) => match state.connect.list_friends(caller).await {
            Ok(entries) => entries.into_iter().map(|e| e.actor_id).collect(),
            Err(_) => std::collections::HashSet::new(),
        },
        None => std::collections::HashSet::new(),
    };

    // ── is_friend post-filter (caller-dependent) + pagination ──────────────────
    let filtered: Vec<BotQueryEntry> = result
        .items
        .into_iter()
        .filter(|bot| match q.is_friend {
            Some(want_friend) => friend_ids.contains(&bot.bot_uuid) == want_friend,
            None => true,
        })
        .collect();

    let total = filtered.len() as u64;
    let page_items: Vec<BotQueryEntry> = filtered
        .into_iter()
        .skip(offset as usize)
        .take(limit as usize)
        .collect();

    // ── Build response (is_friend field only for authenticated callers) ────────
    let items: Vec<BotSearchEntry> = page_items
        .into_iter()
        .map(|bot| BotSearchEntry {
            bot_uuid: bot.bot_uuid.clone(),
            name: bot.capabilities.name.clone(),
            summary: bot.capabilities.summary.clone(),
            visibility: bot.visibility,
            status: actor_status_to_wire(bot.status).to_string(),
            actor_kind: actor_kind_to_wire(bot.actor_kind).to_string(),
            is_online: bot.dynamic_status.status == "active",
            is_friend: caller_id.as_ref().map(|_| friend_ids.contains(&bot.bot_uuid)),
        })
        .collect();

    Ok(Json(serde_json::json!({
        "items": items,
        "total": total,
        "offset": offset,
        "limit": limit,
    })))
}

async fn resolve_bot_caller(
    state: &HttpAppState,
    headers: &HeaderMap,
) -> Result<String, HttpAdapterError> {
    require_bot_id_from_headers(state, headers).await
}

async fn extract_caller_actor_id(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
) -> Option<String> {
    caller_actor_id_from_headers(state, headers, uri).await
}

async fn extract_human_actor_id(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
) -> Option<String> {
    state
        .user_identity
        .extract(headers, uri)
        .await
        .and_then(|u| u.staff_no)
        .filter(|staff_no| !staff_no.is_empty())
        .map(|staff_no| format!("human_{staff_no}"))
}

async fn extract_visibility_caller_actor_id(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
) -> Result<Option<String>, Response> {
    if bot_token_from_headers(headers).is_some() {
        if let Some(bot_id) = state.bot_uuid_from_headers(headers).await {
            return Ok(Some(bot_id));
        }

        return Err((
            StatusCode::UNAUTHORIZED,
            Json(serde_json::json!({
                "success": false,
                "error": "valid bot token is required"
            })),
        )
            .into_response());
    }

    Ok(state
        .user_identity
        .extract(headers, uri)
        .await
        .and_then(|u| u.staff_no)
        .filter(|staff_no| !staff_no.is_empty())
        .map(|staff_no| format!("human_{staff_no}")))
}

fn actor_status_to_wire(status: ActorStatus) -> &'static str {
    match status {
        ActorStatus::Online => "online",
        ActorStatus::Hidden => "hidden",
    }
}

fn actor_kind_to_wire(kind: ActorKind) -> &'static str {
    match kind {
        ActorKind::Bot => "bot",
        ActorKind::Human => "human",
    }
}

fn connect_error_to_http(error: ConnectError) -> HttpAdapterError {
    match error {
        ConnectError::AlreadyConnected(id) => {
            HttpAdapterError::Conflict(format!("Bot '{}' is already connected", id))
        }
        ConnectError::AlreadyRegistered(id) => {
            HttpAdapterError::Conflict(format!("Bot '{}' is already registered", id))
        }
        ConnectError::InvalidBotId => {
            HttpAdapterError::BadRequest("Bot ID cannot be empty".to_string())
        }
        ConnectError::InvalidToken => {
            HttpAdapterError::Unauthorized("valid bot token is required".to_string())
        }
        ConnectError::InternalError(message) => {
            HttpAdapterError::Service(ServiceError::InternalError(message))
        }
    }
}

pub(crate) fn bot_use_case_error_to_http(error: BotUseCaseError) -> HttpAdapterError {
    match error {
        BotUseCaseError::Unauthorized(message) => HttpAdapterError::Unauthorized(message),
        BotUseCaseError::Forbidden(message) => HttpAdapterError::Forbidden(message),
        BotUseCaseError::InvalidVisibility(_) => {
            HttpAdapterError::BadRequest(invalid_visibility_message().to_string())
        }
        BotUseCaseError::InvalidBotId(message) => HttpAdapterError::BadRequest(message),
        BotUseCaseError::InvalidProviderBotRef(message) => HttpAdapterError::BadRequest(message),
        BotUseCaseError::ProviderNotFound(p) => {
            HttpAdapterError::NotFound(format!("Provider '{p}' not found"))
        }
        BotUseCaseError::ProviderNotReadyForDownlink { provider_id, reason } => {
            HttpAdapterError::Conflict(format!(
                "Provider '{provider_id}' downlink not ready: {reason}"
            ))
        }
        BotUseCaseError::BotAlreadyBound {
            bot_id,
            existing_provider_id,
            existing_provider_bot_ref,
        } => HttpAdapterError::Conflict(format!(
            "Bot '{bot_id}' already bound to provider '{existing_provider_id}' (ref '{existing_provider_bot_ref}')"
        )),
        BotUseCaseError::Connect(error) => connect_error_to_http(error),
        BotUseCaseError::Service(error) => HttpAdapterError::Service(error),
    }
}

fn bot_use_case_error_to_visibility_response(error: BotUseCaseError, bot_uuid: &str) -> Response {
    match error {
        BotUseCaseError::InvalidVisibility(_) => (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "success": false,
                "error": invalid_visibility_message()
            })),
        )
            .into_response(),
        BotUseCaseError::Unauthorized(message) => (
            StatusCode::UNAUTHORIZED,
            Json(serde_json::json!({
                "success": false,
                "error": message
            })),
        )
            .into_response(),
        BotUseCaseError::Forbidden(message) => (
            StatusCode::FORBIDDEN,
            Json(serde_json::json!({
                "success": false,
                "error": message
            })),
        )
            .into_response(),
        BotUseCaseError::InvalidBotId(message) => (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "success": false,
                "error": message
            })),
        )
            .into_response(),
        BotUseCaseError::InvalidProviderBotRef(message) => (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "success": false,
                "error": message
            })),
        )
            .into_response(),
        BotUseCaseError::ProviderNotFound(p) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({
                "success": false,
                "error": format!("Provider '{}' not found", p)
            })),
        )
            .into_response(),
        BotUseCaseError::ProviderNotReadyForDownlink { provider_id, reason } => (
            StatusCode::CONFLICT,
            Json(serde_json::json!({
                "success": false,
                "error": format!("Provider '{}' downlink not ready: {}", provider_id, reason)
            })),
        )
            .into_response(),
        BotUseCaseError::BotAlreadyBound {
            bot_id,
            existing_provider_id,
            existing_provider_bot_ref,
        } => (
            StatusCode::CONFLICT,
            Json(serde_json::json!({
                "success": false,
                "error": format!(
                    "Bot '{}' already bound to provider '{}' (ref '{}')",
                    bot_id, existing_provider_id, existing_provider_bot_ref
                )
            })),
        )
            .into_response(),
        BotUseCaseError::Service(ServiceError::BotNotFound(_)) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({
                "success": false,
                "error": format!("Bot '{}' not found", bot_uuid)
            })),
        )
            .into_response(),
        BotUseCaseError::Service(_) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({
                "success": false,
                "error": "Failed to process visibility"
            })),
        )
            .into_response(),
        BotUseCaseError::Connect(error) => {
            bot_use_case_error_to_http(BotUseCaseError::Connect(error)).into_response()
        }
    }
}

async fn dispatch_visibility_sync_after_update(
    state: &HttpAppState,
    bot_uuid: &str,
    visibility: &str,
) {
    let Ok(bot) = state
        .services
        .bot_query
        .get_bot(BotDetailCommand {
            caller_actor_id: None,
            bot_id: bot_uuid.to_string(),
        })
        .await
    else {
        return;
    };

    let sync_request = VisibilitySyncRequest {
        bot_uuid: bot_uuid.to_string(),
        capabilities: bot.capabilities,
        visibility: visibility.to_string(),
        actor_kind: bot.actor_kind,
    };
    state.dispatch_visibility_sync(sync_request);
}

fn invalid_visibility_message() -> &'static str {
    "visibility must be 'public', 'protected', or 'private'"
}

