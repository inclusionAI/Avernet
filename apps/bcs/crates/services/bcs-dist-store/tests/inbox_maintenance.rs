#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]
mod common;
use bcs_dist_model::*;
use bcs_dist_store::{inbox::*, layout::KeyLayout};
use bcs_service_api::port::repo::distributed::{admission::*, common::*, inbox::*};
use common::*;
use std::sync::Arc;

fn layout() -> KeyLayout {
    KeyLayout::new("test", "maintenance").unwrap()
}

fn provider(store: Arc<Recorder>, config: InboxProducerConfig) -> StoreInboxProducer {
    StoreInboxProducer::new(
        Arc::new(
            bcs_dist_store::budget::SharedScanBudget::new(
                bcs_dist_store::budget::ScanBudgetConfig::default(),
            )
            .unwrap(),
        ),
        Arc::new(bcs_dist_projection::OutputProjectionService),
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        layout(),
        Arc::new(FixedClock(100)),
        config,
    )
    .unwrap()
}

fn config() -> InboxProducerConfig {
    InboxProducerConfig::new(BackendId("model".into()))
}

fn request(id: &str) -> AdmitRequest {
    AdmitRequest {
        scope: scope(),
        input: user_input(id, "hello"),
        create_session: Some(spec("g")),
        create_task: Some(task()),
    }
}

#[tokio::test]
async fn maintenance_hooks_complete_without_a_shard_wide_admission_journal() {
    let store = Arc::new(Recorder::default());
    let p = provider(store, config());
    p.admit(request("durable-admission")).await.unwrap();

    let repair = p.repair(ShardId(42)).await.unwrap();
    assert!(repair.round_complete);
    assert_eq!(repair.examined, 0);
    assert_eq!(repair.pending, 0);
    assert_eq!(repair.completed_through, 0);

    let discovery = p.discover(ShardId(42)).await.unwrap();
    assert!(discovery.round_complete);
    assert_eq!(discovery.examined, 0);
    assert_eq!(discovery.pending, 0);

    let observation = p.maintenance_observation(ShardId(42)).await.unwrap();
    assert!(observation.capacity.is_none());
    assert!(observation.repair_backlog.is_none());
    assert!(observation.discovery_confirmed_age.is_none());
}

#[tokio::test]
async fn takeover_enumeration_uses_the_durable_active_index() {
    let store = Arc::new(Recorder::default());
    let p = provider(store, config());
    p.admit(request("first")).await.unwrap();

    let mut second = request("second");
    let sid = Sid("s0042.later".into());
    let tid = Tid("s0042.later.task".into());
    second.input.sid = sid.clone();
    second.input.tid = tid.clone();
    second.create_session.as_mut().unwrap().sid = sid.clone();
    let task = second.create_task.as_mut().unwrap();
    task.sid = sid.clone();
    task.tid = tid;
    p.admit(second).await.unwrap();

    let first = p
        .enumerate_pending(
            ShardId(42),
            PageRequest {
                after: None,
                limit: std::num::NonZeroU32::new(1).unwrap(),
            },
        )
        .await
        .unwrap();
    assert_eq!(first.items, vec![Sid(SID.into())]);
    let second_page = p
        .enumerate_pending(
            ShardId(42),
            PageRequest {
                after: first.next,
                limit: std::num::NonZeroU32::new(1).unwrap(),
            },
        )
        .await
        .unwrap();
    assert_eq!(second_page.items, vec![sid]);
    let end = p
        .enumerate_pending(
            ShardId(42),
            PageRequest {
                after: second_page.next,
                limit: std::num::NonZeroU32::new(1).unwrap(),
            },
        )
        .await
        .unwrap();
    assert!(end.items.is_empty());
    assert!(end.next.is_none());
}

#[tokio::test]
async fn valid_single_io_profile_can_advance_maintenance() {
    let store = Arc::new(Recorder::default());
    let budget = Arc::new(
        bcs_dist_store::budget::SharedScanBudget::new(
            bcs_dist_store::budget::ScanBudgetConfig {
                pool_size: 4,
                io_slots: 1,
                ..Default::default()
            },
        )
        .unwrap(),
    );
    let mut config = config();
    config.connection_pool_size = 4;
    let p = StoreInboxProducer::new(
        budget,
        Arc::new(bcs_dist_projection::OutputProjectionService),
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        layout(),
        Arc::new(FixedClock(100)),
        config,
    )
    .unwrap();
    assert!(p.repair(ShardId(42)).await.unwrap().round_complete);
    assert!(p.discover(ShardId(42)).await.unwrap().round_complete);
}
