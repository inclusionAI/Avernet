//! Secret backend plugin API for BCS.
//!
//! This crate is the provider-neutral extension contract used by the public BCS
//! bootstrap. Public binaries include built-in `noop` and `env` secret backends;
//! product-specific binaries can link crates that submit [`SecretPluginFactory`]
//! entries through `inventory`.
//!
//! Provider crates receive only their own config table from
//! `secret.providers.<provider>`. Ant-internal providers must keep Ant-specific
//! configuration types and SDK calls in their internal crates.

use std::sync::Arc;

use bcs_config_api::SecretProviderConfig;
use bcs_service_api::port::secret::SecretAccessPort;
use futures::future::BoxFuture;
use thiserror::Error;

/// Error returned while constructing a selected secret provider.
#[derive(Debug, Error)]
pub enum SecretPluginError {
    /// The provider config table is syntactically valid TOML/JSON but invalid
    /// for this provider.
    #[error("invalid secret provider config: {0}")]
    InvalidConfig(String),

    /// The provider could not initialize its client or runtime dependencies.
    #[error("secret provider initialization failed: {0}")]
    Init(String),
}

/// A constructed secret backend registration.
#[derive(Clone)]
pub struct SecretPluginRegistration {
    /// Provider name, used for diagnostics.
    pub provider: String,

    /// Secret access implementation to expose through the BCS SecretService.
    pub access: Arc<dyn SecretAccessPort>,
}

/// Factory function implemented by linked secret provider crates.
pub type SecretPluginBuild = fn(
    SecretProviderConfig,
) -> BoxFuture<'static, Result<SecretPluginRegistration, SecretPluginError>>;

/// Inventory entry for a secret backend provider.
pub struct SecretPluginFactory {
    /// Provider name selected by `secret.provider`.
    pub name: &'static str,

    /// Build the provider from `secret.providers.<name>`.
    pub build: SecretPluginBuild,
}

inventory::collect!(SecretPluginFactory);
