//! Deploy profile: built-in default endpoints for `register`.

use anyhow::{Result, bail};

/// Supplies endpoint defaults when they are not configured explicitly.
/// Each method is called only when its endpoint is actually needed, so a
/// profile may fail for configurations that do not need a default.
pub trait DeployProfile: Send + Sync {
    /// Registration API prefix, used when neither `--api-url` nor
    /// `[registration] api_url` is set.
    fn default_api_url(&self) -> Result<String>;
    /// BCS Bot WebSocket endpoint for plugin mode, used when `--upstream-url`
    /// is not set.
    fn default_upstream_url(&self) -> Result<String>;
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
