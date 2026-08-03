use axum::Router;
use axum::extract::rejection::{JsonRejection, PathRejection};
use axum::extract::{Extension, Json, Path, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::post;
use bcs_service_api::application::v1::AuthenticatedCaller;

use crate::v1::common::{
    ApiState, Envelope, ErrorResponse, RequestId, application_error_response, invalid_request,
};
use crate::v1::openapi::dto::invitation::{AcceptInvitationRequest, CreateInvitationRequest};

pub fn router() -> Router<ApiState> {
    Router::new()
        .route(
            "/openapi/v1/groups/{group_id}/invitations",
            post(create_group_invitation),
        )
        .route(
            "/openapi/v1/group-sessions/{session_id}/invitations",
            post(create_session_invitation),
        )
        .route(
            "/openapi/v1/invitations/{token}/accept",
            post(accept_invitation),
        )
}

async fn create_group_invitation(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    body: Result<Json<CreateInvitationRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(group_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .invitation_service
        .create_group_invitation(body.into_group_command(caller, group_id))
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::CREATED,
        Json(Envelope::success(20_100, "Created", result, request_id.0)),
    )
        .into_response())
}

async fn create_session_invitation(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    body: Result<Json<CreateInvitationRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(session_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .invitation_service
        .create_session_invitation(body.into_session_command(caller, session_id))
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::CREATED,
        Json(Envelope::success(20_100, "Created", result, request_id.0)),
    )
        .into_response())
}

async fn accept_invitation(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    body: Result<Json<AcceptInvitationRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(token) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .invitation_service
        .accept_invitation(body.into_command(caller, token))
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}
