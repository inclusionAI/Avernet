//! Static-link extension point. Composition roots alone call the factory selector.
use std::sync::Arc;
use bcs_config_api::CallerIdentityServiceConfig;
use crate::{CallerIdentityError, CallerIdentityPlugin};

/// Each linked implementation registers exactly one factory per configured name.
/// Factories validate their settings, never echo secrets, and fail closed.
pub struct CallerIdentityPluginFactory {
    pub name: &'static str,
    pub build: fn(&CallerIdentityServiceConfig) -> Result<Arc<dyn CallerIdentityPlugin>, CallerIdentityError>,
}

inventory::collect!(CallerIdentityPluginFactory);

/// Resolve exactly one factory; never choose an arbitrary duplicate or fall back.
pub fn build_caller_identity_plugin(
    config: &CallerIdentityServiceConfig,
) -> Result<Arc<dyn CallerIdentityPlugin>, CallerIdentityError> {
    let mut factories = inventory::iter::<CallerIdentityPluginFactory>.into_iter()
        .filter(|factory| factory.name == config.plugin);
    let factory = factories.next().ok_or(CallerIdentityError::IdentityProviderUnavailable)?;
    if config.plugin.trim().is_empty() || factories.next().is_some() {
        return Err(CallerIdentityError::InvalidRequest);
    }
    (factory.build)(config)
}
