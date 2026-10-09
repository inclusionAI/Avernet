//! Outbound Event Delivery contract.
//!
//! The adapter owns URL/DNS safety checks, HTTP transmission, response-size
//! limiting, and response classification. Eventing owns ordering, retry,
//! dead-letter, and subscription policy.

use std::fmt;

use async_trait::async_trait;
#[derive(Clone)]
pub struct EventDeliveryRequest {
    pub endpoint_url: String,
    pub body: Vec<u8>,
    pub request_timeout_ms: u64,
}

impl fmt::Debug for EventDeliveryRequest {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("EventDeliveryRequest")
            .field("endpoint_url", &"[REDACTED]")
            .field("body", &"[REDACTED]")
            .field("request_timeout_ms", &self.request_timeout_ms)
            .finish()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EventDeliveryDisposition {
    Succeeded,
    Retryable,
    Terminal,
    DisableSubscription,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EventDeliveryResponse {
    pub disposition: EventDeliveryDisposition,
    pub http_status: Option<u16>,
    pub retry_after_ms: Option<u64>,
    pub response_bytes_observed: u64,
    pub error_category: Option<String>,
    pub error_summary: Option<String>,
}

#[derive(Debug, thiserror::Error)]
pub enum EventDeliveryError {
    #[error("invalid delivery request: {0}")]
    InvalidRequest(String),
    #[error("event delivery adapter failed internally: {0}")]
    Internal(String),
}

#[async_trait]
pub trait EventDeliveryPort: Send + Sync {
    async fn deliver(
        &self,
        request: EventDeliveryRequest,
    ) -> Result<EventDeliveryResponse, EventDeliveryError>;
}
