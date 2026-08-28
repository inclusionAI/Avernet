use axum::Router;
use axum::extract::rejection::QueryRejection;
use axum::extract::{Extension, Json, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use bcs_service_api::application::v1::{AuthenticatedCaller, IssueRegisterToken, RegisterBot};
use serde::Deserialize;

use crate::v1::common::{
    ApiState, Envelope, ErrorResponse, RequestId, application_error_response, invalid_request,
};

pub fn router() -> Router<ApiState> {
    Router::new().route("/register/token", get(get_register_token))
}

/// Mounted OUTSIDE the `verify_principal` boundary: POST /register is
/// anonymous and the register token is its only credential.
pub fn public_router() -> Router<ApiState> {
    Router::new().route("/register", post(register_bot))
}

#[derive(Debug, Deserialize)]
pub struct RegisterQuery {
    pub token: Option<String>,
    #[serde(alias = "bot-name")]
    pub bot_name: Option<String>,
}

async fn get_register_token(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
) -> Result<Response, ErrorResponse> {
    let result = state
        .register_service
        .issue_register_token(IssueRegisterToken { caller })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn register_bot(
    State(state): State<ApiState>,
    headers: HeaderMap,
    query: Result<Query<RegisterQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    let request_id = RequestId::from_headers(&headers);
    let Query(query) = query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let token = query
        .token
        .filter(|token| !token.is_empty())
        .ok_or_else(|| invalid_request(&request_id, "missing required parameter: token"))?;
    let bot_name = query
        .bot_name
        .map(|name| name.trim().to_string())
        .filter(|name| !name.is_empty())
        .ok_or_else(|| invalid_request(&request_id, "missing required parameter: bot-name"))?;
    let result = state
        .register_service
        .register_bot(RegisterBot { token, bot_name })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::CREATED,
        Json(Envelope::success(20_100, "Created", result, request_id.0)),
    )
        .into_response())
}
