pub use crate::types::dist_runtime::{DrainOutcome, RuntimeError, RuntimeSnapshot};
use async_trait::async_trait;
use std::time::Duration;

/// No business submission API; inputs arrive through SessionInbox.
#[async_trait]
pub trait DistWorkerRuntimeService: Send + Sync {
    async fn snapshot(&self) -> Result<RuntimeSnapshot, RuntimeError>;
    async fn drain(&self, grace: Duration) -> Result<DrainOutcome, RuntimeError>;
}

/// No business submission API; calls arrive through DispatchBoard.
#[async_trait]
pub trait DistConnectorRuntimeService: Send + Sync {
    async fn snapshot(&self) -> Result<RuntimeSnapshot, RuntimeError>;
    async fn drain(&self, grace: Duration) -> Result<DrainOutcome, RuntimeError>;
}
