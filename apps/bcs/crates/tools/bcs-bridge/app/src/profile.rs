//! Deploy profile: registration defaults and distribution-owned CLI environment.

use std::collections::BTreeMap;
use anyhow::{Result, bail};
use bcs_bridge_core::config::ProviderConfig;

/// Supplies registration endpoint defaults and runtime CLI settings.
/// Endpoint default methods are called only when their endpoint is needed.
/// The CLI environment hook is called when loading a runtime configuration.
pub trait DeployProfile: Send + Sync {
    /// Registration API prefix, used when neither `--api-url` nor
    /// `[registration] api_url` is set.
    fn default_api_url(&self) -> Result<String>;
    /// BCS Bot WebSocket endpoint for plugin mode, used when `--upstream-url`
    /// is not set.
    fn default_upstream_url(&self) -> Result<String>;

    /// Resolve non-secret CLI settings from the saved runtime endpoints.
    /// Called for each Provider when loading the startup configuration. The default
    /// leaves the configured API URL in use. Overrides affect owned engine
    /// children only, never the registration API, plugin WebSocket or process
    /// environment. Bot identity and tokens belong to ProviderConfig, not here.
    fn cli_environment(&self, _config: &ProviderConfig) -> Result<BTreeMap<String, String>> {
        Ok(BTreeMap::new())
    }
}

/// No built-in endpoints: every endpoint must be configured explicitly.
pub struct ExplicitProfile;

impl DeployProfile for ExplicitProfile {
    fn default_api_url(&self) -> Result<String> {
        bail!("No registration API is configured; pass --api-url or set [registration] api_url in the bridge configuration")
    }

    fn default_upstream_url(&self) -> Result<String> {
        bail!("No BCS Bot WebSocket endpoint is configured; pass --upstream-url for plugin mode")
    }
}
