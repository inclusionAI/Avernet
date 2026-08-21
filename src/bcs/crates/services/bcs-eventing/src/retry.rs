//! Retry scheduling with exponential backoff and full jitter.

use bcs_config_api::EventingRetryConfig;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct EventRetryPolicy {
    pub base_delay_ms: u64,
    pub max_delay_ms: u64,
    pub max_attempts: u32,
    pub max_elapsed_ms: u64,
}

impl From<&EventingRetryConfig> for EventRetryPolicy {
    fn from(config: &EventingRetryConfig) -> Self {
        Self {
            base_delay_ms: config.base_delay_ms,
            max_delay_ms: config.max_delay_ms,
            max_attempts: config.max_attempts,
            max_elapsed_ms: config.max_elapsed_ms,
        }
    }
}

impl Default for EventRetryPolicy {
    fn default() -> Self {
        Self::from(&EventingRetryConfig::default())
    }
}

impl EventRetryPolicy {
    pub fn retry_at_ms(
        &self,
        attempt_no: u32,
        first_attempt_at_ms: u64,
        completed_at_ms: u64,
        retry_after_ms: Option<u64>,
        random_sample: u64,
    ) -> Option<u64> {
        if attempt_no >= self.max_attempts
            || completed_at_ms.saturating_sub(first_attempt_at_ms) >= self.max_elapsed_ms
        {
            return None;
        }
        let exponent = attempt_no.saturating_sub(1).min(63);
        let exponential_cap = self
            .base_delay_ms
            .saturating_mul(1_u64.checked_shl(exponent).unwrap_or(u64::MAX))
            .min(self.max_delay_ms);
        let jitter = (random_sample % exponential_cap.saturating_add(1).max(1)).max(1);
        let delay = retry_after_ms.map_or(jitter, |retry_after| retry_after.max(jitter));
        let next = completed_at_ms.saturating_add(delay);
        (next.saturating_sub(first_attempt_at_ms) <= self.max_elapsed_ms).then_some(next)
    }
}
