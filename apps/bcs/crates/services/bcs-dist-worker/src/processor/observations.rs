//! Body-free local gauges. Durable ages survive takeover; unknown ages stay None.
use super::engine::WorkerProcessor;
use bcs_dist_model::TimestampMillis;
use bcs_service_api::{
    port::repo::distributed::{control::ControlMode, session::TaskPhase},
    types::dist_runtime::RuntimeError,
};
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ProcessorObservations {
    pub resident_sessions: usize,
    pub pending_acquisitions: usize,
    pub unknown_commits: usize,
    pub oldest_unknown_millis: Option<u64>,
    pub pending_outboxes: usize,
    pub oldest_outbox_millis: Option<u64>,
    pub outboxes_without_timestamp: usize,
    pub awaiting_results: usize,
    pub oldest_archive_wait_millis: Option<u64>,
    /// Counts only resident evidence. Parked durable isolation is queried through
    /// the authorized isolation facade, never inferred absent from these gauges.
    pub resident_quarantined: usize,
    pub resident_blocked: usize,
    pub cached_dispatch_specs: usize,
    pub cached_reader_handles: usize,
    pub reader_restarts: u64,
    pub scan_io_in_use: Option<usize>,
}
impl WorkerProcessor {
    pub async fn observations(&self) -> Result<ProcessorObservations, RuntimeError> {
        let state = self.state.lock().await;
        let now = (self.ports.now)();
        let mut result = ProcessorObservations {
            resident_sessions: state.slots.len(),
            pending_acquisitions: self.runtime.state()?.acquisitions.len(),
            cached_reader_handles: self
                .runtime
                .feeds
                .lock()
                .map_err(|_| crate::runtime::err("worker_feed_poisoned"))?
                .len(),
            reader_restarts: self
                .runtime
                .reader_restarts
                .load(std::sync::atomic::Ordering::Relaxed),
            scan_io_in_use: self.ports.scan_budget.io_in_use(),
            ..Default::default()
        };
        for slot in state.slots.values() {
            result.cached_dispatch_specs += slot.dispatch_specs.len();
            if slot.pending.is_some() {
                result.unknown_commits += 1;
                if let Some(at) = slot.pending_since {
                    max_age(
                        &mut result.oldest_unknown_millis,
                        at.elapsed().as_millis().min(u128::from(u64::MAX)) as u64,
                    );
                }
            }
            if (slot.effects_pending || slot.recovering)
                && let Some(batch) = &slot.acquired.envelope.checkpoint.outbox
            {
                result.pending_outboxes += 1;
                if let Some(at) = batch.created_at {
                    max_age(&mut result.oldest_outbox_millis, age(now, at));
                } else {
                    result.outboxes_without_timestamp += 1;
                }
            }
            for task in &slot.acquired.envelope.checkpoint.tasks {
                if matches!(task.phase, TaskPhase::AwaitingResult { .. }) {
                    result.awaiting_results += 1;
                    if let Some(frozen) = &task.finalization {
                        max_age(
                            &mut result.oldest_archive_wait_millis,
                            age(now, frozen.record.finalized_at),
                        );
                    }
                }
            }
            match slot.control.as_ref().map(|c| &c.control.mode) {
                Some(ControlMode::Quarantined { .. }) => result.resident_quarantined += 1,
                Some(ControlMode::Blocked { .. }) => result.resident_blocked += 1,
                _ => {}
            }
        }
        Ok(result)
    }
}
fn age(now: TimestampMillis, at: TimestampMillis) -> u64 {
    now.0.saturating_sub(at.0).max(0) as u64
}
fn max_age(current: &mut Option<u64>, next: u64) {
    *current = Some(current.map_or(next, |prior| prior.max(next)));
}
