use axum::extract::rejection::{JsonRejection, PathRejection, QueryRejection};
use axum::extract::{Extension, Json, Path, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::Router;
use bcs_service_api::application::v1::{
    AcceptFriendConnectionRequest, AuthenticatedCaller, FriendConnectionService,
};

use crate::v1::common::{
    ApiState, Envelope, ErrorResponse, RequestId, application_error_response, invalid_request,
};
use crate::v1::openapi::dto::friend_connection::{
    cancel_command, CreateFriendConnectionRequestBody, DeleteFriendConnectionQuery,
    ListFriendConnectionRequestsQuery, ListFriendConnectionsQuery,
    RejectFriendConnectionRequestBody,
};

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
    body: Result<Json<CreateFriendConnectionRequestBody>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = service(&state, &request_id)?
        .create_friend_connection_request(body.into_command(caller))
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
