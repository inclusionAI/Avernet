//! baas plugin config. Originates from `StorageBackendConfig.backend` keys:
//! endpoint (host only), tenant, share_link_ttl, health_probe_path, auth
//! header bearers, timeouts. The factory parses & validates these.

use std::time::Duration;

#[derive(Debug, Clone)]
pub struct BaasConfig {
    /// baas host only, e.g. "http://baas.xxx:8080" (no API path).
    pub endpoint: String,
    /// Tenant segment in baas session path.
    pub tenant: String,
    /// Seconds for share-link expire_seconds (in-session + share download).
    pub share_link_ttl: u64,
    /// Optional health path relative to endpoint ("", "/health"...).
    pub health_probe_path: String,
    /// Auth header(s) to attach to every baas request (plugin-held, never leaked to clients).
    pub auth_headers: Vec<(String, String)>,
    pub http_timeout: Duration,
}

impl Default for BaasConfig {
    fn default() -> Self {
        Self {
            endpoint: String::new(),
            tenant: String::new(),
            share_link_ttl: 3600,
            health_probe_path: String::new(),
            auth_headers: Vec::new(),
            http_timeout: Duration::from_secs(30),
        }
    }
}