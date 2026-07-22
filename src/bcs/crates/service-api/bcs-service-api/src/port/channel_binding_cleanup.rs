//! Channel-binding cleanup used by group lifecycle orchestration.

use async_trait::async_trait;

use crate::types::ServiceResult;

/// Removes channel bindings that would otherwise reference a deleted group.
#[async_trait]
pub trait ChannelBindingCleanupPort: Send + Sync {
    /// Delete every channel binding whose target is the given group.
    async fn delete_bindings_for_group(&self, group_id: &str) -> ServiceResult<u64>;
}

/// Default implementation for runtimes where the channel bridge is unavailable.
#[derive(Debug, Default)]
pub struct NoopChannelBindingCleanupPort;

#[async_trait]
impl ChannelBindingCleanupPort for NoopChannelBindingCleanupPort {
    async fn delete_bindings_for_group(&self, _group_id: &str) -> ServiceResult<u64> {
        Ok(0)
    }
}

#[cfg(test)]
mod tests {
    use super::{ChannelBindingCleanupPort, NoopChannelBindingCleanupPort};

    #[tokio::test]
    async fn noop_cleanup_reports_no_removed_bindings() {
        let cleanup = NoopChannelBindingCleanupPort;

        assert_eq!(
            cleanup
                .delete_bindings_for_group("group_1")
                .await
                .expect("noop cleanup should succeed"),
            0
        );
    }
}
