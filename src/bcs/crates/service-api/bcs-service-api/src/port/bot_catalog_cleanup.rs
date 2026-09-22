//! Cleanup contract for external Bot discovery indexes.

use async_trait::async_trait;

use crate::types::ServiceResult;

#[async_trait]
pub trait BotCatalogCleanupPort: Send + Sync {
    async fn delete_bot(&self, bot_id: &str) -> ServiceResult<()>;
}

#[derive(Debug, Default)]
pub struct NoopBotCatalogCleanupPort;

#[async_trait]
impl BotCatalogCleanupPort for NoopBotCatalogCleanupPort {
    async fn delete_bot(&self, _bot_id: &str) -> ServiceResult<()> {
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::{BotCatalogCleanupPort, NoopBotCatalogCleanupPort};

    #[tokio::test]
    async fn noop_cleanup_is_idempotent() {
        NoopBotCatalogCleanupPort
            .delete_bot("bot-1")
            .await
            .expect("noop cleanup should succeed");
    }
}
