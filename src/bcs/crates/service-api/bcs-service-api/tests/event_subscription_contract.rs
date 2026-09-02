use std::collections::BTreeMap;

use bcs_service_api::application::v1::{
    ApplicationError, CreateEventSubscriptionRequest, ERROR_EVENT_DELIVERY_LANE_BLOCKED,
    ERROR_EVENT_DELIVERY_NOT_FOUND, ERROR_EVENT_DELIVERY_NOT_REPLAYABLE,
    ERROR_EVENT_SUBSCRIPTION_FORBIDDEN, ERROR_EVENT_SUBSCRIPTION_LIMIT_REACHED,
    ERROR_EVENT_SUBSCRIPTION_NOT_FOUND, ERROR_EVENT_SUBSCRIPTION_REVISION_CONFLICT,
    ERROR_INVALID_EVENT_FILTER, ERROR_INVALID_EVENT_SCOPE, ERROR_INVALID_WEBHOOK_URL, EventActor,
    EventActorType, EventDeliveryAttemptSummary, EventEnvelope, EventPayload, EventPayloadMode,
    EventScope, EventStream, EventSubject, EventSubscriptionScope, EventSubscriptionScopeType,
    EventSubscriptionService, PatchEventSubscriptionRequest,
};
use serde_json::json;

#[test]
fn event_envelope_has_stable_json_shape_and_accepts_additive_fields() {
    let envelope = EventEnvelope {
        spec_version: "1.0".into(),
        event_id: "evt-1".into(),
        event_type: "state_machine.node.completed".into(),
        schema_version: "1.0".into(),
        source: "bcs".into(),
        occurred_at: "2026-08-18T10:00:00.123Z".into(),
        recorded_at: "2026-08-18T10:00:00.130Z".into(),
        subject: EventSubject {
            subject_type: "state_machine.node".into(),
            id: "review".into(),
        },
        scope: EventScope {
            group_id: Some("group-1".into()),
            session_id: Some("session-1".into()),
            run_id: Some("run-1".into()),
            ..EventScope::default()
        },
        stream: EventStream {
            key: "state-machine-run:run-1".into(),
            sequence: 12,
        },
        actor: Some(EventActor {
            actor_type: EventActorType::Bot,
            id: "bot-1".into(),
            display_name: Some("Reviewer".into()),
        }),
        correlation_id: Some("corr-1".into()),
        causation_event_id: Some("evt-0".into()),
        trace_id: Some("trace-1".into()),
        data: BTreeMap::from([("outcome".into(), json!("sensitive-contract-payload"))]),
    };

    let mut value = serde_json::to_value(&envelope).expect("serialize event envelope");
    assert!(!format!("{envelope:?}").contains("sensitive-contract-payload"));
    assert_eq!(value["subject"]["type"], "state_machine.node");
    assert_eq!(value["scope"]["run_id"], "run-1");
    assert_eq!(value["stream"]["sequence"], 12);
    assert!(value["scope"].get("task_id").is_none());

    value["future_optional_field"] = json!({"added": true});
    let decoded: EventEnvelope =
        serde_json::from_value(value).expect("receivers must tolerate additive envelope fields");
    assert_eq!(decoded.event_id, "evt-1");
    assert_eq!(
        decoded.actor.expect("actor").actor_type,
        EventActorType::Bot
    );
}

#[test]
fn in_flight_attempt_summary_omits_completion_fields() {
    let value = serde_json::to_value(EventDeliveryAttemptSummary {
        attempt_no: 4,
        started_at: "2026-08-28T08:00:00.000Z".to_string(),
        completed_at: None,
        latency_ms: None,
        result: None,
        http_status: None,
        error_category: None,
    })
    .expect("serialize in-flight Attempt");

    assert_eq!(value["attempt_no"], 4);
    assert_eq!(value["started_at"], "2026-08-28T08:00:00.000Z");
    assert!(value.get("completed_at").is_none());
    assert!(value.get("latency_ms").is_none());
    assert!(value.get("result").is_none());
}

#[test]
fn create_subscription_input_is_strict_and_endpoint_is_redacted() {
    let request: CreateEventSubscriptionRequest = serde_json::from_value(json!({
        "name": "workflow-observer",
        "scope": {"type": "group", "id": "group-1"},
        "event_filters": ["state_machine.*"],
        "payload": {"mode": "metadata_only"},
        "sink": {
            "type": "webhook",
            "url": "https://example.com/bcs/events"
        }
    }))
    .expect("deserialize create request");

    assert_eq!(
        request.scope,
        EventSubscriptionScope {
            scope_type: EventSubscriptionScopeType::Group,
            id: "group-1".into(),
        }
    );
    assert_eq!(
        request.payload,
        EventPayload {
            mode: EventPayloadMode::MetadataOnly,
        }
    );
    let debug = format!("{request:?}");
    assert!(!debug.contains("https://example.com/bcs/events"));
    assert!(debug.contains("[REDACTED"));

    let unknown = serde_json::from_value::<CreateEventSubscriptionRequest>(json!({
        "name": "bad",
        "scope": {"type": "group", "id": "group-1"},
        "event_filters": ["group.*"],
        "payload": {"mode": "metadata_only"},
        "ordering": {"mode": "strict_per_stream"},
        "sink": {
            "type": "webhook",
            "url": "https://example.com/events"
        },
        "unexpected": true
    }));
    assert!(unknown.is_err(), "request DTO must reject unknown fields");

    let legacy_auth = serde_json::from_value::<CreateEventSubscriptionRequest>(json!({
        "name": "bad",
        "scope": {"type": "group", "id": "group-1"},
        "event_filters": ["group.*"],
        "sink": {
            "type": "webhook",
            "url": "https://example.com/events",
            "auth": {"type": "hmac_sha256", "secret": "legacy"}
        }
    }));
    assert!(
        legacy_auth.is_err(),
        "subscription-level auth is not supported"
    );
}

#[test]
fn patch_rejects_unknown_and_explicit_null_fields() {
    let patch: PatchEventSubscriptionRequest = serde_json::from_value(json!({
        "name": "renamed",
        "payload": {"mode": "metadata_only"}
    }))
    .expect("deserialize patch");
    assert_eq!(patch.name.as_deref(), Some("renamed"));
    assert_eq!(
        patch.payload.expect("payload").mode,
        EventPayloadMode::MetadataOnly
    );

    assert!(
        serde_json::from_value::<PatchEventSubscriptionRequest>(json!({
            "name": null
        }))
        .is_err()
    );
    assert!(
        serde_json::from_value::<PatchEventSubscriptionRequest>(json!({
            "scope": {"type": "group", "id": "other"}
        }))
        .is_err()
    );

    let endpoint_patch: PatchEventSubscriptionRequest = serde_json::from_value(json!({
        "sink": {
            "type": "webhook",
            "url": "https://example.com/replacement",
            "request_timeout_ms": 5000
        }
    }))
    .expect("PATCH can update webhook endpoint and timeout");
    let debug = format!("{endpoint_patch:?}");
    assert!(!debug.contains("https://example.com/replacement"));

    assert!(
        serde_json::from_value::<PatchEventSubscriptionRequest>(json!({
            "sink": {"type": "webhook", "auth": {"type": "hmac_sha256", "secret": "legacy"}}
        }))
        .is_err()
    );
}

#[test]
fn event_application_error_codes_are_stable() {
    let cases = [
        ApplicationError::event_subscription_not_found("contract"),
        ApplicationError::event_delivery_not_found("contract"),
        ApplicationError::invalid_event_filter("contract"),
        ApplicationError::invalid_event_scope("contract"),
        ApplicationError::invalid_webhook_url("contract"),
        ApplicationError::event_subscription_limit_reached("contract"),
        ApplicationError::event_subscription_revision_conflict("contract"),
        ApplicationError::event_subscription_forbidden("contract"),
        ApplicationError::event_delivery_not_replayable("contract"),
        ApplicationError::event_delivery_lane_blocked("contract"),
    ];
    let expected = [
        ERROR_EVENT_SUBSCRIPTION_NOT_FOUND,
        ERROR_EVENT_DELIVERY_NOT_FOUND,
        ERROR_INVALID_EVENT_FILTER,
        ERROR_INVALID_EVENT_SCOPE,
        ERROR_INVALID_WEBHOOK_URL,
        ERROR_EVENT_SUBSCRIPTION_LIMIT_REACHED,
        ERROR_EVENT_SUBSCRIPTION_REVISION_CONFLICT,
        ERROR_EVENT_SUBSCRIPTION_FORBIDDEN,
        ERROR_EVENT_DELIVERY_NOT_REPLAYABLE,
        ERROR_EVENT_DELIVERY_LANE_BLOCKED,
    ];
    for (error, expected_code) in cases.into_iter().zip(expected) {
        assert_eq!(error.code(), expected_code);
    }
    assert!(matches!(
        ApplicationError::event_subscription_not_found("contract"),
        ApplicationError::NotFound { .. }
    ));
    assert!(matches!(
        ApplicationError::invalid_event_filter("contract"),
        ApplicationError::InvalidInput { .. }
    ));
    assert!(matches!(
        ApplicationError::event_subscription_forbidden("contract"),
        ApplicationError::ForbiddenCode { .. }
    ));
    assert!(matches!(
        ApplicationError::event_delivery_lane_blocked("contract"),
        ApplicationError::Conflict { .. }
    ));
}

#[test]
fn dead_letter_ends_attempts_but_keeps_the_strict_lane_blocked() {
    use bcs_service_api::application::v1::EventDeliveryStatus;

    assert!(EventDeliveryStatus::DeadLettered.is_attempt_terminal());
    assert!(!EventDeliveryStatus::DeadLettered.unblocks_strict_lane());
    assert!(EventDeliveryStatus::Succeeded.unblocks_strict_lane());
    assert!(EventDeliveryStatus::Skipped.unblocks_strict_lane());
}

#[test]
fn subscription_status_transitions_match_the_contract() {
    use bcs_service_api::application::v1::EventSubscriptionStatus as Status;

    for (from, to) in [
        (Status::Pending, Status::Active),
        (Status::Pending, Status::Deleted),
        (Status::Active, Status::Disabled),
        (Status::Active, Status::Deleted),
        (Status::Disabled, Status::Active),
        (Status::Disabled, Status::Deleted),
    ] {
        assert!(from.can_transition_to(to), "{from:?} -> {to:?}");
    }
    assert!(!Status::Deleted.can_transition_to(Status::Active));
    assert!(!Status::Active.can_transition_to(Status::Active));
}

#[test]
fn event_subscription_service_is_object_safe() {
    fn accepts(_: Option<&dyn EventSubscriptionService>) {}
    accepts(None);
}
