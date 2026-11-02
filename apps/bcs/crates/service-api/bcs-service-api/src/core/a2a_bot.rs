use async_trait::async_trait;
use crate::ServiceResult;
use crate::bot_provider::BotProviderRecord;
use crate::port::repo::a2a_bot::A2aBotRecord;

/// Transport-independent Provider-admin registration result. Credentials are
/// returned only after the ownership/Card composite commits.
pub struct A2aProviderRegistrationResult {
    pub binding: crate::ProviderBotBinding,
    pub bot_runtime_token: String,
    pub capabilities: crate::BotCapabilities,
    pub identity: crate::types::provider_identity::ProviderBotIdentity,
    pub auth_mode: crate::ProviderAuthMode,
}

#[async_trait]
pub trait A2aProviderRegistrationCoreService: Send + Sync {
    async fn register(
        &self, provider_id: &str, admin_token: &str, params: crate::RegisterProviderBotParams,
        card: crate::types::a2a_bot::A2aRegistrationCard,
    ) -> ServiceResult<A2aProviderRegistrationResult>;
}

#[async_trait]
pub trait A2aBotCoreService: Send + Sync {
    async fn cards_for_bots(&self, bot_uuids: &[String]) -> ServiceResult<std::collections::BTreeMap<String, serde_json::Value>>;
    async fn discovery_cards(&self, bot_uuids: &[String], query: Option<&str>, skills: &[String])
        -> ServiceResult<std::collections::BTreeMap<String, Option<serde_json::Value>>>;
    async fn provider_binding(&self, provider_id: &str, bot_uuid: &str) -> ServiceResult<BotProviderRecord>;
    async fn get_card(&self, bot_uuid: &str) -> ServiceResult<Option<A2aBotRecord>>;
    async fn refreshable_cards(&self, after_bot_uuid: Option<&str>, limit:u32) -> ServiceResult<Vec<A2aBotRecord>>;
    /// Validates immutable affiliation and commits a complete validated Card.
    async fn commit_card(&self, record: A2aBotRecord, expected_revision: u64) -> ServiceResult<A2aBotRecord>;
}
