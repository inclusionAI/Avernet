//! Durable asynchronous state-machine judge worker.
//!
//! Bot callbacks only persist the `running -> judging` transition. This worker
//! claims judging nodes with a renewable lease and performs the slow judge
//! evaluation outside the callback request lifetime.

use std::sync::Arc;
use std::time::Duration;

use bcs_service_api::CollaborationRuntimeService;
use tracing::{debug, info, warn};

pub const DEFAULT_SCAN_INTERVAL: Duration = Duration::from_millis(500);
pub const DEFAULT_BATCH_SIZE: usize = 4;
pub const DEFAULT_LEASE_MS: u64 = 30_000;

pub async fn scan_once(
    runtime: &Arc<dyn CollaborationRuntimeService>,
    batch_size: usize,
    lease_ms: u64,
) -> usize {
    if batch_size == 0 {
        return 0;
    }
    match runtime.process_pending_judges(batch_size, lease_ms).await {
        Ok(processed) => processed,
        Err(error) => {
            warn!(
                target: "state_machine_judge_worker",
                event = "worker.scan_failed",
                error = %error,
            );
            0
        }
    }
}

pub fn spawn(
    runtime: Arc<dyn CollaborationRuntimeService>,
    interval: Duration,
    batch_size: usize,
    lease_ms: u64,
) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        info!(
            target: "state_machine_judge_worker",
            event = "worker.started",
            interval_ms = interval.as_millis() as u64,
            batch_size = batch_size,
            lease_ms = lease_ms,
        );
        let mut ticker = tokio::time::interval(interval);
        ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        loop {
            ticker.tick().await;
            let processed = scan_once(&runtime, batch_size, lease_ms).await;
            if processed > 0 {
                debug!(
                    target: "state_machine_judge_worker",
                    event = "worker.tick",
                    processed = processed,
                );
            }
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn scan_once_skips_zero_batch() {
        let runtime = bcs_services_container::Services::builder()
            .build_for_test()
            .collaboration_runtime;

        assert_eq!(scan_once(&runtime, 0, DEFAULT_LEASE_MS).await, 0);
    }
}
