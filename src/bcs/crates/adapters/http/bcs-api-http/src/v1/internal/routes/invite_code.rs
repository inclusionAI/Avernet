use axum::Router;
use axum::extract::{Extension, Json, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::post;
use bcs_service_api::application::v1::{AuthenticatedCaller, InitInviteCodes, InviteCodeService};

use crate::v1::common::{ApiState, Envelope, ErrorResponse, RequestId, application_error_response, invalid_request};
use crate::v1::openapi::dto::invite_code::InitInviteCodesRequest;

pub fn router() -> Router<ApiState> {
    Router::new().route("/invite-codes/init", post(init_invite_codes))
}

fn service<'a>(state: &'a ApiState, request_id: &'a RequestId) -> Result<&'a dyn InviteCodeService, ErrorResponse> {
    state
        .invite_code_service
        .as_deref()
        .ok_or_else(|| application_error_response(request_id, bcs_service_api::application::v1::ApplicationError::internal("V1 InviteCode service is not configured")))
}

async fn init_invite_codes(
    State(state): State<ApiState>,
    Extension(_caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    Json(body): Json<InitInviteCodesRequest>,
) -> Result<Response, ErrorResponse> {
    if body.count == 0 {
        return Err(invalid_request(&request_id, "count must be greater than 0"));
    }
    let result = service(&state, &request_id)?
        .init_invite_codes(InitInviteCodes { count: body.count })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}
