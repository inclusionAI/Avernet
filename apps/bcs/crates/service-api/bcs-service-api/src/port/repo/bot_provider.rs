//! Bot-owned Provider metadata and its gateway-only compatibility projection.
use async_trait::async_trait;
use crate::types::OwnershipInitialization;
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
    /// Repeated identical affiliation does not update the webhook or timestamps.
    async fn attach_provider_bot(&self, record: BotProviderRecord) -> ServiceResult<()>;

    /// Strict create: do not replace an existing Bot, token or Provider/ref.
    /// SQL implementations atomically create the Bot and gateway projection.
    /// Upstream must never create a compatibility binding.
    async fn create_provider_bot(
        &self, record: BotProviderRecord, capabilities: BotCapabilities,
        owner: &str, token: &str,
    ) -> ServiceResult<()>;

    /// Strict create consuming the trusted first-ownership
    /// [`OwnershipInitialization`] in the SAME single commit as the Bot
    /// and gateway-projection INSERTs (plan Task 5): the plain
    /// [`create_provider_bot`](Self::create_provider_bot) SQL steps are
    /// extended with the ownership CAS (version 0 -> 1), the unique
    /// approved owner edge, the Human actor materialization, the default
    /// permission-profile ensure and the initialization audit row
    /// (`bot_ownership_initializations`, source `registration`). Any step
    /// failure rolls the Bot, its binding and the authority state back
    /// together; a successful retry must not replay credentials or
    /// duplicate authority rows.
    async fn create_provider_bot_with_initialization(
        &self, record: BotProviderRecord, capabilities: BotCapabilities,
        owner: &str, token: &str,
        initialization: OwnershipInitialization,
    ) -> ServiceResult<()> {
        let _ = (record, capabilities, owner, token, initialization);
        Err(crate::ServiceError::InternalError(
            "Bot Provider creation with ownership initialization is not configured".into(),
        ))
    }

    /// Atomically update the gateway Bot override and compatibility binding.
    /// Authorization and effective-endpoint validation belong to the core.
    /// No caller version is required; explicit updates use write ordering.
    /// updated_at is the legacy binding timestamp input, not a Bot revision.
    async fn update_provider_webhook(
        &self, provider_id: &str, bot_uuid: &str, webhook_url: Option<String>, updated_at: u64,
    ) -> ServiceResult<BotProviderRecord>;

    /// Soft-delete the Bot and disable its gateway compatibility projection.
    /// Returns false for an already-deleted Bot. Never releases its Provider/ref.
    /// updated_at is for the legacy binding only; no metadata version is stored.
    async fn delete_provider_bot(
        &self, provider_id: &str, bot_uuid: &str, updated_at: u64,
    ) -> ServiceResult<bool>;
}
