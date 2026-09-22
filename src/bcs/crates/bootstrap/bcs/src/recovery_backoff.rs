//! Fault backoff for recovery schedulers, independent of idle polling.

use std::time::Duration;
use tokio::time::Instant;

pub(crate) struct RecoveryBackoff {
    base: Duration,
    current: Duration,
    ready_at: Instant,
}

impl RecoveryBackoff {
    pub(crate) fn new(base: Duration) -> Self {
        Self { base, current: base, ready_at: Instant::now() }
    }

    pub(crate) fn ready(&self) -> bool { Instant::now() >= self.ready_at }

    pub(crate) fn record(&mut self, failed: bool) {
        self.record_with_jitter(failed, fastrand::u64(..));
    }

    pub(crate) fn record_page(&mut self, scanned: usize, failed: bool) {
        // An empty tail only wraps the cursor; it does not prove that failing
        // candidates recovered. Keep their backoff across successive sweeps.
        if failed || scanned > 0 { self.record(failed); }
    }

    fn record_with_jitter(&mut self, failed: bool, random: u64) {
        let delay = if failed {
            self.current = self.current.saturating_mul(2).min(Duration::from_secs(30).max(self.base));
            let lower = self.base.max(self.current - self.current / 4);
            lower + (self.current - lower).mul_f64((random % 1001) as f64 / 1000.0)
        } else {
            self.current = self.base;
            self.base
        };
        // The scheduler's ticker supplies normal cadence. Adding the base here
        // would skip the next tick whenever a successful page took any time.
        self.ready_at = if failed { Instant::now() + delay } else { Instant::now() };
        if failed {
            tracing::warn!(target: "state_machine_recovery", retry_after_ms = delay.as_millis() as u64,
                "recovery scan backed off after failure");
        }
    }
}

pub(crate) async fn when_ready<T>(ready: bool, work: impl std::future::Future<Output = T>) -> Option<T> {
    if ready { Some(work.await) } else { None }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn failures_back_off_with_bounded_jitter_and_success_resets() {
        let mut backoff = RecoveryBackoff::new(Duration::from_secs(1));
        assert!(backoff.ready());
        for expected in [2, 4, 8, 16, 30, 30] {
            let before = Instant::now();
            backoff.record_with_jitter(true, 0);
            let maximum = Duration::from_secs(expected);
            assert_eq!(backoff.current, maximum);
            assert!(backoff.ready_at >= before + maximum.mul_f64(0.75));
            assert!(backoff.ready_at <= Instant::now() + maximum);
            assert!(!backoff.ready());
        }
        backoff.record_with_jitter(false, 1000);
        assert_eq!(backoff.current, Duration::from_secs(1));
        assert!(backoff.ready());
        backoff.record_with_jitter(true, 1000);
        assert_eq!(backoff.current, Duration::from_secs(2));
    }

    #[tokio::test]
    async fn deferred_page_does_not_poll_work_or_block_another_page() {
        let deferred = async { panic!("must not poll a backed-off page"); };
        assert_eq!(tokio::join!(when_ready(false, deferred), when_ready(true, async { 7 })), (None, Some(7)));
    }

    #[test]
    fn empty_tail_preserves_failure_backoff_until_candidates_succeed() {
        let mut backoff = RecoveryBackoff::new(Duration::from_secs(1));
        backoff.record_page(0, false);
        assert!(backoff.ready()); // An idle scan does not create backoff.
        for expected in [2, 4, 8, 16, 30, 30] {
            backoff.record_page(32, true);
            assert_eq!(backoff.current, Duration::from_secs(expected));
            let ready_at = backoff.ready_at;
            backoff.record_page(0, false);
            assert_eq!(backoff.current, Duration::from_secs(expected));
            assert_eq!(backoff.ready_at, ready_at);
        }
        backoff.record_page(32, false);
        assert_eq!(backoff.current, Duration::from_secs(1));
        assert!(backoff.ready());
    }
}
