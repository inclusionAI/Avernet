//! Legacy BCS `/groupcontext/*` routes used by `bcs-cli` bot calls.
//!
//! These endpoints intentionally follow the same auth boundary as `bcs-cli chat`:
//! the CLI sends the normal bot token (`Authorization: Bearer` or
//! `X-BCS-Bot-Token` when OAuth occupies Authorization), and the HTTP adapter
//! resolves the effective actor from trusted headers/server-side token lookup.
//! The actor is never trusted from the JSON body or URL query.

use axum::extract::rejection::{JsonRejection, QueryRejection};
use axum::extract::{Json, Query, State};
use axum::http::{HeaderMap, StatusCode, Uri};
use axum::response::{IntoResponse, Response};
use bcs_domain::{GroupContextPolicy, GroupContextTime, ScopeLevel};
use bcs_service_api::application::v1::{
    AddGroupContext, ApplicationError, GetGroupContext, GroupContextAck, GroupContextGetResponse,
    GroupContextListResponse, GroupContextRequestContext, GroupContextStatus, ListGroupContexts,
    UpdateGroupContext,
};
use chrono::{DateTime, Local, NaiveDateTime, TimeZone, Utc};
use serde::{Deserialize, Deserializer, Serialize};

use crate::state::HttpAppState;

use super::require_caller_actor_id_from_headers;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct GroupContextQuery {
    group_id: Option<String>,
    session_id: Option<String>,
    round_id: Option<String>,
}

impl GroupContextQuery {
    fn request_context(&self) -> GroupContextRequestContext {
        GroupContextRequestContext {
            group_id: self.group_id.clone(),
            session_id: self.session_id.clone(),
            round_id: self.round_id.clone(),
        }
    }
}

#[derive(Debug, Deserialize)]
pub(crate) struct AddRequest {
    name: String,
    scope_level: ScopeLevel,
    content: String,
    visible_to: GroupContextPolicy,
    collect_from: GroupContextPolicy,
    #[serde(default, deserialize_with = "deserialize_optional_group_context_time")]
    valid_from: Option<GroupContextTime>,
    #[serde(default, deserialize_with = "deserialize_optional_group_context_time")]
    valid_to: Option<GroupContextTime>,
}

#[derive(Debug, Deserialize)]
pub(crate) struct UpdateRequest {
    name: String,
    scope_level: ScopeLevel,
    #[serde(default)]
    content: Option<String>,
    #[serde(default)]
    change_reason: Option<String>,
    #[serde(default)]
    visible_to: Option<GroupContextPolicy>,
    #[serde(default)]
    collect_from: Option<GroupContextPolicy>,
    #[serde(default, deserialize_with = "deserialize_optional_group_context_time")]
    valid_from: Option<GroupContextTime>,
    #[serde(default, deserialize_with = "deserialize_optional_group_context_time")]
    valid_to: Option<GroupContextTime>,
}

#[derive(Debug, Deserialize)]
pub(crate) struct ListRequest {
    #[serde(default)]
    scope_levels: Option<Vec<ScopeLevel>>,
}

#[derive(Debug, Deserialize)]
pub(crate) struct GetRequest {
    name: String,
    #[serde(default)]
    scope_levels: Option<Vec<ScopeLevel>>,
    #[serde(default)]
    limit: Option<u32>,
}

#[derive(Debug, Serialize)]
struct GroupContextErrorBody {
    status: GroupContextStatus,
    error_msg: String,
}

pub(crate) async fn add(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    query: Result<Query<GroupContextQuery>, QueryRejection>,
    body: Result<Json<AddRequest>, JsonRejection>,
) -> Result<Response, GroupContextHttpError> {
    let actor_id = actor_id(&state, &headers, &uri).await?;
    let Query(query) = query.map_err(GroupContextHttpError::from_query_rejection)?;
    let Json(body) = body.map_err(GroupContextHttpError::from_json_rejection)?;
    let result = state
        .services
        .group_context
        .add(AddGroupContext {
            actor_id,
            request_context: query.request_context(),
            name: body.name,
            scope_level: body.scope_level,
            content: body.content,
            visible_to: body.visible_to,
            collect_from: body.collect_from,
            valid_from: body.valid_from,
            valid_to: body.valid_to,
        })
        .await?;
    Ok(json_ok(result))
}

pub(crate) async fn update(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    query: Result<Query<GroupContextQuery>, QueryRejection>,
    body: Result<Json<UpdateRequest>, JsonRejection>,
) -> Result<Response, GroupContextHttpError> {
    let actor_id = actor_id(&state, &headers, &uri).await?;
    let Query(query) = query.map_err(GroupContextHttpError::from_query_rejection)?;
    let Json(body) = body.map_err(GroupContextHttpError::from_json_rejection)?;
    let result = state
        .services
        .group_context
        .update(UpdateGroupContext {
            actor_id,
            request_context: query.request_context(),
            name: body.name,
            scope_level: body.scope_level,
            content: body.content,
            change_reason: body.change_reason,
            visible_to: body.visible_to,
            collect_from: body.collect_from,
            owners: None,
            valid_from: body.valid_from,
            valid_to: body.valid_to,
        })
        .await?;
    Ok(json_ok(result))
}

pub(crate) async fn list(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    query: Result<Query<GroupContextQuery>, QueryRejection>,
    body: Result<Json<ListRequest>, JsonRejection>,
) -> Result<Response, GroupContextHttpError> {
    let actor_id = actor_id(&state, &headers, &uri).await?;
    let Query(query) = query.map_err(GroupContextHttpError::from_query_rejection)?;
    let Json(body) = body.map_err(GroupContextHttpError::from_json_rejection)?;
    let result = state
        .services
        .group_context
        .list(ListGroupContexts {
            actor_id,
            request_context: query.request_context(),
            scope_levels: body.scope_levels,
        })
        .await?;
    Ok(json_ok(result))
}

pub(crate) async fn get(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    query: Result<Query<GroupContextQuery>, QueryRejection>,
    body: Result<Json<GetRequest>, JsonRejection>,
) -> Result<Response, GroupContextHttpError> {
    let actor_id = actor_id(&state, &headers, &uri).await?;
    let Query(query) = query.map_err(GroupContextHttpError::from_query_rejection)?;
    let Json(body) = body.map_err(GroupContextHttpError::from_json_rejection)?;
    let result = state
        .services
        .group_context
        .get(GetGroupContext {
            actor_id,
            request_context: query.request_context(),
            name: body.name,
            scope_levels: body.scope_levels,
            limit: body.limit,
        })
        .await?;
    Ok(json_ok(result))
}

fn deserialize_optional_group_context_time<'de, D>(
    deserializer: D,
) -> Result<Option<GroupContextTime>, D::Error>
where
    D: Deserializer<'de>,
{
    let Some(value) = Option::<serde_json::Value>::deserialize(deserializer)? else {
        return Ok(None);
    };
    parse_group_context_time(value)
        .map(Some)
        .map_err(serde::de::Error::custom)
}

fn parse_group_context_time(value: serde_json::Value) -> Result<GroupContextTime, String> {
    match value {
        serde_json::Value::Number(number) => {
            let raw = number
                .as_i64()
                .ok_or_else(|| "time value must be an integer timestamp".to_string())?;
            timestamp_from_seconds_or_millis(raw)
        }
        serde_json::Value::String(raw) => parse_group_context_time_string(&raw),
        other => Err(format!(
            "time value must be RFC3339, YYYY-MM-DD HH:MM:SS, or unix timestamp, got {other}"
        )),
    }
}

fn timestamp_from_seconds_or_millis(raw: i64) -> Result<GroupContextTime, String> {
    let (seconds, nanos) = if raw.abs() >= 1_000_000_000_000 {
        let seconds = raw / 1_000;
        let millis = (raw % 1_000).abs() as u32;
        (seconds, millis * 1_000_000)
    } else {
        (raw, 0)
    };
    Utc.timestamp_opt(seconds, nanos)
        .single()
        .ok_or_else(|| format!("timestamp {raw} is out of range"))
}

fn parse_group_context_time_string(raw: &str) -> Result<GroupContextTime, String> {
    // Accept numeric strings as unix timestamps (defense-in-depth for clients
    // that send timestamps as JSON strings instead of JSON numbers).
    if let Ok(raw_num) = raw.parse::<i64>() {
        return timestamp_from_seconds_or_millis(raw_num);
    }
    if DateTime::parse_from_rfc3339(raw).is_ok() {
        return DateTime::parse_from_rfc3339(raw)
            .map(|dt| dt.with_timezone(&Utc))
            .map_err(|error| error.to_string());
    }
    if raw.len() == "YYYY-MM-DD".len() && chrono::NaiveDate::parse_from_str(raw, "%Y-%m-%d").is_ok()
    {
        return Err("pure date values are not accepted; include a time".to_string());
    }
    let naive = NaiveDateTime::parse_from_str(raw, "%Y-%m-%d %H:%M:%S").map_err(|_| {
        "time value must be RFC3339, YYYY-MM-DD HH:MM:SS, or unix timestamp".to_string()
    })?;
    Local
        .from_local_datetime(&naive)
        .single()
        .map(|dt| dt.with_timezone(&Utc))
        .ok_or_else(|| format!("local time {raw} is ambiguous or invalid"))
}

async fn actor_id(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
) -> Result<String, GroupContextHttpError> {
    require_caller_actor_id_from_headers(state, headers, uri)
        .await
        .map_err(|_| ApplicationError::Unauthenticated.into())
}

fn json_ok<T: Serialize>(value: T) -> Response {
    (StatusCode::OK, Json(value)).into_response()
}

pub(crate) struct GroupContextHttpError {
    status: StatusCode,
    body: GroupContextErrorBody,
}

impl GroupContextHttpError {
    fn from_query_rejection(error: QueryRejection) -> Self {
        Self::from_application(ApplicationError::invalid(
            "invalid_param",
            error.body_text(),
        ))
    }

    fn from_json_rejection(error: JsonRejection) -> Self {
        Self::from_application(ApplicationError::invalid(
            "invalid_param",
            error.body_text(),
        ))
    }

    fn from_application(error: ApplicationError) -> Self {
        let (status, group_status, message) = match error {
            ApplicationError::InvalidInput { message, .. } => (
                StatusCode::BAD_REQUEST,
                GroupContextStatus::InvalidParam,
                message,
            ),
            ApplicationError::Unauthenticated => (
                StatusCode::UNAUTHORIZED,
                GroupContextStatus::InvalidParam,
                "Authentication is required".to_string(),
            ),
            ApplicationError::Forbidden(message) => (
                StatusCode::FORBIDDEN,
                GroupContextStatus::PermissionDenied,
                message,
            ),
            ApplicationError::ForbiddenCode { message, .. } => (
                StatusCode::FORBIDDEN,
                GroupContextStatus::PermissionDenied,
                message,
            ),
            ApplicationError::NotFound { message, .. } => {
                (StatusCode::NOT_FOUND, GroupContextStatus::NotFound, message)
            }
            ApplicationError::Conflict { message, .. } => {
                (StatusCode::CONFLICT, GroupContextStatus::Conflict, message)
            }
            ApplicationError::PayloadTooLarge { message, .. } => (
                StatusCode::PAYLOAD_TOO_LARGE,
                GroupContextStatus::PayloadTooLarge,
                message,
            ),
            ApplicationError::Internal(detail) => {
                tracing::error!(error = %detail, "Group Context request failed");
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    GroupContextStatus::InternalError,
                    "Internal server error".to_string(),
                )
            }
            other => (
                StatusCode::INTERNAL_SERVER_ERROR,
                GroupContextStatus::InternalError,
                other.to_string(),
            ),
        };
        Self {
            status,
            body: GroupContextErrorBody {
                status: group_status,
                error_msg: message,
            },
        }
    }
}

impl From<ApplicationError> for GroupContextHttpError {
    fn from(error: ApplicationError) -> Self {
        Self::from_application(error)
    }
}

impl IntoResponse for GroupContextHttpError {
    fn into_response(self) -> Response {
        (self.status, Json(self.body)).into_response()
    }
}

#[allow(dead_code)]
fn _assert_response_shapes(
    _: GroupContextAck,
    _: GroupContextListResponse,
    _: GroupContextGetResponse,
) {
}

#[cfg(test)]
mod group_context_error_tests {
    use super::*;
    use axum::body::to_bytes;

    #[tokio::test]
    async fn application_errors_map_to_http_status_and_response_body() {
        for (error, status, expected_message) in [
            (
                ApplicationError::forbidden("actor may not update"),
                StatusCode::FORBIDDEN,
                "actor may not update",
            ),
            (
                ApplicationError::internal("storage failed"),
                StatusCode::INTERNAL_SERVER_ERROR,
                "Internal server error",
            ),
            (
                ApplicationError::Gone {
                    code: "gone".into(),
                    message: "context was retired".into(),
                },
                StatusCode::INTERNAL_SERVER_ERROR,
                "gone: context was retired",
            ),
        ] {
            let response = GroupContextHttpError::from_application(error).into_response();
            assert_eq!(response.status(), status);
            let bytes = to_bytes(response.into_body(), usize::MAX)
                .await
                .expect("response body");
            let body: serde_json::Value = serde_json::from_slice(&bytes).expect("JSON error body");
            assert_eq!(body["error_msg"], expected_message);
        }
    }
}
