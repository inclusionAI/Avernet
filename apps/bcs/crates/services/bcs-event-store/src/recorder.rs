//! Application-facing Event Recorder backed by the transactional repository.

use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use bcs_service_api::port::repo::{AppendEventRecord, EventRepoError, EventRepoPort};
use bcs_service_api::port::{
    EventRecordError, EventRecordErrorCategory, EventRecordFactoryPort, EventRecordResult,
    EventRecorderPort, NewEvent,
};
use chrono::{SecondsFormat, TimeZone, Utc};

const MILLIS_PER_DAY: u64 = 86_400_000;

#[derive(Clone)]
pub struct EventRecorder {
    repo: Arc<dyn EventRepoPort>,
    enabled: bool,
    env: String,
    retention_days: u32,
    max_event_body_bytes: usize,
}

impl EventRecorder {
    pub fn new(
        repo: Arc<dyn EventRepoPort>,
        enabled: bool,
        env: impl Into<String>,
        retention_days: u32,
        max_event_body_bytes: usize,
    ) -> Self {
        Self {
            repo,
            enabled,
            env: env.into(),
            retention_days,
            max_event_body_bytes,
        }
    }
}

impl EventRecordFactoryPort for EventRecorder {
    fn prepare(&self, event: NewEvent) -> Result<Option<AppendEventRecord>, EventRecordError> {
        if !self.enabled {
            return Ok(None);
        }
        if self.env.trim().is_empty() {
            return Err(record_error(
                EventRecordErrorCategory::InvalidEvent,
                "Event Recorder environment is empty",
            ));
        }
        // Validate the whole canonical envelope shape rather than only
        // `data`; long IDs, scope, actor, and trace fields count toward the
        // externally delivered body limit as well.
        let size_probe = serde_json::json!({
            "spec_version": bcs_service_api::types::EVENT_SPEC_VERSION,
            "event_id": &event.event_id,
            "event_type": &event.event_type,
            "schema_version": &event.schema_version,
            "source": bcs_service_api::types::EVENT_SOURCE,
            "occurred_at": &event.occurred_at,
            "recorded_at": &event.occurred_at,
            "subject": &event.subject,
            "scope": &event.scope,
            "stream": { "key": &event.stream_key, "sequence": u64::MAX },
            "actor": &event.actor,
            "correlation_id": &event.correlation_id,
            "causation_event_id": &event.causation_event_id,
            "trace_id": &event.trace_id,
            "data": &event.data,
        });
        let event_size = serde_json::to_vec(&size_probe)
            .map_err(|error| {
                record_error(
                    EventRecordErrorCategory::InvalidEvent,
                    format!("serialize Event data: {error}"),
                )
            })?
            .len();
        if event_size > self.max_event_body_bytes {
            return Err(record_error(
                EventRecordErrorCategory::PayloadTooLarge,
                format!(
                    "Event data is {event_size} bytes, limit is {} bytes",
                    self.max_event_body_bytes
                ),
            ));
        }

        let now_ms: u64 = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|error| {
                record_error(
                    EventRecordErrorCategory::Storage,
                    format!("system clock predates Unix epoch: {error}"),
                )
            })?
            .as_millis()
            .try_into()
            .map_err(|_| {
                record_error(
                    EventRecordErrorCategory::Storage,
                    "system clock is outside supported range",
                )
            })?;
        let retention_ms = u64::from(self.retention_days)
            .checked_mul(MILLIS_PER_DAY)
            .and_then(|duration| now_ms.checked_add(duration))
            .ok_or_else(|| {
                record_error(
                    EventRecordErrorCategory::Storage,
                    "Event retention timestamp overflow",
                )
            })?;
        let recorded_at = Utc
            .timestamp_millis_opt(i64::try_from(now_ms).map_err(|_| {
                record_error(
                    EventRecordErrorCategory::Storage,
                    "system clock cannot be represented as RFC3339",
                )
            })?)
            .single()
            .ok_or_else(|| {
                record_error(
                    EventRecordErrorCategory::Storage,
                    "system clock cannot be represented as RFC3339",
                )
            })?
            .to_rfc3339_opts(SecondsFormat::Millis, true);

        Ok(Some(AppendEventRecord {
            event,
            recorded_at,
            retention_until_ms: retention_ms,
            env: self.env.clone(),
        }))
    }
}

#[async_trait]
impl EventRecorderPort for EventRecorder {
    async fn record(&self, event: NewEvent) -> Result<EventRecordResult, EventRecordError> {
        let Some(command) = self.prepare(event)? else {
            return Ok(EventRecordResult::Disabled);
        };
        let result = self
            .repo
            .append_event(command)
            .await
            .map_err(map_repo_error)?;
        Ok(EventRecordResult::Recorded {
            event_id: result.event.envelope.event_id,
            stream_sequence: result.event.envelope.stream.sequence,
            fanout_target_count: result.fanout_target_ids.len() as u32,
        })
    }
}

fn map_repo_error(error: EventRepoError) -> EventRecordError {
    let category = match error {
        EventRepoError::InvalidInput(_) => EventRecordErrorCategory::InvalidEvent,
        EventRepoError::Conflict(_) => EventRecordErrorCategory::DuplicateProducerKey,
        EventRepoError::CausationViolation(_) => EventRecordErrorCategory::CausationViolation,
        EventRepoError::NotFound(_) => EventRecordErrorCategory::CausationViolation,
        EventRepoError::LimitReached(_)
        | EventRepoError::LeaseLost(_)
        | EventRepoError::Unsupported(_)
        | EventRepoError::Storage(_) => EventRecordErrorCategory::Storage,
    };
    record_error(category, error.to_string())
}

fn record_error(
    category: EventRecordErrorCategory,
    message: impl Into<String>,
) -> EventRecordError {
    EventRecordError {
        category,
        message: message.into(),
    }
}
