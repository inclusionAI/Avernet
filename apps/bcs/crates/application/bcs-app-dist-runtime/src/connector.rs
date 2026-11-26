//! Health and graceful shutdown of a Connector process.
//!
//! The facade wires the application use cases
//! (`DistConnectorRuntimeService`: `snapshot`, `drain`, `accept_handoff`)
//! onto the engine's control port (`DistRuntimeControlPort`, implemented by
//! `bcs_dist_connector::ConnectorEngine`). It adds the grace-period limit and
//! the caller checks of a handoff. It does not choose the engine's ports:
//! whoever builds the engine passes `ConnectorPorts`, including the
//! membership provider and the partition owner records.
//!
//! There is no business API here; calls reach the Connector only through
//! dispatch documents and the command stream. `accept_handoff` is the peer
//! route a draining Connector uses to offer one of its command partitions.

use async_trait::async_trait;
use bcs_dist_model::Instance;
use bcs_service_api::application::dist_runtime::{
    DistConnectorRuntimeService, DrainOutcome, HandoffOutcome, HandoffRequest, RuntimeError, RuntimeSnapshot,
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
    /// A peer offers a partition. The delivery layer bound `source` to the
    /// caller's authenticated instance; it must be the partition's current
    /// owner named in the request, and the target must be this incarnation.
    async fn accept_handoff(&self, source: Instance, request: HandoffRequest) -> Result<HandoffOutcome, RuntimeError> {
        if source != request.from.owner.instance {
            return Err(RuntimeError {
                code: "peer_instance_mismatch".to_owned(),
            });
        }
        if request.target != self.engine.snapshot().await?.instance {
            return Err(RuntimeError {
                code: "target_incarnation_mismatch".to_owned(),
            });
        }
        self.engine.accept_handoff(request).await
    }

    /// The engine's health: ready, draining, active calls (live actors plus
    /// calls waiting for a resume) and how many of them are unsettled.
    async fn snapshot(&self) -> Result<RuntimeSnapshot, RuntimeError> {
        self.engine.snapshot().await
    }

    /// Stops claiming, lets held calls stop at a safe point, hands the
    /// partitions over and retires, within `grace` (see
    /// `ConnectorEngine::drain`).
    async fn drain(&self, grace: Duration) -> Result<DrainOutcome, RuntimeError> {
        if grace > MAX_GRACE {
            return Err(RuntimeError {
                code: "drain_grace_too_long".to_owned(),
            });
        }
        self.engine.drain(grace).await
    }
}
