use async_trait::async_trait;
use axum::extract::{Request, State};
use axum::http::HeaderMap;
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use tracing::warn;

use super::{ErrorResponse, PrincipalVerificationState, RequestId};
use super::authentication::{VerifiedRequestIdentity, VerificationAttempt};

/// Error taxonomy for `PrincipalVerifier`. Display never includes SQL,
/// endpoints, secrets, or token fragments — only the category name. HTTP
/// mapping is done in `error.rs`/`principal.rs::verify_principal`: Missing →
/// 401 (chain exhausted), Invalid → 401, Forbidden → 403, Unavailable → 503,
/// Internal → 500.
#[derive(Clone, Debug, PartialEq, Eq, thiserror::Error)]
pub enum PrincipalVerificationError {
    #[error("Principal is missing")]
    Missing,
    #[error("Principal is invalid")]
    Invalid,
    #[error("Principal is forbidden")]
    Forbidden,
    #[error("Principal verifier is unavailable")]
    Unavailable,
    #[error("Principal verifier encountered an internal error")]
    Internal,
}

/// Gateway-to-BCN trust boundary.
///
/// Production bootstrap must inject the approved verifier or
/// `CompositePrincipalVerifier` chain. This crate does not provide a
/// verifier that trusts an unsigned Principal header.
///
/// The verifier returns `VerifiedRequestIdentity` (caller identity plus
/// provenance metadata) so the `verify_principal` middleware can run an
/// Origin CSRF check keyed off the ACTUAL source before the request reaches
/// downstream use cases — see `csrf::TrustedBrowserOrigins::validate`.
#[async_trait]
pub trait PrincipalVerifier: Send + Sync {
    async fn verify(
        &self,
        headers: &HeaderMap,
    ) -> Result<VerifiedRequestIdentity, PrincipalVerificationError>;

    /// Same contract as `verify`, but receives a per-request `VerificationAttempt`
    /// so a verifier that performs OAuth-session verification can cache its
    /// strict result for the duration of a single request. Default delegates
    /// to `verify`. Composite calls this in chain order.
    async fn verify_in_attempt(
        &self,
        headers: &HeaderMap,
        _attempt: &mut VerificationAttempt,
    ) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
        self.verify(headers).await
    }
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
    let method = request.method().clone();
    let origin = request
        .headers()
        .get(axum::http::header::ORIGIN)
        .cloned();
    let verifier = state.principal_verifier().clone();
    match verifier.verify(request.headers()).await {
        Ok(verified) => {
            if let Some(origins) = state.trusted_browser_origins() {
                if let Err(csrf_error) =
                    origins.validate(&method, origin.as_ref(), &verified.authentication_context)
                {
                    warn!(
                        request_id = %request_id.0,
                        source = %verified.authentication_context.source,
                        error = ?csrf_error,
                        "Origin check rejected by trusted browser origins"
                    );
                    return principal_error_response(&request_id.0, csrf_error);
                }
            }
            request.extensions_mut().insert(verified.caller);
            request.extensions_mut().insert(request_id);
            next.run(request).await
        }
        Err(error) => principal_error_response(&request_id.0, error),
    }
}

fn principal_error_response(
    request_id: &str,
    error: PrincipalVerificationError,
) -> Response {
    match error {
        PrincipalVerificationError::Missing => {
            warn!(request_id = request_id, "Principal verifier chain exhausted with no successful source");
            ErrorResponse::unauthenticated(request_id).into_response()
        }
        PrincipalVerificationError::Invalid => {
            warn!(request_id = request_id, "Principal verification rejected the request");
            ErrorResponse::unauthenticated(request_id).into_response()
        }
        PrincipalVerificationError::Forbidden => {
            ErrorResponse::forbidden(request_id).into_response()
        }
        PrincipalVerificationError::Unavailable => {
            ErrorResponse::unavailable(request_id).into_response()
        }
        PrincipalVerificationError::Internal => {
            warn!(request_id = request_id, "Principal verifier internal error");
            ErrorResponse::internal(request_id).into_response()
        }
    }
}
