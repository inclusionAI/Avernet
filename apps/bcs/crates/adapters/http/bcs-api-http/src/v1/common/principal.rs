use async_trait::async_trait;
use axum::extract::{Request, State};
use axum::http::HeaderMap;
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use bcs_service_api::application::v1::AuthenticatedCaller;
use tracing::warn;

use super::{ErrorResponse, PrincipalVerificationState, RequestId};

#[derive(Debug, thiserror::Error)]
pub enum PrincipalVerificationError {
    #[error("Principal is missing")]
    Missing,
    #[error("Principal is invalid: {0}")]
    Invalid(String),
    #[error("Bot identity store is unavailable")]
    Unavailable,
}

/// Gateway-to-BCN trust boundary.
///
/// Production bootstrap must inject the approved verifier. This crate does
/// not provide a verifier that trusts an unsigned Principal header.
#[async_trait]
pub trait PrincipalVerifier: Send + Sync {
    async fn verify(
        &self,
        headers: &HeaderMap,
    ) -> Result<AuthenticatedCaller, PrincipalVerificationError>;
}

pub async fn verify_principal<S>(
    State(state): State<S>,
    mut request: Request,
    next: Next,
) -> Response
where
    S: PrincipalVerificationState,
{
    let request_id = RequestId::from_headers(request.headers());
    match state.principal_verifier().verify(request.headers()).await {
        Ok(caller) => {
            // A comparison only, never an authentication source. Prevent a browser
            // account switch from submitting a configuration prepared by another Human.
            if request.headers().contains_key("x-bcs-expected-user") {
                let values = request.headers().get_all("x-bcs-expected-user");
                let expected = values.iter().next().and_then(|v| v.to_str().ok());
                if values.iter().count() != 1 || expected.is_none()
                    || expected != caller.user.as_ref().map(|user| user.id.as_str()) {
                    return super::application_error_response(&request_id,
                        bcs_service_api::application::v1::ApplicationError::forbidden_code(
                            "user_context_changed", "Current user changed; reopen the configuration",
                        )).into_response();
                }
            }
            request.extensions_mut().insert(caller);
            request.extensions_mut().insert(request_id);
            next.run(request).await
        }
        Err(PrincipalVerificationError::Missing) => {
            warn!(request_id = %request_id.0, "Gateway Principal header is missing");
            ErrorResponse::unauthenticated(request_id.0).into_response()
        }
        Err(PrincipalVerificationError::Invalid(reason)) => {
            warn!(request_id = %request_id.0, %reason, "Gateway Principal verification failed");
            ErrorResponse::unauthenticated(request_id.0).into_response()
        }
        Err(PrincipalVerificationError::Unavailable) => {
            super::application_error_response(&request_id,
                bcs_service_api::application::v1::ApplicationError::bad_gateway(
                    "bot_identity_unavailable", "Bot identity store unavailable",
                )).into_response()
        }
    }
}
