#![allow(clippy::expect_used, clippy::unwrap_used)]

mod support;

use std::collections::BTreeMap;
use std::sync::atomic::Ordering;

use bcs_config_api::{EventingConfig, PrivateEndpointAllowlistEntryConfig};
use bcs_eventing::{
    EventMatcherError, EventProjectionError, event_filter_matches, project_event,
    subscription_scope_matches, validate_event_filter,
};
use bcs_service_api::application::v1::{
    EventSinkInput, EventSinkView, EventSubscriptionDesiredStatus, EventSubscriptionService,
    GetEventSubscription, GroupEventSubscriptionProvisioner, InlineGroupEventSubscriptionRequest,
    PatchEventSinkInput, PatchEventSubscription, PatchEventSubscriptionRequest,
    ReplayEventDelivery, SkipEventDelivery, TestEventSubscription,
};
use bcs_service_api::port::NewEvent;
use bcs_service_api::port::repo::{
    AppendEventRecord, ClaimEventDeliveries, ClaimFanoutTargets, CompleteEventDeliveryAttempt,
    EventDeliveryAttemptRecordResult, EventDeliveryRecord, EventRepoPort,
    ListEventSubscriptionRecords, MaterializeFanoutTarget,
};
use bcs_service_api::types::{
    EVENT_SCHEMA_VERSION_V1, EVENT_SOURCE, EVENT_SPEC_VERSION, EventActorType, EventDeliveryStatus,
    EventEnvelope, EventPayload, EventPayloadMode, EventScope, EventStream, EventSubject,
    EventSubscriptionScope, EventSubscriptionScopeType, EventSubscriptionStatus,
};
use bcs_service_api::{
    Group, GroupCoreService, Participant, ParticipantRole, Session, SessionKind, SessionStatus,
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use support::{
    NOW_MS, caller, create_command, group_scope, harness, harness_with_eventing_config,
};

fn inline_subscription(name: &str, filters: Vec<&str>) -> InlineGroupEventSubscriptionRequest {
    InlineGroupEventSubscriptionRequest {
        name: name.to_string(),
        event_filters: filters.into_iter().map(str::to_string).collect(),
        payload: EventPayload::default(),
        sink: EventSinkInput::Webhook {
            url: "https://events.example.com/group-create".to_string(),
            request_timeout_ms: None,
        },
    }
}

#[tokio::test]
async fn inline_group_prepare_fixes_scope_and_cancellation_never_activates() {
    let harness = harness(true);
    harness.authorizer.allowed.store(false, Ordering::SeqCst);

    let prepared = harness
        .service
        .prepare(
            &caller(),
            "server-group-id",
            vec![inline_subscription("inline", vec!["group.*"])],
        )
        .await
        .expect("prepare inline subscription");
    assert_eq!(prepared.group_id, "server-group-id");
    assert_eq!(prepared.actor.id, "human_owner");
    let (record, _) = harness
        .repo
        .get_subscription(&prepared.subscription_ids[0], "test")
        .await
        .expect("load pending subscription")
        .expect("pending subscription");
    assert_eq!(record.status, EventSubscriptionStatus::Pending);
    assert_eq!(record.scope.scope_type, EventSubscriptionScopeType::Group);
    assert_eq!(record.scope.id, "server-group-id");

    harness
        .service
        .cancel(&prepared, "group_create_failed")
        .await
        .expect("cancel pending subscription");
    let (cancelled, _) = harness
        .repo
        .get_subscription(&prepared.subscription_ids[0], "test")
        .await
        .expect("load cancelled subscription")
        .expect("cancelled subscription");
    assert_eq!(cancelled.status, EventSubscriptionStatus::Deleted);
}

#[tokio::test]
async fn pending_group_sets_are_grouped_for_orphan_recovery() {
    let harness = harness(true);
    let first = harness
        .service
        .prepare(
            &caller(),
            "group-orphan-a",
            vec![
                inline_subscription("first-a", vec!["group.*"]),
                inline_subscription("second-a", vec!["session.*"]),
            ],
        )
        .await
        .expect("prepare first Group set");
    let second = harness
        .service
        .prepare(
            &caller(),
            "group-orphan-b",
            vec![inline_subscription("first-b", vec!["group.*"])],
        )
        .await
        .expect("prepare second Group set");

    let pending = harness
        .service
        .list_pending_groups()
        .await
        .expect("list pending Group sets");

    assert_eq!(pending.len(), 2);
    assert_eq!(pending[0].prepared.group_id, "group-orphan-a");
    assert_eq!(pending[0].prepared.subscription_ids, first.subscription_ids);
    assert_eq!(pending[0].created_at_ms, NOW_MS);
    assert_eq!(pending[1].prepared.group_id, "group-orphan-b");
    assert_eq!(
        pending[1].prepared.subscription_ids,
        second.subscription_ids
    );
    assert_eq!(pending[1].prepared.actor.actor_type, EventActorType::System);
}

#[tokio::test]
async fn inline_group_prepare_compensates_earlier_pending_rows_on_validation_failure() {
    let harness = harness(true);
    let error = harness
        .service
        .prepare(
            &caller(),
            "server-group-id",
            vec![
                inline_subscription("valid", vec!["group.*"]),
                inline_subscription("invalid", vec!["group.typo"]),
            ],
        )
        .await
        .expect_err("unknown filter rejects the complete inline set");
    assert_eq!(error.code(), "invalid_event_filter");

    let records = harness
        .repo
        .list_subscriptions(ListEventSubscriptionRecords {
            scope: Some(EventSubscriptionScope {
                scope_type: EventSubscriptionScopeType::Group,
                id: "server-group-id".to_string(),
            }),
            status: None,
            after_subscription_id: None,
            limit: 100,
            env: "test".to_string(),
        })
        .await
        .expect("list compensated subscriptions");
    assert_eq!(records.len(), 1);
    assert_eq!(records[0].status, EventSubscriptionStatus::Deleted);
}

#[tokio::test]
async fn inline_group_finalization_activates_and_snapshots_ordered_creation_events() {
    let harness = harness(true);
    let prepared = harness
        .service
        .prepare(
            &caller(),
            "group-provisioning",
            vec![inline_subscription(
                "creation-events",
                vec!["group.created", "session.created"],
            )],
        )
        .await
        .expect("prepare inline subscription");
    let mut group = Group::new(
        "group-provisioning",
        "bot-driver",
        vec![Participant::bot("bot-driver", ParticipantRole::Driver)],
    );
    group.label = Some("Provisioned Group".to_string());
    group.originator = Some("human_owner".to_string());
    group.record_status = "provisioning".to_string();
    group.created_at = NOW_MS - 100;
    group.updated_at = NOW_MS - 100;
    harness
        .groups
        .upsert(group.clone())
        .await
        .expect("persist provisioning Group");
    let session = Session {
        id: "group-provisioning:12345678".to_string(),
        group_id: group.id.clone(),
        session_title: Some("Initial".to_string()),
        env: Some("test".to_string()),
        status: SessionStatus::Running,
        session_kind: SessionKind::Chat,
        participants: group.participants.clone(),
        group_version: Some(group.version),
        caller_id: Some("human_owner".to_string()),
        input: None,
        output: None,
        error_message: None,
        callback_status: None,
        activation_count: 1,
        caller_principal: None,
        created_by: Some("human_owner".to_string()),
        current_msg_seq: 0,
        participant_join_seq: None,
        created_at: NOW_MS - 50,
        updated_at: NOW_MS - 50,
        completed_at: None,
        collected_at: None,
        meta: None,
    };

    harness
        .service
        .finalize(&prepared, &group, Some(&session))
        .await
        .expect("atomically finalize Group provisioning");

    let stored_group = harness
        .groups
        .try_get(&group.id)
        .await
        .expect("load Group")
        .expect("stored Group");
    assert_eq!(stored_group.record_status, "active");
    let (subscription, revision) = harness
        .repo
        .get_subscription(&prepared.subscription_ids[0], "test")
        .await
        .expect("load Subscription")
        .expect("stored Subscription");
    assert_eq!(subscription.status, EventSubscriptionStatus::Active);
    assert_eq!(revision.activated_at_ms, NOW_MS);

    let targets = claim_targets(&harness).await;
    assert_eq!(targets.len(), 2);
    let mut events = Vec::new();
    for target in &targets {
        events.push(
            harness
                .repo
                .get_event(&target.event_id, "test")
                .await
                .expect("load Event")
                .expect("stored Event"),
        );
    }
    let group_event = events
        .iter()
        .find(|event| event.envelope.event_type == "group.created")
        .expect("group.created");
    let session_event = events
        .iter()
        .find(|event| event.envelope.event_type == "session.created")
        .expect("session.created");
    assert_eq!(group_event.envelope.stream.sequence, 1);
    assert_eq!(session_event.envelope.stream.sequence, 1);
    assert_eq!(
        session_event.envelope.causation_event_id.as_deref(),
        Some(group_event.envelope.event_id.as_str())
    );
    let group_target = targets
        .iter()
        .find(|target| target.event_id == group_event.envelope.event_id)
        .expect("group target");
    let session_target = targets
        .iter()
        .find(|target| target.event_id == session_event.envelope.event_id)
        .expect("session target");
    assert_eq!(
        session_target.depends_on_target_id.as_deref(),
        Some(group_target.target_id.as_str())
    );

    let redacted = harness
        .service
        .load_activated(&prepared)
        .await
        .expect("load redacted Subscription summaries");
    assert_eq!(redacted.len(), 1);
    assert!(!format!("{:?}", redacted[0]).contains("/group-create"));
}

#[tokio::test]
async fn create_enforces_authorization_catalog_and_redacts_webhook_endpoint() {
    let harness = harness(false);
    let full_error = harness
        .service
        .create(create_command(
            vec!["message.created".to_string()],
            EventPayloadMode::Full,
        ))
        .await
        .expect_err("full payload needs a separate grant");
    assert_eq!(full_error.code(), "event_subscription_forbidden");

    let unknown_filter = harness
        .service
        .create(create_command(
            vec!["message.typo".to_string()],
            EventPayloadMode::MetadataOnly,
        ))
        .await
        .expect_err("unknown filters fail closed");
    assert_eq!(unknown_filter.code(), "invalid_event_filter");

    let created = harness
        .service
        .create(create_command(
            vec!["group.*".to_string()],
            EventPayloadMode::MetadataOnly,
        ))
        .await
        .expect("valid subscription");
    assert!(
        created.include_descendants,
        "group scope defaults to descendants"
    );
    let EventSinkView::Webhook { endpoint, .. } = &created.sink;
    assert_eq!(endpoint.scheme, "https");
    assert_eq!(endpoint.host, "events.example.com");
    assert_ne!(endpoint.path_hash, "/bcs/events");
    assert!(!format!("{created:?}").contains("/bcs/events"));

    harness.authorizer.allowed.store(false, Ordering::SeqCst);
    let hidden = harness
        .service
        .get(GetEventSubscription {
            caller: caller(),
            subscription_id: created.subscription_id,
        })
        .await
        .expect_err("read authorization failures hide existence");
    assert_eq!(hidden.code(), "event_subscription_not_found");
}

#[tokio::test]
async fn optimistic_revision_and_endpoint_validation_fail_before_writes() {
    let harness = harness(true);
    let mut query_url = create_command(vec!["group.*".to_string()], EventPayloadMode::MetadataOnly);
    let EventSinkInput::Webhook { url, .. } = &mut query_url.request.sink;
    *url = "https://events.example.com/hook?token=secret".to_string();
    let error = harness
        .service
        .create(query_url)
        .await
        .expect_err("query strings are rejected");
    assert_eq!(error.code(), "invalid_webhook_url");
    assert!(!error.to_string().contains("token=secret"));

    let created = harness
        .service
        .create(create_command(
            vec!["group.*".to_string()],
            EventPayloadMode::MetadataOnly,
        ))
        .await
        .expect("valid subscription");
    let conflict = harness
        .service
        .patch(PatchEventSubscription {
            caller: caller(),
            subscription_id: created.subscription_id,
            expected_revision: created.revision + 1,
            patch: PatchEventSubscriptionRequest {
                name: Some("lost-update".to_string()),
                ..PatchEventSubscriptionRequest::default()
            },
        })
        .await
        .expect_err("stale revision is rejected");
    assert_eq!(conflict.code(), "event_subscription_revision_conflict");
}

#[tokio::test]
async fn local_policy_accepts_http_loopback_without_accepting_public_http() {
    let mut eventing_config = EventingConfig {
        enabled: true,
        ..EventingConfig::default()
    };
    eventing_config.webhook.allow_http_loopback = true;
    eventing_config.webhook.allow_non_standard_ports = true;
    let harness = harness_with_eventing_config(true, eventing_config);

    let mut loopback =
        create_command(vec!["group.*".to_string()], EventPayloadMode::MetadataOnly);
    let EventSinkInput::Webhook { url, .. } = &mut loopback.request.sink;
    *url = "http://127.0.0.1:28082/events".to_string();
    let created = harness
        .service
        .create(loopback)
        .await
        .expect("local loopback Receiver should be accepted");
    let EventSinkView::Webhook { endpoint, .. } = created.sink;
    assert_eq!(endpoint.scheme, "http");
    assert_eq!(endpoint.host, "127.0.0.1");

    let mut public_http =
        create_command(vec!["group.*".to_string()], EventPayloadMode::MetadataOnly);
    let EventSinkInput::Webhook { url, .. } = &mut public_http.request.sink;
    *url = "http://events.example.com/events".to_string();
    let error = harness
        .service
        .create(public_http)
        .await
        .expect_err("local policy must not enable public HTTP endpoints");
    assert_eq!(error.code(), "invalid_webhook_url");
}

#[tokio::test]
async fn private_endpoint_allowlist_permits_only_matching_host_and_port() {
    let mut eventing_config = EventingConfig {
        enabled: true,
        ..EventingConfig::default()
    };
    eventing_config.webhook.private_endpoint_allowlist = vec![
        PrivateEndpointAllowlistEntryConfig {
            host: "*.hooks.example.internal".to_string(),
            cidrs: vec!["10.20.0.0/16".to_string()],
            ports: vec![8443],
        },
    ];
    let harness = harness_with_eventing_config(true, eventing_config);

    let mut allowed =
        create_command(vec!["group.*".to_string()], EventPayloadMode::MetadataOnly);
    let EventSinkInput::Webhook { url, .. } = &mut allowed.request.sink;
    *url = "https://worker.hooks.example.internal:8443/events".to_string();
    harness
        .service
        .create(allowed)
        .await
        .expect("matching wildcard host and port should pass static validation");

    for rejected_url in [
        "https://hooks.example.internal:8443/events",
        "https://worker.hooks.example.internal:9443/events",
        "https://worker.evilhooks.example.internal:8443/events",
    ] {
        let mut rejected =
            create_command(vec!["group.*".to_string()], EventPayloadMode::MetadataOnly);
        let EventSinkInput::Webhook { url, .. } = &mut rejected.request.sink;
        *url = rejected_url.to_string();
        let error = harness
            .service
            .create(rejected)
            .await
            .expect_err("non-matching private endpoint rule should be rejected");
        assert_eq!(error.code(), "invalid_webhook_url");
    }
}

#[tokio::test]
async fn active_subscription_count_is_limited_per_scope() {
    let harness = harness(true);
    for _ in 0..10 {
        let command = create_command(
            vec!["session.*".to_string()],
            EventPayloadMode::MetadataOnly,
        );
        harness
            .service
            .create(command)
            .await
            .expect("subscription below configured limit");
    }
    let over_limit = create_command(
        vec!["session.*".to_string()],
        EventPayloadMode::MetadataOnly,
    );
    let error = harness
        .service
        .create(over_limit)
        .await
        .expect_err("configured scope limit is enforced");
    assert_eq!(error.code(), "event_subscription_limit_reached");
}

#[tokio::test]
async fn filter_and_timeout_changes_keep_old_targets_but_endpoint_change_cancels_them() {
    let keep_filter = harness(true);
    let created = create_group_subscription(&keep_filter).await;
    append_group_event(&keep_filter, "evt-filter").await;
    keep_filter
        .service
        .patch(PatchEventSubscription {
            caller: caller(),
            subscription_id: created.subscription_id,
            expected_revision: created.revision,
            patch: PatchEventSubscriptionRequest {
                event_filters: Some(vec!["session.*".to_string()]),
                ..PatchEventSubscriptionRequest::default()
            },
        })
        .await
        .expect("filter revision");
    assert_eq!(claim_targets(&keep_filter).await.len(), 1);

    let keep_timeout = harness(true);
    let created = create_group_subscription(&keep_timeout).await;
    append_group_event(&keep_timeout, "evt-timeout").await;
    keep_timeout
        .service
        .patch(PatchEventSubscription {
            caller: caller(),
            subscription_id: created.subscription_id,
            expected_revision: created.revision,
            patch: PatchEventSubscriptionRequest {
                sink: Some(PatchEventSinkInput::Webhook {
                    url: None,
                    request_timeout_ms: Some(7_000),
                }),
                ..PatchEventSubscriptionRequest::default()
            },
        })
        .await
        .expect("timeout revision");
    assert_eq!(claim_targets(&keep_timeout).await.len(), 1);

    let cancel_endpoint = harness(true);
    let created = create_group_subscription(&cancel_endpoint).await;
    append_group_event(&cancel_endpoint, "evt-endpoint").await;
    cancel_endpoint
        .service
        .patch(PatchEventSubscription {
            caller: caller(),
            subscription_id: created.subscription_id,
            expected_revision: created.revision,
            patch: PatchEventSubscriptionRequest {
                sink: Some(PatchEventSinkInput::Webhook {
                    url: Some("https://replacement.example.com/events".to_string()),
                    request_timeout_ms: None,
                }),
                ..PatchEventSubscriptionRequest::default()
            },
        })
        .await
        .expect("endpoint revision");
    assert!(claim_targets(&cancel_endpoint).await.is_empty());

    let cancel_payload = harness(true);
    let created = cancel_payload
        .service
        .create(create_command(
            vec!["group.*".to_string()],
            EventPayloadMode::Full,
        ))
        .await
        .expect("full-payload subscription");
    append_group_event(&cancel_payload, "evt-payload").await;
    cancel_payload
        .service
        .patch(PatchEventSubscription {
            caller: caller(),
            subscription_id: created.subscription_id,
            expected_revision: created.revision,
            patch: PatchEventSubscriptionRequest {
                payload: Some(EventPayload {
                    mode: EventPayloadMode::MetadataOnly,
                }),
                ..PatchEventSubscriptionRequest::default()
            },
        })
        .await
        .expect("payload tightening");
    assert!(claim_targets(&cancel_payload).await.is_empty());
}

#[tokio::test]
async fn disabling_cancels_backlog_from_every_older_revision() {
    let harness = harness(true);
    let created = create_group_subscription(&harness).await;
    append_group_event(&harness, "evt-old-revision").await;
    let updated = harness
        .service
        .patch(PatchEventSubscription {
            caller: caller(),
            subscription_id: created.subscription_id.clone(),
            expected_revision: created.revision,
            patch: PatchEventSubscriptionRequest {
                event_filters: Some(vec!["session.*".to_string()]),
                ..PatchEventSubscriptionRequest::default()
            },
        })
        .await
        .expect("filter update keeps old target");
    harness
        .service
        .patch(PatchEventSubscription {
            caller: caller(),
            subscription_id: created.subscription_id,
            expected_revision: updated.revision,
            patch: PatchEventSubscriptionRequest {
                status: Some(EventSubscriptionDesiredStatus::Disabled),
                ..PatchEventSubscriptionRequest::default()
            },
        })
        .await
        .expect("disable Subscription");
    assert!(claim_targets(&harness).await.is_empty());
}

#[tokio::test]
async fn test_delivery_uses_subscription_configuration_without_persisting_an_event() {
    let harness = harness(true);
    let created = create_group_subscription(&harness).await;
    let result = harness
        .service
        .test(TestEventSubscription {
            caller: caller(),
            subscription_id: created.subscription_id,
        })
        .await
        .expect("test Delivery");
    assert!(result.delivered);
    let event_id = {
        let requests = harness
            .delivery
            .requests
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        assert_eq!(requests.len(), 1);
        assert_eq!(
            requests[0].endpoint_url,
            "https://events.example.com/bcs/events"
        );
        let body: Value = serde_json::from_slice(&requests[0].body).expect("test Event JSON");
        assert_eq!(body["event_type"], "event_subscription.test");
        assert_eq!(body["data"]["test"], true);
        body["event_id"]
            .as_str()
            .expect("test Event id")
            .to_string()
    };
    assert!(
        harness
            .repo
            .get_event(&event_id, "test")
            .await
            .expect("query Event store")
            .is_none(),
        "test Event must not enter the canonical Event Store"
    );
}

#[tokio::test]
async fn replay_is_idempotent_and_skip_records_the_data_loss_decision() {
    let harness = harness(true);
    let created = create_group_subscription(&harness).await;
    append_group_event(&harness, "evt-dead-letter").await;
    materialize_first_target(&harness, &created.subscription_id, "del-dead-letter").await;
    dead_letter_delivery(&harness, "del-dead-letter").await;

    let replay = harness
        .service
        .replay_delivery(ReplayEventDelivery {
            caller: caller(),
            delivery_id: "del-dead-letter".to_string(),
            replay_request_id: "replay-request-1".to_string(),
            expected_subscription_revision: created.revision,
        })
        .await
        .expect("replay dead letter");
    assert_eq!(replay.replacement.status, EventDeliveryStatus::Pending);

    let duplicate = harness
        .service
        .replay_delivery(ReplayEventDelivery {
            caller: caller(),
            delivery_id: "del-dead-letter".to_string(),
            replay_request_id: "replay-request-1".to_string(),
            expected_subscription_revision: created.revision,
        })
        .await
        .expect("same replay request is idempotent");
    assert_eq!(
        duplicate.replacement.delivery_id,
        replay.replacement.delivery_id
    );

    let skipped = harness
        .service
        .skip_delivery(SkipEventDelivery {
            caller: caller(),
            delivery_id: "del-dead-letter".to_string(),
            reason: "operator accepted downstream data loss".to_string(),
        })
        .await
        .expect("skip unresolved dead letter");
    assert_eq!(skipped.status, EventDeliveryStatus::Skipped);
}

#[test]
fn matcher_validates_catalog_syntax_and_descendant_boundaries() {
    let catalog = bcs_eventing::EventCatalog::load_embedded().expect("catalog");
    assert!(validate_event_filter(&catalog, "message.created").is_ok());
    assert!(validate_event_filter(&catalog, "state_machine.node.*").is_ok());
    assert_eq!(
        validate_event_filter(&catalog, "state_*.created"),
        Err(EventMatcherError::InvalidFilterSyntax)
    );
    assert_eq!(
        validate_event_filter(&catalog, "message.unknown"),
        Err(EventMatcherError::UnknownFilter)
    );
    assert!(event_filter_matches(
        "state_machine.*",
        "state_machine.node.started"
    ));
    assert!(!event_filter_matches(
        "task.*",
        "state_machine.node.started"
    ));

    let event_scope = EventScope {
        group_id: Some("group-1".to_string()),
        session_id: Some("session-1".to_string()),
        ..EventScope::default()
    };
    assert!(subscription_scope_matches(&group_scope(), &event_scope));
    let other_scope = EventScope {
        group_id: Some("other-group".to_string()),
        ..event_scope
    };
    assert!(!subscription_scope_matches(&group_scope(), &other_scope));
}

#[test]
fn projection_removes_content_and_sensitive_fields_or_truncates_utf8_safely() {
    let catalog = bcs_eventing::EventCatalog::load_embedded().expect("catalog");
    let text = "你".repeat(30_000);
    let event = message_event(text.clone());

    let metadata = project_event(&event, &catalog, EventPayloadMode::MetadataOnly, 262_144)
        .expect("metadata projection");
    let metadata: Value = serde_json::from_slice(&metadata).expect("metadata JSON");
    assert_eq!(metadata["data"]["content"]["included"], false);
    assert!(metadata["data"]["content"].get("text").is_none());
    assert!(metadata["data"]["content"].get("sha256").is_none());
    assert_eq!(metadata["data"]["attachments"], json!([]));
    assert!(metadata["data"].get("token").is_none());
    assert!(metadata["data"]["nested"].get("thinking").is_none());

    let full =
        project_event(&event, &catalog, EventPayloadMode::Full, 262_144).expect("full projection");
    let full: Value = serde_json::from_slice(&full).expect("full JSON");
    let delivered = full["data"]["content"]["text"].as_str().expect("full text");
    assert!(delivered.len() <= 65_536);
    assert!(delivered.is_char_boundary(delivered.len()));
    assert_eq!(full["data"]["content"]["truncated"], true);
    assert_eq!(full["data"]["content"]["size_bytes"], text.len() as u64);
    assert_eq!(
        full["data"]["content"]["delivered_bytes"],
        delivered.len() as u64
    );

    assert_eq!(
        project_event(&event, &catalog, EventPayloadMode::Full, 1_024),
        Err(EventProjectionError::PayloadTooLarge)
    );
}

async fn create_group_subscription(
    harness: &support::Harness,
) -> bcs_service_api::application::v1::EventSubscription {
    harness
        .service
        .create(create_command(
            vec!["group.*".to_string()],
            EventPayloadMode::MetadataOnly,
        ))
        .await
        .expect("create group Subscription")
}

async fn append_group_event(harness: &support::Harness, event_id: &str) {
    harness
        .repo
        .append_event(AppendEventRecord {
            event: NewEvent {
                event_id: event_id.to_string(),
                event_type: "group.created".to_string(),
                schema_version: EVENT_SCHEMA_VERSION_V1.to_string(),
                producer: "subscription-test".to_string(),
                producer_key: event_id.to_string(),
                occurred_at: "2026-08-19T00:00:00.000Z".to_string(),
                subject: EventSubject {
                    subject_type: "group".to_string(),
                    id: "group-1".to_string(),
                },
                scope: EventScope {
                    group_id: Some("group-1".to_string()),
                    ..EventScope::default()
                },
                stream_key: "group:group-1".to_string(),
                actor: None,
                correlation_id: None,
                causation_event_id: None,
                trace_id: None,
                data: BTreeMap::new(),
            },
            recorded_at: "2026-08-19T00:00:00.001Z".to_string(),
            retention_until_ms: NOW_MS + 86_400_000,
            env: "test".to_string(),
        })
        .await
        .expect("append group Event");
}

async fn claim_targets(
    harness: &support::Harness,
) -> Vec<bcs_service_api::port::repo::EventFanoutTargetRecord> {
    harness
        .repo
        .claim_fanout_targets(ClaimFanoutTargets {
            worker_id: "fanout-test".to_string(),
            now_ms: NOW_MS,
            lease_until_ms: NOW_MS + 10_000,
            limit: 100,
            env: "test".to_string(),
        })
        .await
        .expect("claim fanout targets")
}

async fn materialize_first_target(
    harness: &support::Harness,
    subscription_id: &str,
    delivery_id: &str,
) {
    let target = claim_targets(harness)
        .await
        .into_iter()
        .next()
        .expect("fanout target");
    let payload = br#"{"event_id":"evt-pending"}"#.to_vec();
    let payload_sha256 = format!("{:x}", Sha256::digest(&payload));
    harness
        .repo
        .materialize_fanout_target(MaterializeFanoutTarget {
            target_id: target.target_id.clone(),
            expected_lease_owner: target.lease_owner.expect("claim fencing token"),
            delivery: EventDeliveryRecord {
                delivery_id: delivery_id.to_string(),
                fanout_target_id: target.target_id,
                event_id: target.event_id,
                event_type: "group.created".to_string(),
                subscription_id: subscription_id.to_string(),
                subscription_revision: target.subscription_revision,
                stream_key: "group:group-1".to_string(),
                sequence: 1,
                payload_bytes: payload,
                payload_sha256,
                status: EventDeliveryStatus::Pending,
                attempt_count: 0,
                first_attempt_at_ms: None,
                last_attempt_at_ms: None,
                next_attempt_at_ms: None,
                lease_owner: None,
                lease_until_ms: None,
                last_http_status: None,
                last_error_category: None,
                last_error_summary: None,
                dead_lettered_at_ms: None,
                cancelled_at_ms: None,
                skipped_at_ms: None,
                skip_actor: None,
                skip_reason: None,
                replay_of_delivery_id: None,
                resolved_by_delivery_id: None,
                resolved_at_ms: None,
                created_at_ms: NOW_MS,
                succeeded_at_ms: None,
                env: "test".to_string(),
            },
            materialized_at_ms: NOW_MS,
        })
        .await
        .expect("materialize pending Delivery");
}

async fn dead_letter_delivery(harness: &support::Harness, delivery_id: &str) {
    let delivery = harness
        .repo
        .claim_deliveries(ClaimEventDeliveries {
            worker_id: "delivery-test".to_string(),
            now_ms: NOW_MS,
            lease_until_ms: NOW_MS + 10_000,
            limit: 1,
            env: "test".to_string(),
        })
        .await
        .expect("claim Delivery")
        .into_iter()
        .next()
        .expect("pending Delivery");
    assert_eq!(delivery.delivery_id, delivery_id);
    harness
        .repo
        .complete_delivery_attempt(CompleteEventDeliveryAttempt {
            delivery_id: delivery_id.to_string(),
            expected_lease_owner: delivery.lease_owner.expect("claim fencing token"),
            attempt_no: 1,
            started_at_ms: NOW_MS,
            completed_at_ms: NOW_MS + 10,
            result: EventDeliveryAttemptRecordResult::Terminal,
            next_status: EventDeliveryStatus::DeadLettered,
            next_attempt_at_ms: None,
            http_status: Some(422),
            error_category: Some("http_status".to_string()),
            error_summary: Some("terminal response".to_string()),
            response_bytes_observed: 0,
        })
        .await
        .expect("dead-letter Delivery");
}

fn message_event(text: String) -> EventEnvelope {
    EventEnvelope {
        spec_version: EVENT_SPEC_VERSION.to_string(),
        event_id: "evt-message".to_string(),
        event_type: "message.created".to_string(),
        schema_version: EVENT_SCHEMA_VERSION_V1.to_string(),
        source: EVENT_SOURCE.to_string(),
        occurred_at: "2026-08-19T00:00:00.000Z".to_string(),
        recorded_at: "2026-08-19T00:00:00.001Z".to_string(),
        subject: EventSubject {
            subject_type: "message".to_string(),
            id: "message-1".to_string(),
        },
        scope: EventScope {
            group_id: Some("group-1".to_string()),
            session_id: Some("session-1".to_string()),
            ..EventScope::default()
        },
        stream: EventStream {
            key: "session:session-1".to_string(),
            sequence: 1,
        },
        actor: None,
        correlation_id: None,
        causation_event_id: None,
        trace_id: None,
        data: BTreeMap::from([
            ("logical_message_id".to_string(), json!("message-1")),
            ("message_type".to_string(), json!("chat")),
            ("sender".to_string(), json!({"type": "bot", "id": "bot-1"})),
            ("session_seq".to_string(), json!(1)),
            ("content".to_string(), Value::String(text)),
            ("attachments".to_string(), json!([{"file_id": "file-1"}])),
            ("token".to_string(), json!("forbidden")),
            (
                "nested".to_string(),
                json!({"thinking": "forbidden", "safe": true}),
            ),
        ]),
    }
}
