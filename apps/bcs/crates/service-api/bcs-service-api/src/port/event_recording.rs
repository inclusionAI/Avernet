//! Application-facing port for recording canonical BCS Events.
//!
//! Domain stores that own a persistent mutation must compose Event persistence
//! into that same storage transaction. This port is for transitions whose
//! unit of work is owned by the Event recorder (including non-persistent
//! sources); it is not permission to append after a business commit.

use std::collections::BTreeMap;
use std::fmt;

use async_trait::async_trait;
use serde_json::Value;

use crate::port::repo::AppendEventRecord;
use crate::types::{EventActor, EventScope, EventSubject};

#[derive(Clone, PartialEq)]
pub struct NewEvent {
    pub event_id: String,
    pub event_type: String,
    pub schema_version: String,
    pub producer: String,
    pub producer_key: String,
    pub occurred_at: String,
    pub subject: EventSubject,
    pub scope: EventScope,
    pub stream_key: String,
    pub actor: Option<EventActor>,
    pub correlation_id: Option<String>,
    pub causation_event_id: Option<String>,
    pub trace_id: Option<String>,
    pub data: BTreeMap<String, Value>,
}

impl fmt::Debug for NewEvent {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("NewEvent")
            .field("event_id", &self.event_id)
            .field("event_type", &self.event_type)
            .field("schema_version", &self.schema_version)
            .field("producer", &self.producer)
            .field("producer_key", &self.producer_key)
            .field("occurred_at", &self.occurred_at)
            .field("subject", &self.subject)
            .field("scope", &self.scope)
            .field("stream_key", &self.stream_key)
            .field("actor", &self.actor)
            .field("correlation_id", &self.correlation_id)
            .field("causation_event_id", &self.causation_event_id)
            .field("trace_id", &self.trace_id)
            .field("data", &"[REDACTED]")
            .finish()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EventRecordResult {
    Recorded {
        event_id: String,
        stream_sequence: u64,
        fanout_target_count: u32,
    },
    Disabled,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EventRecordErrorCategory {
    InvalidEvent,
    DuplicateProducerKey,
    CausationViolation,
    PayloadTooLarge,
    Storage,
}

#[derive(Debug, thiserror::Error)]
#[error("event recording failed ({category:?}): {message}")]
pub struct EventRecordError {
    pub category: EventRecordErrorCategory,
    pub message: String,
}

/// Builds the persistence command that an owning business repository can
/// compose into its own transaction. `None` means Eventing is disabled.
pub trait EventRecordFactoryPort: Send + Sync {
    fn prepare(&self, event: NewEvent) -> Result<Option<AppendEventRecord>, EventRecordError>;
}

#[async_trait]
pub trait EventRecorderPort: Send + Sync {
    async fn record(&self, event: NewEvent) -> Result<EventRecordResult, EventRecordError>;
}
