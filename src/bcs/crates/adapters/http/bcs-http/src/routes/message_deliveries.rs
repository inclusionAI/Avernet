//! Queue status/cancellation transport. Session/message authorization belongs
//! to the application and is repeated for every requested message.
use crate::{
    routes::group_messages::{group_chat_caller_context, resolve_group_chat_caller},
    state::HttpAppState,
};
use axum::{
    Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode, Uri},
    response::{IntoResponse, Response},
};
use bcs_service_api::ServiceError;
use bcs_service_api::application::message_delivery::{
    CancelMessageDeliveryCommand, DeliveryStatusQuery,
};
use serde::Deserialize;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SessionScope {
    session_id: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BatchQuery {
    #[serde(default)]
    message_ids: Vec<String>,
    #[serde(default)]
    client_msg_id: Option<String>,
}

fn error(error: ServiceError) -> Response {
    let status = match error {
        ServiceError::Unauthorized(_) => StatusCode::UNAUTHORIZED,
        ServiceError::Forbidden(_) => StatusCode::FORBIDDEN,
        ServiceError::SessionNotFound(_) | ServiceError::GroupNotFound(_) => StatusCode::NOT_FOUND,
        ServiceError::InvalidOperation { .. } => StatusCode::BAD_REQUEST,
        _ => StatusCode::SERVICE_UNAVAILABLE,
    };
    (
        status,
        Json(serde_json::json!({"error": status.canonical_reason().unwrap_or("request failed")})),
    )
        .into_response()
}

pub async fn get(
    State(state): State<HttpAppState>,
    Path(message_id): Path<String>,
    Query(scope): Query<SessionScope>,
    headers: HeaderMap,
    uri: Uri,
) -> Response {
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(c) => group_chat_caller_context(&c),
        Err(e) => return e.into_response(),
    };
    match state
        .services
        .message_flow
        .query_message_deliveries(DeliveryStatusQuery {
            caller,
            session_id: scope.session_id,
            message_ids: vec![message_id],
            client_msg_id: None,
        })
        .await
    {
        Ok(result) => Json(result).into_response(),
        Err(e) => error(e),
    }
}

pub async fn query(
    State(state): State<HttpAppState>,
    Path(session_id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Json(query): Json<BatchQuery>,
) -> Response {
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(c) => group_chat_caller_context(&c),
        Err(e) => return e.into_response(),
    };
    match state
        .services
        .message_flow
        .query_message_deliveries(DeliveryStatusQuery {
            caller,
            session_id,
            message_ids: query.message_ids,
            client_msg_id: query.client_msg_id,
        })
        .await
    {
        Ok(result) => Json(result).into_response(),
        Err(e) => error(e),
    }
}

async fn cancel(
    state: HttpAppState,
    headers: HeaderMap,
    uri: Uri,
    message_id: String,
    delivery_id: Option<String>,
    scope: SessionScope,
) -> Response {
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(c) => group_chat_caller_context(&c),
        Err(e) => return e.into_response(),
    };
    match state
        .services
        .message_flow
        .cancel_message_deliveries(CancelMessageDeliveryCommand {
            caller,
            session_id: scope.session_id,
            message_id,
            delivery_id,
        })
        .await
    {
        Ok(result) => Json(result).into_response(),
        Err(e) => error(e),
    }
}

pub async fn cancel_one(
    State(state): State<HttpAppState>,
    Path((message_id, delivery_id)): Path<(String, String)>,
    headers: HeaderMap,
    uri: Uri,
    Json(scope): Json<SessionScope>,
) -> Response {
    cancel(state, headers, uri, message_id, Some(delivery_id), scope).await
}

pub async fn cancel_message(
    State(state): State<HttpAppState>,
    Path(message_id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Json(scope): Json<SessionScope>,
) -> Response {
    cancel(state, headers, uri, message_id, None, scope).await
}
