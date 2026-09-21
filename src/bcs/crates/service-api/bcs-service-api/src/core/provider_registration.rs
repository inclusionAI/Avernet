//! Scoped registration, independent of Provider-admin and delivery bindings.
use crate::ServiceResult;
pub use crate::types::provider_registration::{
    ProviderRegistrationMode, ProviderRegistrationRecord,
};
use async_trait::async_trait;

#[derive(Clone)]
pub struct RegisterProviderBot {
    pub provider_id: String,
    /// Stable Provider identity. For AgentPass, the real agent code; persisted
    /// as BotCapabilities.agent_code in both plugin and gateway modes.
    pub provider_bot_ref: String,
    /// Human staff/user ID without the human_ actor prefix; taken from token.
    pub owner: String,
    pub mode: ProviderRegistrationMode,
    pub bot_name: String,
    pub webhook_url: Option<String>,
}

pub struct ProviderRegistrationResult {
    pub record: ProviderRegistrationRecord,
    pub effective_webhook_url: Option<String>,
}

#[async_trait]
pub trait ProviderRegistrationCoreService: Send + Sync {
    /// Checks current existence, enabled state and owner/self-service policy.
    /// Issuance deliberately does not require a delivery endpoint.
    async fn authorize(
        &self,
        provider_id: &str,
        owner: &str,
    ) -> ServiceResult<Vec<ProviderRegistrationMode>>;

    /// Reauthorizes and creates a scoped Bot. Duplicate Provider/ref conflicts;
    /// no credentials are replayed. Human/owner-edge failures propagate and may
    /// require explicit reconciliation after the Bot transaction has committed.
    async fn register(
        &self,
        command: RegisterProviderBot,
    ) -> ServiceResult<ProviderRegistrationResult>;
}
