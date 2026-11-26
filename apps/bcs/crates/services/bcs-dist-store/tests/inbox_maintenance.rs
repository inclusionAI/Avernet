#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]
mod common;
use bcs_dist_model::*;
use bcs_dist_store::{budget::SharedScanBudget, inbox::*, layout::KeyLayout};
use bcs_service_api::port::repo::distributed::{admission::*, budget::*, common::*, inbox::*};
use common::*;
use bcs_store_api::{
    document::DocumentStorePlugin,
    zset::{ZAddMode, ZSetEntry, ZSetStorePlugin},
};
use std::{num::NonZeroU32, sync::Arc, time::Duration};

fn layout() -> KeyLayout {
    KeyLayout::new("test", "maintenance").unwrap()
}

fn provider(store: Arc<Recorder>, config: InboxProducerConfig) -> StoreInboxProducer {
    provider_with_budget(store, config, Arc::new(SharedScanBudget::new(Default::default()).unwrap()))
}

fn provider_with_budget(
    store: Arc<Recorder>,
    config: InboxProducerConfig,
    budget: Arc<SharedScanBudget>,
) -> StoreInboxProducer {
    StoreInboxProducer::new(
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
                limit: NonZeroU32::new(1).unwrap(),
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
                limit: NonZeroU32::new(1).unwrap(),
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
                limit: NonZeroU32::new(1).unwrap(),
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
        SharedScanBudget::new(
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

#[tokio::test(start_paused = true)]
async fn takeover_scan_shrinks_to_budget_and_resumes_at_its_cursor() {
    let store = Arc::new(Recorder::default());
    let budget = Arc::new(SharedScanBudget::new(Default::default()).unwrap());
    let p = provider_with_budget(store.clone(), config(), budget.clone());
    let shard = ShardId(42);
    let sids = ["s0042.a", "s0042.b", "s0042.c"];
    store
        .add(
            &layout().active(shard),
            &sids.map(|sid| ZSetEntry { score: 1.0, member: sid.into() }),
            ZAddMode::Upsert,
        )
        .await
        .unwrap();
    let slots: Vec<_> = (0..budget.profile().io_slots)
        .map(|_| budget.try_io().unwrap())
        .collect();
    assert!(budget.claim(ScanClass::Discovery, 62, 0).unwrap());
    let request = |after| PageRequest { after, limit: NonZeroU32::new(128).unwrap() };
    assert!(matches!(
        p.enumerate_pending(shard, request(None)).await,
        Err(StoreError::NotApplied { code }) if code == PENDING_SCAN_DEFERRED
    ));
    drop(slots);
    let first = p.enumerate_pending(shard, request(None)).await.unwrap();
    assert_eq!(first.items, vec![Sid(sids[0].into()), Sid(sids[1].into())]);
    let cursor = first.next.expect("a full budget-limited page retains its cursor");
    assert!(matches!(
        p.enumerate_pending(shard, request(Some(cursor.clone()))).await,
        Err(StoreError::NotApplied { code }) if code == PENDING_SCAN_DEFERRED
    ));
    assert!(budget.claim(ScanClass::Discovery, 0, 64).unwrap());
    tokio::time::advance(Duration::from_millis(20)).await;
    let second = p.enumerate_pending(shard, request(Some(cursor))).await.unwrap();
    assert_eq!(second.items, vec![Sid(sids[2].into())]);
    assert!(second.next.is_none());
}

fn start(id: &str) -> AdmitRequest {
    let mut r = request(id);
    let tid = Tid(format!("{SID}.{id}"));
    r.input.tid = tid.clone();
    r.create_task.as_mut().unwrap().tid = tid;
    r
}

async fn abandon_at(p: &StoreInboxProducer, r: AdmitRequest, pause: Arc<Pause>) {
    {
        let admission = p.admit(r);
        tokio::pin!(admission);
        tokio::select! {
            _ = pause.reached() => (),
            result = &mut admission => panic!("admission finished before crash: {result:?}"),
        }
    }
    pause.resume();
}

#[tokio::test]
async fn abandoned_append_does_not_block_later_accepted_input() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone(), config());
    let sid = Sid(SID.into());
    assert!(matches!(
        p.admit(start("a")).await.unwrap(),
        AdmitOutcome::Accepted(_)
    ));
    let pause = store.pause_after("stream.append", &layout().inbox(&sid).unwrap(), 1);
    abandon_at(&p, start("b"), pause).await;
    assert!(matches!(
        p.admit(start("c")).await.unwrap(),
        AdmitOutcome::Accepted(_)
    ));
    let worker = provider(store, config());
    let mut feed = worker.open(ShardId(42)).await.unwrap();
    feed.watch(sid, None).await.unwrap();
    let mut ids = Vec::new();
    for _ in 0..12 {
        let batch = feed
            .next(
                ReadLimits {
                    max_sessions: NonZeroU32::MIN,
                    max_entries: NonZeroU32::new(16).unwrap(),
                    max_bytes: NonZeroU32::new(1024 * 1024).unwrap(),
                },
                Duration::ZERO,
            )
            .await
            .unwrap();
        assert!(batch.failures.is_empty(), "{:?}", batch.failures);
        ids.extend(
            batch
                .sessions
                .into_iter()
                .flat_map(|s| s.entries.into_iter().map(|e| e.input_id.0)),
        );
    }
    ids.sort();
    assert_eq!(ids, ["a", "b", "c"]);
}

#[tokio::test]
async fn abandoned_registrations_do_not_block_next_start() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone(), config());
    p.admit(start("seed")).await.unwrap();
    let sk = layout().schedule(&Sid(SID.into())).unwrap();
    for i in 0..56 {
        // The slot CAS succeeded, but the admission still says Created.
        let pause = store.pause_after("document.replace", &sk, 1);
        abandon_at(&p, start(&format!("lost{i}")), pause).await;
    }
    store.clear();
    let next = provider(store.clone(), config());
    assert!(matches!(
        next.admit(start("next")).await,
        Ok(AdmitOutcome::Accepted(_))
    ));
    assert!(
        !store
            .calls()
            .iter()
            .any(|call| call.starts_with("zset.range"))
    );
}

async fn full_schedule_with_conflicting_start() -> (Arc<Recorder>, AdmitRequest) {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone(), config());
    let mut pending: Vec<_> = (0..56).map(|i| start(&format!("lost{i}"))).collect();
    // A fresh helper starts with the first persisted key, which is hashed.
    pending.sort_by_key(|r| layout().admission(&r.scope, &r.input.input_id).unwrap());
    let mut loser = pending.remove(0);
    loser.create_session.as_mut().unwrap().target = SessionTarget::Group { group: GroupId("other-group".into()) };
    let sk = layout().schedule(&loser.input.sid).unwrap();
    let pause = store.pause_after("document.create", &sk, 1);
    abandon_at(&p, loser.clone(), pause).await;
    assert!(matches!(p.admit(start("winner")).await.unwrap(), AdmitOutcome::Accepted(_)));
    for r in pending {
        let pause = store.pause_after("document.replace", &sk, 1);
        abandon_at(&p, r, pause).await;
    }
    (store, loser)
}

#[tokio::test]
async fn conflicting_start_releases_slot_under_pressure() {
    let (store, loser) = full_schedule_with_conflicting_start().await;
    let p = provider(store.clone(), config());
    let outcome = p.admit(start("next")).await;
    assert!(matches!(outcome, Ok(AdmitOutcome::Accepted(_))), "{outcome:?}");
    assert_eq!(p.admission_decision(&loser.scope, &loser.input.input_id).await.unwrap(),
        Some(AdmitOutcome::ContentConflict));
    assert_eq!(p.admit(loser.clone()).await.unwrap(), AdmitOutcome::ContentConflict);

    let sk = layout().schedule(&loser.input.sid).unwrap();
    let key = layout().admission(&loser.scope, &loser.input.input_id).unwrap();
    let schedule = store.read_document(&sk).await.unwrap().unwrap().value;
    assert!(schedule["slots"].get(&key).is_none());
    assert_eq!(schedule["activation"], 2);
    let mut feed = p.open(ShardId(42)).await.unwrap();
    feed.watch(loser.input.sid, None).await.unwrap();
    let batch = feed.next(
        ReadLimits {
            max_sessions: NonZeroU32::MIN,
            max_entries: NonZeroU32::new(16).unwrap(),
            max_bytes: NonZeroU32::new(1024 * 1024).unwrap(),
        },
        Duration::ZERO,
    ).await.unwrap();
    assert!(batch.failures.is_empty(), "{:?}", batch.failures);
    let ids: Vec<_> = batch.sessions.into_iter()
        .flat_map(|s| s.entries.into_iter().map(|e| e.input_id.0)).collect();
    assert_eq!(ids, ["winner", "next"]);
}

#[tokio::test]
async fn conflicting_start_replays_a_decision_written_only_to_the_slot() {
    let (store, loser) = full_schedule_with_conflicting_start().await;
    let sk = layout().schedule(&loser.input.sid).unwrap();
    let key = layout().admission(&loser.scope, &loser.input.input_id).unwrap();
    let p = provider(store.clone(), config());
    let pause = store.pause_after("document.replace", &sk, 1);
    abandon_at(&p, start("next"), pause).await;
    let schedule = store.read_document(&sk).await.unwrap().unwrap().value;
    assert_eq!(schedule["slots"][&key]["decision"], "ContentConflict");
    assert_eq!(p.admission_decision(&loser.scope, &loser.input.input_id).await.unwrap(), None);

    let restarted = provider(store.clone(), config());
    assert!(matches!(
        restarted.admit(start("next")).await.unwrap(),
        AdmitOutcome::Duplicate(receipt) if receipt.input_id == InputId("next".into())
    ));
    assert_eq!(restarted.admission_decision(&loser.scope, &loser.input.input_id).await.unwrap(),
        Some(AdmitOutcome::ContentConflict));
    let schedule = store.read_document(&sk).await.unwrap().unwrap().value;
    assert!(schedule["slots"].get(&key).is_none());
    assert_eq!(schedule["activation"], 2);
}
