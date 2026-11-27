//! Attach deployment credentials at the A2A HTTP send boundary.

use std::sync::Arc;

use a2a::A2AError;
use bcs_app_identity_api::{CredentialError, OutboundAppIdentity, OutboundTarget, SensitiveHeader};
use bcs_network_tracing::{ClientWithDefaults, RequestWithDefaults};
use reqwest::header::{HeaderName, HeaderValue};

const DESTINATION_REJECTED: &str = "A2A application identity destination rejected";
const CREDENTIAL_UNAVAILABLE: &str = "A2A application credential unavailable";
const INVALID_CREDENTIAL: &str = "A2A application credential header invalid";

pub(super) fn identity_failure(error: &A2AError) -> Option<&'static str> {
    // Only the local HTTP adapter emits these messages. Remote decoders
    // replace peer messages with fixed diagnostics.
    match error.message.as_str() {
        DESTINATION_REJECTED => Some(DESTINATION_REJECTED),
        CREDENTIAL_UNAVAILABLE => Some(CREDENTIAL_UNAVAILABLE),
        INVALID_CREDENTIAL => Some(INVALID_CREDENTIAL),
        _ => None,
    }
}

pub(super) struct HttpClient {
    pub(super) transport: ClientWithDefaults<reqwest::Client>,
    identity: Option<(Arc<dyn OutboundAppIdentity>, String)>,
    require_identity: bool,
}

impl HttpClient {
    pub(super) fn new(transport: ClientWithDefaults<reqwest::Client>) -> Self {
        Self { transport, identity: None, require_identity: false }
    }

    pub(super) fn with_identity(
        mut self,
        policy: Arc<dyn OutboundAppIdentity>,
        provider_id: String,
    ) -> Self {
        self.identity = Some((policy, provider_id));
        self
    }

    pub(super) fn require_identity(mut self) -> Self {
        self.require_identity = true;
        self
    }

    pub(super) async fn send_observed(
        &self,
        request: RequestWithDefaults<reqwest::RequestBuilder>,
        connection_error: &'static str,
        guard: &mut crate::digest::DigestGuard,
    ) -> Result<reqwest::Response, A2AError> {
        let result = self.send_inner(request, connection_error, guard).await;
        guard.observe_err(result)
    }

    async fn send_inner(
        &self,
        request: RequestWithDefaults<reqwest::RequestBuilder>,
        connection_error: &'static str,
        guard: &mut crate::digest::DigestGuard,
    ) -> Result<reqwest::Response, A2AError> {
        let mut request = request.build().map_err(|_| A2AError::internal(connection_error))?;
        guard.set_req_bytes(request.body().and_then(|body| body.as_bytes()).map_or(0, |b| b.len()));
        if let Some((identity, provider_id)) = &self.identity {
            let origin = request.url().origin().ascii_serialization();
            let target = OutboundTarget { provider_id, origin: &origin };
            let credential = identity.credential_for(&target).map_err(|error| {
                A2AError::internal(match error {
                    CredentialError::DestinationRejected => DESTINATION_REJECTED,
                    _ => CREDENTIAL_UNAVAILABLE,
                })
            })?;
            match credential {
                Some(credential) => apply(&mut request, credential)?,
                None if self.require_identity => return Err(A2AError::internal(CREDENTIAL_UNAVAILABLE)),
                None => {}
            }
        } else if self.require_identity {
            return Err(A2AError::internal(CREDENTIAL_UNAVAILABLE));
        }
        // A required identity is scoped to the pre-release test submission.
        let diagnostic = self.require_identity
            .then(|| crate::test_response::SensitiveRequestValues::from_headers(request.headers()));
        // Keeps whether a connection was established at all (see `transport_error`):
        // a connect failure becomes `NOT_CONNECTED` (certainly not sent), any
        // other failure the generic `connection_error` (outcome unknown).
        let (result, correlation) = self.transport.execute_traced_with_correlation(request).await;
        if let Some(correlation) = correlation { guard.set_log_correlation(&correlation); }
        let mut response = result.map_err(|error| {
            let (rc, ec) = crate::digest::classify_reqwest_error(&error);
            guard.finish_err(rc, ec);
            crate::transport_error::send_failure(&error, connection_error)
        })?;
        if let Some(diagnostic) = diagnostic {
            response.extensions_mut().insert(diagnostic);
        }
        Ok(response)
    }
}

fn apply(request: &mut reqwest::Request, credential: SensitiveHeader) -> Result<(), A2AError> {
    let invalid = || A2AError::internal(INVALID_CREDENTIAL);
    let name = HeaderName::try_from(credential.name).map_err(|_| invalid())?;
    let header = name.as_str();
    if request.headers().contains_key(&name)
        || header.starts_with("a2a-")
        || header.starts_with("x-b3-")
        || matches!(header,
            "authorization" | "proxy-authorization" | "host" | "cookie"
            | "accept" | "content-type" | "content-length" | "transfer-encoding" | "connection"
            | "upgrade" | "te" | "trailer" | "expect" | "traceparent" | "tracestate"
            | "baggage" | "b3" | "x-trace-id" | "x-request-id")
    {
        return Err(invalid());
    }
    let value = credential.value.expose_secret();
    if value.trim().is_empty() {
        return Err(invalid());
    }
    let mut value = HeaderValue::from_str(value).map_err(|_| invalid())?;
    value.set_sensitive(true);
    request.headers_mut().insert(name, value);
    Ok(())
}

#[cfg(test)]
mod tests;
