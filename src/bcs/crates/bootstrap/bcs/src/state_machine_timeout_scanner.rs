//! State-machine node timeout scanner.
//!
//! The runtime owns timeout semantics and CAS protection; this module only
//! schedules periodic scans and drains full batches to avoid backlog growth.

use std::sync::Arc;
use std::time::Duration;

use bcs_service_api::CollaborationRuntimeService;
use tracing::{debug, info, warn};

pub const DEFAULT_SCAN_INTERVAL: Duration = Duration::from_millis(1_000);
pub const DEFAULT_BATCH_SIZE: usize = 100;
pub const DEFAULT_TIMEOUT_GRACE_MS: u64 = 500;

pub async fn scan_once(
    runtime: &Arc<dyn CollaborationRuntimeService>,
    batch_size: usize,
    timeout_grace_ms: u64,
) -> usize {
    if batch_size == 0 {
        return 0;
    }
    let mut total = 0usize;
    loop {
        match runtime
            .process_expired_node_timeouts(batch_size, timeout_grace_ms)
            .await
        {
            Ok(processed) => {
                total += processed;
                if processed < batch_size {
                    break;
                }
            }
            Err(error) => {
                warn!(
                    target: "state_machine_timeout_scanner",
                    event = "scanner.scan_failed",
                    error = %error,
                );
                break;
            }
        }
    }
    total
}

pub fn spawn(
    runtime: Arc<dyn CollaborationRuntimeService>,
    interval: Duration,
    batch_size: usize,
    timeout_grace_ms: u64,
) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        info!(
            target: "state_machine_timeout_scanner",
            event = "scanner.started",
            interval_ms = interval.as_millis() as u64,
            batch_size = batch_size,
            timeout_grace_ms = timeout_grace_ms,
        );
        let mut ticker = tokio::time::interval(interval);
        ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        loop {
            ticker.tick().await;
            let processed = scan_once(&runtime, batch_size, timeout_grace_ms).await;
            if processed > 0 {
                debug!(
                    target: "state_machine_timeout_scanner",
                    event = "scanner.tick",
                    processed = processed,
                );
            }
        }
    })
}
