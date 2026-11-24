//! Health and graceful shutdown of a Connector process.
//!
//! The facade wires one thing: the application use cases
//! (`DistConnectorRuntimeService`: `snapshot`, `drain`) onto the engine's
//! control port (`DistRuntimeControlPort`, implemented by
//! `bcs_dist_connector::ConnectorEngine`). It adds only the grace-period
//! limit. It does not choose the engine's ports: whoever builds the engine
//! passes `ConnectorPorts`, including the membership provider. No code in
//! this repository wires the real provider
//! (`bcs_dist_store::coordination::CoordinationStore`) into a Connector, so
//! a Connector runs with the stub `PendingMembership`: no registration, and
//! no takeover of another instance's calls.
//!
//! There is no business API here; calls reach the Connector only through
//! dispatch documents and the command stream.

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
    /// The engine's health: ready, draining, active calls (live actors plus
    /// calls waiting for a resume) and how many of them are unsettled.
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
