//! Bounded idle polling; delivery retry and retention policies remain separate.

use std::time::Duration;

pub(super) struct IdleBackoff {
    base: Duration,
    maximum: Duration,
    current: Duration,
}

impl IdleBackoff {
    pub(super) fn new(base: Duration, maximum: Duration) -> Self {
        Self { base, maximum, current: base }
    }

    pub(super) fn next_delay(&mut self, empty: bool, random: u64) -> Duration {
        if !empty {
            self.current = self.base;
            return self.base;
        }
        self.current = self.current.saturating_mul(2).min(self.maximum);
        // Jitter the upper quarter of the window, bounded by the configured
        // base and ceiling. Fixed polling (base == maximum) stays exact.
        let lower = self.base.max(self.current - self.current / 4);
        let fraction = (random % 1001) as f64 / 1000.0;
        (lower + (self.current - lower).mul_f64(fraction)).min(self.maximum)
    }
}
