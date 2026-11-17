//! Health and graceful shutdown of a Connector process.

use async_trait::async_trait;
use bcs_service_api::application::dist_runtime::{
    DistConnectorRuntimeService, DrainOutcome, RuntimeError, RuntimeSnapshot,
};
use bcs_service_api::port::dist_runtime::DistRuntimeControlPort;
use std::sync::Arc;
use std::time::Duration;

/// Longest grace period an operator may ask for; a longer request is refused
/// instead of holding a rollout open indefinitely.
const MAX_GRACE: Duration = Duration::from_secs(3600);

/// The Connector's admin use cases over its engine's control port.
pub struct ConnectorRuntimeFacade {
    engine: Arc<dyn DistRuntimeControlPort>,
}

impl ConnectorRuntimeFacade {
    pub fn new(engine: Arc<dyn DistRuntimeControlPort>) -> Self {
        Self { engine }
    }
}

#[async_trait]
impl DistConnectorRuntimeService for ConnectorRuntimeFacade {
    async fn snapshot(&self) -> Result<RuntimeSnapshot, RuntimeError> {
        self.engine.snapshot().await
    }

    /// Stops claiming and waits up to `grace` for held calls. Calls still
    /// running when it expires stay held and recoverable; they are reported as
    /// `remaining`, never aborted.
    async fn drain(&self, grace: Duration) -> Result<DrainOutcome, RuntimeError> {
        if grace > MAX_GRACE {
            return Err(RuntimeError {
                code: "drain_grace_too_long".to_owned(),
            });
        }
        self.engine.drain(grace).await
    }
}
