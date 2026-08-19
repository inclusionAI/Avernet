use std::sync::Arc;

use async_trait::async_trait;
use axum::extract::{Request, State};
use axum::http::{HeaderMap, header};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use bcs_service_api::application::v1::ApplicationError;
use tracing::{debug, warn};

use crate::v1::common::{ErrorResponse, RequestId, application_error_response};

const PROVIDER_ID_HEADER: &str = "X-BCN-Provider-Id";
const BEARER_PREFIX: &[u8] = b"bearer ";

#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum InternalProviderAuthError {
    #[error("provider credentials are invalid")]
    Unauthorized,
    #[error("provider access is forbidden")]
    Forbidden,
}

/// HTTP-boundary authentication contract for trusted internal Providers.
///
/// The bootstrap implementation owns Provider lookup and trust configuration;
/// route code receives only this narrow decision boundary.
#[async_trait]
pub trait InternalProviderAuthenticator: Send + Sync {
    async fn authenticate(
        &self,
        token: &str,
        provider_id: &str,
    ) -> Result<(), InternalProviderAuthError>;
}

pub async fn authenticate_internal_provider(
    State(authenticator): State<Arc<dyn InternalProviderAuthenticator>>,
    mut request: Request,
    next: Next,
) -> Response {
    let request_id = RequestId::from_headers(request.headers());
    let Some(token) = extract_bearer_token(request.headers()) else {
        warn!(
            request_id = %request_id.0,
            failure = "missing_or_malformed_bearer",
            "Internal Provider authentication failed"
        );
        return ErrorResponse::unauthenticated(request_id.0).into_response();
    };
    let Some(provider_id) = provider_id(request.headers()) else {
        warn!(
            request_id = %request_id.0,
            failure = "missing_or_blank_provider_id",
            "Internal Provider authentication failed"
        );
        return application_error_response(
            &request_id,
            ApplicationError::forbidden("Provider access is forbidden"),
        )
        .into_response();
    };

    // COSEC: The internal route is fail-closed behind the injected Provider
    // authenticator; no handler runs until token, header, status, and trust pass.
    if let Err(error) = authenticator.authenticate(&token, provider_id).await {
        warn!(
            request_id = %request_id.0,
            provider_id,
            failure = %error,
            "Internal Provider authentication failed"
        );
        return match error {
            InternalProviderAuthError::Unauthorized => {
                ErrorResponse::unauthenticated(request_id.0).into_response()
            }
            InternalProviderAuthError::Forbidden => application_error_response(
                &request_id,
                ApplicationError::forbidden("Provider access is forbidden"),
            )
            .into_response(),
        };
    }

    debug!(
        request_id = %request_id.0,
        provider_id,
        "Internal Provider authentication succeeded"
    );
    request.extensions_mut().insert(request_id);
    next.run(request).await
}

fn provider_id(headers: &HeaderMap) -> Option<&str> {
    headers
        .get(PROVIDER_ID_HEADER)
        .and_then(|value| value.to_str().ok())
        .map(str::trim)
        .filter(|value| !value.is_empty())
}

fn extract_bearer_token(headers: &HeaderMap) -> Option<String> {
    headers
        .get(header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .filter(|value| value.len() >= BEARER_PREFIX.len())
        .and_then(|value| {
            value.as_bytes()[..BEARER_PREFIX.len()]
                .eq_ignore_ascii_case(BEARER_PREFIX)
                .then_some(&value[BEARER_PREFIX.len()..])
        })
        .map(str::trim)
        .filter(|token| !token.is_empty())
        .map(str::to_string)
}
