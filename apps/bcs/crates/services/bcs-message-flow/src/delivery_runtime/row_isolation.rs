//! Row-scoped fault isolation for the delivery scheduler.
//!
//! The scheduler is one serial loop. A batch read failing is almost always a
//! database outage, so batch reads keep their unbounded backoff. A single
//! row's operation failing is usually a fault of that row (deleted Session,
//! corrupt projection, unexpected shape): it gets a few quick retries, then
//! the row is isolated so every other row keeps moving.
use super::{DeliveryRuntime, event};
use bcs_domain::message_delivery::MessageDeliveryStatus as Status;
use bcs_service_api::ManagedDeliveryError;
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;
use std::collections::BTreeMap;
use std::time::Duration;
use tokio::time::Instant;

const ROW_ATTEMPTS: u32 = 3;
const FIRST_SKIP: Duration = Duration::from_secs(5);
const MAX_SKIP: Duration = Duration::from_secs(300);
/// Bounds process memory even if a large share of rows becomes faulty.
const MAX_TRACKED: usize = 10_000;

/// In-memory skip list for rows that could not be settled. Never persisted:
/// a new master epoch re-examines every row from scratch.
#[derive(Default)]
pub(super) struct RowIsolation {
    skipped: BTreeMap<String, (Instant, u32)>,
}

impl RowIsolation {
    pub(super) fn blocked(&self, delivery_id: &str) -> bool {
        self.skipped.get(delivery_id).is_some_and(|(until, _)| Instant::now() < *until)
    }

    /// Rows currently skipped, passed to ordered scans as exclusions so they
    /// cannot keep occupying page slots ahead of healthy rows.
    pub(super) fn excluded(&self) -> Vec<String> {
        let now = Instant::now();
        self.skipped.iter().filter(|(_, (until, _))| now < *until).map(|(id, _)| id.clone()).take(256).collect()
    }

    /// Isolate a row whose shape the scheduler cannot act on.
    pub(super) fn isolate(&mut self, delivery_id: &str) {
        let backoff = self.skip(delivery_id);
        tracing::error!(%delivery_id, skip_ms = backoff.as_millis() as u64, "delivery row isolated by the scheduler");
    }

    fn clear(&mut self, delivery_id: &str) {
        self.skipped.remove(delivery_id);
    }

    fn skip(&mut self, delivery_id: &str) -> Duration {
        let now = Instant::now();
        if self.skipped.len() >= MAX_TRACKED {
            self.skipped.retain(|_, (until, _)| *until > now);
        }
        let failures = self.skipped.get(delivery_id).map_or(0, |(_, n)| *n).saturating_add(1);
        let backoff = FIRST_SKIP.saturating_mul(1u32 << (failures - 1).min(6)).min(MAX_SKIP);
        if self.skipped.len() < MAX_TRACKED || self.skipped.contains_key(delivery_id) {
            self.skipped.insert(delivery_id.to_owned(), (now + backoff, failures));
        }
        backoff
    }
}

fn retryable(error: &ManagedDeliveryError) -> bool {
    crate::storage_retry::managed_storage(error)
}

impl DeliveryRuntime {
    /// Run one row-scoped operation. `None` means the row was isolated (or
    /// settled by quarantine) and the caller must move on to the next row.
    pub(super) async fn row_op<T, F, Fut>(
        &self,
        delivery_id: &str,
        isolation: &mut RowIsolation,
        mut work: F,
    ) -> Option<T>
    where
        F: FnMut() -> Fut,
        Fut: Future<Output = Result<T, ManagedDeliveryError>>,
    {
        let mut delay = Duration::from_millis(100);
        let mut attempt = 1;
        let error = loop {
            match work().await {
                Ok(value) => {
                    isolation.clear(delivery_id);
                    return Some(value);
                }
                Err(error) if retryable(&error) && attempt < ROW_ATTEMPTS => {
                    tokio::time::sleep(delay).await;
                    delay *= 2;
                    attempt += 1;
                }
                Err(error) => break error,
            }
        };
        // Only a deterministic fault may settle the row. An outage that
        // outlived the quick retries must never fail a healthy Queued row.
        if !retryable(&error) && self.quarantine(delivery_id).await {
            isolation.clear(delivery_id);
            tracing::error!(%delivery_id, %error, "delivery row settled by scheduler quarantine");
            return None;
        }
        let backoff = isolation.skip(delivery_id);
        tracing::error!(%delivery_id, %error, retryable = retryable(&error), skip_ms = backoff.as_millis() as u64,
            "delivery row isolated; the scheduler continues with other rows");
        None
    }

    /// A never-sent Queued row is settled `Failed` without any notice. Rows
    /// that may have reached a Bot are never settled here: they keep their
    /// uncertainty for the run deadline / manual resolution paths.
    async fn quarantine(&self, delivery_id: &str) -> bool {
        let Ok(Some(row)) = self.get(delivery_id).await else { return false };
        if row.state.status != Status::Queued || row.state.may_have_been_sent {
            return false;
        }
        let mut command = event(&row, Event::PreparationFailed);
        command.transport_context_json =
            Some(serde_json::json!({ crate::managed_delivery::SCHEDULER_QUARANTINED: true }));
        matches!(self.transition(command).await, Ok(Some(_)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test(start_paused = true)]
    async fn skip_backoff_grows_and_expires() {
        let mut isolation = RowIsolation::default();
        assert!(!isolation.blocked("d"));
        assert_eq!(isolation.skip("d"), FIRST_SKIP);
        assert!(isolation.blocked("d"));
        assert_eq!(isolation.skip("d"), FIRST_SKIP * 2);
        tokio::time::advance(FIRST_SKIP * 2 + Duration::from_millis(1)).await;
        assert!(!isolation.blocked("d"));
        for _ in 0..20 { isolation.skip("d"); }
        assert_eq!(isolation.skip("d"), MAX_SKIP);
        isolation.clear("d");
        assert!(!isolation.blocked("d"));
    }

    #[tokio::test(start_paused = true)]
    async fn excluded_lists_only_rows_still_skipped() {
        let mut isolation = RowIsolation::default();
        isolation.skip("a");
        isolation.skip("b");
        isolation.skip("b");
        tokio::time::advance(FIRST_SKIP + Duration::from_millis(1)).await;
        assert_eq!(isolation.excluded(), vec!["b".to_string()], "a's backoff expired; b's doubled");
    }
}
