//! Low-cardinality Eventing instrumentation contract.
//!
//! Context types intentionally have no subscription ID, Event ID, endpoint,
//! payload, secret, group ID, or stream key fields.

use async_trait::async_trait;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum EventMetricFamily {
    Group,
    Session,
    Message,
    Task,
    StateMachine,
    EventSubscription,
    Other,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum EventProductionMetricResult {
    Recorded,
    Disabled,
    Failed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum EventDeliveryMetricResult {
    Success,
    Retryable,
    Terminal,
    Cancelled,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum EventHttpStatusClass {
    None,
    Informational,
    Success,
    Redirection,
    ClientError,
    ServerError,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum EventErrorCategory {
    Validation,
    Storage,
    Projection,
    Dns,
    Connect,
    Tls,
    Timeout,
    Http,
    Lease,
    Internal,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum WebhookGuardBlockReason {
    InvalidScheme,
    CredentialsInUrl,
    QueryOrFragment,
    PrivateAddress,
    LoopbackAddress,
    LinkLocalAddress,
    DnsRebinding,
    Redirect,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct EventProductionMetric {
    pub family: EventMetricFamily,
    pub result: EventProductionMetricResult,
    pub error_category: Option<EventErrorCategory>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct EventDeliveryAttemptMetric {
    pub family: EventMetricFamily,
    pub result: EventDeliveryMetricResult,
    pub status_class: EventHttpStatusClass,
    pub error_category: Option<EventErrorCategory>,
}

#[async_trait]
pub trait EventingInstrumentationPort: Send + Sync {
    async fn event_produced(&self, metric: EventProductionMetric);
    async fn fanout_failed(&self, error_category: EventErrorCategory);
    async fn delivery_attempted(&self, metric: EventDeliveryAttemptMetric);
    async fn webhook_guard_blocked(&self, reason: WebhookGuardBlockReason);
}
