//! Transport-independent Event and Event Subscription application contracts.
//!
//! Delivery adapters translate HTTP DTOs into these commands. The service
//! contract intentionally contains no Axum, Reqwest, HTTP status, or header
//! types. Webhook endpoint URLs are write-only at the transport boundary and
//! are redacted from Debug and read responses.

use std::fmt;

use async_trait::async_trait;
use serde::{Deserialize, Deserializer, Serialize};

use super::{ApplicationError, AuthenticatedCaller};
pub use crate::types::{
    EVENT_SCHEMA_VERSION_V1, EVENT_SOURCE, EVENT_SPEC_VERSION, EventActor, EventActorType,
    EventDeliveryStatus, EventEnvelope, EventOrdering, EventOrderingMode, EventPayload,
    EventPayloadMode, EventScope, EventStream, EventSubject, EventSubscriptionScope,
    EventSubscriptionScopeType, EventSubscriptionStatus,
};

#[derive(Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum EventSinkInput {
    Webhook {
        url: String,
        #[serde(default)]
        request_timeout_ms: Option<u64>,
    },
}

impl fmt::Debug for EventSinkInput {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Webhook {
                request_timeout_ms, ..
            } => formatter
                .debug_struct("Webhook")
                .field("url", &"[REDACTED]")
                .field("request_timeout_ms", request_timeout_ms)
                .finish(),
        }
    }
}

#[derive(Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum PatchEventSinkInput {
    Webhook {
        #[serde(default, deserialize_with = "deserialize_present")]
        url: Option<String>,
        #[serde(default, deserialize_with = "deserialize_present")]
        request_timeout_ms: Option<u64>,
    },
}

impl fmt::Debug for PatchEventSinkInput {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Webhook {
                url,
                request_timeout_ms,
            } => formatter
                .debug_struct("Webhook")
                .field("url", &url.as_ref().map(|_| "[REDACTED]"))
                .field("request_timeout_ms", request_timeout_ms)
                .finish(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EventWebhookEndpointView {
    pub scheme: String,
    pub host: String,
    pub path_hash: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum EventSinkView {
    Webhook {
        endpoint: EventWebhookEndpointView,
        request_timeout_ms: u64,
    },
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreateEventSubscriptionRequest {
    pub name: String,
    pub scope: EventSubscriptionScope,
    pub event_filters: Vec<String>,
    #[serde(default)]
    pub payload: EventPayload,
    pub sink: EventSinkInput,
}

fn deserialize_present<'de, D, T>(deserializer: D) -> Result<Option<T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    T::deserialize(deserializer).map(Some)
}

/// PATCH input. Missing fields are unchanged; explicit JSON `null` is invalid.
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PatchEventSubscriptionRequest {
    #[serde(default, deserialize_with = "deserialize_present")]
    pub name: Option<String>,
    #[serde(default, deserialize_with = "deserialize_present")]
    pub event_filters: Option<Vec<String>>,
    #[serde(default, deserialize_with = "deserialize_present")]
    pub payload: Option<EventPayload>,
    #[serde(default, deserialize_with = "deserialize_present")]
    pub sink: Option<PatchEventSinkInput>,
    #[serde(default, deserialize_with = "deserialize_present")]
    pub status: Option<EventSubscriptionDesiredStatus>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EventSubscriptionDesiredStatus {
    Active,
    Disabled,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EventSubscription {
    pub subscription_id: String,
    pub name: String,
    pub scope: EventSubscriptionScope,
    pub include_descendants: bool,
    pub event_filters: Vec<String>,
    pub payload: EventPayload,
    pub ordering: EventOrdering,
    pub sink: EventSinkView,
    pub status: EventSubscriptionStatus,
    pub revision: u64,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CursorPage<T> {
    pub items: Vec<T>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub next_cursor: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EventDeliveryAttemptResult {
    Success,
    Retryable,
    Terminal,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EventDeliveryAttemptSummary {
    pub attempt_no: u32,
    pub started_at: String,
    pub completed_at: String,
    pub latency_ms: u64,
    pub result: EventDeliveryAttemptResult,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub http_status: Option<u16>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error_category: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EventDeliverySummary {
    pub delivery_id: String,
    pub event_id: String,
    pub event_type: String,
    pub subscription_id: String,
    pub subscription_revision: u64,
    pub stream_key_hash: String,
    pub sequence: u64,
    pub status: EventDeliveryStatus,
    pub attempt_count: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_http_status: Option<u16>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_error_category: Option<String>,
    pub created_at: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EventDeliveryDetail {
    pub delivery: EventDeliverySummary,
    pub attempts: Vec<EventDeliveryAttemptSummary>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub replay_of_delivery_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub resolved_by_delivery_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EventSubscriptionTestResult {
    pub request_id: String,
    pub delivered: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub http_status: Option<u16>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error_category: Option<String>,
    pub completed_at: String,
}

#[derive(Debug, Clone)]
pub struct CreateEventSubscription {
    pub caller: AuthenticatedCaller,
    pub request: CreateEventSubscriptionRequest,
}

#[derive(Debug, Clone)]
pub struct ListEventSubscriptions {
    pub caller: AuthenticatedCaller,
    pub scope: Option<EventSubscriptionScope>,
    pub status: Option<EventSubscriptionStatus>,
    pub cursor: Option<String>,
    pub limit: u32,
}

#[derive(Debug, Clone)]
pub struct GetEventSubscription {
    pub caller: AuthenticatedCaller,
    pub subscription_id: String,
}

#[derive(Debug, Clone)]
pub struct PatchEventSubscription {
    pub caller: AuthenticatedCaller,
    pub subscription_id: String,
    pub expected_revision: u64,
    pub patch: PatchEventSubscriptionRequest,
}

#[derive(Debug, Clone)]
pub struct DeleteEventSubscription {
    pub caller: AuthenticatedCaller,
    pub subscription_id: String,
    pub expected_revision: u64,
}

#[derive(Debug, Clone)]
pub struct TestEventSubscription {
    pub caller: AuthenticatedCaller,
    pub subscription_id: String,
}

#[derive(Debug, Clone)]
pub struct ListEventDeliveries {
    pub caller: AuthenticatedCaller,
    pub subscription_id: String,
    pub status: Option<EventDeliveryStatus>,
    pub cursor: Option<String>,
    pub limit: u32,
}

#[derive(Debug, Clone)]
pub struct GetEventDelivery {
    pub caller: AuthenticatedCaller,
    pub delivery_id: String,
}

#[derive(Debug, Clone)]
pub struct ReplayEventDelivery {
    pub caller: AuthenticatedCaller,
    pub delivery_id: String,
    pub replay_request_id: String,
    pub expected_subscription_revision: u64,
}

#[derive(Debug, Clone)]
pub struct SkipEventDelivery {
    pub caller: AuthenticatedCaller,
    pub delivery_id: String,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ReplayEventDeliveryResult {
    pub original_delivery_id: String,
    pub replacement: EventDeliverySummary,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SkipEventDeliveryResult {
    pub delivery_id: String,
    pub status: EventDeliveryStatus,
    pub skipped_at: String,
}

#[async_trait]
pub trait EventSubscriptionService: Send + Sync {
    async fn create(
        &self,
        command: CreateEventSubscription,
    ) -> Result<EventSubscription, ApplicationError>;
    async fn list(
        &self,
        query: ListEventSubscriptions,
    ) -> Result<CursorPage<EventSubscription>, ApplicationError>;
    async fn get(&self, query: GetEventSubscription)
    -> Result<EventSubscription, ApplicationError>;
    async fn patch(
        &self,
        command: PatchEventSubscription,
    ) -> Result<EventSubscription, ApplicationError>;
    async fn delete(
        &self,
        command: DeleteEventSubscription,
    ) -> Result<EventSubscription, ApplicationError>;
    async fn test(
        &self,
        command: TestEventSubscription,
    ) -> Result<EventSubscriptionTestResult, ApplicationError>;
    async fn list_deliveries(
        &self,
        query: ListEventDeliveries,
    ) -> Result<CursorPage<EventDeliverySummary>, ApplicationError>;
    async fn get_delivery(
        &self,
        query: GetEventDelivery,
    ) -> Result<EventDeliveryDetail, ApplicationError>;
    async fn replay_delivery(
        &self,
        command: ReplayEventDelivery,
    ) -> Result<ReplayEventDeliveryResult, ApplicationError>;
    async fn skip_delivery(
        &self,
        command: SkipEventDelivery,
    ) -> Result<SkipEventDeliveryResult, ApplicationError>;
}
