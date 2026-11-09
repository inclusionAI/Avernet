use std::sync::{Arc, atomic::{AtomicU64, Ordering}};
use bcn_runtime_model::{RuntimeProfile, StorageError};
use bcn_runtime_storage_api::{AtomicStore, ServerClock, conformance};
use bcn_storage_memory::MemoryAtomicStore;

struct Clock(AtomicU64);
impl ServerClock for Clock {
    fn now_ms(&self) -> u64 { self.0.load(Ordering::SeqCst) }
}
fn store() -> Arc<dyn AtomicStore> {
    Arc::new(MemoryAtomicStore::new(RuntimeProfile::Test, Arc::new(Clock(AtomicU64::new(1234)))).unwrap())
}
#[tokio::test]
async fn concurrent_cas_has_one_winner() { conformance::concurrent_cas(store()).await; }
#[tokio::test]
async fn failed_condition_writes_nothing() { conformance::failed_condition(store()).await; }
#[tokio::test]
async fn deleted_recreated_key_rejects_stale_revision() { conformance::delete_recreate(store()).await; }
#[tokio::test]
async fn isolation_includes_environment_tenant_namespace_and_partition() { conformance::isolation(store()).await; }
#[tokio::test]
async fn pages_are_ordered_bounded_and_cursor_scoped() { conformance::pagination(store()).await; }
#[tokio::test]
async fn invalid_requests_fail_closed() { conformance::limits_and_validation(store()).await; }
#[tokio::test]
async fn injected_server_clock_is_used() {
    let clock = Arc::new(Clock(AtomicU64::new(42)));
    let store = MemoryAtomicStore::new(RuntimeProfile::Local, clock.clone()).unwrap();
    assert_eq!(store.server_time_ms().await.unwrap(), 42);
    clock.0.store(99, Ordering::SeqCst);
    assert_eq!(store.server_time_ms().await.unwrap(), 99);
}
#[test]
fn production_profile_cannot_construct_memory_storage() {
    assert!(matches!(MemoryAtomicStore::new(RuntimeProfile::Production,
        Arc::new(Clock(AtomicU64::new(0)))), Err(StorageError::UnsupportedProfile)));
}
#[tokio::test]
async fn aggregate_payload_limit_rejects_whole_transaction() { conformance::total_batch_bytes(store()).await; }

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn competing_threads_cannot_both_claim_one_revision() {
    use bcn_runtime_model::{AtomicBatch, Condition, Expected, Mutation, Scope};
    let storage = store();
    let barrier = Arc::new(tokio::sync::Barrier::new(32));
    let mut handles = Vec::new();
    for i in 0..32 {
        let storage = storage.clone();
        let barrier = barrier.clone();
        handles.push(tokio::spawn(async move {
            barrier.wait().await;
            storage.transact(&Scope::new("test", "tenant", "threads"), "p", AtomicBatch {
                conditions: vec![Condition { key: "owner".into(), expected: Expected::Absent }],
                mutations: vec![Mutation::Put { key: "owner".into(), value: vec![i] }],
            }).await
        }));
    }
    let mut winners = 0;
    for handle in handles {
        match handle.await.unwrap() {
            Ok(_) => winners += 1,
            Err(StorageError::ConditionFailed { .. }) => {},
            other => panic!("unexpected claim result: {other:?}"),
        }
    }
    assert_eq!(winners, 1);
}
#[tokio::test]
async fn reads_keep_requested_order_and_validation_writes_nothing() { conformance::ordered_reads_and_validation_atomicity(store()).await; }
