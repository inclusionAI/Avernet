use std::sync::{Arc, atomic::{AtomicU64, Ordering}};
use bcn_runtime_model::*;
use bcn_runtime_storage_api::{ServerClock, NodeLivenessPort};
use bcn_storage_memory::MemoryAtomicStore;
use bcn_runtime_store::RuntimeStore;
struct Clock(AtomicU64);
impl ServerClock for Clock { fn now_ms(&self) -> u64 { self.0.load(Ordering::SeqCst) } }
#[tokio::test]
async fn incarnation_heartbeats_use_server_time_and_bounded_membership() {
 let clock = Arc::new(Clock(AtomicU64::new(1000)));
 let atomic = Arc::new(MemoryAtomicStore::new(RuntimeProfile::Test, clock.clone()).unwrap());
 let repo = RuntimeStore::new(atomic);
 let scope = Scope::new("test", "tenant", "runtime");
 let a = NodeIdentity { scope: scope.clone(), node_id: "a/b".into(), incarnation: "one".into() };
 let b = NodeIdentity { incarnation: "two".into(), ..a.clone() };
 repo.register(a.clone(), 3000).await.unwrap();
 repo.register(b.clone(), 3000).await.unwrap();
 clock.0.store(3999, Ordering::SeqCst);
 assert_eq!(repo.inspect_batch(&[a.clone()]).await.unwrap()[0].liveness, Liveness::Alive);
 repo.renew(&b, 3000).await.unwrap();
 clock.0.store(4000, Ordering::SeqCst);
 let observed = repo.inspect_batch(&[a.clone(), b.clone()]).await.unwrap();
 assert_eq!(observed[0].liveness, Liveness::Dead);
 assert_eq!(observed[1].liveness, Liveness::Alive);
 repo.mark_draining(&b).await.unwrap();
 assert!(repo.inspect_batch(&[b]).await.unwrap()[0].draining);
 let first = repo.list_members(&scope, None, 1).await.unwrap();
 assert_eq!(first.items.len(), 1);
 let second = repo.list_members(&scope, first.next_cursor, 1).await.unwrap();
 assert_eq!(second.items.len(), 1);
 assert!(second.next_cursor.is_none());
 assert_ne!(first.items[0].node, second.items[0].node);
}
