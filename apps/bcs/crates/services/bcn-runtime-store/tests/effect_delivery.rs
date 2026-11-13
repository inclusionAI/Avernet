mod common;
use common::*;
use std::{collections::BTreeMap,sync::{Arc,atomic::{AtomicBool,AtomicU64,Ordering}}};
use async_trait::async_trait;
use bcn_runtime_api::port::repo::*;
use bcn_runtime_model::*;
use bcn_runtime_storage_api::{AtomicStore,ServerClock};
use bcn_runtime_store::RuntimeStore;
use bcn_storage_memory::MemoryAtomicStore;
struct TestClock(AtomicU64);
impl ServerClock for TestClock {fn now_ms(&self)->u64{self.0.load(Ordering::SeqCst)}}
struct Gate {inner:Arc<MemoryAtomicStore>,block:AtomicBool,entered:tokio::sync::Notify,resume:tokio::sync::Notify}
#[async_trait]
impl AtomicStore for Gate {
 async fn read_many(&self,s:&Scope,p:&str,k:&[String])->StorageResult<Vec<Option<VersionedValue>>>{self.inner.read_many(s,p,k).await}
 async fn transact(&self,s:&Scope,p:&str,b:AtomicBatch)->StorageResult<TransactionReceipt>{
  if b.mutations.iter().any(|m|m.key().starts_with("input-id/")) && self.block.swap(false,Ordering::SeqCst){self.entered.notify_one();self.resume.notified().await;}
  self.inner.transact(s,p,b).await
 }
 async fn scan_page(&self,s:&Scope,p:&str,k:&str,c:Option<ScanCursor>,l:usize)->StorageResult<ScanPage>{self.inner.scan_page(s,p,k,c,l).await}
 async fn server_time_ms(&self)->StorageResult<u64>{self.inner.server_time_ms().await}
}
async fn prepared(effect:Option<EffectIntent>)->(Arc<RuntimeStore>,Arc<TestClock>,Arc<Gate>,AdmissionRequest,SessionInput){
 let (_,_,req)=fixture();let clock=Arc::new(TestClock(AtomicU64::new(1000)));
 let atomic=Arc::new(Gate{inner:Arc::new(MemoryAtomicStore::new(RuntimeProfile::Test,clock.clone()).unwrap()),block:AtomicBool::new(false),entered:tokio::sync::Notify::new(),resume:tokio::sync::Notify::new()});
 let repo=Arc::new(RuntimeStore::new(atomic.clone()));let cp=activated(&repo,&req).await;
 let effect=effect.unwrap_or_else(||EffectIntent::Judge{request:JudgeRequest{operation_id:OperationId::new(scope(),"judge"),task_id:req.task.task_id.clone(),node_id:"node".into(),prompt:"choose".into(),choices:vec!["yes".into(),"no".into()],inputs:BTreeMap::new()}});
 let events=if matches!(effect,EffectIntent::SaveResult{..}) {vec![DomainEvent{event_id:EventId::new(scope(),"completed"),task_id:req.task.task_id.clone(),kind:DomainEventKind::TaskStatusChanged{status:TaskStatus::Completed}}]} else {vec![]};
 let id=effect_command_id(&req.spec.session_id,&effect).unwrap();
 let cp=repo.commit(SessionCommit{session_id:req.spec.session_id.clone(),fence:cp.fence,expected_revision:cp.revision,consumed_input:0,transition:Transition{next_state:cp.state,effects:vec![effect],events,timer_changes:vec![]}}).await.unwrap();
 repo.publish(&cp.fence,CommittedEffectRef{session_id:req.spec.session_id.clone(),command_id:id}).await.unwrap();
 let input=SessionInput{event_id:EventId::new(scope(),"decision"),at_ms:1000,kind:SessionInputKind::JudgeDecided{task_id:req.task.task_id.clone(),node_id:"node".into(),branch:"yes".into()}};
 (repo,clock,atomic,req,input)
}
#[tokio::test]
async fn conformance_command_effect_expiry_aba_and_exact_completion(){
 let (repo,clock,_,req,input)=prepared(None).await;
 bcs_test_support::contract::bcn_runtime::command_effect_contract_tests(repo.clone(),repo,req.spec.session_id,node("a"),node("b"),input,|now|clock.0.store(now,Ordering::SeqCst)).await;
}
#[tokio::test]
async fn takeover_between_completion_read_and_transaction_cannot_publish_stale_judge(){
 let (repo,clock,gate,req,input)=prepared(None).await;
 let a=repo.receive_effects(&scope(),&node("a"),None,10).await.unwrap().items.remove(0);
 gate.block.store(true,Ordering::SeqCst);
 let stale=tokio::spawn({let repo=repo.clone();let input=input.clone();async move{repo.complete_effect(&a.fence,input).await}});
 gate.entered.notified().await;clock.0.store(5000,Ordering::SeqCst);
 let b=repo.receive_effects(&scope(),&node("b"),None,10).await.unwrap().items.remove(0);
 gate.resume.notify_one();assert_eq!(stale.await.unwrap(),Err(RuntimeError::OwnershipLost));
 assert!(repo.find_input(&req.spec.session_id,&input.event_id).await.unwrap().is_none());
 let mut winner=input.clone();if let SessionInputKind::JudgeDecided{branch,..}=&mut winner.kind {*branch="no".into();}
 repo.complete_effect(&b.fence,winner.clone()).await.unwrap();
 assert_eq!(repo.find_input(&req.spec.session_id,&winner.event_id).await.unwrap(),Some(winner));
 assert!(repo.append_and_wake(&req.spec.session_id,input).await.is_err(),"unfenced Judge append is not an alternative completion path");
}
#[tokio::test]
async fn completion_rejects_wrong_judge_and_preserves_saved_result_barrier(){
 let (repo,_,_,_,mut input)=prepared(None).await;
 let leased=repo.receive_effects(&scope(),&node("a"),None,10).await.unwrap().items.remove(0);
 if let SessionInputKind::JudgeDecided{node_id,..}=&mut input.kind {*node_id="wrong".into();}
 assert!(repo.complete_effect(&leased.fence,input).await.is_err());
 let (_,_,req)=fixture();
 let intent=ResultIntent{result_id:ResultId::new(scope(),"saved"),task_id:req.task.task_id.clone(),node_id:None,attempt:0,status:TaskStatus::Completed,output:vec![MessagePart::Text{text:"canonical".into()}],error:None};
 let (repo,_,_,req,_)=prepared(Some(EffectIntent::SaveResult{intent:intent.clone()})).await;
 let leased=repo.receive_effects(&scope(),&node("a"),None,10).await.unwrap().items.remove(0);
 let saved=SavedResult{intent,saved_at_ms:1234};
 let input=SessionInput{event_id:EventId::new(scope(),"saved-input"),at_ms:1234,kind:SessionInputKind::ResultSaved{result:saved.clone()}};
 let mut forged=input.clone();if let SessionInputKind::ResultSaved{result}=&mut forged.kind {result.intent.output.clear();}
 assert!(repo.complete_effect(&leased.fence,forged).await.is_err());
 assert_eq!(repo.get_task(&req.caller,&req.task.task_id).await.unwrap().status,TaskStatus::Submitted);
 let cp=repo.load(&req.spec.session_id).await.unwrap().unwrap();
 let event=CommittedEventRef{session_id:req.spec.session_id.clone(),event_id:EventId::new(scope(),"completed")};
 assert!(repo.publish_hard_event(&cp.fence,event.clone()).await.is_err(),"terminal publication waits for saved receipt");
 let pos=repo.complete_effect(&leased.fence,input.clone()).await.unwrap();
 assert_eq!(repo.complete_effect(&leased.fence,input).await.unwrap(),pos);
 repo.publish_hard_event(&cp.fence,event).await.unwrap();
 assert_eq!(repo.get_task(&req.caller,&req.task.task_id).await.unwrap().output,saved.intent.output);
}
