use axum::{
    Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode, Uri},
    response::{IntoResponse, Response},
};
use bcs_service_api::{
    ActorListCommand, ActorListResult, ActorSearchCommand, ActorSearchResult, ActorStatus,
    ActorStatusUpdateCommand, ServiceError,
};
use serde::Deserialize;

use crate::error::HttpAdapterError;
use crate::state::HttpAppState;


const DEFAULT_SEARCH_TOP_K: usize = 20;

#[derive(Debug, Deserialize)]
pub struct ListActorsQuery {
    #[serde(default)]
    pub name: Option<String>,
    pub current_bot_uuid: String,
    pub cooperatable_only: bool,
    #[serde(default)]
    pub page_size: Option<u32>,
    #[serde(default)]
    pub page_no: Option<u32>,
}

#[derive(Debug, Deserialize)]
pub struct SearchActorsQuery {
    pub q: String,
    pub current_bot_uuid: String,
    pub cooperatable_only: bool,
}

#[derive(Debug, Deserialize)]
pub struct PutActorStatusRequest {
    pub status: ActorStatus,
}

pub async fn list_actors(
    State(state): State<HttpAppState>,
    Query(query): Query<ListActorsQuery>,
) -> Result<Json<ActorListResult>, HttpAdapterError> {
    let page_size = query.page_size.unwrap_or(20).min(100);
    let page_no = std::cmp::max(query.page_no.unwrap_or(1), 1);
    let offset = ((page_no - 1) * page_size) as usize;
    let limit = page_size as usize;

    let result = state
        .services
        .actor_directory
        .list_actors(ActorListCommand {
            name: query.name,
            current_bot_uuid: query.current_bot_uuid,
            cooperatable_only: query.cooperatable_only,
            offset,
            limit,
        })
        .await;

    Ok(Json(result))
}

pub async fn search_actors(
    State(state): State<HttpAppState>,
    Query(query): Query<SearchActorsQuery>,
) -> Result<Json<ActorSearchResult>, HttpAdapterError> {
    let result = state
        .services
        .actor_directory
        .search_actors(ActorSearchCommand {
            query: query.q,
            current_bot_uuid: query.current_bot_uuid,
            cooperatable_only: query.cooperatable_only,
            limit: DEFAULT_SEARCH_TOP_K,
        })
        .await;

    Ok(Json(result))
}

pub async fn put_actor_status(
    State(state): State<HttpAppState>,
    Path(actor_id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Json(req): Json<PutActorStatusRequest>,
) -> Response {
    let caller = match resolve_put_caller(&state, &headers, &uri).await {
        Ok(caller) => caller,
        Err(response) => return response,
    };

    let result = match state
        .services
        .actor_directory
        .update_actor_status_for_caller(ActorStatusUpdateCommand {
            caller_actor_id: caller,
            actor_id,
            status: req.status,
        })
        .await
    {
        Ok(result) => result,
        Err(error) => return actor_service_error_response(error),
    };

    (
        StatusCode::OK,
        Json(serde_json::json!({
            "success": true,
            "data": {
                "actor_id": result.actor_id,
                "status": result.status,
            }
        })),
    )
        .into_response()
}

async fn resolve_put_caller(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
) -> Result<String, Response> {
    if let Some(caller) = state.bot_uuid_from_headers(headers).await {
        return Ok(caller);
    }

    if let Some(staff_no) = state
        .user_identity
        .extract(headers, uri)
        .await
        .and_then(|identity| identity.staff_no)
        .filter(|staff_no| !staff_no.is_empty())
    {
        return Ok(format!("human_{}", staff_no));
    }

    Err((
        StatusCode::UNAUTHORIZED,
        Json(serde_json::json!({
            "success": false,
            "error": "Unauthorized: no valid token or login session"
        })),
    )
        .into_response())
}

fn actor_service_error_response(error: ServiceError) -> Response {
    let (status, message) = match error {
        ServiceError::BotNotFound(actor_id) => (
            StatusCode::NOT_FOUND,
            format!("Actor '{}' not found", actor_id),
        ),
        ServiceError::Unauthorized(message) => (StatusCode::FORBIDDEN, message),
        ServiceError::Forbidden(message) => (StatusCode::FORBIDDEN, message),
        other => {
            tracing::warn!(
                error = %other,
                "actor route service error"
            );
            (StatusCode::INTERNAL_SERVER_ERROR, other.to_string())
        }
    };

    (
        status,
        Json(serde_json::json!({
            "success": false,
            "error": message
        })),
    )
        .into_response()
}
