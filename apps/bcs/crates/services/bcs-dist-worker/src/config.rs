use bcs_dist_model::{Instance, ShardId};
use bcs_service_api::types::dist_runtime::RuntimeError;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::time::Duration;
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HrwIdentity {
    Node,
    NodeIncarnation,
}
#[derive(Debug, Clone)]
pub struct WorkerConfig {
    pub heartbeat_interval: Duration,
    pub lease_ttl: Duration,
    pub check_interval: Duration,
    pub candidate_backoff: Duration,
    pub request_timeout: Duration,
    /// Reserve for final confirmation/retirement. No new handoff starts within
    /// this margin; drain(0) remains a valid immediate shutdown deadline.
    pub exit_margin: Duration,
    /// Concurrent session transfers per drain round, within the global budget.
    pub handoff_concurrency: usize,
    pub identity: HrwIdentity,
    pub page_size: u32,
    pub max_acquires_per_tick: usize,
    pub max_shards_per_tick: usize,
    pub max_member_pages: usize,
    pub max_active_sessions: usize,
    pub max_inflight_handoffs: usize,
    pub shards: Vec<ShardId>,
}
impl Default for WorkerConfig {
    fn default() -> Self {
        Self {
            heartbeat_interval: Duration::from_secs(1),
            lease_ttl: Duration::from_secs(3),
            check_interval: Duration::from_millis(500),
            candidate_backoff: Duration::from_millis(500),
            request_timeout: Duration::from_millis(500),
            exit_margin: Duration::from_secs(1),
            handoff_concurrency: 8,
            identity: HrwIdentity::Node,
            page_size: 200,
            max_acquires_per_tick: 32,
            max_shards_per_tick: 32,
            max_member_pages: 32,
            max_active_sessions: 10_000,
            max_inflight_handoffs: 32,
            shards: vec![],
        }
    }
}
impl WorkerConfig {
    pub fn validate(&self) -> Result<(), RuntimeError> {
        if self.heartbeat_interval.is_zero()
            || self
                .heartbeat_interval
                .checked_add(self.request_timeout)
                .is_none_or(|budget| budget >= self.lease_ttl)
            || self.request_timeout.is_zero()
            || self.request_timeout >= self.lease_ttl
            || self.check_interval.is_zero()
            || self.candidate_backoff.is_zero()
            || self.page_size == 0
            || self.page_size > 10_000
            || self.max_acquires_per_tick == 0
            || self.max_shards_per_tick == 0
            || self.max_member_pages == 0
            || self.max_active_sessions == 0
            || self.max_inflight_handoffs == 0
            || self.handoff_concurrency == 0
        {
            return Err(RuntimeError {
                code: "invalid_worker_config".into(),
            });
        }
        let mut shards = self.shards.clone();
        shards.sort();
        shards.dedup();
        if shards.len() != self.shards.len() {
            return Err(RuntimeError {
                code: "duplicate_worker_shard".into(),
            });
        }
        Ok(())
    }
}
/// Stable cross-process HRW encoding from the coordination protocol. Healthy
/// ownership is never rebalanced merely because this ordering changes.
pub fn rank_candidates(
    shard: ShardId,
    candidates: &[Instance],
    mode: HrwIdentity,
) -> Vec<Instance> {
    let score = |instance: &Instance| {
        let mut h = Sha256::new();
        h.update(b"bcn-worker-hrw-v1");
        h.update(shard.0.to_be_bytes());
        h.update([if mode == HrwIdentity::Node { 0 } else { 1 }]);
        h.update((instance.node.len() as u32).to_be_bytes());
        h.update(instance.node.as_bytes());
        if mode == HrwIdentity::NodeIncarnation {
            h.update((instance.incarnation.len() as u32).to_be_bytes());
            h.update(instance.incarnation.as_bytes());
        }
        <[u8; 32]>::from(h.finalize())
    };
    let mut ranked = candidates
        .iter()
        .map(|i| (score(i), i.clone()))
        .collect::<Vec<_>>();
    ranked.sort_by(|(a, x), (b, y)| {
        b.cmp(a)
            .then_with(|| (&x.node, &x.incarnation).cmp(&(&y.node, &y.incarnation)))
    });
    ranked.dedup_by(|(_, a), (_, b)| a == b);
    ranked.into_iter().map(|(_, i)| i).collect()
}
