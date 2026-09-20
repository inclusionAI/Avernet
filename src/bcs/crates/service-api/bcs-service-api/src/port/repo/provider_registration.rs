use crate::ServiceResult;
use crate::types::provider_registration::ProviderRegistrationRecord;
use async_trait::async_trait;

/// Environment-scoped durable membership / registration journal. This is NOT
/// a delivery binding. Implementations must serialize reservation by Provider
/// and ref, including across processes, and must not overwrite a winning row.
#[async_trait]
pub trait ProviderRegistrationRepoPort: Send + Sync {
    async fn get(
        &self,
        provider_id: &str,
        provider_bot_ref: &str,
    ) -> ServiceResult<Option<ProviderRegistrationRecord>>;
    /// Atomically insert if absent; return the winning record (possibly from
    /// another request). Core compares its immutable inputs before proceeding.
    async fn reserve(
        &self,
        record: ProviderRegistrationRecord,
    ) -> ServiceResult<ProviderRegistrationRecord>;
    /// Missing reservation is an error, not a successful no-op.
    async fn complete(&self, provider_id: &str, provider_bot_ref: &str) -> ServiceResult<()>;
}
