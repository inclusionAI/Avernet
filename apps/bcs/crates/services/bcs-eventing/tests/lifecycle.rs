#![allow(clippy::expect_used, clippy::unwrap_used)]

mod support;

use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::Duration;

use bcs_eventing::{
    EventCatalog, EventDispatcher, EventFanoutWorker, EventRetentionWorker, EventRetryPolicy,
    EventingLifecycle,
};
use bcs_service_api::application::v1::EventSubscriptionService;
use bcs_service_api::lifecycle::ServiceLifecycle;
use bcs_service_api::port::NewEvent;
use bcs_service_api::port::repo::{AppendEventRecord, EventRepoPort, ListEventDeliveryRecords};
use bcs_service_api::types::{
    EVENT_SCHEMA_VERSION_V1, EventDeliveryStatus, EventPayloadMode, EventScope, EventSubject,
};

use support::{CaptureMetrics, NOW_MS, create_command, harness};

#[tokio::test]
async fn dispatcher_disabled_preserves_backlog_and_restart_resumes_delivery() {
    let harness = harness(true);
    let created = harness
        .service
        .create(create_command(
            vec!["group.*".to_string()],
            EventPayloadMode::MetadataOnly,
        ))
        .await
        .expect("Subscription");
    harness
        .repo
        .append_event(AppendEventRecord {
            event: NewEvent {
                event_id: "evt-backlog".to_string(),
                event_type: "group.created".to_string(),
                schema_version: EVENT_SCHEMA_VERSION_V1.to_string(),
                producer: "lifecycle-test".to_string(),
                producer_key: "evt-backlog".to_string(),
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
            retention_until_ms: u64::MAX - 1,
            env: "test".to_string(),
        })
        .await
        .expect("append Event");
    let fanout = Arc::new(
        EventFanoutWorker::new(
            harness.repo.clone(),
            Arc::new(CaptureMetrics::default()),
            Arc::new(EventCatalog::load_embedded().expect("catalog")),
            "test",
            1_000,
            10,
            262_144,
        )
        .with_runtime(Arc::new(|| NOW_MS), Arc::new(|| "del-backlog".to_string())),
    );
    let lifecycle = EventingLifecycle::new(
        fanout,
        None,
        Arc::new(EventRetentionWorker::new(
            harness.repo.clone(),
            "test",
            100,
            100,
        )),
        Duration::from_millis(5),
        Duration::from_millis(5),
        Duration::from_secs(60),
        Duration::from_secs(1),
    )
    .expect("lifecycle");
    lifecycle.initialize().await.expect("initialize");
    tokio::time::sleep(Duration::from_millis(20)).await;
    lifecycle.shutdown().await.expect("shutdown");

    let deliveries = harness
        .repo
        .list_deliveries(ListEventDeliveryRecords {
            subscription_id: Some(created.subscription_id.clone()),
            event_id: None,
            status: None,
            after_delivery_id: None,
            limit: 10,
            env: "test".to_string(),
        })
        .await
        .expect("list backlog");
    assert_eq!(deliveries.len(), 1);
    assert_eq!(deliveries[0].status, EventDeliveryStatus::Pending);
    assert!(
        harness
            .delivery
            .requests
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .is_empty()
    );

    let restarted = EventingLifecycle::new(
        Arc::new(
            EventFanoutWorker::new(
                harness.repo.clone(),
                Arc::new(CaptureMetrics::default()),
                Arc::new(EventCatalog::load_embedded().expect("catalog")),
                "test",
                1_000,
                10,
                262_144,
            )
            .with_runtime(
                Arc::new(|| NOW_MS + 1),
                Arc::new(|| "unused-delivery".to_string()),
            ),
        ),
        Some(Arc::new(
            EventDispatcher::new(
                harness.repo.clone(),
                harness.delivery.clone(),
                Arc::new(CaptureMetrics::default()),
                EventRetryPolicy::default(),
                "test",
                1_000,
                10,
                1,
                1,
            )
            .with_runtime(Arc::new(|| NOW_MS + 1), Arc::new(|| 0)),
        )),
        Arc::new(EventRetentionWorker::new(
            harness.repo.clone(),
            "test",
            100,
            100,
        )),
        Duration::from_millis(5),
        Duration::from_millis(5),
        Duration::from_secs(60),
        Duration::from_secs(1),
    )
    .expect("restarted lifecycle");
    restarted.initialize().await.expect("restart initialize");
    tokio::time::sleep(Duration::from_millis(20)).await;
    restarted.shutdown().await.expect("restart shutdown");

    let deliveries = harness
        .repo
        .list_deliveries(ListEventDeliveryRecords {
            subscription_id: Some(created.subscription_id),
            event_id: None,
            status: None,
            after_delivery_id: None,
            limit: 10,
            env: "test".to_string(),
        })
        .await
        .expect("list delivered backlog");
    assert_eq!(deliveries[0].status, EventDeliveryStatus::Succeeded);
    assert_eq!(
        harness
            .delivery
            .requests
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .len(),
        1
    );
}
