//! Synthetic credentials for local A2A HTTP peers in the Singlebox story.

use std::sync::{
    Arc,
    atomic::{AtomicU64, Ordering},
};

use bcs_app_identity_api::{
    CredentialError, OutboundAppIdentity, OutboundAppIdentityConfig, OutboundAppIdentityFactory,
    OutboundTarget, SensitiveHeader,
};
use bcs_domain::RedactedToken;

#[derive(Default)]
struct LoopbackIdentity(AtomicU64);

impl OutboundAppIdentity for LoopbackIdentity {
    fn credential_for(
        &self,
        target: &OutboundTarget<'_>,
    ) -> Result<Option<SensitiveHeader>, CredentialError> {
        if !target.origin.starts_with("http://127.0.0.1:") {
            return Ok(None);
        }
        let sequence = self.0.fetch_add(1, Ordering::Relaxed);
        Ok(Some(SensitiveHeader {
            name: "x-e2e-app-identity".into(),
            value: RedactedToken::new(format!("singlebox-{sequence}")),
        }))
    }
}

fn build(config: &OutboundAppIdentityConfig) -> Result<Arc<dyn OutboundAppIdentity>, CredentialError> {
    if !config.options.is_empty() {
        return Err(CredentialError::InvalidConfiguration);
    }
    Ok(Arc::new(LoopbackIdentity::default()))
}

inventory::submit! {
    OutboundAppIdentityFactory { name: "e2e-loopback-identity", build }
}
