//! Slow-consumer connection retirement (spec §9 transport floor).
//!
//! A terminal stream-end frame that cannot be enqueued within the
//! bounded backpressure budget is a TRANSPORT failure, not an
//! authorization event: the saturated connection is retired — its
//! shutdown is cancelled and every state keyed to the instance is
//! removed — so the client observes a closed socket and recovers
//! through its normal reconnect protocol. Never leave a live
//! connection with a silently final-less stream.

use async_trait::async_trait;

/// Retires one saturated connection instance. Implemented by the
/// Workbench connection registry; consulted by the fanout lane and
/// the run-channel manager on terminal-frame loss.
#[async_trait]
pub trait SlowConnectionRetirement: Send + Sync + std::fmt::Debug {
    /// Cancel and remove the connection instance (idempotent).
    async fn retire(&self, connection_instance_id: u64, reason: &'static str);
}
