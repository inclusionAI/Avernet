//! Service lifecycle contract for BCS.
//!
//! Implementations declare initialize/shutdown semantics. The bootstrap
//! LifecycleOrchestrator (Phase 5) uses this trait to drive the global
//! startup/shutdown sequence.

use async_trait::async_trait;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum LifecycleError {
    /// Startup precondition not met. Fail fast.
    #[error("precondition failed: {0}")]
    Precondition(String),

    /// Transient error during startup; orchestrator may retry once.
    #[error("transient: {0}")]
    Transient(String),

    /// Shutdown took longer than the configured timeout.
    #[error("shutdown timeout: {0}")]
    ShutdownTimeout(String),

    /// Shutdown reported an unspecified error.
    #[error("shutdown error: {0}")]
    ShutdownFailed(String),
}

#[async_trait]
pub trait ServiceLifecycle: Send + Sync {
    /// Initialize the service. Idempotent. Default: no-op.
    async fn initialize(&self) -> Result<(), LifecycleError> {
        Ok(())
    }

    /// Shutdown gracefully. Idempotent. Default: no-op.
    async fn shutdown(&self) -> Result<(), LifecycleError> {
        Ok(())
    }
}
