use axum::Router;
use axum::extract::rejection::{JsonRejection, PathRejection, QueryRejection};
use axum::extract::{Extension, Json, Path, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, patch, post};
use bcs_service_api::application::v1::{
    AddGroupParticipant, AuthenticatedCaller, CreateGroup, DeleteGroup, DeleteGroupParticipant,
    GetGroup, ListGroups, UpdateGroup, UpdateGroupParticipant,
};

use crate::v1::common::{
    ApiState, Envelope, ErrorResponse, RequestId, application_error_response, invalid_request,
};
use crate::v1::openapi::dto::group::{
    AddParticipantRequest, CreateGroupRequest, ListGroupsQuery, UpdateGroupRequest,
    UpdateParticipantRequest,
};

pub fn router() -> Router<ApiState> {
    Router::new()
        .route("/groups", get(list_groups).post(create_group))
        .route(
            "/groups/{group_id}",
            get(get_group).patch(update_group).delete(delete_group),
        )
        .route(
            "/groups/{group_id}/participants",
            post(add_group_participant),
        )
        .route(
            "/groups/{group_id}/participants/{actor_id}",
            patch(update_group_participant).delete(remove_group_participant),
        )
}

async fn list_groups(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    query: Result<Query<ListGroupsQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    let Query(query) = query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let membership = query.membership_filter();
    let kind = query.kind_filter();
    let result = state
        .group_service
        .list_groups(ListGroups {
            caller,
            view_bot_id: query.view_bot_id,
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
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    body: Result<Json<CreateGroupRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .group_service
        .create_with_outcome(CreateGroup {
            caller,
            group: body.into(),
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    let (status, code, message) = if result.created {
        (StatusCode::CREATED, 20_100, "Created")
    } else {
        (StatusCode::OK, 20_000, "OK")
    };
    Ok((
        status,
        Json(Envelope::success(code, message, result.group, request_id.0)),
    )
        .into_response())
}

async fn get_group(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(group_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .group_service
        .get(GetGroup {
            caller,
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
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    body: Result<Json<UpdateGroupRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(group_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .group_service
        .update(UpdateGroup {
            caller,
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
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(group_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .group_service
        .delete(DeleteGroup {
            caller,
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

async fn add_group_participant(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    body: Result<Json<AddParticipantRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(group_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .group_service
        .add_participant(AddGroupParticipant {
            caller,
            group_id,
            actor_id: body.actor_id,
            role: body.role,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn update_group_participant(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<(String, String)>, PathRejection>,
    body: Result<Json<UpdateParticipantRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Path((group_id, actor_id)) =
        path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .group_service
        .update_participant(UpdateGroupParticipant {
            caller,
            group_id,
            actor_id,
            mode: body.mode,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn remove_group_participant(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<(String, String)>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path((group_id, actor_id)) =
        path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .group_service
        .delete_participant(DeleteGroupParticipant {
            caller,
            group_id,
            actor_id,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}
