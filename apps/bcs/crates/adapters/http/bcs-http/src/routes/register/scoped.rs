//! Legacy HTTP translation for the shared Provider-scoped registration facade.

use axum::{
    Json,
    extract::Query,
    http::{StatusCode, Uri, header},
    response::{IntoResponse, Response},
};
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedCaller, AuthenticatedUserIdentity, IssueRegisterToken,
    ProviderRegistrationMode, RegisterBot, RegisterService,
};
use serde::{Deserialize, Serialize};

use crate::state::HttpAppState;

#[derive(Deserialize)]
struct ScopedRegisterQuery {
    mode: Option<ProviderRegistrationMode>,
    provider_bot_ref: Option<String>,
    webhook_url: Option<String>,
}

fn registration_service(state: &HttpAppState) -> Result<&dyn RegisterService, ApplicationError> {
    state.register_application.as_deref()
        .ok_or_else(|| ApplicationError::internal("registration service is not configured"))
}

pub(super) async fn issue_token(
    state: &HttpAppState,
    staff_no: String,
    nick_name: Option<String>,
    provider_id: String,
) -> Response {
    let service = match registration_service(state) {
        Ok(service) => service,
        Err(error) => return error_response(error),
    };
    let caller = AuthenticatedCaller {
        tenant: None,
        user: Some(AuthenticatedUserIdentity {
            username: staff_no.clone(),
            id: staff_no,
            display_name: nick_name,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    };
    match service.issue_register_token(IssueRegisterToken {
        caller, provider_id: Some(provider_id),
    }).await {
        Ok(token) => success_response(token),
        Err(error) => error_response(error),
    }
}

pub(super) async fn register_bot(
    state: &HttpAppState,
    uri: &Uri,
    token: String,
    bot_name: String,
) -> Response {
    // V1 keeps ignoring these historically unknown fields; only scoped calls parse them.
    let Query(query) = match Query::<ScopedRegisterQuery>::try_from_uri(uri) {
        Ok(query) => query,
        Err(_) => return error_response(ApplicationError::invalid(
            "invalid_request", "invalid registration query",
        )),
    };
    let service = match registration_service(state) {
        Ok(service) => service,
        Err(error) => return error_response(error),
    };
    match service.register_bot(RegisterBot {
        token, bot_name, mode: query.mode,
        provider_bot_ref: query.provider_bot_ref, webhook_url: query.webhook_url,
    }).await {
        Ok(bot) => success_response(bot),
        Err(error) => error_response(error),
    }
}

pub(super) fn success_response(value: impl Serialize) -> Response {
    ([(header::CACHE_CONTROL, "no-store")], Json(value)).into_response()
}

pub(super) fn error_response(error: ApplicationError) -> Response {
    let code = match &error {
        ApplicationError::Unauthenticated => "unauthorized",
        ApplicationError::Internal(_) => "internal",
        _ => error.code(),
    }.to_string();
    let (status, message) = match error {
        ApplicationError::InvalidInput { message, .. } => (StatusCode::BAD_REQUEST, message),
        ApplicationError::Unauthenticated => (StatusCode::UNAUTHORIZED, "invalid register token".into()),
        ApplicationError::Forbidden(message) | ApplicationError::ForbiddenCode { message, .. } => (StatusCode::FORBIDDEN, message),
        ApplicationError::NotFound { message, .. } => (StatusCode::NOT_FOUND, message),
        ApplicationError::Conflict { message, .. } => (StatusCode::CONFLICT, message),
        ApplicationError::Gone { message, .. } => (StatusCode::GONE, message),
        ApplicationError::QuotaExceeded { message, .. } => (StatusCode::TOO_MANY_REQUESTS, message),
        ApplicationError::PayloadTooLarge { message, .. } => (StatusCode::PAYLOAD_TOO_LARGE, message),
        ApplicationError::Unprocessable { message, .. } => (StatusCode::UNPROCESSABLE_ENTITY, message),
        ApplicationError::BadGateway { message, .. } => (StatusCode::BAD_GATEWAY, message),
        ApplicationError::Internal(error) => {
            tracing::error!(request_id = %bcs_observability::CurrentRequestId, error = %error, "legacy scoped registration failed");
            (StatusCode::INTERNAL_SERVER_ERROR, "internal server error".into())
        }
    };
    (status, Json(serde_json::json!({"error": code, "message": message}))).into_response()
}
