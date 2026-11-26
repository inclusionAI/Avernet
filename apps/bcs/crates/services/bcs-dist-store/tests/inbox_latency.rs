#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]
mod common;
mod latency_support;
use bcs_dist_model::*;
use bcs_dist_store::{inbox::*, layout::KeyLayout};
use bcs_service_api::port::repo::distributed::{admission::*, common::*, inbox::*};
use common::*;
use latency_support::DelayedDocuments;
use std::{
    num::NonZeroU32,
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
    time::Duration,
};
fn layout() -> KeyLayout {
    KeyLayout::new("test", "latency").unwrap()
}
fn request(id: &str) -> AdmitRequest {
    AdmitRequest {
        scope: scope(),
        input: user_input(id, "hello"),
        create_session: Some(spec("g")),
        create_task: Some(task()),
    }
}
fn provider(
    store: Arc<Recorder>,
    delay: Arc<DelayedDocuments>,
    cfg: InboxProducerConfig,
) -> StoreInboxProducer {
    StoreInboxProducer::new(
        Arc::new(
            bcs_dist_store::budget::SharedScanBudget::new(
                bcs_dist_store::budget::ScanBudgetConfig::default(),
            )
            .unwrap(),
        ),
        Arc::new(bcs_dist_projection::OutputProjectionService),
        store.clone(),
        delay,
        store.clone(),
        store,
        layout(),
        Arc::new(FixedClock(100)),
        cfg,
    )
    .unwrap()
}
fn setup(cfg: InboxProducerConfig) -> (Arc<Recorder>, Arc<DelayedDocuments>, StoreInboxProducer) {
    let store = Arc::new(Recorder::default());
    let delay = Arc::new(DelayedDocuments {
        store: store.clone(),
        millis: AtomicU64::new(0),
        slow_key: std::sync::Mutex::new(None),
    });
    let p = provider(store.clone(), delay.clone(), cfg);
    (store, delay, p)
}
#[tokio::test]
async fn repair_is_complete_when_admission_was_never_created() {
    let (store, _, p) = setup(InboxProducerConfig::new(BackendId("model".into())));
    store.fail_write(1);
    assert!(p.admit(request("retry-after-create-failure")).await.is_err());

    let maintenance = p.repair(ShardId(42)).await.unwrap();
    assert!(maintenance.round_complete);
    assert!(p
        .admission_decision(&scope(), &InputId("retry-after-create-failure".into()))
        .await
        .unwrap()
        .is_none());

    let retry = p.admit(request("retry-after-create-failure")).await;
    assert!(retry.is_ok(), "retry failed: {retry:?}");
    assert!(matches!(retry.unwrap(), AdmitOutcome::Accepted(_)));
}

#[tokio::test]
async fn active_index_enumeration_discovers_admitted_sessions() {
    let (_, _, p) = setup(InboxProducerConfig::new(BackendId("model".into())));
    p.admit(request("active-index")).await.unwrap();

    let page = p
        .enumerate_pending(
            ShardId(42),
            PageRequest {
                after: None,
                limit: NonZeroU32::new(64).unwrap(),
            },
        )
        .await
        .unwrap();
    assert_eq!(page.items, vec![Sid(SID.into())]);
    assert!(page.next.is_none());
}

#[tokio::test]
async fn reader_returns_classified_prefix_when_later_row_exhausts_time_budget() {
    let (_, delay, p) = setup(InboxProducerConfig::new(BackendId("model".into())));
    for i in 0..16 {
        p.admit(request(&format!("row-{i}"))).await.unwrap();
    }
    let mut feed = p.open(ShardId(42)).await.unwrap();
    feed.watch(Sid(SID.into()), None).await.unwrap();
    delay.millis.store(3, Ordering::Relaxed);
    let batch = feed
        .next(
            ReadLimits {
                max_sessions: NonZeroU32::MIN,
                max_entries: NonZeroU32::new(128).unwrap(),
                max_bytes: NonZeroU32::new(1024 * 1024).unwrap(),
            },
            Duration::ZERO,
        )
        .await
        .unwrap();
    assert!(batch.failures.is_empty());
    assert_eq!(batch.sessions.len(), 1);
    assert!(!batch.sessions[0].entries.is_empty());
}

#[tokio::test]
async fn reader_accepts_normal_eighty_millisecond_authoritative_reads() {
    let (_, delay, p) = setup(InboxProducerConfig::new(BackendId("model".into())));
    p.admit(request("reader-realistic-rtt")).await.unwrap();
    let mut feed = p.open(ShardId(42)).await.unwrap();
    feed.watch(Sid(SID.into()), None).await.unwrap();
    delay.millis.store(80, Ordering::Relaxed);
    let batch = feed
        .next(
            ReadLimits {
                max_sessions: NonZeroU32::MIN,
                max_entries: NonZeroU32::MIN,
                max_bytes: NonZeroU32::new(128 * 1024).unwrap(),
            },
            Duration::ZERO,
        )
        .await
        .unwrap();
    assert!(batch.failures.is_empty(), "{:?}", batch.failures);
    assert_eq!(batch.sessions.len(), 1);
    assert_eq!(batch.sessions[0].entries.len(), 1);
}

#[tokio::test]
async fn cancelling_a_read_keeps_its_watch_and_undelivered_input() {
    let (_, delay, p) = setup(InboxProducerConfig::new(BackendId("model".into())));
    p.admit(request("cancel-reader")).await.unwrap();
    let mut feed = p.open(ShardId(42)).await.unwrap();
    feed.watch(Sid(SID.into()), None).await.unwrap();
    delay.millis.store(80, Ordering::Relaxed);
    let limits = ReadLimits {
        max_sessions: NonZeroU32::MIN,
        max_entries: NonZeroU32::MIN,
        max_bytes: NonZeroU32::new(128 * 1024).unwrap(),
    };
    assert!(
        tokio::time::timeout(
            Duration::from_millis(20),
            feed.next(limits.clone(), Duration::ZERO)
        )
        .await
        .is_err()
    );
    delay.millis.store(0, Ordering::Relaxed);
    let batch = feed.next(limits, Duration::ZERO).await.unwrap();
    assert!(batch.failures.is_empty());
    assert_eq!(batch.sessions.len(), 1);
    assert_eq!(
        batch.sessions[0].entries[0].input_id,
        InputId("cancel-reader".into())
    );
}
