#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "contract assertions"
)]
//! Connector partition owners reuse the shard lease code under their own keys.
use bcs_dist_model::*;
use bcs_dist_store::{coordination::CoordinationStore, layout::KeyLayout};
use bcs_service_api::port::repo::distributed::ownership::*;
use bcs_store_api::document::DocumentStorePlugin;
use bcs_test_support::store_model::Model;
use std::{sync::Arc, time::Duration};

fn instance(name: &str) -> Instance {
    Instance {
        node: name.into(),
        incarnation: format!("{name}-1"),
    }
}
fn layout() -> KeyLayout {
    KeyLayout::new("c01", "main").unwrap()
}
fn store(model: &Arc<Model>) -> CoordinationStore {
    CoordinationStore::new(model.clone(), model.clone(), model.clone(), layout()).unwrap()
}
async fn register(s: &CoordinationStore, name: &str, role: RuntimeRole) {
    s.register(instance(name), role, format!("http://{name}.example.test:8080"), Duration::from_secs(3))
        .await
        .unwrap();
}
fn claim(name: &str, expected: Option<u64>) -> ClaimShard {
    ClaimShard {
        shard: ShardId(3),
        me: instance(name),
        expected_epoch: expected.map(WorkerEpoch),
        request_id: RequestId(format!("claim-{name}")),
    }
}

#[tokio::test]
async fn worker_shards_and_connector_partitions_have_separate_owner_records() {
    let model = Arc::new(Model::default());
    let workers = store(&model);
    let connectors = store(&model).with_partition_owners(PoolId("a2a".into())).unwrap();
    register(&workers, "worker", RuntimeRole::Worker).await;
    register(&connectors, "conn", RuntimeRole::Connector).await;

    let worker = workers.claim(claim("worker", None)).await.unwrap();
    let connector = connectors.claim(claim("conn", None)).await.unwrap();
    assert!(matches!(worker, LeaseClaim::Claimed(ref c) if c.owner.instance == instance("worker")));
    assert!(matches!(connector, LeaseClaim::Claimed(ref c) if c.owner.instance == instance("conn")));

    let shard = model.read_document("bcn:v1:c01:main:s0003:owner").await.unwrap();
    let partition = model.read_document("bcn:v1:c01:main:a2a:p0003:owner").await.unwrap();
    assert!(shard.is_some() && partition.is_some());
    assert_eq!(
        workers.current(ShardId(3)).await.unwrap().unwrap().claim.owner.instance,
        instance("worker")
    );
    assert_eq!(
        connectors.current(ShardId(3)).await.unwrap().unwrap().claim.owner.instance,
        instance("conn")
    );
}

#[tokio::test]
async fn a_live_partition_owner_is_not_displaced_and_hands_over_on_request() {
    let model = Arc::new(Model::default());
    let connectors = store(&model).with_partition_owners(PoolId("a2a".into())).unwrap();
    register(&connectors, "a", RuntimeRole::Connector).await;
    register(&connectors, "b", RuntimeRole::Connector).await;
    let LeaseClaim::Claimed(owned) = connectors.claim(claim("a", None)).await.unwrap() else {
        panic!("first claim");
    };
    assert_eq!(connectors.claim(claim("b", Some(1))).await.unwrap(), LeaseClaim::Conflict);

    let request = HandoffRequest {
        request_id: RequestId("handoff-1".into()),
        from: owned.clone(),
        target: instance("b"),
        grace: Duration::from_secs(5),
    };
    let HandoffOutcome::Accepted(record) = connectors.handoff(request).await.unwrap() else {
        panic!("handoff");
    };
    assert_eq!(record.claim.owner.instance, instance("b"));
    assert_eq!(record.claim.owner.epoch, WorkerEpoch(2));
    assert_eq!(record.handoff.unwrap().from, owned);
}

#[test]
fn an_invalid_pool_is_refused() {
    let model = Arc::new(Model::default());
    assert!(store(&model).with_partition_owners(PoolId("a:b".into())).is_err());
}
