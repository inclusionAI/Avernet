mod common;
use common::*;
use async_trait::async_trait;
use bcn_runtime_api::{application::*, port::*};
use bcn_runtime_model::*;
use bcn_runtime_storage_api::AtomicStore;
use bcn_runtime_store::RuntimeStore;
use std::sync::{Arc, atomic::{AtomicU64, AtomicBool, Ordering}};
use std::time::Duration;

struct Heartbeats { calls: AtomicU64, partial: AtomicBool }
#[async_trait]
impl RuntimeLiveness for Heartbeats {
    async fn register(&self, _: NodeIdentity, _: u64) -> RuntimeResult<()> { Ok(()) }
    async fn renew(&self, _: &NodeIdentity, _: u64) -> RuntimeResult<()> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        if self.partial.load(Ordering::SeqCst) { Err(RuntimeError::Unavailable("one slot unavailable".into())) } else { Ok(()) }
    }
    async fn inspect_batch(&self, _: &[NodeIdentity]) -> RuntimeResult<Vec<NodeObservation>> { Ok(vec![]) }
    async fn mark_draining(&self, _: &NodeIdentity) -> RuntimeResult<()> { Ok(()) }
}
struct SlowStore;
#[async_trait]
impl AtomicStore for SlowStore {
    async fn read_many(&self, _: &Scope, _: &str, _: &[String]) -> StorageResult<Vec<Option<VersionedValue>>> { futures::future::pending().await }
    async fn transact(&self, _: &Scope, _: &str, _: AtomicBatch) -> StorageResult<TransactionReceipt> { futures::future::pending().await }
    async fn scan_page(&self, _: &Scope, _: &str, _: &str, _: Option<ScanCursor>, _: usize) -> StorageResult<ScanPage> { futures::future::pending().await }
    async fn server_time_ms(&self) -> StorageResult<u64> { futures::future::pending().await }
}
async fn run_case(slow: bool, partial: bool) {
    let mut f=Fixture::new();
    let live=Arc::new(Heartbeats{calls:AtomicU64::new(0),partial:AtomicBool::new(partial)});
    f.ports.liveness=live.clone();
    if slow {
        let repo=Arc::new(RuntimeStore::new(Arc::new(SlowStore)));
        f.ports.admissions=repo.clone(); f.ports.commands=repo;
    }
    let w=Arc::new(f.worker("worker"));
    let c=Arc::new(f.connector("connector",Arc::new(Transport::new(vec![]))));
    let (tx,rx)=tokio::sync::watch::channel(false);
    let wh=tokio::spawn({let w=w.clone();let rx=rx.clone();async move{w.run(rx).await}});
    let ch=tokio::spawn({let c=c.clone();async move{c.run(rx).await}});
    for i in 1..=5 {
        f.clock.set(1000+i*1000);
        tokio::time::sleep(Duration::from_millis(1050)).await;
    }
    let calls=live.calls.load(Ordering::SeqCst);
    let alive=!wh.is_finished() && !ch.is_finished();
    tx.send(true).ok(); wh.abort(); ch.abort();
    assert!(alive,"recoverable backend errors must not terminate actual run loops");
    assert!(calls>=8,"heartbeat schedule blocked by business I/O: {calls}");
}
#[tokio::test]
async fn actual_loops_survive_partial_renewal() { run_case(false,true).await; }
#[tokio::test]
async fn slow_business_io_cannot_block_renewal() { run_case(true,false).await; }
