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
    /// Deployment identity for endpoint aliases. Used only to prevent mixing
    /// deployments, never as an authentication or authorization decision.
    fn endpoint_scope(&self, api: Option<&str>, upstream: Option<&str>) -> Result<Option<String>> {
        endpoint_scope(api, upstream)
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

/// Endpoints of one Provider; distributions can recognize multiple aliases for
/// the same deployment without placing private domain knowledge in shared code.
pub fn endpoint_scope(api: Option<&str>, upstream: Option<&str>) -> Result<Option<String>> {
    let Some(raw) = upstream.or(api) else { return Ok(None); };
    let url = url::Url::parse(raw).map_err(|_| anyhow::anyhow!("Invalid Provider endpoint"))?;
    let host = url.host_str().ok_or_else(|| anyhow::anyhow!("Provider endpoint needs a host"))?;
    let port = url.port_or_known_default().ok_or_else(|| anyhow::anyhow!("Unsupported Provider endpoint scheme"))?;
    Ok(Some(format!("{host}:{port}")))
}

pub(crate) fn validate_environment(profile: &dyn DeployProfile, endpoints: &[(Option<String>, Option<String>)]) -> Result<()> {
    let mut selected = None;
    for (api, upstream) in endpoints {
        if let Some(scope) = profile.endpoint_scope(api.as_deref(), upstream.as_deref())? {
            if selected.as_ref().is_some_and(|previous| previous != &scope) {
                bail!("One bridge configuration must target a single BCS environment; use a separate profile");
            }
            selected = Some(scope);
        }
    }
    Ok(())
}
