//! Local maintenance observations for the single-document admission MVP.
//!
//! There is no shard-wide journal/root to health-check. The public observation
//! shape remains available for callers, but aggregate retention and repair
//! counters are intentionally absent.
use super::StoreInboxProducer;
use bcs_dist_model::ShardId;
use bcs_service_api::port::repo::distributed::common::StoreError;
use std::time::Duration;

#[derive(Debug, Clone)]
pub struct MaintenanceObservation {
    pub retained_entries: Option<u64>,
    pub retained_bytes: Option<u64>,
    pub retained_sessions: Option<u64>,
    pub repair_backlog: Option<u64>,
    pub repair_round_age: Option<Duration>,
    pub discovery_round_age: Option<Duration>,
    pub repair_confirmed_age: Option<Duration>,
    pub discovery_confirmed_age: Option<Duration>,
    pub capacity: Option<super::RetentionCapacityObservation>,
}

impl StoreInboxProducer {
    pub async fn maintenance_observation(
        &self,
        _shard: ShardId,
    ) -> Result<MaintenanceObservation, StoreError> {
        Ok(MaintenanceObservation {
            retained_entries: None,
            retained_bytes: None,
            retained_sessions: None,
            repair_backlog: None,
            repair_round_age: None,
            discovery_round_age: None,
            repair_confirmed_age: None,
            discovery_confirmed_age: None,
            capacity: None,
        })
    }
}
