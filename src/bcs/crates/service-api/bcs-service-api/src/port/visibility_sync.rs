//! Best-effort synchronization of bot visibility to an external index.

use async_trait::async_trait;
use bcs_domain::BotCapabilities;

/// Latest persisted bot state to publish to an external visibility index.
#[derive(Debug, Clone)]
pub struct VisibilitySyncRequest {
    pub bot_uuid: String,
    pub capabilities: BotCapabilities,
}

/// Outbound capability for publishing a bot to an external visibility index.
///
/// Implementations own retry and external error reporting. Completion means
/// the configured best-effort attempt has finished; callers must not treat it
/// as part of the persistence transaction.
#[async_trait]
pub trait VisibilitySyncPort: Send + Sync {
    async fn sync_visibility(&self, request: VisibilitySyncRequest);
}

#[derive(Debug, Default)]
pub struct NoopVisibilitySyncPort;

#[async_trait]
impl VisibilitySyncPort for NoopVisibilitySyncPort {
    async fn sync_visibility(&self, _request: VisibilitySyncRequest) {}
}
