mod common;
use common::*;
use async_trait::async_trait;
use bcn_runtime_model::*;
use bcn_runtime_app::*;
use bcn_runtime_api::{application::*,port::*};
use std::sync::{Arc,atomic::Ordering};
struct NoContinueCatalog;
#[async_trait]
impl AgentCatalog for NoContinueCatalog {async fn resolve(&self,c:&CallerContext,t:&ExecutionTarget,s:&SessionId)->RuntimeResult<SessionSpec>{let mut spec=Catalog.resolve(c,t,s).await?;spec.bindings.get_mut("agent").unwrap().capabilities.input_continuation=false;Ok(spec)}}
struct Ask;
#[async_trait]
impl AgentConnector for Ask {
 fn capabilities(&self)->ConnectorCapabilities {let mut c=binding().capabilities;c.input_continuation=false;c}
 async fn send_streaming_message(&self,_:AgentSendRequest)->ConnectorResult<AgentEventStream>{Ok(Box::pin(futures::stream::iter(vec![Ok(AgentEvent::InputRequired{handle:handle(),prompt:text("answer?")})]).chain(futures::stream::pending())))}
}
use futures::StreamExt;

async fn waiting(f:&Fixture)->(DefaultGateway,DefaultWorker,DefaultConnector,TaskView) {
 let g=DefaultGateway::new(f.ports.clone(),f.runners(),Arc::new(NoContinueCatalog),vec![Arc::new(Allow)],f.clock.clone(),true).unwrap();let w=f.worker("w");let mut b=binding();b.capabilities.input_continuation=false;
 let c=DefaultConnector::new(node("c"),f.ports.clone(),vec![(b,Arc::new(Ask))],f.clock.clone()).unwrap();
 let task=g.submit(caller(),request("ask")).await.unwrap();w.step(&task.session_id).await.unwrap();f.pump(&w,&c,4).await;
 (g,w,c,task)
}
#[tokio::test]
async fn unsupported_reply_is_rejected_without_inbox_append() {
 let f=Fixture::new();let (g,w,_c,task)=waiting(&f).await;
 let before=f.ports.inbox.read_batch(&task.session_id,0,100).await.unwrap();
 let mut reply=request("reply");reply.message.task_id=Some(task.task_id.clone());
 assert!(matches!(g.submit(caller(),reply).await,Err(RuntimeError::Unsupported(_))));
 assert_eq!(f.ports.inbox.read_batch(&task.session_id,0,100).await.unwrap(),before);
 assert!(w.step(&task.session_id).await.is_ok());
 let unrelated=g.submit(caller(),request("other-session")).await.unwrap();assert!(w.step(&unrelated.session_id).await.is_ok());
}
#[tokio::test]
async fn already_durable_unsupported_reply_finishes_locally_after_restart_and_saved_result() {
 let f=Fixture::new();let (g,w,c,task)=waiting(&f).await;
 let cp=f.ports.sessions.load(&task.session_id).await.unwrap().unwrap();
 let interaction=cp.state.current_task.as_ref().unwrap().nodes["direct"].interaction_id.clone().unwrap();
 let mut reply=request("legacy-reply").message;reply.task_id=Some(task.task_id.clone());reply.context_id=Some(task.session_id.clone());
 let pos=f.ports.inbox.append_and_wake(&task.session_id,SessionInput{event_id:EventId::new(scope(),"legacy-reply"),at_ms:2000,kind:SessionInputKind::HumanReply{task_id:task.task_id.clone(),interaction_id:interaction,caller:caller(),message:reply}}).await.unwrap();
 drop(w);let restarted=f.worker("w");f.results.down.store(true,Ordering::SeqCst);
 assert!(restarted.step(&task.session_id).await.is_ok(),"durable unsupported input must not crash Worker");
 let cp=f.ports.sessions.load(&task.session_id).await.unwrap().unwrap();assert_eq!(cp.consumed_input,pos);
 assert!(cp.state.context_blocked,"unsupported continuation does not prove remote termination");
 assert_eq!(cp.state.current_task.as_ref().unwrap().nodes["direct"].handle,Some(handle()));
 assert!(cp.state.current_task.as_ref().unwrap().nodes["direct"].result_intent.is_some());
 assert!(!g.get(caller(),task.task_id.clone()).await.unwrap().status.is_terminal(),"must wait for authoritative save");
 let unrelated=g.submit(caller(),request("unrelated")).await.unwrap();assert!(restarted.step(&unrelated.session_id).await.is_ok());
 drop(restarted);f.results.down.store(false,Ordering::SeqCst);let restarted=f.worker("w");
 for _ in 0..20 {restarted.step(&task.session_id).await.unwrap();f.pump(&restarted,&c,1).await;}
 let result=g.get(caller(),task.task_id.clone()).await.unwrap();assert_eq!(result.status,TaskStatus::Failed);assert!(matches!(result.error,Some(RuntimeError::Unsupported(_))));
 let saved=f.ports.results.read_group_records(&task.task_id,None,100).await.unwrap();assert!(saved.items.iter().any(|r|r.intent.status==TaskStatus::Failed));
 assert!(restarted.step(&unrelated.session_id).await.is_ok());
}
