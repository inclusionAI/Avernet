//! Engine control consumed by the application admin facade.
use crate::types::dist_runtime::{DrainOutcome, RuntimeError, RuntimeSnapshot};
use async_trait::async_trait;
use std::time::Duration;

/// The engine implements this port without importing application-layer contracts.
#[async_trait]
pub trait DistRuntimeControlPort: Send + Sync {
    async fn snapshot(&self) -> Result<RuntimeSnapshot, RuntimeError>;
    /// Stop acquiring new work; retain recoverable obligations if grace expires.
    async fn drain(&self, grace: Duration) -> Result<DrainOutcome, RuntimeError>;
}
