//! Deployment-supplied application credentials for built-in A2A HTTP delivery.

use std::{collections::BTreeMap, sync::Arc};

use bcs_domain::RedactedToken;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct OutboundAppIdentityConfig {
    pub plugin: String,
    /// Plugin-specific destination settings. Configuration contains no credentials.
    #[serde(default)]
    pub options: BTreeMap<String, serde_json::Value>,
}

pub struct OutboundTarget<'a> {
    pub provider_id: &'a str,
    /// Canonical HTTP origin after outbound address validation.
    pub origin: &'a str,
}

pub struct SensitiveHeader {
    pub name: String,
    pub value: RedactedToken,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum CredentialError {
    #[error("invalid application identity configuration")]
    InvalidConfiguration,
    #[error("application identity destination rejected")]
    DestinationRejected,
    #[error("application credential unavailable")]
    Unavailable,
}

/// Called immediately before each A2A HTTP business request. Return `None`
/// for an unconfigured target and an error when its required credential
/// cannot be supplied. Implementations must be safe for concurrent calls and
/// must not block or do network I/O here. Credentials live only on the request.
pub trait OutboundAppIdentity: Send + Sync {
    fn credential_for(
        &self,
        target: &OutboundTarget<'_>,
    ) -> Result<Option<SensitiveHeader>, CredentialError>;
}

pub struct OutboundAppIdentityFactory {
    pub name: &'static str,
    pub build: fn(&OutboundAppIdentityConfig) -> Result<Arc<dyn OutboundAppIdentity>, CredentialError>,
}

inventory::collect!(OutboundAppIdentityFactory);

/// Called by the composition root. Missing or ambiguous factory names fail.
pub fn build_outbound_app_identity(
    config: &OutboundAppIdentityConfig,
) -> Result<Arc<dyn OutboundAppIdentity>, CredentialError> {
    let mut matches = inventory::iter::<OutboundAppIdentityFactory>
        .into_iter()
        .filter(|factory| factory.name == config.plugin);
    let factory = matches.next().ok_or(CredentialError::InvalidConfiguration)?;
    if config.plugin.trim().is_empty() || matches.next().is_some() {
        return Err(CredentialError::InvalidConfiguration);
    }
    (factory.build)(config)
}
