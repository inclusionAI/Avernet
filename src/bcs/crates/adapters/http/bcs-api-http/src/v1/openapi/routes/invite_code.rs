use axum::Router;
use axum::extract::{Extension, Json, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedCaller, BindInviteCode, GetMyInviteCodeBinding,
    InviteCodeService,
};

use crate::v1::common::{
    ApiState, Envelope, ErrorResponse, RequestId, application_error_response, invalid_request,
};
use crate::v1::openapi::dto::invite_code::BindInviteCodeRequest;

pub fn router() -> Router<ApiState> {
    Router::new()
        .route("/invite-codes/bind", post(bind_invite_code))
        .route("/invite-codes/me", get(get_my_invite_code_binding))
}

fn service<'a>(state: &'a ApiState, request_id: &'a RequestId) -> Result<&'a dyn InviteCodeService, ErrorResponse> {
    state
        .invite_code_service
        .as_deref()
        .ok_or_else(|| application_error_response(request_id, ApplicationError::internal("V1 InviteCode service is not configured")))
}

async fn bind_invite_code(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    Json(body): Json<BindInviteCodeRequest>,
) -> Result<Response, ErrorResponse> {
    if body.code.trim().is_empty() {
        return Err(invalid_request(&request_id, "code must not be empty"));
    }
    let result = service(&state, &request_id)?
        .bind_invite_code(BindInviteCode {
            caller,
            code: body.code,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn get_my_invite_code_binding(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
) -> Result<Response, ErrorResponse> {
    let result = service(&state, &request_id)?
        .get_my_invite_code_binding(GetMyInviteCodeBinding { caller })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}
