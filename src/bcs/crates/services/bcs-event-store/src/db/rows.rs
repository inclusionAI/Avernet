//! Event store rows helpers.

use super::*;

pub(super) fn subscription_from_row(row: &DbRow) -> Result<EventSubscriptionRecord, EventRepoError> {
    Ok(EventSubscriptionRecord {
        subscription_id: column(row, "subscription_id")?,
        name: column(row, "name")?,
        scope: EventSubscriptionScope {
            scope_type: parse_scope_type(&column::<String>(row, "scope_type")?)?,
            id: column(row, "scope_id")?,
        },
        status: parse_subscription_status(&column::<String>(row, "status")?)?,
        current_revision: column(row, "current_revision")?,
        created_by: EventActor {
            actor_type: parse_actor_type(&column::<String>(row, "created_by_type")?)?,
            id: column(row, "created_by_id")?,
            display_name: None,
        },
        created_at_ms: column(row, "created_at_ms")?,
        updated_at_ms: column(row, "updated_at_ms")?,
        deleted_at_ms: optional_column(row, "deleted_at_ms")?,
        env: column(row, "env")?,
    })
}

pub(super) fn revision_from_row(row: &DbRow) -> Result<EventSubscriptionRevisionRecord, EventRepoError> {
    let filters_json: String = column(row, "event_filters_json")?;
    Ok(EventSubscriptionRevisionRecord {
        subscription_id: column(row, "subscription_id")?,
        revision: column(row, "revision")?,
        event_filters: serde_json::from_str(&filters_json).map_err(|error| {
            EventRepoError::Storage(format!("parse subscription filters: {error}"))
        })?,
        payload_mode: parse_payload_mode(&column::<String>(row, "payload_mode")?)?,
        endpoint_url: column(row, "endpoint_url")?,
        request_timeout_ms: column(row, "request_timeout_ms")?,
        activated_at_ms: column(row, "activated_at_ms")?,
        retired_at_ms: optional_column(row, "retired_at_ms")?,
    })
}

pub(super) fn event_from_row(row: &DbRow) -> Result<EventRecord, EventRepoError> {
    let actor_json: Option<String> = optional_column(row, "actor_json")?;
    let data_json: String = column(row, "data_json")?;
    let occurred_at_ms: u64 = column(row, "occurred_at_ms")?;
    let recorded_at_ms: u64 = column(row, "recorded_at_ms")?;
    Ok(EventRecord {
        envelope: EventEnvelope {
            spec_version: EVENT_SPEC_VERSION.to_string(),
            event_id: column(row, "event_id")?,
            event_type: column(row, "event_type")?,
            schema_version: column(row, "schema_version")?,
            source: EVENT_SOURCE.to_string(),
            occurred_at: rfc3339_from_ms(occurred_at_ms)?,
            recorded_at: rfc3339_from_ms(recorded_at_ms)?,
            subject: bcs_service_api::types::EventSubject {
                subject_type: column(row, "subject_type")?,
                id: column(row, "subject_id")?,
            },
            scope: EventScope {
                group_id: optional_column(row, "group_id")?,
                session_id: optional_column(row, "session_id")?,
                task_id: optional_column(row, "task_id")?,
                run_id: optional_column(row, "run_id")?,
            },
            stream: EventStream {
                key: column(row, "stream_key")?,
                sequence: column(row, "sequence")?,
            },
            actor: actor_json
                .map(|json| {
                    serde_json::from_str(&json).map_err(|error| {
                        EventRepoError::Storage(format!("parse Event actor: {error}"))
                    })
                })
                .transpose()?,
            correlation_id: optional_column(row, "correlation_id")?,
            causation_event_id: optional_column(row, "causation_event_id")?,
            trace_id: optional_column(row, "trace_id")?,
            data: serde_json::from_str::<BTreeMap<String, serde_json::Value>>(&data_json)
                .map_err(|error| EventRepoError::Storage(format!("parse Event data: {error}")))?,
        },
        producer: column(row, "producer")?,
        producer_key: column(row, "producer_key")?,
        fanout_status: parse_fanout_status(&column::<String>(row, "fanout_status")?)?,
        retention_until_ms: column(row, "retention_until_ms")?,
        env: column(row, "env")?,
    })
}

pub(super) fn target_from_row(row: &DbRow) -> Result<EventFanoutTargetRecord, EventRepoError> {
    let replay_request_id: Option<String> = optional_column(row, "replay_request_id")?;
    Ok(EventFanoutTargetRecord {
        target_id: column(row, "target_id")?,
        event_id: column(row, "event_id")?,
        subscription_id: column(row, "subscription_id")?,
        subscription_revision: column(row, "subscription_revision")?,
        purpose: parse_target_purpose(&column::<String>(row, "purpose")?)?,
        replay_request_id: replay_request_id.filter(|value| !value.is_empty()),
        replay_of_delivery_id: optional_column(row, "replay_of_delivery_id")?,
        depends_on_target_id: optional_column(row, "depends_on_target_id")?,
        status: parse_target_status(&column::<String>(row, "status")?)?,
        created_at_ms: column(row, "created_at_ms")?,
        materialized_at_ms: optional_column(row, "materialized_at_ms")?,
        cancelled_at_ms: optional_column(row, "cancelled_at_ms")?,
        lease_owner: optional_column(row, "lease_owner")?,
        lease_until_ms: optional_column(row, "lease_until_ms")?,
        env: column(row, "env")?,
    })
}

pub(super) fn delivery_from_row(row: &DbRow) -> Result<EventDeliveryRecord, EventRepoError> {
    let skip_actor_json: Option<String> = optional_column(row, "skip_actor")?;
    let http_status = optional_column::<u64>(row, "last_http_status")?
        .map(u16::try_from)
        .transpose()
        .map_err(|_| EventRepoError::Storage("HTTP status is outside u16 range".into()))?;
    Ok(EventDeliveryRecord {
        delivery_id: column(row, "delivery_id")?,
        fanout_target_id: column(row, "fanout_target_id")?,
        event_id: column(row, "event_id")?,
        event_type: column(row, "event_type")?,
        subscription_id: column(row, "subscription_id")?,
        subscription_revision: column(row, "subscription_revision")?,
        stream_key: column(row, "stream_key")?,
        sequence: column(row, "sequence")?,
        payload_bytes: column(row, "payload_bytes")?,
        payload_sha256: column(row, "payload_sha256")?,
        status: parse_delivery_status(&column::<String>(row, "status")?)?,
        attempt_count: column(row, "attempt_count")?,
        first_attempt_at_ms: optional_column(row, "first_attempt_at_ms")?,
        last_attempt_at_ms: optional_column(row, "last_attempt_at_ms")?,
        next_attempt_at_ms: optional_column(row, "next_attempt_at_ms")?,
        lease_owner: optional_column(row, "lease_owner")?,
        lease_until_ms: optional_column(row, "lease_until_ms")?,
        last_http_status: http_status,
        last_error_category: optional_column(row, "last_error_category")?,
        last_error_summary: optional_column(row, "last_error_summary")?,
        dead_lettered_at_ms: optional_column(row, "dead_lettered_at_ms")?,
        cancelled_at_ms: optional_column(row, "cancelled_at_ms")?,
        skipped_at_ms: optional_column(row, "skipped_at_ms")?,
        skip_actor: skip_actor_json
            .map(|json| {
                serde_json::from_str(&json)
                    .map_err(|error| EventRepoError::Storage(format!("parse skip actor: {error}")))
            })
            .transpose()?,
        skip_reason: optional_column(row, "skip_reason")?,
        replay_of_delivery_id: optional_column(row, "replay_of_delivery_id")?,
        resolved_by_delivery_id: optional_column(row, "resolved_by_delivery_id")?,
        resolved_at_ms: optional_column(row, "resolved_at_ms")?,
        created_at_ms: column(row, "created_at_ms")?,
        succeeded_at_ms: optional_column(row, "succeeded_at_ms")?,
        env: column(row, "env")?,
    })
}

pub(super) fn attempt_from_row(row: &DbRow) -> Result<EventDeliveryAttemptRecord, EventRepoError> {
    let result = optional_column::<String>(row, "result")?
        .map(|result| parse_attempt_result(&result))
        .transpose()?;
    let http_status = optional_column::<u64>(row, "http_status")?
        .map(u16::try_from)
        .transpose()
        .map_err(|_| EventRepoError::Storage("HTTP status is outside u16 range".into()))?;
    Ok(EventDeliveryAttemptRecord {
        delivery_id: column(row, "delivery_id")?,
        attempt_no: column(row, "attempt_no")?,
        started_at_ms: column(row, "started_at_ms")?,
        completed_at_ms: optional_column(row, "completed_at_ms")?,
        latency_ms: optional_column(row, "latency_ms")?,
        result,
        http_status,
        error_category: optional_column(row, "error_category")?,
        error_summary: optional_column(row, "error_summary")?,
        response_bytes_observed: optional_column(row, "response_bytes_observed")?,
        worker_id: column(row, "worker_id")?,
    })
}
pub(super) fn timestamp_ms_expr(flavor: DbSqlFlavor, column: &str, alias: &str) -> String {
    match flavor {
        DbSqlFlavor::Mysql => {
            format!("CAST(UNIX_TIMESTAMP({column}) * 1000 AS UNSIGNED) AS {alias}")
        }
        DbSqlFlavor::Sqlite => {
            format!(
                "(CAST(strftime('%s', {column}) AS INTEGER) * 1000 + \
                 CAST(substr(strftime('%f', {column}), 4, 3) AS INTEGER)) AS {alias}"
            )
        }
    }
}

pub(super) fn rfc3339_from_ms(timestamp_ms: u64) -> Result<String, EventRepoError> {
    let timestamp_ms = i64::try_from(timestamp_ms)
        .map_err(|_| EventRepoError::Storage("timestamp is outside supported range".to_string()))?;
    Utc.timestamp_millis_opt(timestamp_ms)
        .single()
        .map(|timestamp| timestamp.to_rfc3339_opts(SecondsFormat::Millis, true))
        .ok_or_else(|| EventRepoError::Storage("timestamp is outside supported range".to_string()))
}

pub(super) fn scope_storage_id(scope: &EventSubscriptionScope) -> String {
    scope.id.clone()
}

pub(super) fn scope_db_value(scope: &EventSubscriptionScope) -> DbValue {
    DbValue::from(scope.id.as_str())
}

pub(super) fn scope_type_name(value: EventSubscriptionScopeType) -> &'static str {
    match value {
        EventSubscriptionScopeType::Group => "group",
    }
}

pub(super) fn parse_scope_type(value: &str) -> Result<EventSubscriptionScopeType, EventRepoError> {
    match value {
        "group" => Ok(EventSubscriptionScopeType::Group),
        _ => Err(EventRepoError::Storage(format!(
            "unknown scope type {value}"
        ))),
    }
}

pub(super) fn actor_type_name(value: EventActorType) -> &'static str {
    match value {
        EventActorType::Human => "human",
        EventActorType::Bot => "bot",
        EventActorType::App => "app",
        EventActorType::System => "system",
    }
}

pub(super) fn parse_actor_type(value: &str) -> Result<EventActorType, EventRepoError> {
    match value {
        "human" => Ok(EventActorType::Human),
        "bot" => Ok(EventActorType::Bot),
        "app" => Ok(EventActorType::App),
        "system" => Ok(EventActorType::System),
        _ => Err(EventRepoError::Storage(format!(
            "unknown actor type {value}"
        ))),
    }
}

pub(super) fn subscription_status_name(value: EventSubscriptionStatus) -> &'static str {
    match value {
        EventSubscriptionStatus::Pending => "pending",
        EventSubscriptionStatus::Active => "active",
        EventSubscriptionStatus::Disabled => "disabled",
        EventSubscriptionStatus::Deleted => "deleted",
    }
}

pub(super) fn parse_subscription_status(value: &str) -> Result<EventSubscriptionStatus, EventRepoError> {
    match value {
        "pending" => Ok(EventSubscriptionStatus::Pending),
        "active" => Ok(EventSubscriptionStatus::Active),
        "disabled" => Ok(EventSubscriptionStatus::Disabled),
        "deleted" => Ok(EventSubscriptionStatus::Deleted),
        _ => Err(EventRepoError::Storage(format!(
            "unknown subscription status {value}"
        ))),
    }
}

pub(super) fn payload_mode_name(value: EventPayloadMode) -> &'static str {
    match value {
        EventPayloadMode::MetadataOnly => "metadata_only",
        EventPayloadMode::Full => "full",
    }
}

pub(super) fn parse_payload_mode(value: &str) -> Result<EventPayloadMode, EventRepoError> {
    match value {
        "metadata_only" => Ok(EventPayloadMode::MetadataOnly),
        "full" => Ok(EventPayloadMode::Full),
        _ => Err(EventRepoError::Storage(format!(
            "unknown payload mode {value}"
        ))),
    }
}

pub(super) fn parse_fanout_status(value: &str) -> Result<EventFanoutStatus, EventRepoError> {
    match value {
        "pending" => Ok(EventFanoutStatus::Pending),
        "completed" => Ok(EventFanoutStatus::Completed),
        "failed" => Ok(EventFanoutStatus::Failed),
        _ => Err(EventRepoError::Storage(format!(
            "unknown fanout status {value}"
        ))),
    }
}

pub(super) fn parse_target_purpose(value: &str) -> Result<EventFanoutTargetPurpose, EventRepoError> {
    match value {
        "normal" => Ok(EventFanoutTargetPurpose::Normal),
        "causal_prerequisite" => Ok(EventFanoutTargetPurpose::CausalPrerequisite),
        "manual_replay" => Ok(EventFanoutTargetPurpose::ManualReplay),
        _ => Err(EventRepoError::Storage(format!(
            "unknown fanout target purpose {value}"
        ))),
    }
}

pub(super) fn parse_target_status(value: &str) -> Result<EventFanoutTargetStatus, EventRepoError> {
    match value {
        "pending" => Ok(EventFanoutTargetStatus::Pending),
        "materialized" => Ok(EventFanoutTargetStatus::Materialized),
        "cancelled" => Ok(EventFanoutTargetStatus::Cancelled),
        "failed" => Ok(EventFanoutTargetStatus::Failed),
        _ => Err(EventRepoError::Storage(format!(
            "unknown fanout target status {value}"
        ))),
    }
}

pub(super) fn delivery_status_name(value: EventDeliveryStatus) -> &'static str {
    match value {
        EventDeliveryStatus::Pending => "pending",
        EventDeliveryStatus::InFlight => "in_flight",
        EventDeliveryStatus::RetryWait => "retry_wait",
        EventDeliveryStatus::Succeeded => "succeeded",
        EventDeliveryStatus::DeadLettered => "dead_lettered",
        EventDeliveryStatus::Cancelled => "cancelled",
        EventDeliveryStatus::Skipped => "skipped",
    }
}

pub(super) fn parse_delivery_status(value: &str) -> Result<EventDeliveryStatus, EventRepoError> {
    match value {
        "pending" => Ok(EventDeliveryStatus::Pending),
        "in_flight" => Ok(EventDeliveryStatus::InFlight),
        "retry_wait" => Ok(EventDeliveryStatus::RetryWait),
        "succeeded" => Ok(EventDeliveryStatus::Succeeded),
        "dead_lettered" => Ok(EventDeliveryStatus::DeadLettered),
        "cancelled" => Ok(EventDeliveryStatus::Cancelled),
        "skipped" => Ok(EventDeliveryStatus::Skipped),
        _ => Err(EventRepoError::Storage(format!(
            "unknown Delivery status {value}"
        ))),
    }
}

pub(super) fn attempt_result_name(value: EventDeliveryAttemptRecordResult) -> &'static str {
    match value {
        EventDeliveryAttemptRecordResult::Success => "success",
        EventDeliveryAttemptRecordResult::Retryable => "retryable",
        EventDeliveryAttemptRecordResult::Terminal => "terminal",
    }
}

pub(super) fn parse_attempt_result(value: &str) -> Result<EventDeliveryAttemptRecordResult, EventRepoError> {
    match value {
        "success" => Ok(EventDeliveryAttemptRecordResult::Success),
        "retryable" => Ok(EventDeliveryAttemptRecordResult::Retryable),
        "terminal" => Ok(EventDeliveryAttemptRecordResult::Terminal),
        _ => Err(EventRepoError::Storage(format!(
            "unknown Delivery Attempt result {value}"
        ))),
    }
}

pub(super) fn column<T: bcs_db_api::FromDbColumn>(row: &DbRow, name: &str) -> Result<T, EventRepoError> {
    db_get_column(row, name).map_err(storage_error)
}

pub(super) fn optional_column<T: bcs_db_api::FromDbColumn>(
    row: &DbRow,
    name: &str,
) -> Result<Option<T>, EventRepoError> {
    db_get_column_opt(row, name).map_err(storage_error)
}

pub(super) fn optional_u64_value(value: Option<u64>) -> DbValue {
    value.map(DbValue::from).unwrap_or(DbValue::Null)
}
