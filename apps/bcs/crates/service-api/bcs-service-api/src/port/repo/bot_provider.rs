//! Bot-owned Provider metadata and its gateway-only compatibility projection.
use async_trait::async_trait;
use crate::{BotCapabilities, ServiceResult};
use crate::bot_provider::{BotConnectionMode, BotProviderRecord};

#[async_trait]
pub trait BotProviderRepoPort: Send + Sync {
    /// Bounded batch of fresh authorization projections. Implementations must not
    /// interpret storage failures as missing affiliation or standard policy.
    async fn collaboration_actors(&self, _ids: &[String]) -> ServiceResult<Vec<super::collaboration_actor::CollaborationActor>> {
        Err(crate::ServiceError::InternalError("collaboration policy lookup is not configured".into()))
    }
    /// Includes tombstones so a deleted Provider/ref cannot be silently reused.
    async fn get_provider_bot(&self, bot_uuid: &str) -> ServiceResult<Option<BotProviderRecord>>;

    async fn get_provider_bot_by_ref(&self, provider_id: &str, provider_bot_ref: &str) -> ServiceResult<Option<BotProviderRecord>>;

    /// None selects every Provider; includes tombstones, for migration/read adapters.
    async fn list_provider_bot_metadata(&self, provider_id: Option<&str>) -> ServiceResult<Vec<BotProviderRecord>>;

    /// None means missing Bot. An existing, unmigrated Bot is an error.
    async fn get_connection_mode(&self, bot_uuid: &str) -> ServiceResult<Option<BotConnectionMode>>;

    /// Preflight before merging an existing Bot's capabilities. The attachment
    /// write must revalidate atomically; this read alone is not a concurrency fence.
    async fn validate_provider_affiliation(&self, _provider_id: &str, _bot_uuid: &str) -> ServiceResult<()> {
        Err(crate::ServiceError::InternalError("Provider affiliation validation is not configured".into()))
    }

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

    /// Trusted atomic Provider registration with ownership
    /// initialization (bot authority spec §10.2).
    ///
    /// The same atomic semantics as
    /// [`BotRepoPort::create_registration_if_absent_with_ownership`],
    /// including the NECESSARY gateway projection (the compatibility
    /// binding for gateway-mode records) inside the SAME
    /// transaction. Duplicate Provider/ref — including tombstoned
    /// rows — conflicts and writes nothing. `initialization.
    /// owner_user_id` comes only from the verified registration
    /// token; the mutation keeps the real initiator.
    async fn create_provider_bot_with_ownership(
        &self, _record: BotProviderRecord, _capabilities: BotCapabilities,
        _owner: &str, _token: &str,
        _initialization: crate::types::bot_authority::OwnershipInitialization,
    ) -> ServiceResult<()> {
        Err(crate::types::ServiceError::InternalError(
            "trusted Provider registration is not configured".into(),
        ))
    }

    /// Atomic Provider retirement (bot authority spec §7.1):
    /// ONE command commits the Bot tombstone, the Provider/ref
    /// tombstone, the necessary binding disable, the owner/
    /// manager edge revocation, the pending-transfer closure
    /// (system decision, fixed reason) and the retire audit —
    /// with the branch re-verified under the lock (`expected_
    /// branch` mismatch is an error, never a reinterpretation).
    /// Never followed by a second ordinary delete; a repeated
    /// command on an already-tombstoned Bot is idempotent and
    /// returns `changed = false` with the identity still
    /// retrievable from the retained tombstone/metadata/binding.
    async fn retire_with_authority(
        &self, _retire: crate::core::provider::ProviderRetire,
    ) -> ServiceResult<crate::core::provider::ProviderRetirement> {
        Err(crate::types::ServiceError::InternalError(
            "atomic Provider retirement is not configured".into(),
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
