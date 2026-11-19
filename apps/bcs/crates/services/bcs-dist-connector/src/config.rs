//! Engine settings and the ports it runs on.

use crate::delayed::HeldCallRecovery;
use bcs_dist_model::{Did, Instance, PoolId};
use bcs_service_api::core::dist_connector::DistConnectorCoreService;
use bcs_service_api::port::dist_agent::DistAgentClientPort;
use bcs_service_api::port::repo::distributed::dispatch::DispatchQueuePort;
use bcs_service_api::port::repo::distributed::inbox::InboxProducerPort;
use bcs_service_api::port::repo::distributed::ownership::MembershipPort;
use bcs_service_api::port::repo::distributed::result::BlobStorePort;
use std::sync::Arc;
use std::time::Duration;

/// Settings of one Connector process. Nothing is read from the environment.
#[derive(Debug, Clone)]
pub struct ConnectorConfig {
    /// This process: node name and incarnation (process start time, epoch ms).
    pub me: Instance,
    /// The command-stream pool this Connector serves.
    pub pool: PoolId,
    /// Calls held at once; at the limit no hints are read.
    pub max_active_calls: usize,
    /// How often a held call's dispatch is re-read for replies and cancels.
    pub poll_interval: Duration,
    /// Longest wait of one read of the command feed.
    pub receive_timeout: Duration,
    /// Event payloads above this size are stored as blobs.
    pub max_inline_bytes: usize,
    /// Backoff of safe retries: starts here, doubles up to `backoff_max`.
    pub backoff_min: Duration,
    pub backoff_max: Duration,
    /// Retries of a send that definitely did not leave the process.
    pub not_sent_retries: u32,
    pub membership_ttl: Duration,
    pub membership_renew: Duration,
    /// Where other instances reach this process's control endpoint; stored
    /// with the registration. Empty until a control endpoint is wired.
    pub control_address: String,
}

impl ConnectorConfig {
    pub fn new(me: Instance, pool: PoolId) -> Self {
        Self {
            me,
            pool,
            max_active_calls: 1000,
            poll_interval: Duration::from_millis(1500),
            receive_timeout: Duration::from_secs(1),
            max_inline_bytes: 65_536,
            backoff_min: Duration::from_millis(200),
            backoff_max: Duration::from_secs(5),
            not_sent_retries: 3,
            membership_ttl: Duration::from_secs(3),
            membership_renew: Duration::from_secs(1),
            control_address: String::new(),
        }
    }

    /// The poll interval of one call, spread over ±20 % by its id so that
    /// calls claimed together do not all poll at the same instant.
    pub(crate) fn poll_interval_of(&self, did: &Did) -> Duration {
        let spread = did.0.bytes().fold(0u32, |acc, b| acc.wrapping_mul(31).wrapping_add(u32::from(b))) % 401;
        // 800..=1200 thousandths of the configured interval.
        self.poll_interval.mul_f64(f64::from(800 + spread) / 1000.0)
    }
}

/// Everything the engine reaches the outside through.
#[derive(Clone)]
pub struct ConnectorPorts {
    pub queue: Arc<dyn DispatchQueuePort>,
    pub inbox: Arc<dyn InboxProducerPort>,
    pub agent: Arc<dyn DistAgentClientPort>,
    pub membership: Arc<dyn MembershipPort>,
    pub blobs: Arc<dyn BlobStorePort>,
    pub recovery: Arc<dyn HeldCallRecovery>,
    pub rules: Arc<dyn DistConnectorCoreService>,
}

/// Exponential backoff between safe retries.
pub(crate) struct Backoff {
    next: Duration,
    max: Duration,
}

impl Backoff {
    pub fn new(config: &ConnectorConfig) -> Self {
        Self {
            next: config.backoff_min,
            max: config.backoff_max,
        }
    }

    pub async fn wait(&mut self) {
        tokio::time::sleep(self.next).await;
        self.next = self.next.saturating_mul(2).min(self.max);
    }

    /// Like `wait`, but never sleeps longer than `limit` in one go, so the
    /// caller can do periodic work (a heartbeat) between slices.
    pub async fn wait_at_most(&mut self, limit: Duration) {
        tokio::time::sleep(self.next.min(limit)).await;
        self.next = self.next.saturating_mul(2).min(self.max);
    }
}
