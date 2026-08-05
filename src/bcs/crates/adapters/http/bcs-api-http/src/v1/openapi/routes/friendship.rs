use axum::Router;
use axum::extract::rejection::{JsonRejection, PathRejection, QueryRejection};
use axum::extract::{Extension, Json, Path, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{delete, get, post};
use bcs_service_api::application::v1::{
    AcceptFriendRequest, AuthenticatedCaller, DeleteBotFriendship, ListBotFriendRequests,
    ListBotFriendships, RejectFriendRequest,
};

use crate::v1::common::{
    ApiState, Envelope, ErrorResponse, RequestId, application_error_response, invalid_request,
};
use crate::v1::openapi::dto::friendship::{
    CreateFriendRequestRequest, ListFriendRequestsQuery, ListFriendshipsQuery,
};

pub fn router() -> Router<ApiState> {
    Router::new()
        .route(
            "/bots/{bot_uuid}/friendships",
            get(list_bot_friendships),
        )
        .route(
            "/bots/{bot_uuid}/friendships/{friend_bot_uuid}",
            delete(delete_bot_friendship),
        )
        .route(
            "/bots/{bot_uuid}/friend-requests",
            post(create_bot_friend_request).get(list_bot_friend_requests),
        )
        .route(
            "/friend-requests/{request_id}/accept",
            post(accept_friend_request),
        )
        .route(
            "/friend-requests/{request_id}/reject",
            post(reject_friend_request),
        )
}

async fn list_bot_friendships(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    query: Result<Query<ListFriendshipsQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(bot_uuid) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Query(query) = query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .friendship_service
        .list_bot_friendships(ListBotFriendships {
            caller,
            bot_uuid,
            offset: query.offset,
            limit: query.limit,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn delete_bot_friendship(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<(String, String)>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path((bot_uuid, friend_bot_uuid)) =
        path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .friendship_service
        .delete_bot_friendship(DeleteBotFriendship {
            caller,
            bot_uuid,
            friend_bot_uuid,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn create_bot_friend_request(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    body: Result<Json<CreateFriendRequestRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(bot_uuid) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .friendship_service
        .create_bot_friend_request(body.into_command(caller, bot_uuid))
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::CREATED,
        Json(Envelope::success(20_100, "Created", result, request_id.0)),
    )
        .into_response())
}

async fn list_bot_friend_requests(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    query: Result<Query<ListFriendRequestsQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(bot_uuid) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Query(query) = query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .friendship_service
        .list_bot_friend_requests(ListBotFriendRequests {
            caller,
            bot_uuid,
            direction: query.direction.unwrap_or_default(),
            status: query.status,
            offset: query.offset,
            limit: query.limit,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn accept_friend_request(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(request_id_path) =
        path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .friendship_service
        .accept_friend_request(AcceptFriendRequest {
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

async fn reject_friend_request(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(request_id_path) =
        path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .friendship_service
        .reject_friend_request(RejectFriendRequest {
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
