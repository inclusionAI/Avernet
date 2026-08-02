use async_trait::async_trait;
use axum::extract::{Request, State};
use axum::http::HeaderMap;
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use bcs_service_api::application::v1::AuthenticatedCaller;

use super::{ApiState, ErrorResponse, RequestId};

#[derive(Debug, thiserror::Error)]
pub enum PrincipalVerificationError {
    #[error("Principal is missing")]
    Missing,
    #[error("Principal is invalid: {0}")]
    Invalid(String),
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

pub async fn verify_principal(
    State(state): State<ApiState>,
    mut request: Request,
    next: Next,
) -> Response {
    let request_id = RequestId::from_headers(request.headers());
    match state.principal_verifier.verify(request.headers()).await {
        Ok(caller) => {
            request.extensions_mut().insert(caller);
            request.extensions_mut().insert(request_id);
            next.run(request).await
        }
        Err(PrincipalVerificationError::Missing | PrincipalVerificationError::Invalid(_)) => {
            ErrorResponse::unauthenticated(request_id.0).into_response()
        }
    }
}
