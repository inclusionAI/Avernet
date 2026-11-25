//! Trusted composition policy for request-only Provider HTTP headers.
//! Implementations receive runtime data only after caller credentials have been
//! resolved. Returned secrets must never enter jobs, history, logs or caches.
use bcs_domain::RedactedToken;
use serde_json::Value;

pub struct ProviderRequestHeaderContext<'a> {
    pub method: &'a str,
    pub provider_id: &'a str,
    pub provider_bot_ref: &'a str,
    pub extensions: Option<&'a Value>,
}

/// Optional deployment-owned policy. Public composition installs no policy.
/// Do not block or perform I/O. Header names are validated by the HTTP adapter;
/// service authentication and protocol headers cannot be replaced. Invalid
/// names or values fail closed before network I/O. Values are marked sensitive.
pub trait ProviderRequestHeaderPolicy: Send + Sync {
    fn headers(&self, context: &ProviderRequestHeaderContext<'_>) -> Vec<(String, RedactedToken)>;
}
