use axum::Router;
use axum::extract::rejection::{JsonRejection, QueryRejection};
use axum::extract::{Extension, Json, Path, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use bcs_service_api::application::v1::{
    CreateGroup, DeleteGroup, GetGroup, ListBotGroups, Principal, UpdateGroup,
};

use crate::v1::common::{
    ApiState, Envelope, ErrorResponse, RequestId, application_error_response, invalid_request,
};
use crate::v1::openapi::dto::group::{CreateGroupRequest, ListGroupsQuery, UpdateGroupRequest};

pub fn router() -> Router<ApiState> {
    Router::new()
        .route(
            "/openapi/v1/bots/collaboration/{bot_uuid}/groups",
            get(list_bot_groups),
        )
        .route("/openapi/v1/groups", post(create_group))
        .route(
            "/openapi/v1/groups/{group_id}",
            get(get_group).patch(update_group).delete(delete_group),
        )
}

async fn list_bot_groups(
    State(state): State<ApiState>,
    Extension(principal): Extension<Principal>,
    Extension(request_id): Extension<RequestId>,
    Path(bot_uuid): Path<String>,
    query: Result<Query<ListGroupsQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    let Query(query) = query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let membership = query.membership_filter();
    let kind = query.kind_filter();
    let result = state
        .group_service
        .list_bot_groups(ListBotGroups {
            principal,
            bot_uuid,
            offset: query.offset,
            limit: query.limit,
            q: query.q,
            membership,
            kind,
            strategy: query.strategy,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn create_group(
    State(state): State<ApiState>,
    Extension(principal): Extension<Principal>,
    Extension(request_id): Extension<RequestId>,
    body: Result<Json<CreateGroupRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .group_service
        .create(CreateGroup {
            principal,
            group: body.into(),
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::CREATED,
        Json(Envelope::success(20_100, "Created", result, request_id.0)),
    )
        .into_response())
}

async fn get_group(
    State(state): State<ApiState>,
    Extension(principal): Extension<Principal>,
    Extension(request_id): Extension<RequestId>,
    Path(group_id): Path<String>,
) -> Result<Response, ErrorResponse> {
    let result = state
        .group_service
        .get(GetGroup {
            principal,
            group_id,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn update_group(
    State(state): State<ApiState>,
    Extension(principal): Extension<Principal>,
    Extension(request_id): Extension<RequestId>,
    Path(group_id): Path<String>,
    body: Result<Json<UpdateGroupRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .group_service
        .update(UpdateGroup {
            principal,
            group_id,
            patch: body.into(),
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn delete_group(
    State(state): State<ApiState>,
    Extension(principal): Extension<Principal>,
    Extension(request_id): Extension<RequestId>,
    Path(group_id): Path<String>,
) -> Result<Response, ErrorResponse> {
    let result = state
        .group_service
        .delete(DeleteGroup {
            principal,
            group_id,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}
