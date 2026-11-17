//! The command feed: consumer group, ack, idle takeover, kept-alive reads.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "tests")]

mod common;

use bcs_dist_model::*;
use bcs_dist_store::command::CommandStreams;
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_service_api::port::repo::distributed::dispatch::*;
use bcs_store_api::stream::{AppendId, StreamStorePlugin};
use common::*;
use std::collections::BTreeMap;
use std::num::NonZeroU32;
use std::sync::Arc;
use std::time::Duration;

fn max(n: u32) -> NonZeroU32 {
    NonZeroU32::new(n).unwrap()
}

const SHORT: Duration = Duration::from_millis(60);

async fn feed(store: &Arc<Recorder>, node: &str) -> Box<dyn CommandFeedPort> {
    queue(store).notifications(pool(), instance(node)).await.unwrap()
}

#[tokio::test]
async fn hints_posted_before_the_group_existed_are_delivered() {
    let store = Arc::new(Recorder::default());
    let worker = WorkerFixture::new(&store);
    worker.notify().await; // no Connector, no group yet
    let mut feed = feed(&store, "conn-03").await;
    let got = feed.receive(max(10), SHORT).await.unwrap();
    assert_eq!(got.len(), 1);
    assert_eq!(got[0].did, did());
    assert_eq!(got[0].backend, BackendId("model".into()));
    assert_eq!(got[0].consumer_generation, "conn-03:1794370950000");
    assert!(got[0].opaque.starts_with("p0:"), "{}", got[0].opaque);
    // Opening again never resets the group.
    let mut again = feed_for(&store, "conn-03").await;
    assert!(again.receive(max(10), SHORT).await.unwrap().is_empty());
}

async fn feed_for(store: &Arc<Recorder>, node: &str) -> Box<dyn CommandFeedPort> {
    feed(store, node).await
}

#[tokio::test]
async fn each_hint_goes_to_one_consumer_and_ack_is_repeatable() {
    let store = Arc::new(Recorder::default());
    let worker = WorkerFixture::new(&store);
    let mut a = feed(&store, "conn-a").await;
    let mut b = feed(&store, "conn-b").await;
    worker.notify().await;
    let got_a = a.receive(max(10), SHORT).await.unwrap();
    let got_b = b.receive(max(10), SHORT).await.unwrap();
    assert_eq!(got_a.len() + got_b.len(), 1);
    let (owner, delivery) = if got_a.is_empty() { (&mut b, &got_b[0]) } else { (&mut a, &got_a[0]) };
    owner.ack(delivery).await.unwrap();
    owner.ack(delivery).await.unwrap();

    // Acked: nothing left to take over, even with no idle time required.
    let mut config = config(1);
    config.reclaim_min_idle = Duration::ZERO;
    let mut c = queue_with(&store, Arc::new(FixedLiveness(
        bcs_service_api::port::repo::distributed::ownership::Liveness::Unknown,
    )), config)
    .notifications(pool(), instance("conn-c"))
    .await
    .unwrap();
    assert!(c.receive(max(10), SHORT).await.unwrap().is_empty());
}

#[tokio::test]
async fn pending_hints_of_a_dead_consumer_are_taken_over() {
    let store = Arc::new(Recorder::default());
    let worker = WorkerFixture::new(&store);
    let mut dead = feed(&store, "conn-dead").await;
    worker.notify().await;
    let taken = dead.receive(max(10), SHORT).await.unwrap();
    assert_eq!(taken.len(), 1);
    drop(dead); // crashed before the claim decision: never acked

    // With the default idle time the entry is not stolen yet.
    let mut patient = feed(&store, "conn-07").await;
    assert!(patient.receive(max(10), SHORT).await.unwrap().is_empty());

    let mut config = config(1);
    config.reclaim_min_idle = Duration::ZERO;
    let mut live = queue_with(&store, Arc::new(bcs_dist_connector_store::membership::PendingMembership), config)
        .notifications(pool(), instance("conn-08"))
        .await
        .unwrap();
    let got = live.receive(max(10), SHORT).await.unwrap();
    assert_eq!(got.len(), 1);
    assert_eq!(got[0].did, did());
    assert_eq!(got[0].consumer_generation, "conn-08:1794370950000");
    assert_eq!(got[0].opaque, taken[0].opaque, "the same entry");
    live.ack(&got[0]).await.unwrap();
}

#[tokio::test]
async fn a_started_read_is_kept_across_receive_calls() {
    let store = Arc::new(Recorder::default());
    let worker = WorkerFixture::new(&store);
    let mut feed = feed(&store, "conn-03").await;
    store.clear();
    // Three receives that give up long before the group read's 20 ms block
    // ends: the one read stays in flight, it is not dropped and restarted.
    for _ in 0..3 {
        assert!(feed.receive(max(10), Duration::from_millis(2)).await.unwrap().is_empty());
    }
    assert_eq!(store.count("stream.read_group"), 1, "{:?}", store.calls());
    worker.notify().await;
    let got = feed.receive(max(10), Duration::from_millis(300)).await.unwrap();
    assert_eq!(got.len(), 1);
    assert_eq!(got[0].did, did());
}

#[tokio::test]
async fn idle_takeover_covers_every_partition_with_a_small_max() {
    let store = Arc::new(Recorder::default());
    let streams = CommandStreams::new(max(3));
    let worker = WorkerFixture { store: store.clone(), streams };
    let pending_stub = || Arc::new(bcs_dist_connector_store::membership::PendingMembership);
    let mut dead = queue_with(&store, pending_stub(), config(3))
        .notifications(pool(), instance("conn-dead"))
        .await
        .unwrap();
    let dids: Vec<Did> = (0..9).map(|n| Did(format!("s0042.7f3a9c.b21e.n{n}.a1"))).collect();
    for did in &dids {
        worker.notify_did(did).await;
    }
    let mut taken = 0;
    while taken < dids.len() {
        taken += dead.receive(max(20), Duration::from_millis(300)).await.unwrap().len();
    }
    drop(dead); // nothing acked

    let mut config = config(3);
    config.reclaim_min_idle = Duration::ZERO;
    config.reclaim_every = Duration::ZERO;
    let mut live = queue_with(&store, pending_stub(), config)
        .notifications(pool(), instance("conn-08"))
        .await
        .unwrap();
    let mut got = std::collections::BTreeSet::new();
    for _ in 0..40 {
        // One at a time: the takeover must still reach all three partitions.
        for delivery in live.receive(max(1), Duration::from_millis(30)).await.unwrap() {
            live.ack(&delivery).await.unwrap();
            got.insert(delivery.did.0.clone());
        }
        if got.len() == dids.len() {
            break;
        }
    }
    assert_eq!(got.len(), dids.len(), "taken over: {got:?}");
}

#[tokio::test]
async fn receive_honours_max_and_keeps_the_rest() {
    let store = Arc::new(Recorder::default());
    let worker = WorkerFixture::new(&store);
    for n in 0..5 {
        worker.notify_did(&Did(format!("s0042.7f3a9c.b21e.n{n}.a1"))).await;
    }
    let mut feed = feed(&store, "conn-03").await;
    let first = feed.receive(max(2), SHORT).await.unwrap();
    assert_eq!(first.len(), 2);
    let rest = feed.receive(max(10), SHORT).await.unwrap();
    assert_eq!(rest.len(), 3);
    let order: Vec<_> = first.iter().chain(&rest).map(|d| d.did.0.clone()).collect();
    assert_eq!(order[0], "s0042.7f3a9c.b21e.n0.a1");
    assert_eq!(order[4], "s0042.7f3a9c.b21e.n4.a1");
}

#[tokio::test]
async fn every_partition_is_read_and_acked_on_its_own_stream() {
    let store = Arc::new(Recorder::default());
    let streams = CommandStreams::new(max(4));
    let worker = WorkerFixture { store: store.clone(), streams };
    let q = queue_with(
        &store,
        Arc::new(bcs_dist_connector_store::membership::PendingMembership),
        config(4),
    );
    let mut feed = q.notifications(pool(), instance("conn-03")).await.unwrap();
    let dids: Vec<Did> = (0..12).map(|n| Did(format!("s0042.7f3a9c.b21e.n{n}.a1"))).collect();
    for did in &dids {
        worker.notify_did(did).await;
    }
    let mut got = Vec::new();
    while got.len() < dids.len() {
        let batch = feed.receive(max(20), Duration::from_millis(300)).await.unwrap();
        assert!(!batch.is_empty(), "missing hints: {} of {}", got.len(), dids.len());
        got.extend(batch);
    }
    let mut partitions = std::collections::BTreeSet::new();
    for delivery in &got {
        let partition: u32 = delivery.opaque[1..].split(':').next().unwrap().parse().unwrap();
        assert_eq!(partition, streams.partition_of(&delivery.did), "read from the partition it was posted to");
        partitions.insert(partition);
        feed.ack(delivery).await.unwrap();
    }
    assert!(partitions.len() > 1, "the fixture should spread over partitions");
    let mut seen: Vec<_> = got.iter().map(|d| d.did.0.clone()).collect();
    seen.sort();
    let mut want: Vec<_> = dids.iter().map(|d| d.0.clone()).collect();
    want.sort();
    assert_eq!(seen, want);
}

#[tokio::test]
async fn malformed_entries_are_acked_and_skipped() {
    let store = Arc::new(Recorder::default());
    let worker = WorkerFixture::new(&store);
    let key = layout().command(&pool(), 0).unwrap();
    let mut junk = BTreeMap::new();
    junk.insert("other".to_owned(), b"x".to_vec());
    store.model.append(&key, AppendId::Auto, &junk).await.unwrap();
    let mut empty = BTreeMap::new();
    empty.insert("did".to_owned(), Vec::new());
    store.model.append(&key, AppendId::Auto, &empty).await.unwrap();
    worker.notify().await;

    let mut feed = feed(&store, "conn-03").await;
    let got = feed.receive(max(10), SHORT).await.unwrap();
    assert_eq!(got.iter().map(|d| d.did.clone()).collect::<Vec<_>>(), vec![did()]);
    feed.ack(&got[0]).await.unwrap();
    // Nothing is left pending for anyone.
    let mut config = config(1);
    config.reclaim_min_idle = Duration::ZERO;
    let mut other = queue_with(&store, Arc::new(bcs_dist_connector_store::membership::PendingMembership), config)
        .notifications(pool(), instance("conn-09"))
        .await
        .unwrap();
    assert!(other.receive(max(10), SHORT).await.unwrap().is_empty());
}

#[tokio::test]
async fn bad_delivery_handles_and_instances_are_invalid() {
    let store = Arc::new(Recorder::default());
    let mut feed = feed(&store, "conn-03").await;
    for opaque in ["", "p0", "p9:1-0", "px:1-0", "p0:1", "p0:a-b"] {
        let delivery = CommandDelivery {
            did: did(),
            backend: BackendId("model".into()),
            consumer_generation: "c".into(),
            opaque: opaque.into(),
        };
        assert!(matches!(feed.ack(&delivery).await, Err(StoreError::Invalid { .. })), "{opaque}");
    }
    let bad = Instance { node: "conn:03".into(), incarnation: "1".into() };
    assert!(matches!(
        queue(&store).notifications(pool(), bad).await.map(|_| ()),
        Err(StoreError::Invalid { .. })
    ));
}
