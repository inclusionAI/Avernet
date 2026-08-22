#![allow(dead_code)]

use std::collections::BTreeMap;

use bcs_service_api::port::NewEvent;
use bcs_service_api::port::repo::{
    AppendEventRecord, CreateEventSubscriptionRecord, EventSubscriptionRecord,
    EventSubscriptionRevisionRecord,
};
use bcs_service_api::types::{
    EVENT_SCHEMA_VERSION_V1, EventActor, EventActorType, EventPayloadMode, EventScope,
    EventSubject, EventSubscriptionScope, EventSubscriptionScopeType, EventSubscriptionStatus,
};

pub const ENV: &str = "contract";
pub const GROUP_ID: &str = "group-contract";
pub const OCCURRED_AT: &str = "2026-08-19T00:00:00.000Z";
pub const RECORDED_AT: &str = "2026-08-19T00:00:01.000Z";

pub fn subscription(subscription_id: &str) -> CreateEventSubscriptionRecord {
    CreateEventSubscriptionRecord {
        subscription: EventSubscriptionRecord {
            subscription_id: subscription_id.to_string(),
            name: "group events".to_string(),
            scope: EventSubscriptionScope {
                scope_type: EventSubscriptionScopeType::Group,
                id: GROUP_ID.to_string(),
            },
            status: EventSubscriptionStatus::Active,
            current_revision: 1,
            created_by: actor(),
            created_at_ms: 1_755_561_600_000,
            updated_at_ms: 1_755_561_600_000,
            deleted_at_ms: None,
            env: ENV.to_string(),
        },
        revision: revision(subscription_id, 1, vec!["group.*"]),
        scope_limit: 10,
    }
}

pub fn revision(
    subscription_id: &str,
    revision: u64,
    event_filters: Vec<&str>,
) -> EventSubscriptionRevisionRecord {
    EventSubscriptionRevisionRecord {
        subscription_id: subscription_id.to_string(),
        revision,
        event_filters: event_filters.into_iter().map(str::to_string).collect(),
        payload_mode: EventPayloadMode::MetadataOnly,
        endpoint_url: "https://events.example.com/contract".to_string(),
        request_timeout_ms: 10_000,
        activated_at_ms: 1_755_561_600_000 + revision,
        retired_at_ms: None,
    }
}

pub fn append(event_id: &str, producer_key: &str, event_type: &str) -> AppendEventRecord {
    AppendEventRecord {
        event: NewEvent {
            event_id: event_id.to_string(),
            event_type: event_type.to_string(),
            schema_version: EVENT_SCHEMA_VERSION_V1.to_string(),
            producer: "group-service".to_string(),
            producer_key: producer_key.to_string(),
            occurred_at: OCCURRED_AT.to_string(),
            subject: EventSubject {
                subject_type: "group".to_string(),
                id: GROUP_ID.to_string(),
            },
            scope: EventScope {
                group_id: Some(GROUP_ID.to_string()),
                ..EventScope::default()
            },
            stream_key: format!("group:{GROUP_ID}"),
            actor: Some(actor()),
            correlation_id: Some("corr-contract".to_string()),
            causation_event_id: None,
            trace_id: Some("trace-contract".to_string()),
            data: BTreeMap::new(),
        },
        recorded_at: RECORDED_AT.to_string(),
        retention_until_ms: 2_000_000_000_000,
        env: ENV.to_string(),
    }
}

pub fn actor() -> EventActor {
    EventActor {
        actor_type: EventActorType::App,
        id: "contract-app".to_string(),
        display_name: None,
    }
}
