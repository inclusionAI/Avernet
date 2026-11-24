//! Attach deployment credentials at the A2A HTTP send boundary.

use std::sync::Arc;

use a2a::A2AError;
use bcs_app_identity_api::{CredentialError, OutboundAppIdentity, OutboundTarget, SensitiveHeader};
use bcs_network_tracing::{ClientExt, ClientWithDefaults, RequestWithDefaults};
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

    pub(super) async fn send(
        &self,
        request: RequestWithDefaults<reqwest::RequestBuilder>,
        connection_error: &'static str,
    ) -> Result<reqwest::Response, A2AError> {
        let mut request = request.build().map_err(|_| A2AError::internal(connection_error))?;
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
        // Keeps whether a connection was established at all (see `transport_error`).
        self.transport
            .execute_traced(request)
            .await
            .map_err(|error| crate::transport_error::send_failure(&error, connection_error))
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
