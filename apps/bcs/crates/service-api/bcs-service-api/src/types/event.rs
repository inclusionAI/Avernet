//! Shared Event contract types used by application, core, and port layers.

use std::collections::BTreeMap;
use std::fmt;

use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const EVENT_SPEC_VERSION: &str = "1.0";
pub const EVENT_SCHEMA_VERSION_V1: &str = "1.0";
pub const EVENT_SOURCE: &str = "bcs";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EventSubject {
    #[serde(rename = "type")]
    pub subject_type: String,
    pub id: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct EventScope {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub group_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub session_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub task_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub run_id: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EventActorType {
    Human,
    Bot,
    App,
    System,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EventActor {
    #[serde(rename = "type")]
    pub actor_type: EventActorType,
    pub id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub display_name: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EventStream {
    pub key: String,
    pub sequence: u64,
}

/// Canonical Event body persisted before fanout.
///
/// This type deliberately does not use `deny_unknown_fields`: consumers must
/// accept additive optional fields within the same envelope major version.
#[derive(Clone, PartialEq, Serialize, Deserialize)]
pub struct EventEnvelope {
    pub spec_version: String,
    pub event_id: String,
    pub event_type: String,
    pub schema_version: String,
    pub source: String,
    pub occurred_at: String,
    pub recorded_at: String,
    pub subject: EventSubject,
    pub scope: EventScope,
    pub stream: EventStream,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub actor: Option<EventActor>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub correlation_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub causation_event_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub trace_id: Option<String>,
    pub data: BTreeMap<String, Value>,
}

impl fmt::Debug for EventEnvelope {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("EventEnvelope")
            .field("spec_version", &self.spec_version)
            .field("event_id", &self.event_id)
            .field("event_type", &self.event_type)
            .field("schema_version", &self.schema_version)
            .field("source", &self.source)
            .field("occurred_at", &self.occurred_at)
            .field("recorded_at", &self.recorded_at)
            .field("subject", &self.subject)
            .field("scope", &self.scope)
            .field("stream", &self.stream)
            .field("actor", &self.actor)
            .field("correlation_id", &self.correlation_id)
            .field("causation_event_id", &self.causation_event_id)
            .field("trace_id", &self.trace_id)
            .field("data", &"[REDACTED]")
            .finish()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EventSubscriptionScopeType {
    Group,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EventSubscriptionScope {
    #[serde(rename = "type")]
    pub scope_type: EventSubscriptionScopeType,
    pub id: String,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EventPayloadMode {
    #[default]
    MetadataOnly,
    Full,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EventPayload {
    #[serde(default)]
    pub mode: EventPayloadMode,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EventOrderingMode {
    #[default]
    StrictPerStream,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EventOrdering {
    #[serde(default)]
    pub mode: EventOrderingMode,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EventSubscriptionStatus {
    Pending,
    Active,
    Disabled,
    Deleted,
}

impl EventSubscriptionStatus {
    pub fn can_transition_to(self, next: Self) -> bool {
        matches!(
            (self, next),
            (Self::Pending, Self::Active)
                | (Self::Pending, Self::Deleted)
                | (Self::Active, Self::Disabled)
                | (Self::Active, Self::Deleted)
                | (Self::Disabled, Self::Active)
                | (Self::Disabled, Self::Deleted)
        )
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EventDeliveryStatus {
    Pending,
    InFlight,
    RetryWait,
    Succeeded,
    DeadLettered,
    Cancelled,
    Skipped,
}

impl EventDeliveryStatus {
    /// Whether automatic attempts for this logical Delivery have ended.
    pub fn is_attempt_terminal(self) -> bool {
        matches!(
            self,
            Self::Succeeded | Self::DeadLettered | Self::Cancelled | Self::Skipped
        )
    }

    /// Whether this state permits a later Delivery in the same strict lane.
    /// Dead-lettered Delivery remains a blocker until replay resolves it or an
    /// administrator explicitly skips it.
    pub fn unblocks_strict_lane(self) -> bool {
        matches!(self, Self::Succeeded | Self::Cancelled | Self::Skipped)
    }
}
