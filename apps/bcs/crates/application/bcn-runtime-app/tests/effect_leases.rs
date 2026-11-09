mod common;
use common::*;
use async_trait::async_trait;
use bcn_runtime_api::{application::*,port::{repo::*,*}};
use bcn_runtime_app::DefaultWorker;
use bcn_runtime_model::*;
use std::{collections::BTreeMap,sync::{Arc,atomic::{AtomicBool,AtomicU64,Ordering}},time::{Duration,Instant}};

struct BlockedAdmission {inner:Arc<dyn AdmissionStore>,blocked:AtomicBool,entered:AtomicBool}
#[async_trait]
impl AdmissionStore for BlockedAdmission {
 async fn admit(&self,r:AdmissionRequest)->RuntimeResult<AdmissionReceipt>{self.inner.admit(r).await}
 async fn get_receipt(&self,id:&AdmissionId)->RuntimeResult<Option<AdmissionReceipt>>{self.inner.get_receipt(id).await}
 async fn repair_pending(&self,s:&Scope,c:Option<String>,l:u32)->RuntimeResult<Page<AdmissionReceipt>>{
  if self.blocked.load(Ordering::SeqCst){self.entered.store(true,Ordering::SeqCst);futures::future::pending::<()>().await;}
  self.inner.repair_pending(s,c,l).await
 }
}
struct SlowJudge {calls:AtomicU64,dropped:Arc<AtomicBool>,release:tokio::sync::Notify}
struct OnDrop(Arc<AtomicBool>);
impl Drop for OnDrop {fn drop(&mut self){self.0.store(true,Ordering::SeqCst);}}
#[async_trait]
impl JudgeExecutor for SlowJudge {
 async fn execute(&self,_:JudgeRequest)->RuntimeResult<String>{
  self.calls.fetch_add(1,Ordering::SeqCst);let _guard=OnDrop(self.dropped.clone());
  self.release.notified().await;Ok("yes".into())
 }
}
async fn wait_until(mut ready:impl FnMut()->bool,millis:u64){
 tokio::time::timeout(Duration::from_millis(millis),async{while !ready(){tokio::time::sleep(Duration::from_millis(10)).await}}).await.unwrap();
}
async fn setup()->(Fixture,TaskView,Arc<DefaultWorker>,Arc<BlockedAdmission>,Arc<SlowJudge>){
 let mut f=Fixture::new();let task=f.gateway().submit(caller(),request("judge-lease")).await.unwrap();
 let admission=Arc::new(BlockedAdmission{inner:f.ports.admissions.clone(),blocked:AtomicBool::new(false),entered:AtomicBool::new(false)});
 f.ports.admissions=admission.clone();
 let judge=Arc::new(SlowJudge{calls:AtomicU64::new(0),dropped:Arc::new(AtomicBool::new(false)),release:tokio::sync::Notify::new()});
 let w=Arc::new(DefaultWorker::new(node("one"),f.ports.clone(),f.runners(),judge.clone(),f.clock.clone()).unwrap());
 w.step(&task.session_id).await.unwrap();
 // A real committed/published Worker effect; unrelated Direct Send remains Connector-owned.
 let cp=f.repo.load(&task.session_id).await.unwrap().unwrap();
 let effect=EffectIntent::Judge{request:JudgeRequest{operation_id:OperationId::new(scope(),"judge-op"),task_id:task.task_id.clone(),node_id:"review".into(),prompt:"approve".into(),choices:vec!["yes".into(),"no".into()],inputs:BTreeMap::new()}};
 let command_id=effect_command_id(&task.session_id,&effect).unwrap();
 let cp=f.repo.commit(SessionCommit{session_id:task.session_id.clone(),fence:cp.fence,expected_revision:cp.revision,consumed_input:cp.consumed_input,transition:Transition{next_state:cp.state,effects:vec![effect],events:vec![],timer_changes:vec![]}}).await.unwrap();
 f.repo.publish(&cp.fence,CommittedEffectRef{session_id:task.session_id.clone(),command_id}).await.unwrap();
 w.tick().await.unwrap();wait_until(||judge.calls.load(Ordering::SeqCst)==1,500).await;
 (f,task,w,admission,judge)
}
fn advance_clock(f:&Fixture)->tokio::task::JoinHandle<()>{
 let clock=f.clock.clone();tokio::spawn(async move{let start=Instant::now();loop{clock.set(1000+start.elapsed().as_millis() as u64);tokio::time::sleep(Duration::from_millis(10)).await;}})
}
#[tokio::test]
async fn slow_judge_keeps_one_delivery_across_three_second_lease_with_blocked_tick(){
 let (f,task,w,admission,judge)=setup().await;let clock=advance_clock(&f);
 admission.blocked.store(true,Ordering::SeqCst);let (tx,rx)=tokio::sync::watch::channel(false);
 let running=tokio::spawn({let w=w.clone();async move{w.run(rx).await}});
 wait_until(||admission.entered.load(Ordering::SeqCst),500).await;
 tokio::time::sleep(Duration::from_millis(3500)).await;
 let mut other=f.ports.clone();other.admissions=f.repo.clone();
 let second=DefaultWorker::new(node("two"),other,f.runners(),judge.clone(),f.clock.clone()).unwrap();
 second.tick().await.unwrap();tokio::time::sleep(Duration::from_millis(50)).await;
 let calls=judge.calls.load(Ordering::SeqCst);judge.release.notify_waiters();
 tokio::time::sleep(Duration::from_millis(100)).await;
 let inputs=f.repo.read_batch(&task.session_id,0,100).await.unwrap();
 tx.send(true).ok();running.abort();clock.abort();drop(w);
 assert_eq!(calls,1,"a healthy pending Judge must renew independently of the blocked scheduler");
 assert_eq!(inputs.iter().filter(|e|matches!(e.input.kind,SessionInputKind::JudgeDecided{..})).count(),1);
}
#[tokio::test]
async fn effect_deadline_cancels_judge_while_scheduler_preamble_is_blocked(){
 let (f,task,w,admission,judge)=setup().await;let clock=advance_clock(&f);
 admission.blocked.store(true,Ordering::SeqCst);let (tx,rx)=tokio::sync::watch::channel(false);
 let running=tokio::spawn({let w=w.clone();async move{w.run(rx).await}});
 wait_until(||admission.entered.load(Ordering::SeqCst),500).await;
 tokio::time::sleep(Duration::from_millis(15300)).await;
 let cancelled=judge.dropped.load(Ordering::SeqCst);
 let inputs=f.repo.read_batch(&task.session_id,0,100).await.unwrap();
 tx.send(true).ok();running.abort();clock.abort();drop(w);
 assert!(cancelled,"15s effect deadline must be polled even while the business preamble never returns");
 assert!(!inputs.iter().any(|e|matches!(e.input.kind,SessionInputKind::JudgeDecided{..})));
}
#[tokio::test]
async fn lost_delivery_cancels_old_judge_and_only_current_worker_publishes(){
 let (f,task,w,admission,old)=setup().await;
 admission.blocked.store(true,Ordering::SeqCst);let (tx,rx)=tokio::sync::watch::channel(false);
 let running=tokio::spawn({let w=w.clone();async move{w.run(rx).await}});
 wait_until(||admission.entered.load(Ordering::SeqCst),500).await;
 // Simulate expiration/reclaim before the old effect's next independent renewal.
 f.clock.set(5000);
 let winner=Arc::new(SlowJudge{calls:AtomicU64::new(0),dropped:Arc::new(AtomicBool::new(false)),release:tokio::sync::Notify::new()});
 let mut ports=f.ports.clone();ports.admissions=f.repo.clone();
 let second=DefaultWorker::new(node("two"),ports,f.runners(),winner.clone(),f.clock.clone()).unwrap();
 second.tick().await.unwrap();wait_until(||winner.calls.load(Ordering::SeqCst)==1,500).await;
 wait_until(||old.dropped.load(Ordering::SeqCst),1500).await;
 winner.release.notify_one();tokio::time::sleep(Duration::from_millis(50)).await;
 let inputs=f.repo.read_batch(&task.session_id,0,100).await.unwrap();
 tx.send(true).ok();running.abort();drop(w);
 assert_eq!(inputs.iter().filter(|e|matches!(e.input.kind,SessionInputKind::JudgeDecided{..})).count(),1);
 assert!(second.tick().await.unwrap().errors.is_empty(),"the winning completion must not hit conflicting stale Judge dedup");
}
