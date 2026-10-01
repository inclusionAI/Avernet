//! Safe Eventing retention entrypoint.

use std::sync::Arc;

use bcs_service_api::port::repo::{EventRepoPort, EventRetentionRequest, EventRetentionResult};

use crate::subscription::system_now_ms_for_workers;

#[derive(Debug, thiserror::Error)]
#[error("Event retention repository operation failed")]
pub struct EventRetentionError;

pub struct EventRetentionWorker {
    repo: Arc<dyn EventRepoPort>,
    env: String,
    event_limit: u32,
    audit_limit: u32,
    now_ms: Arc<dyn Fn() -> u64 + Send + Sync>,
}

impl EventRetentionWorker {
    pub fn new(
        repo: Arc<dyn EventRepoPort>,
        env: impl Into<String>,
        event_limit: u32,
        audit_limit: u32,
    ) -> Self {
        Self {
            repo,
            env: env.into(),
            event_limit,
            audit_limit,
            now_ms: Arc::new(system_now_ms_for_workers),
        }
    }

    pub fn with_clock(mut self, now_ms: Arc<dyn Fn() -> u64 + Send + Sync>) -> Self {
        self.now_ms = now_ms;
        self
    }

    pub async fn run_once(&self) -> Result<EventRetentionResult, EventRetentionError> {
        self.repo
            .purge_expired(EventRetentionRequest {
                now_ms: (self.now_ms)(),
                event_limit: self.event_limit,
                audit_limit: self.audit_limit,
                env: self.env.clone(),
            })
            .await
            .map_err(|_| EventRetentionError)
    }
}
