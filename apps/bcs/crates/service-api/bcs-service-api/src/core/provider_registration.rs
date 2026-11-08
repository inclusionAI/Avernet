//! Scoped registration, independent of Provider-admin and delivery bindings.
use crate::ServiceResult;
pub use crate::types::bot_authority::OwnershipInitialization;
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
    /// Mode from a verified registration token, never from an HTTP request field.
    /// Historical tokens omit it and retain their existing registration semantics.
    pub token_auth_mode: Option<bcs_domain::ProviderAuthMode>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProviderRegistrationAuthorization {
    pub allowed_modes: Vec<ProviderRegistrationMode>,
    pub auth_mode: bcs_domain::ProviderAuthMode,
}

pub struct ProviderRegistrationResult {
    pub record: ProviderRegistrationRecord,
    pub effective_webhook_url: Option<String>,
    pub identity: bcs_domain::provider_identity::ProviderBotIdentity,
    pub auth_mode: bcs_domain::ProviderAuthMode,
}

#[async_trait]
pub trait ProviderRegistrationCoreService: Send + Sync {
    /// Checks current existence, enabled state and owner/self-service policy.
    /// Issuance deliberately does not require a delivery endpoint.
    async fn authorize(
        &self,
        provider_id: &str,
        owner: &str,
    ) -> ServiceResult<ProviderRegistrationAuthorization>;

    /// Reauthorizes and creates a scoped Bot. Duplicate Provider/ref conflicts;
    /// no credentials are replayed. Human/owner-edge failures propagate and may
    /// require explicit reconciliation after the Bot transaction has committed.
    ///
    /// Trusted v2 registration surfaces call
    /// [`Self::register_with_ownership`] instead.
    async fn register(
        &self,
        command: RegisterProviderBot,
    ) -> ServiceResult<ProviderRegistrationResult>;

    /// Trusted scoped registration: the existing Provider admission,
    /// mode/ref/webhook validation and result types are reused, and
    /// the repo's composite create commits Bot, Human, owner,
    /// ownership version, default profile, gateway projection and
    /// the initialization audit in ONE transaction.
    /// `initialization.owner_user_id` comes only from the verified
    /// registration token and the mutation keeps the real initiator;
    /// failure returns no credential and publishes nothing.
    async fn register_with_ownership(
        &self,
        _command: RegisterProviderBot,
        _initialization: OwnershipInitialization,
    ) -> ServiceResult<ProviderRegistrationResult> {
        Err(crate::ServiceError::InternalError(
            "trusted provider registration is not configured".into(),
        ))
    }
}
