//! Failure-detector warm-up and bounded retry scheduling; neither is ownership authority.
use bcn_runtime_api::{application::RuntimePhase, port::RuntimeLiveness};
use bcn_runtime_model::*;
pub(crate) struct Lifecycle {
    pub phase: RuntimePhase,
    pub registered: bool,
    pub healthy_since: Option<u64>,
    pub renewed_at: u64,
    pub retry_at: u64,
    pub failures: u32,
    pub drain_deadline: Option<u64>,
}
impl Default for Lifecycle {
    fn default() -> Self {
        Self {
            phase: RuntimePhase::Starting,
            registered: false,
            healthy_since: None,
            renewed_at: 0,
            retry_at: 0,
            failures: 0,
            drain_deadline: None,
        }
    }
}
impl Lifecycle {
    pub async fn heartbeat(
        &mut self,
        node: &NodeIdentity,
        port: &dyn RuntimeLiveness,
        now: u64,
    ) -> RuntimeResult<()> {
        let d = RuntimeDefaults::default();
        if self.registered && now.saturating_sub(self.renewed_at) < d.heartbeat_ms {
            return Ok(());
        }
        let result = if self.registered {
            port.renew(node, d.heartbeat_ttl_ms).await
        } else {
            port.register(node.clone(), d.heartbeat_ttl_ms).await
        };
        match result {
            Ok(()) => {
                if self.registered && now.saturating_sub(self.renewed_at) > d.heartbeat_ttl_ms {
                    self.healthy_since = None;
                }
                self.registered = true;
                self.renewed_at = now;
                self.healthy_since.get_or_insert(now);
                if self.phase == RuntimePhase::Starting {
                    self.phase = RuntimePhase::Running;
                }
                Ok(())
            }
            Err(e) => {
                self.healthy_since = None;
                self.backoff(now);
                Err(e)
            }
        }
    }
    pub fn may_takeover(&self, now: u64) -> bool {
        self.healthy_since.is_some_and(|start| {
            now.saturating_sub(start) >= RuntimeDefaults::default().heartbeat_ttl_ms
        })
    }
    pub fn backoff(&mut self, now: u64) {
        self.failures = self.failures.saturating_add(1);
        self.retry_at = now.saturating_add((500u64 << self.failures.min(4)).min(5000));
    }
    pub fn progressed(&mut self) {
        self.failures = 0;
        self.retry_at = 0;
    }
    pub fn expired(&self, now: u64) -> bool {
        self.drain_deadline.is_some_and(|d| now >= d)
    }
}
