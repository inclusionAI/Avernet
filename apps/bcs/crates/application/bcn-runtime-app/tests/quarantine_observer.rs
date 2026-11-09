mod common;
use common::*;
use bcn_runtime_api::{application::*,port::*};
use bcn_runtime_model::*;
use bcn_runtime_app::DefaultWorker;
use std::sync::{Arc,Mutex};
#[derive(Default)] struct Observed(Mutex<Vec<(SessionId,SessionRecovery)>>);
impl RuntimeObserverPort for Observed {
 fn session_quarantined(&self,s:&SessionId,r:&SessionRecovery){self.0.lock().unwrap().push((s.clone(),r.clone()));}
}
fn observed_worker(f:&Fixture,id:&str,o:Arc<Observed>)->DefaultWorker {
 DefaultWorker::new(node(id),f.ports.clone(),f.runners(),Arc::new(NoJudge),f.clock.clone()).unwrap().with_observer(o)
}
#[tokio::test]
async fn quarantine_notifies_once_per_durable_transition_and_new_fence_without_poll_spam() {
 let f=Fixture::new();let task=f.gateway().submit(caller(),request("poison")).await.unwrap();let s=&task.session_id;
 let shard=session_shard(s);let mut fence=f.ports.ownership.claim_shard(shard.clone(),node("seed0"),None).await.unwrap();
 f.ports.sessions.begin_recovery(s,&fence,3).await.unwrap();
 for id in ["seed1","seed2"] {fence=f.ports.ownership.claim_shard(shard.clone(),node(id),Some(fence)).await.unwrap();f.ports.sessions.begin_recovery(s,&fence,3).await.unwrap();}
 fence=f.ports.ownership.claim_shard(shard.clone(),node("observer"),Some(fence)).await.unwrap();
 let observed=Arc::new(Observed::default());let worker=observed_worker(&f,"observer",observed.clone());
 for _ in 0..3 {assert!(matches!(worker.step(s).await,Err(RuntimeError::Unavailable(_))));}
 let seen=observed.0.lock().unwrap();assert_eq!(seen.len(),1);assert_eq!(&seen[0].0,s);assert_eq!(seen[0].1.takeovers,3);assert!(seen[0].1.quarantined);drop(seen);
 drop(worker);let same=observed_worker(&f,"observer",observed.clone());let _=same.step(s).await;
 assert_eq!(observed.0.lock().unwrap().len(),1,"same durable fence after process restart is not a new alert");
 f.ports.ownership.claim_shard(shard,node("next"),Some(fence)).await.unwrap();
 let next=observed_worker(&f,"next",observed.clone());for _ in 0..3 {let _=next.step(s).await;}
 let seen=observed.0.lock().unwrap();assert_eq!(seen.len(),2);assert_eq!(seen[1].1.takeovers,4);assert_eq!(seen[1].1.fence.owner,node("next"));
}
