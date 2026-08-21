#![allow(clippy::expect_used, clippy::unwrap_used)]

mod support;

use std::collections::BTreeMap;
use std::sync::{
    Arc,
    atomic::{AtomicU64, AtomicUsize, Ordering},
};

use async_trait::async_trait;
use bcs_eventing::{EventCatalog, EventDispatcher, EventFanoutWorker, EventRetryPolicy};
use bcs_service_api::application::v1::EventSubscriptionService;
use bcs_service_api::port::repo::{
    AppendEventRecord, ClaimEventDeliveries, EventRepoPort, ListEventDeliveryRecords,
};
use bcs_service_api::port::{
    EventDeliveryDisposition, EventDeliveryError, EventDeliveryPort, EventDeliveryRequest,
    EventDeliveryResponse, NewEvent,
};
use bcs_service_api::types::{
    EVENT_SCHEMA_VERSION_V1, EventDeliveryStatus, EventPayloadMode, EventScope, EventSubject,
    EventSubscriptionStatus,
};

use support::{CaptureMetrics, NOW_MS, create_command, harness};
use tokio::sync::Notify;

#[tokio::test]
async fn fanout_projects_the_fixed_revision_and_dispatcher_completes_delivery() {
    let harness = harness(true);
    let created = create_subscription_and_event(&harness, "evt-success").await;
    let metrics = Arc::new(CaptureMetrics::default());
    let fanout = EventFanoutWorker::new(
        harness.repo.clone(),
        metrics.clone(),
        Arc::new(EventCatalog::load_embedded().expect("catalog")),
        "test",
        10_000,
        10,
        262_144,
    )
    .with_runtime(Arc::new(|| NOW_MS), Arc::new(|| "del-success".to_string()));
    assert_eq!(fanout.run_once("fanout-1").await.expect("fanout"), 1);

    let dispatcher = dispatcher(&harness, metrics.clone(), EventRetryPolicy::default());
    assert_eq!(
        dispatcher.run_once("dispatcher-1").await.expect("dispatch"),
        1
    );
    let (delivery, attempts) = harness
        .repo
        .get_delivery("del-success", "test")
        .await
        .expect("read Delivery")
        .expect("stored Delivery");
    assert_eq!(delivery.status, EventDeliveryStatus::Succeeded);
    assert_eq!(delivery.subscription_revision, created.revision);
    assert_eq!(attempts.len(), 1);
    assert_eq!(
        harness
            .delivery
            .requests
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())[0]
            .body,
        delivery.payload_bytes
    );
    assert_eq!(
        metrics
            .attempts
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .len(),
        1
    );
}

#[tokio::test]
async fn retry_wait_uses_injected_full_jitter_and_reuses_identical_payload() {
    let harness = harness(true);
    create_subscription_and_event(&harness, "evt-retry").await;
    let metrics = Arc::new(CaptureMetrics::default());
    fanout(&harness, metrics.clone(), "del-retry").await;
    *harness
        .delivery
        .response
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner()) = EventDeliveryResponse {
        disposition: EventDeliveryDisposition::Retryable,
        http_status: Some(503),
        retry_after_ms: None,
        response_bytes_observed: 0,
        error_category: Some("http_status".to_string()),
        error_summary: Some("Webhook endpoint returned HTTP 503".to_string()),
    };
    let clock = Arc::new(AtomicU64::new(NOW_MS));
    let dispatcher = EventDispatcher::new(
        harness.repo.clone(),
        harness.delivery.clone(),
        metrics,
        EventRetryPolicy {
            base_delay_ms: 100,
            max_delay_ms: 1_000,
            max_attempts: 3,
            max_elapsed_ms: 10_000,
        },
        "test",
        10_000,
        10,
        4,
        2,
    )
    .with_runtime(
        {
            let clock = clock.clone();
            Arc::new(move || clock.load(Ordering::SeqCst))
        },
        Arc::new(|| 0),
    );
    dispatcher
        .run_once("dispatcher-retry")
        .await
        .expect("first attempt");
    let (retrying, _) = harness
        .repo
        .get_delivery("del-retry", "test")
        .await
        .expect("read retry")
        .expect("retry Delivery");
    assert_eq!(retrying.status, EventDeliveryStatus::RetryWait);
    assert_eq!(retrying.next_attempt_at_ms, Some(NOW_MS + 1));

    *harness
        .delivery
        .response
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner()) = EventDeliveryResponse {
        disposition: EventDeliveryDisposition::Succeeded,
        http_status: Some(204),
        retry_after_ms: None,
        response_bytes_observed: 0,
        error_category: None,
        error_summary: None,
    };
    clock.store(NOW_MS + 1, Ordering::SeqCst);
    dispatcher
        .run_once("dispatcher-retry")
        .await
        .expect("retry attempt");
    let (succeeded, attempts) = harness
        .repo
        .get_delivery("del-retry", "test")
        .await
        .expect("read success")
        .expect("Delivery");
    assert_eq!(succeeded.status, EventDeliveryStatus::Succeeded);
    assert_eq!(attempts.len(), 2);
    let requests = harness
        .delivery
        .requests
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    assert_eq!(requests.len(), 2);
    assert_eq!(requests[0].body, requests[1].body);
}

#[tokio::test]
async fn gone_response_disables_subscription_before_terminal_completion() {
    let harness = harness(true);
    let created = create_subscription_and_event(&harness, "evt-gone").await;
    let metrics = Arc::new(CaptureMetrics::default());
    fanout(&harness, metrics.clone(), "del-gone").await;
    *harness
        .delivery
        .response
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner()) = EventDeliveryResponse {
        disposition: EventDeliveryDisposition::DisableSubscription,
        http_status: Some(410),
        retry_after_ms: None,
        response_bytes_observed: 0,
        error_category: Some("http_status".to_string()),
        error_summary: Some("Webhook endpoint returned HTTP 410".to_string()),
    };
    dispatcher(&harness, metrics, EventRetryPolicy::default())
        .run_once("dispatcher-gone")
        .await
        .expect("410 dispatch");
    let (subscription, _) = harness
        .repo
        .get_subscription(&created.subscription_id, "test")
        .await
        .expect("read Subscription")
        .expect("Subscription");
    assert_eq!(subscription.status, EventSubscriptionStatus::Disabled);
    assert_eq!(subscription.current_revision, created.revision + 1);
    let (delivery, _) = harness
        .repo
        .get_delivery("del-gone", "test")
        .await
        .expect("read Delivery")
        .expect("Delivery");
    assert_eq!(delivery.status, EventDeliveryStatus::DeadLettered);
}

#[tokio::test]
async fn deterministic_projection_failure_becomes_an_auditable_dead_letter() {
    let harness = harness(true);
    create_subscription_and_event(&harness, "evt-projection-failure").await;
    let metrics = Arc::new(CaptureMetrics::default());
    let fanout = EventFanoutWorker::new(
        harness.repo.clone(),
        metrics.clone(),
        Arc::new(EventCatalog::load_embedded().expect("catalog")),
        "test",
        10_000,
        10,
        1,
    )
    .with_runtime(
        Arc::new(|| NOW_MS),
        Arc::new(|| "del-projection-failure".to_string()),
    );
    assert_eq!(fanout.run_once("fanout").await.expect("fanout"), 1);
    let (delivery, attempts) = harness
        .repo
        .get_delivery("del-projection-failure", "test")
        .await
        .expect("read projection dead letter")
        .expect("projection dead letter");
    assert_eq!(delivery.status, EventDeliveryStatus::DeadLettered);
    assert_eq!(delivery.attempt_count, 0);
    assert!(attempts.is_empty());
    assert_eq!(delivery.last_error_category.as_deref(), Some("projection"));
    assert_eq!(
        metrics
            .fanout_failures
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .len(),
        1
    );
}

#[tokio::test]
async fn per_host_concurrency_is_enforced_inside_global_dispatch_concurrency() {
    let harness = harness(true);
    harness
        .service
        .create(create_command(
            vec!["group.*".to_string()],
            EventPayloadMode::MetadataOnly,
        ))
        .await
        .expect("first Subscription");
    create_subscription_and_event(&harness, "evt-host-limit").await;
    let metrics = Arc::new(CaptureMetrics::default());
    let id = Arc::new(AtomicUsize::new(0));
    let fanout = EventFanoutWorker::new(
        harness.repo.clone(),
        metrics.clone(),
        Arc::new(EventCatalog::load_embedded().expect("catalog")),
        "test",
        10_000,
        10,
        262_144,
    )
    .with_runtime(Arc::new(|| NOW_MS), {
        let id = id.clone();
        Arc::new(move || format!("del-host-{}", id.fetch_add(1, Ordering::SeqCst)))
    });
    assert_eq!(fanout.run_once("fanout-host").await.expect("fanout"), 2);

    let tracking = Arc::new(TrackingDelivery::default());
    let dispatcher = EventDispatcher::new(
        harness.repo.clone(),
        tracking.clone(),
        metrics,
        EventRetryPolicy::default(),
        "test",
        10_000,
        10,
        4,
        1,
    )
    .with_runtime(Arc::new(|| NOW_MS), Arc::new(|| 1));
    assert_eq!(
        dispatcher
            .run_once("dispatcher-host")
            .await
            .expect("dispatch"),
        2
    );
    assert_eq!(tracking.calls.load(Ordering::SeqCst), 2);
    assert_eq!(tracking.maximum.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn dispatcher_renews_leases_while_delivery_waits_for_a_host_slot() {
    let harness = harness(true);
    harness
        .service
        .create(create_command(
            vec!["group.*".to_string()],
            EventPayloadMode::MetadataOnly,
        ))
        .await
        .expect("first Subscription");
    create_subscription_and_event(&harness, "evt-lease-heartbeat").await;
    let metrics = Arc::new(CaptureMetrics::default());
    let id = Arc::new(AtomicUsize::new(0));
    let fanout = EventFanoutWorker::new(
        harness.repo.clone(),
        metrics.clone(),
        Arc::new(EventCatalog::load_embedded().expect("catalog")),
        "test",
        10_000,
        10,
        262_144,
    )
    .with_runtime(Arc::new(|| NOW_MS), {
        let id = id.clone();
        Arc::new(move || format!("del-heartbeat-{}", id.fetch_add(1, Ordering::SeqCst)))
    });
    assert_eq!(
        fanout.run_once("fanout-heartbeat").await.expect("fanout"),
        2
    );

    let clock = Arc::new(AtomicU64::new(NOW_MS));
    let delivery = Arc::new(OneShotBlockingDelivery::default());
    let dispatcher = Arc::new(
        EventDispatcher::new(
            harness.repo.clone(),
            delivery.clone(),
            metrics,
            EventRetryPolicy::default(),
            "test",
            1_000,
            10,
            2,
            1,
        )
        .with_runtime(
            {
                let clock = clock.clone();
                Arc::new(move || clock.load(Ordering::SeqCst))
            },
            Arc::new(|| 1),
        ),
    );
    let running = {
        let dispatcher = dispatcher.clone();
        tokio::spawn(async move { dispatcher.run_once("dispatcher-heartbeat").await })
    };
    delivery.entered.notified().await;

    // Move logical time close to the original lease boundary and wait until
    // both the active request and the same-host waiter have heartbeated.
    clock.store(NOW_MS + 800, Ordering::SeqCst);
    tokio::time::timeout(std::time::Duration::from_secs(2), async {
        loop {
            let records = delivery_records(&harness).await;
            if records.len() == 2
                && records.iter().all(|record| {
                    record
                        .lease_until_ms
                        .is_some_and(|lease_until_ms| lease_until_ms > NOW_MS + 1_000)
                })
            {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(20)).await;
        }
    })
    .await
    .expect("lease heartbeat");

    clock.store(NOW_MS + 1_100, Ordering::SeqCst);
    let reclaimed = harness
        .repo
        .claim_deliveries(ClaimEventDeliveries {
            worker_id: "dispatcher-rival".to_string(),
            now_ms: NOW_MS + 1_100,
            lease_until_ms: NOW_MS + 2_100,
            limit: 10,
            env: "test".to_string(),
        })
        .await
        .expect("rival claim");
    assert!(reclaimed.is_empty());

    delivery.release.notify_one();
    assert_eq!(
        running.await.expect("dispatcher task").expect("dispatch"),
        2
    );
}

#[test]
fn retry_policy_caps_backoff_attempts_and_elapsed_window() {
    let policy = EventRetryPolicy {
        base_delay_ms: 100,
        max_delay_ms: 500,
        max_attempts: 3,
        max_elapsed_ms: 1_000,
    };
    assert_eq!(policy.retry_at_ms(1, 1_000, 1_000, None, 0), Some(1_001));
    assert_eq!(policy.retry_at_ms(2, 1_000, 1_100, None, 199), Some(1_299));
    assert_eq!(policy.retry_at_ms(3, 1_000, 1_100, None, 1), None);
    assert_eq!(policy.retry_at_ms(1, 1_000, 2_000, None, 1), None);
    assert_eq!(
        policy.retry_at_ms(1, 1_000, 1_000, Some(800), 1),
        Some(1_800)
    );
}

#[derive(Default)]
struct TrackingDelivery {
    current: AtomicUsize,
    maximum: AtomicUsize,
    calls: AtomicUsize,
}

#[derive(Default)]
struct OneShotBlockingDelivery {
    calls: AtomicUsize,
    entered: Notify,
    release: Notify,
}

#[async_trait]
impl EventDeliveryPort for OneShotBlockingDelivery {
    async fn deliver(
        &self,
        _request: EventDeliveryRequest,
    ) -> Result<EventDeliveryResponse, EventDeliveryError> {
        if self.calls.fetch_add(1, Ordering::SeqCst) == 0 {
            self.entered.notify_one();
            self.release.notified().await;
        }
        Ok(EventDeliveryResponse {
            disposition: EventDeliveryDisposition::Succeeded,
            http_status: Some(204),
            retry_after_ms: None,
            response_bytes_observed: 0,
            error_category: None,
            error_summary: None,
        })
    }
}

#[async_trait]
impl EventDeliveryPort for TrackingDelivery {
    async fn deliver(
        &self,
        _request: EventDeliveryRequest,
    ) -> Result<EventDeliveryResponse, EventDeliveryError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        let current = self.current.fetch_add(1, Ordering::SeqCst) + 1;
        self.maximum.fetch_max(current, Ordering::SeqCst);
        tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        self.current.fetch_sub(1, Ordering::SeqCst);
        Ok(EventDeliveryResponse {
            disposition: EventDeliveryDisposition::Succeeded,
            http_status: Some(204),
            retry_after_ms: None,
            response_bytes_observed: 0,
            error_category: None,
            error_summary: None,
        })
    }
}

fn dispatcher(
    harness: &support::Harness,
    metrics: Arc<CaptureMetrics>,
    retry: EventRetryPolicy,
) -> EventDispatcher {
    EventDispatcher::new(
        harness.repo.clone(),
        harness.delivery.clone(),
        metrics,
        retry,
        "test",
        10_000,
        10,
        4,
        2,
    )
    .with_runtime(Arc::new(|| NOW_MS), Arc::new(|| 1))
}

async fn fanout(
    harness: &support::Harness,
    metrics: Arc<CaptureMetrics>,
    delivery_id: &'static str,
) {
    let fanout = EventFanoutWorker::new(
        harness.repo.clone(),
        metrics,
        Arc::new(EventCatalog::load_embedded().expect("catalog")),
        "test",
        10_000,
        10,
        262_144,
    )
    .with_runtime(
        Arc::new(|| NOW_MS),
        Arc::new(move || delivery_id.to_string()),
    );
    assert_eq!(fanout.run_once("fanout").await.expect("fanout"), 1);
}

async fn create_subscription_and_event(
    harness: &support::Harness,
    event_id: &str,
) -> bcs_service_api::application::v1::EventSubscription {
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
                event_id: event_id.to_string(),
                event_type: "group.created".to_string(),
                schema_version: EVENT_SCHEMA_VERSION_V1.to_string(),
                producer: "dispatcher-test".to_string(),
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
        .expect("append Event");
    created
}

#[allow(dead_code)]
async fn delivery_records(
    harness: &support::Harness,
) -> Vec<bcs_service_api::port::repo::EventDeliveryRecord> {
    harness
        .repo
        .list_deliveries(ListEventDeliveryRecords {
            subscription_id: None,
            event_id: None,
            status: None,
            after_delivery_id: None,
            limit: 100,
            env: "test".to_string(),
        })
        .await
        .expect("list Deliveries")
}
