//! Keep defaults outside reqwest so propagation can delete them before sending.
use std::sync::Arc;
use reqwest::header::HeaderMap;

/// A configured transport with defaults merged before tracing, not at wire time.
///
/// Pass a transport built without custom `reqwest::ClientBuilder::default_headers`.
/// Supply those headers here instead. The original transport retains its pools,
/// TLS, proxy, timeout and redirect policy. Raw reqwest extension methods remain
/// available for clients without default propagation fields.
#[derive(Clone)]
pub struct ClientWithDefaults<C> {
    pub(crate) client: C,
    pub(crate) headers: Arc<HeaderMap>,
}

impl<C> From<C> for ClientWithDefaults<C> {
    fn from(client: C) -> Self { Self::new(client, HeaderMap::new()) }
}

impl<C> ClientWithDefaults<C> {
    pub fn new(client: C, headers: HeaderMap) -> Self {
        Self { client, headers: Arc::new(headers) }
    }

    /// Configure a request using the transport's normal request builder API.
    pub fn request<B>(&self, build: impl FnOnce(&C) -> B) -> RequestWithDefaults<B> {
        RequestWithDefaults { builder: build(&self.client), headers: self.headers.clone() }
    }
}

/// Defaults are applied only after the caller has finished configuring headers.
pub struct RequestWithDefaults<B> {
    pub(crate) builder: B,
    pub(crate) headers: Arc<HeaderMap>,
}

impl<B> RequestWithDefaults<B> {
    pub fn map(self, configure: impl FnOnce(B) -> B) -> Self {
        Self { builder: configure(self.builder), headers: self.headers }
    }
}

pub(crate) fn merge_defaults(headers: &mut HeaderMap, defaults: &HeaderMap) {
    for name in defaults.keys() {
        if !headers.contains_key(name) {
            // Preserve repeated values and HeaderValue's sensitive flag. A
            // request override replaces all default values of that field.
            for value in defaults.get_all(name) { headers.append(name, value.clone()); }
        }
    }
}
