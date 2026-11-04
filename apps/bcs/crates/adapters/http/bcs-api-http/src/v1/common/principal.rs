use async_trait::async_trait;
use axum::extract::{Request, State};
use axum::http::{HeaderMap, Method};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use bcs_service_api::application::v1::ApplicationError;
use tracing::warn;

use super::{
    AuthenticationContext, ErrorResponse, PrincipalVerificationState, RequestId,
    VerifiedRequestIdentity, application_error_response,
};

/// Principal verification outcome. `Missing` is the only variant that
/// continues to a later source in the ordered chain (via
/// `CompositePrincipalVerifier`); every other variant is final.
///
/// `Unavailable` keeps its existing mapping for Bot-identity-store faults
/// (502 / `bot_identity_unavailable`); `DependencyUnavailable` carries the
/// new strict-auth dependency fault (503 / `auth_dependency_unavailable`).
#[derive(Debug, Clone, thiserror::Error)]
pub enum PrincipalVerificationError {
    #[error("Principal is missing")]
    Missing,
    #[error("Principal is invalid: {0}")]
    Invalid(String),
    #[error("Principal is forbidden: {0}")]
    Forbidden(String),
    #[error("Bot identity store is unavailable")]
    Unavailable,
    #[error("Authentication dependency is unavailable")]
    DependencyUnavailable,
    #[error("Principal verification failed internally: {0}")]
    Internal(String),
}

/// Gateway-to-BCN trust boundary. A verifier resolves the request's actual
/// credential and returns the business caller PLUS the provenance it pinned:
/// the registered source name (fixed by the registering wrapper, never from
/// request headers) and the credential kind actually used.
///
/// Production bootstrap must inject the approved verifier chain. This crate
/// does not provide a verifier that trusts an unsigned Principal header.
#[async_trait]
pub trait PrincipalVerifier: Send + Sync {
    async fn verify(
        &self,
        headers: &HeaderMap,
    ) -> Result<VerifiedRequestIdentity, PrincipalVerificationError>;

    /// Ordered-chain entry point: `CompositePrincipalVerifier` creates one
    /// attempt per request and passes it here. The default delegates to
    /// `verify`; only a plugin that can be consulted more than once per
    /// request (the strict OAuth session source) overrides this to consult
    /// the request-local cache.
    async fn verify_in_attempt(
        &self,
        headers: &HeaderMap,
        _attempt: &mut super::VerificationAttempt,
    ) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
        self.verify(headers).await
    }
}

/// Read `Origin` without ever silently selecting the first value: zero or
/// one valid UTF-8 value is fine; duplicates or non-UTF-8 are rejected by
/// the adapter before `TrustedBrowserOrigins::validate` runs.
fn strict_request_origin(headers: &HeaderMap) -> Result<Option<&str>, ()> {
    let mut values = headers.get_all("origin").iter();
    let first = values.next();
    if values.next().is_some() {
        return Err(());
    }
    match first {
        Some(value) => value.to_str().map(Some).map_err(|_| ()),
        None => Ok(None),
    }
}

/// Per-actual-source CSRF gate (spec §7.4): runs after first-source success,
/// reads only the pinned context, and rejects with 403 without re-entering
/// the authentication chain to try another source.
fn enforce_browser_origin<S>(
    state: &S,
    request_id: &RequestId,
    method: &Method,
    headers: &HeaderMap,
    context: &AuthenticationContext,
) -> Result<(), Response>
where
    S: PrincipalVerificationState,
{
    if !context.credential_kind.is_cookie_credential() {
        // Header/token provenance carries no ambient authority, and the
        // legacy native Human router keeps its own browser_boundary writes
        // (spec §7.4: the new chain's trusted_browser_origins is not
        // applied to the /collaboration/user router).
        return Ok(());
    }
    // Fail closed: a cookie-kind source that succeeded while no trusted
    // origins were composed is a wiring fault — an unsafe write must not
    // pass just because the list is missing.
    let Some(origins) = state.trusted_browser_origins() else {
        warn!(request_id = %request_id.0,
            "Cookie-authenticated source succeeded without trusted browser origins configured");
        return Err(csrf_response(request_id, ApplicationError::forbidden(
            "Trusted browser origins are not configured; cookie-authenticated writes are denied",
        )));
    };
    let origin = strict_request_origin(headers).map_err(|_| {
        csrf_response(request_id, ApplicationError::forbidden(
            "A single valid browser Origin is required for cookie-authenticated writes",
        ))
    })?;
    origins
        .validate(method, origin, context)
        .map_err(|error| match error {
            // Only Forbidden escapes validate for cookie kinds; map it onto
            // the response contract exactly like the middleware's error arm.
            PrincipalVerificationError::Forbidden(message) => {
                csrf_response(request_id, ApplicationError::forbidden(message))
            }
            // Defensive: an Internal surfacing from validate is a chain
            // wiring fault, not a cross-origin policy decision.
            PrincipalVerificationError::Internal(message) => {
                csrf_response(request_id, ApplicationError::internal(message))
            }
            other => csrf_response(
                request_id,
                ApplicationError::internal(format!("unexpected CSRF gate outcome: {other}")),
            ),
        })
}

/// CSRF gate responses carry the request's own id in both the envelope and
/// the response extensions (consistent with the request-id middleware
/// contract).
fn csrf_response(request_id: &RequestId, error: ApplicationError) -> Response {
    let mut response = application_error_response(request_id, error).into_response();
    response.extensions_mut().insert(request_id.clone());
    response
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
    match state
        .principal_verifier()
        .verify(request.headers())
        .await
    {
        Ok(identity) => {
            // The caller is still the plain business identity; provenance
            // stays a delivery-only fact pinned by the successful source.
            let caller = identity.caller.clone();
            if request.headers().contains_key("x-bcs-expected-user") {
                let values = request.headers().get_all("x-bcs-expected-user");
                let expected = values.iter().next().and_then(|v| v.to_str().ok());
                if values.iter().count() != 1 || expected.is_none()
                    || expected != caller.user.as_ref().map(|user| user.id.as_str()) {
                    return application_error_response(&request_id,
                        ApplicationError::forbidden_code(
                            "user_context_changed", "Current user changed; reopen the configuration",
                        )).into_response();
                }
            }
            // CSRF runs after authentication, on the ACTUAL source's
            // credential kind; an Origin rejection is final and never
            // re-runs the authentication chain to reach another source.
            if let Err(response) = enforce_browser_origin(
                &state,
                &request_id,
                &method,
                request.headers(),
                &identity.authentication_context,
            ) {
                return response;
            }
            request.extensions_mut().insert(caller);
            // Audit/refresh carrier: the pinned provenance is available as a
            // delivery extension; application/core code never consumes it.
            request.extensions_mut().insert(identity.authentication_context);
            request.extensions_mut().insert(request_id);
            next.run(request).await
        }
        Err(PrincipalVerificationError::Missing) => {
            warn!(request_id = %request_id.0, "No authentication source accepted the request");
            ErrorResponse::unauthenticated(request_id.0).into_response()
        }
        Err(PrincipalVerificationError::Invalid(reason)) => {
            warn!(request_id = %request_id.0, %reason, "Authentication source rejected the credential");
            ErrorResponse::unauthenticated(request_id.0).into_response()
        }
        Err(PrincipalVerificationError::Forbidden(reason)) => {
            warn!(request_id = %request_id.0, %reason, "Authentication source denied the principal");
            application_error_response(
                &request_id,
                ApplicationError::forbidden("Authentication refused the verified principal"),
            )
            .into_response()
        }
        Err(PrincipalVerificationError::Unavailable) => {
            application_error_response(&request_id,
                ApplicationError::bad_gateway(
                    "bot_identity_unavailable", "Bot identity store unavailable",
                )).into_response()
        }
        Err(PrincipalVerificationError::DependencyUnavailable) => {
            super::error::auth_dependency_unavailable(&request_id).into_response()
        }
        Err(PrincipalVerificationError::Internal(reason)) => {
            application_error_response(&request_id, ApplicationError::internal(reason)).into_response()
        }
    }
}
