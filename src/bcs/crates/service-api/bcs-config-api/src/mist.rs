//! Mist (secret management) configuration — pure data.
//!
//! This struct only carries the values bootstrap will hand to a secret backend.

use serde::{Deserialize, Serialize};

/// Mist client configuration.
///
/// Fields that map directly to secret-backend init metadata are kept as
/// provider-neutral data here.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MistConfig {
    /// Enable mist. When false the client is constructed in a disabled state
    /// and all calls return `SecretError::Disabled`.
    #[serde(default = "default_enabled")]
    pub enabled: bool,

    /// gRPC endpoint of the local secret sidecar.
    /// Must use the `http://` scheme for plaintext local sidecars.
    #[serde(default = "default_endpoint")]
    pub endpoint: String,

    /// Business tenant. Common values: `"ALIPAY"`, `"MYBANK"`. Required.
    #[serde(default = "default_tenant")]
    pub tenant: String,

    /// Mist environment mode. Must match what the mist platform registered
    /// for this app, typically one of `"dev" | "test" | "stable" | "prod"`.
    #[serde(default = "default_mode")]
    pub mode: String,

    /// Application name on the mist platform. Required for prod, optional
    /// for dev (where the SDK may derive it).
    #[serde(default)]
    pub app_name: String,

    /// Optional overrides passed through to provider metadata if non-empty.
    #[serde(default)]
    pub app_zone: Option<String>,
    #[serde(default)]
    pub secret_server: Option<String>,
    #[serde(default)]
    pub antvip_url: Option<String>,
    #[serde(default)]
    pub mesh_url: Option<String>,
    #[serde(default)]
    pub log_path: Option<String>,

    /// Secrets to pre-warm at init time (the sidecar fetches them upfront so
    /// the first `GetSecret` call avoids the network round-trip).
    #[serde(default)]
    pub secret_list: Vec<String>,

    /// gRPC dial timeout in seconds.
    #[serde(default = "default_timeout_secs")]
    pub connect_timeout_secs: u64,

    /// gRPC per-request timeout in seconds.
    #[serde(default = "default_timeout_secs")]
    pub request_timeout_secs: u64,
}

fn default_enabled() -> bool {
    false
}
fn default_endpoint() -> String {
    "http://127.0.0.1:11004".into()
}
fn default_tenant() -> String {
    "ALIPAY".into()
}
fn default_mode() -> String {
    "dev".into()
}
fn default_timeout_secs() -> u64 {
    5
}

impl Default for MistConfig {
    fn default() -> Self {
        Self {
            enabled: default_enabled(),
            endpoint: default_endpoint(),
            tenant: default_tenant(),
            mode: default_mode(),
            app_name: String::new(),
            app_zone: None,
            secret_server: None,
            antvip_url: None,
            mesh_url: None,
            log_path: None,
            secret_list: Vec::new(),
            connect_timeout_secs: default_timeout_secs(),
            request_timeout_secs: default_timeout_secs(),
        }
    }
}

impl MistConfig {
    /// Convenience constructor used by composition roots.
    pub fn new(tenant: impl Into<String>, mode: impl Into<String>) -> Self {
        Self {
            enabled: true,
            tenant: tenant.into(),
            mode: mode.into(),
            ..Self::default()
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_is_disabled_and_targets_local_sidecar() {
        let cfg = MistConfig::default();
        assert!(!cfg.enabled);
        assert_eq!(cfg.endpoint, "http://127.0.0.1:11004");
        assert_eq!(cfg.tenant, "ALIPAY");
        assert_eq!(cfg.mode, "dev");
    }

    #[test]
    fn yaml_round_trip_keeps_optional_fields() {
        let yaml = r#"
enabled: true
endpoint: "http://127.0.0.1:11004"
tenant: "ALIPAY"
mode: "prod"
app_name: "agentclawproxy"
secret_list:
  - "other_manual_agentclawproxy_proxypass_secret"
"#;
        let cfg: MistConfig = serde_yaml::from_str(yaml).unwrap();
        assert!(cfg.enabled);
        assert_eq!(cfg.app_name, "agentclawproxy");
        assert_eq!(cfg.secret_list.len(), 1);
    }
}
