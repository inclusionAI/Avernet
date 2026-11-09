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

/// Only transient infrastructure failures are retried. Configuration/contract failures
/// remain fatal; no identity reset or alternate transport is introduced.
pub(crate) fn continue_after<T>(result: RuntimeResult<T>) -> RuntimeResult<()> {
    match result {
        Ok(_) | Err(RuntimeError::Unavailable(_) | RuntimeError::OwnershipLost | RuntimeError::Contention) => Ok(()),
        Err(error) => Err(error),
    }
}
/// Heartbeats never wait for the business-state mutex or business connection pool.
/// The same future is retained while a heartbeat fires, so durable business I/O is
/// not cancelled/restarted on every renewal. Dropping run cancels both futures.
pub(crate) async fn with_heartbeat<F: Future<Output=RuntimeResult<()>>>(
    life: &tokio::sync::Mutex<Lifecycle>, node: &NodeIdentity,
    port: &dyn RuntimeLiveness, clock: &dyn bcn_runtime_api::port::Clock, business: F,
) -> RuntimeResult<()> {
    let heartbeat = async {
        let mut interval=tokio::time::interval(std::time::Duration::from_millis(RuntimeDefaults::default().heartbeat_ms));
        interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        loop {
            interval.tick().await;
            let result=life.lock().await.heartbeat(node,port,clock.now_ms()).await;
            continue_after(result)?;
        }
        #[allow(unreachable_code)] Ok(())
    };
    // Both futures must remain polled while either awaits the liveness mutex.
    // Awaiting renewal inside a selected branch could deadlock a yielded tick
    // that already holds that mutex during an actual asynchronous backend call.
    tokio::select! {result=business=>result,result=heartbeat=>result}

}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn structural_storage_errors_are_not_retried() {
        assert!(continue_after::<()>(Err(RuntimeError::Storage("corrupt envelope".into()))).is_err());
        assert!(continue_after::<()>(Err(RuntimeError::AccessDenied)).is_err());
        assert!(continue_after::<()>(Err(RuntimeError::Conflict("immutable result".into()))).is_err());
        assert!(continue_after::<()>(Err(RuntimeError::Contention)).is_ok());
        assert!(continue_after::<()>(Err(RuntimeError::Unavailable("partial slot".into()))).is_ok());
    }
}

/// Tick errors embedded in the report obey the same fail-closed classification.
pub(crate) fn continue_tick(result:RuntimeResult<crate::TickReport>)->RuntimeResult<()> {
    match result {Ok(report)=>{for error in report.errors {continue_after::<()>(Err(error))?;}Ok(())},Err(error)=>continue_after::<()>(Err(error))}
}
