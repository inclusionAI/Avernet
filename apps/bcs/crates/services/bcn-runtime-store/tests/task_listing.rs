mod common;
use common::*;
use bcn_runtime_api::port::repo::*;
use bcn_runtime_model::*;
fn request(template:&AdmissionRequest,id:&str,time:u64)->AdmissionRequest{
 let mut r=template.clone();r.admission_id.value=id.into();r.spec.session_id.value=id.into();r.task.task_id.value=id.into();r.task.session_id=r.spec.session_id.clone();r.input.event_id.value=id.into();r.task.updated_at_ms=time;
 if let SessionInputKind::Submit{task}=&mut r.input.kind{task.task_id=r.task.task_id.clone();task.message.context_id=Some(r.spec.session_id.clone());}r
}
#[tokio::test]
async fn pages_are_newest_first_with_stable_timestamp_ties(){
 let (repo,_,template)=fixture();
 for (id,time) in [("one",100),("two",300),("three",300)]{repo.admit(request(&template,id,time)).await.unwrap();}
 let mut cursor=None;let mut views=vec![];
 for _ in 0..3 {let p=TaskQueryStore::list_tasks(&repo,&template.caller,TaskQuery{target:Some(template.task.target.clone()),session_id:None,cursor,limit:1}).await.unwrap();assert_eq!(p.total_size,3);views.extend(p.items);cursor=p.next_cursor;}
 assert_eq!(views.iter().map(|v|v.updated_at_ms).collect::<Vec<_>>(),vec![300,300,100]);
 assert_ne!(views[0].task_id,views[1].task_id);assert!(cursor.is_none());
}
fn query(target:Option<ExecutionTarget>,session_id:Option<SessionId>,cursor:Option<String>)->TaskQuery{TaskQuery{target,session_id,cursor,limit:1}}
#[tokio::test]
async fn totals_are_scoped_and_receipt_retry_never_increments(){
 let (repo,_,template)=fixture();
 let a=request(&template,"a",100);repo.admit(a.clone()).await.unwrap();repo.admit(a.clone()).await.unwrap();
 let mut b=request(&template,"b",200);b.spec=a.spec.clone();b.task.session_id=a.spec.session_id.clone();repo.admit(b).await.unwrap();
 let mut other=request(&template,"other-target",300);other.task.target=ExecutionTarget::Agent{agent_id:AgentId::new(scope(),"other")};other.spec.target=other.task.target.clone();repo.admit(other.clone()).await.unwrap();
 let mut stranger=request(&template,"bob",400);stranger.caller.principal="bob".into();repo.admit(stranger.clone()).await.unwrap();
 for (target,session,total) in [(None,None,3),(Some(a.task.target.clone()),None,2),(None,Some(a.task.session_id.clone()),2),(Some(other.task.target),Some(a.task.session_id),0)] {
  assert_eq!(TaskQueryStore::list_tasks(&repo,&template.caller,query(target,session,None)).await.unwrap().total_size,total);
 }
 assert_eq!(TaskQueryStore::list_tasks(&repo,&stranger.caller,query(None,None,None)).await.unwrap().total_size,1);
 let mut outsider=template.caller;outsider.scope.tenant="unrelated".into();
 assert_eq!(TaskQueryStore::list_tasks(&repo,&outsider,query(None,None,None)).await.unwrap().total_size,0);
}
#[tokio::test]
async fn hard_and_soft_publication_invalidate_cursors_but_exact_retries_do_not(){
 let (repo,_,template)=fixture();let req=request(&template,"active",100);let cp=submitted(&repo,&req).await;
 repo.admit(request(&template,"peer",50)).await.unwrap();
 let list=||query(None,None,None);
 let old=TaskQueryStore::list_tasks(&repo,&req.caller,list()).await.unwrap();
 let reference=CommittedEventRef{session_id:req.spec.session_id.clone(),event_id:cp.pending_events[0].event_id.clone()};
 let cursor=repo.publish_hard_event(&cp.fence,reference.clone()).await.unwrap();
 assert!(TaskQueryStore::list_tasks(&repo,&req.caller,query(None,None,old.next_cursor)).await.is_err());
 let fresh=TaskQueryStore::list_tasks(&repo,&req.caller,list()).await.unwrap();assert_eq!(fresh.total_size,2);assert_eq!(fresh.items[0].updated_at_ms,1000);
 assert_eq!(repo.publish_hard_event(&cp.fence,reference).await.unwrap(),cursor);
 assert_eq!(TaskQueryStore::list_tasks(&repo,&req.caller,query(None,None,fresh.next_cursor.clone())).await.unwrap().items.len(),1);
 let id=effect_command_id(&req.spec.session_id,&cp.pending_effects[0]).unwrap();
 repo.publish(&cp.fence,CommittedEffectRef{session_id:req.spec.session_id.clone(),command_id:id.clone()}).await.unwrap();
 let d=repo.claim(&id,node("connector")).await.unwrap();let d=repo.mark_may_have_sent(&d.fence,d.revision).await.unwrap();
 let projection=ConnectorProjection{task_id:req.task.task_id.clone(),output:vec![MessagePart::Text{text:"live".into()}],expected_cursor:cursor};let event=EventId::new(scope(),"soft");
 let c=repo.publish_reset(&d.fence,projection.clone(),event.clone()).await.unwrap();
 assert!(TaskQueryStore::list_tasks(&repo,&req.caller,query(None,None,fresh.next_cursor)).await.is_err());
 let fresh=TaskQueryStore::list_tasks(&repo,&req.caller,list()).await.unwrap();
 assert_eq!(repo.publish_reset(&d.fence,projection,event).await.unwrap(),c);
 assert_eq!(TaskQueryStore::list_tasks(&repo,&req.caller,query(None,None,fresh.next_cursor)).await.unwrap().total_size,2);
}
#[tokio::test]
async fn unrepresentable_timestamp_is_rejected_before_admission(){
 let (repo,_,req)=fixture();let bad=request(&req,"future",u64::MAX);
 assert!(repo.admit(bad.clone()).await.is_err());assert!(repo.get_receipt(&bad.admission_id).await.unwrap().is_none());
 assert_eq!(TaskQueryStore::list_tasks(&repo,&req.caller,query(None,None,None)).await.unwrap().total_size,0);
}
#[tokio::test]
async fn invalid_publication_clock_leaves_snapshot_index_and_pending_event_unchanged(){
 use std::sync::{Arc,atomic::{AtomicU64,Ordering}};
 struct Clock(AtomicU64);impl bcn_runtime_storage_api::ServerClock for Clock{fn now_ms(&self)->u64{self.0.load(Ordering::SeqCst)}}
 let clock=Arc::new(Clock(AtomicU64::new(1000)));let atomic=Arc::new(bcn_storage_memory::MemoryAtomicStore::new(RuntimeProfile::Test,clock.clone()).unwrap());
 let repo=bcn_runtime_store::RuntimeStore::new(atomic);let (_,_,req)=fixture();let cp=submitted(&repo,&req).await;
 clock.0.store(u64::MAX,Ordering::SeqCst);
 assert!(repo.publish_hard_event(&cp.fence,CommittedEventRef{session_id:req.spec.session_id.clone(),event_id:cp.pending_events[0].event_id.clone()}).await.is_err());
 assert_eq!(repo.read_snapshot(&req.task.task_id).await.unwrap().unwrap().task,req.task);
 assert_eq!(repo.load(&req.spec.session_id).await.unwrap().unwrap().pending_events,cp.pending_events);
 assert_eq!(TaskQueryStore::list_tasks(&repo,&req.caller,query(None,None,None)).await.unwrap().total_size,1);
}
#[path="common/observed.rs"] mod observed;
#[tokio::test]
async fn index_mutations_share_publication_transaction_and_have_fixed_cost(){
 use std::sync::atomic::Ordering;
 let (_,atomic,template)=fixture();let observed=observed::Observed::new(atomic);let repo=bcn_runtime_store::RuntimeStore::new(observed.clone());
 let req=request(&template,"active",100);let cp=submitted(&repo,&req).await;
 repo.admit(request(&template,"peer",50)).await.unwrap();
 let page=TaskQueryStore::list_tasks(&repo,&req.caller,query(None,None,None)).await.unwrap();
 let reference=CommittedEventRef{session_id:req.spec.session_id.clone(),event_id:cp.pending_events[0].event_id.clone()};
 observed.fail_write.store(true,Ordering::SeqCst);
 assert!(repo.publish_hard_event(&cp.fence,reference.clone()).await.is_err());
 assert_eq!(repo.read_snapshot(&req.task.task_id).await.unwrap().unwrap().task,req.task);
 assert_eq!(TaskQueryStore::list_tasks(&repo,&req.caller,query(None,None,page.next_cursor)).await.unwrap().total_size,2);
 observed.reset();let cursor=repo.publish_hard_event(&cp.fence,reference).await.unwrap();
 {let c=observed.counts.lock().unwrap();assert_eq!(c.reads,vec![1,1,1,1,1,1,4,1]);assert_eq!(c.writes,vec![(8,16)]);assert!(c.scans.is_empty());assert_eq!(c.clocks,1);}
 let id=effect_command_id(&req.spec.session_id,&cp.pending_effects[0]).unwrap();repo.publish(&cp.fence,CommittedEffectRef{session_id:req.spec.session_id.clone(),command_id:id.clone()}).await.unwrap();
 let d=repo.claim(&id,node("connector")).await.unwrap();let d=repo.mark_may_have_sent(&d.fence,d.revision).await.unwrap();
 observed.reset();repo.publish_reset(&d.fence,ConnectorProjection{task_id:req.task.task_id,output:vec![MessagePart::Text{text:"soft".into()}],expected_cursor:cursor},EventId::new(scope(),"soft")).await.unwrap();
 {let c=observed.counts.lock().unwrap();assert_eq!(c.reads,vec![1,1,1,1,1,1,4]);assert_eq!(c.writes,vec![(8,12)]);assert_eq!(c.scans,vec![33]);assert_eq!(c.clocks,1);}
}
