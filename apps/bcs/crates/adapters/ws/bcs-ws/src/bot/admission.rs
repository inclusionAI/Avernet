//! Pre-upgrade admission control for bot WebSocket connections.
//!
//! When BCS restarts, every bot reconnects at once. A process-local token
//! bucket admits only a bounded rate of `/ws/bot` upgrades; excess requests are
//! rejected with HTTP 429 before the upgrade so clients retry later and the
//! reconnect storm is spread over time.

use std::sync::Mutex;
use std::time::{Duration, Instant};

use axum::http::{HeaderValue, StatusCode, header};
use axum::response::{IntoResponse, Response};

/// Token bucket limiting the rate of accepted bot WebSocket upgrades.
pub struct BotWsAdmission {
    rate_per_sec: f64,
    burst: f64,
    state: Mutex<BucketState>,
}

struct BucketState {
    tokens: f64,
    last_refill: Instant,
}

impl BotWsAdmission {
    /// Create a bucket that refills `rate_per_sec` tokens per second up to
    /// `burst` tokens. The bucket starts full.
    pub fn new(rate_per_sec: u32, burst: u32) -> Self {
        assert!(rate_per_sec > 0, "rate_per_sec must be positive");
        assert!(burst > 0, "burst must be positive");
        Self {
            rate_per_sec: f64::from(rate_per_sec),
            burst: f64::from(burst),
            state: Mutex::new(BucketState {
                tokens: f64::from(burst),
                last_refill: Instant::now(),
            }),
        }
    }

    /// Take one token. On rejection, returns how long until a token is available.
    pub fn try_acquire(&self) -> Result<(), Duration> {
        self.try_acquire_at(Instant::now())
    }

    fn try_acquire_at(&self, now: Instant) -> Result<(), Duration> {
        let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
        let elapsed = now.saturating_duration_since(state.last_refill).as_secs_f64();
        state.tokens = (state.tokens + elapsed * self.rate_per_sec).min(self.burst);
        state.last_refill = now;
        if state.tokens >= 1.0 {
            state.tokens -= 1.0;
            Ok(())
        } else {
            Err(Duration::from_secs_f64((1.0 - state.tokens) / self.rate_per_sec))
        }
    }
}

/// HTTP 429 response for a rate-limited bot WebSocket upgrade.
pub fn bot_ws_rate_limited_response(retry_after: Duration) -> Response {
    let secs = retry_after.as_secs_f64().ceil().max(1.0) as u64;
    let mut response = (
        StatusCode::TOO_MANY_REQUESTS,
        axum::Json(serde_json::json!({
            "error": "bot_ws_rate_limited",
            "retry_after_secs": secs,
        })),
    )
        .into_response();
    response
        .headers_mut()
        .insert(header::RETRY_AFTER, HeaderValue::from(secs));
    response
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn admits_burst_then_rejects_with_retry_after() {
        let admission = BotWsAdmission::new(2, 3);
        let now = admission.state.lock().unwrap().last_refill;
        for _ in 0..3 {
            assert!(admission.try_acquire_at(now).is_ok());
        }
        let retry_after = admission.try_acquire_at(now).unwrap_err();
        assert_eq!(retry_after, Duration::from_millis(500));
    }

    #[test]
    fn refills_at_configured_rate() {
        let admission = BotWsAdmission::new(2, 1);
        let now = admission.state.lock().unwrap().last_refill;
        assert!(admission.try_acquire_at(now).is_ok());
        assert!(admission.try_acquire_at(now + Duration::from_millis(100)).is_err());
        assert!(admission.try_acquire_at(now + Duration::from_millis(500)).is_ok());
        assert!(admission.try_acquire_at(now + Duration::from_millis(500)).is_err());
    }

    #[test]
    fn refill_is_capped_at_burst() {
        let admission = BotWsAdmission::new(10, 2);
        let now = admission.state.lock().unwrap().last_refill;
        let later = now + Duration::from_secs(60);
        assert!(admission.try_acquire_at(later).is_ok());
        assert!(admission.try_acquire_at(later).is_ok());
        assert!(admission.try_acquire_at(later).is_err());
    }

    #[test]
    fn rate_limited_response_sets_429_and_retry_after() {
        let response = bot_ws_rate_limited_response(Duration::from_millis(1200));
        assert_eq!(response.status(), StatusCode::TOO_MANY_REQUESTS);
        assert_eq!(response.headers()[header::RETRY_AFTER], "2");

        let response = bot_ws_rate_limited_response(Duration::from_millis(10));
        assert_eq!(response.headers()[header::RETRY_AFTER], "1");
    }
}
