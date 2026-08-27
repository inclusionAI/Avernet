use axum::extract::rejection::{JsonRejection, PathRejection, QueryRejection};
use axum::extract::{Extension, Json, Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::Router;
use bcs_service_api::application::v1::{
    AcceptFriendConnectionRequest, AuthenticatedCaller, FriendConnectionService,
};
use bcs_service_api::application::RequestAuthHeaders;

use crate::v1::common::{
    ApiState, Envelope, ErrorResponse, RequestId, application_error_response, invalid_request,
};
use crate::v1::openapi::dto::friend_connection::{
    cancel_command, CreateFriendConnectionRequestBody, DeleteFriendConnectionQuery,
    ListFriendConnectionRequestsQuery, ListFriendConnectionsQuery,
    RejectFriendConnectionRequestBody,
};

/// Forwards the caller's credential to the backend work-order API.
///
/// Mirrors `extract_bearer_token`/`request_auth_headers` in the legacy
/// `bcs-http` adapter; duplicated here because `bcs-api-http` does not
/// depend on `bcs-http`.
fn request_auth_headers(headers: &HeaderMap) -> RequestAuthHeaders {
    let authorization = extract_bearer_token(headers).map(|token| format!("Bearer {token}"));
    let cookie = headers
        .get(axum::http::header::COOKIE)
        .and_then(|value| value.to_str().ok())
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned);
    let mut forwarded_headers = Vec::new();
    if let Some(value) = &authorization {
        forwarded_headers.push(("authorization".to_string(), value.clone()));
    }
    if let Some(value) = &cookie {
        forwarded_headers.push(("cookie".to_string(), value.clone()));
    }
    for name in ["x-avernet-principal", "x-one-id", "x-request-id", "x-trace-id"] {
        if let Some(value) = headers
            .get(name)
            .and_then(|value| value.to_str().ok())
            .map(str::trim)
            .filter(|value| !value.is_empty())
        {
            forwarded_headers.push((name.to_string(), value.to_string()));
        }
    }
    RequestAuthHeaders { authorization, cookie, forwarded_headers }
}

fn extract_bearer_token(headers: &HeaderMap) -> Option<String> {
    const BEARER_PREFIX: &[u8] = b"Bearer ";
    headers
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .filter(|value| value.len() >= BEARER_PREFIX.len())
        .and_then(|value| {
            if value.as_bytes()[..BEARER_PREFIX.len()].eq_ignore_ascii_case(BEARER_PREFIX) {
                Some(&value[BEARER_PREFIX.len()..])
            } else {
                None
            }
        })
        .map(str::trim)
        .filter(|token| !token.is_empty())
        .map(str::to_string)
}

pub fn router() -> Router<ApiState> {
    Router::new()
        .route(
            "/friend-connections/requests",
            post(create_friend_connection_request).get(list_friend_connection_requests),
        )
        .route(
            "/friend-connections/requests/{request_id}/accept",
            post(accept_friend_connection_request),
        )
        .route(
            "/friend-connections/requests/{request_id}/reject",
            post(reject_friend_connection_request),
        )
        .route(
            "/friend-connections/requests/{request_id}/cancel",
            post(cancel_friend_connection_request),
        )
        .route(
            "/friend-connections",
            get(list_friend_connections).delete(delete_friend_connection),
        )
}

fn service(
    state: &ApiState,
    request_id: &RequestId,
) -> Result<std::sync::Arc<dyn FriendConnectionService>, ErrorResponse> {
    state.friend_connection_service.clone().ok_or_else(|| {
        application_error_response(
            request_id,
            bcs_service_api::application::v1::ApplicationError::internal(
                "friend connection service is not configured",
            ),
        )
    })
}

async fn create_friend_connection_request(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    header_map: HeaderMap,
    body: Result<Json<CreateFriendConnectionRequestBody>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let request_auth = request_auth_headers(&header_map);
    let result = service(&state, &request_id)?
        .create_friend_connection_request(body.into_command(caller, Some(request_auth)))
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::CREATED,
        Json(Envelope::success(20_100, "Created", result, request_id.0)),
    )
        .into_response())
}

async fn list_friend_connection_requests(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    query: Result<Query<ListFriendConnectionRequestsQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    let Query(query) = query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = service(&state, &request_id)?
        .list_friend_connection_requests(query.into_command(caller))
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn accept_friend_connection_request(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(request_id_path) =
        path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = service(&state, &request_id)?
        .accept_friend_connection_request(AcceptFriendConnectionRequest {
            caller,
            request_id: request_id_path,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn reject_friend_connection_request(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    body: Option<Json<RejectFriendConnectionRequestBody>>,
) -> Result<Response, ErrorResponse> {
    let Path(request_id_path) =
        path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let body = body.map(|Json(body)| body).unwrap_or(RejectFriendConnectionRequestBody {
        reason: None,
    });
    let result = service(&state, &request_id)?
        .reject_friend_connection_request(body.into_command(caller, request_id_path))
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn cancel_friend_connection_request(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(request_id_path) =
        path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = service(&state, &request_id)?
        .cancel_friend_connection_request(cancel_command(caller, request_id_path))
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn list_friend_connections(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    query: Result<Query<ListFriendConnectionsQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    let Query(query) = query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = service(&state, &request_id)?
        .list_friend_connections(query.into_command(caller))
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn delete_friend_connection(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    query: Result<Query<DeleteFriendConnectionQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    let Query(query) = query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = service(&state, &request_id)?
        .delete_friend_connection(query.into_command(caller))
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}
