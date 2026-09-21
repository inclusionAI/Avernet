//! Bot-owned Provider metadata and its gateway-only compatibility projection.
use async_trait::async_trait;
use crate::{BotCapabilities, ServiceResult};
use crate::bot_provider::{BotConnectionMode, BotProviderRecord};

#[async_trait]
pub trait BotProviderRepoPort: Send + Sync {
    /// Includes tombstones so a deleted Provider/ref cannot be silently reused.
    async fn get_provider_bot(&self, bot_uuid: &str) -> ServiceResult<Option<BotProviderRecord>>;

    async fn get_provider_bot_by_ref(&self, provider_id: &str, provider_bot_ref: &str) -> ServiceResult<Option<BotProviderRecord>>;

    /// None selects every Provider; includes tombstones, for migration/read adapters.
    async fn list_provider_bot_metadata(&self, provider_id: Option<&str>) -> ServiceResult<Vec<BotProviderRecord>>;

    /// None means missing Bot. An existing, unmigrated Bot is an error.
    async fn get_connection_mode(&self, bot_uuid: &str) -> ServiceResult<Option<BotConnectionMode>>;

    /// Attach an existing active Bot without replacing its credential or owner.
    /// Allows the existing upstream-to-gateway transition, never Provider transfer
    /// or gateway-to-upstream. Maintains gateway projection atomically in SQL.
    async fn attach_provider_bot(&self, record: BotProviderRecord) -> ServiceResult<()>;

    /// Strict create: do not replace an existing Bot, token or Provider/ref.
    /// SQL implementations atomically create the Bot and gateway projection.
    /// Upstream must never create a compatibility binding.
    async fn create_provider_bot(
        &self, record: BotProviderRecord, capabilities: BotCapabilities,
        owner: &str, token: &str,
    ) -> ServiceResult<()>;

    /// Atomically update the gateway Bot override and compatibility binding.
    /// Authorization and effective-endpoint validation belong to the core.
    async fn update_provider_webhook(
        &self, provider_id: &str, bot_uuid: &str, webhook_url: Option<String>, updated_at: u64,
    ) -> ServiceResult<BotProviderRecord>;

    /// Soft-delete the Bot and disable its gateway compatibility projection.
    /// Returns false for an already-deleted Bot. Never releases its Provider/ref.
    async fn delete_provider_bot(
        &self, provider_id: &str, bot_uuid: &str, updated_at: u64,
    ) -> ServiceResult<bool>;
}
