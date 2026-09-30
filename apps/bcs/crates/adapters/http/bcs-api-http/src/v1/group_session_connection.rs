use std::sync::Arc;

use axum::extract::{Extension, Path, State};
use axum::http::StatusCode;
use axum::middleware;
use axum::response::{IntoResponse, Response};
use axum::routing::post;
use axum::{Json, Router};
use bcs_service_api::application::v1::{
    AuthenticatedCaller, GroupSessionConnectionError, GroupSessionConnectionService,
    IssueGroupSessionConnectionToken,
};
use serde::Serialize;

use super::common::{
    Envelope, ErrorResponse, PrincipalVerificationState, PrincipalVerifier, RequestId,
    application_error_response, verify_principal,
};

#[derive(Clone)]
struct GroupSessionConnectionHttpState {
    service: Arc<dyn GroupSessionConnectionService>,
    principal_verifier: Arc<dyn PrincipalVerifier>,
}

impl PrincipalVerificationState for GroupSessionConnectionHttpState {
    fn principal_verifier(&self) -> &Arc<dyn PrincipalVerifier> {
        &self.principal_verifier
    }
}

#[derive(Debug, Serialize)]
struct GroupSessionConnectionTokenResponse {
    token: String,
    expires_at: i64,
}

/// Build only the authenticated session-token issuance slice.
pub fn group_session_connection_router(
    service: Arc<dyn GroupSessionConnectionService>,
    principal_verifier: Arc<dyn PrincipalVerifier>,
) -> Router {
    let state = GroupSessionConnectionHttpState {
        service,
        principal_verifier,
    };
    Router::new()
        .route(
            "/openapi/v1/collaboration/sessions/{session_id}/token",
            post(issue_token),
        )
        .layer(middleware::from_fn_with_state(
            state.clone(),
            verify_principal::<GroupSessionConnectionHttpState>,
        ))
        .with_state(state)
}

async fn issue_token(
    State(state): State<GroupSessionConnectionHttpState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    Path(session_id): Path<String>,
) -> Result<Response, ErrorResponse> {
    let issued = state
        .service
        .issue_token(IssueGroupSessionConnectionToken { caller, session_id })
        .await
        .map_err(|error| connection_error_response(&request_id, error))?;
    let data = GroupSessionConnectionTokenResponse {
        token: issued.token,
        expires_at: issued.expires_at.unix_timestamp(),
    };

    Ok((
        StatusCode::OK,
        [("cache-control", "no-store"), ("pragma", "no-cache")],
        Json(Envelope::success(20_000, "OK", data, request_id.0)),
    )
        .into_response())
}

fn connection_error_response(
    request_id: &RequestId,
    error: GroupSessionConnectionError,
) -> ErrorResponse {
    match error {
        GroupSessionConnectionError::Application(error) => {
            application_error_response(request_id, error)
        }
        GroupSessionConnectionError::InvalidConnectionToken => application_error_response(
            request_id,
            bcs_service_api::application::v1::ApplicationError::internal(
                "unexpected token verification error during issuance",
            ),
        ),
        GroupSessionConnectionError::TokenServiceUnavailable => application_error_response(
            request_id,
            bcs_service_api::application::v1::ApplicationError::internal(
                "group-session token service unavailable",
            ),
        ),
        GroupSessionConnectionError::Internal(_) => application_error_response(
            request_id,
            bcs_service_api::application::v1::ApplicationError::internal(
                "group-session connection token issuance failed",
            ),
        ),
    }
}
