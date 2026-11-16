mod common;
use common::*;
use async_trait::async_trait;
use bcn_runtime_model::*;
use bcn_runtime_app::*;
use bcn_runtime_api::{application::*,port::*};
use std::sync::Arc;
use tokio::sync::Semaphore;

struct PauseCatalog { entered:Arc<Semaphore>,release:Arc<Semaphore> }
#[async_trait]
impl AgentCatalog for PauseCatalog {
 async fn resolve(&self,c:&CallerContext,t:&ExecutionTarget,s:&SessionId)->RuntimeResult<SessionSpec>{
  if c.principal=="bob" {self.entered.add_permits(1);self.release.acquire().await.unwrap().forget();}
  Catalog.resolve(c,t,s).await
 }
}
#[tokio::test]
async fn concurrent_first_admission_does_not_share_cross_caller_context(){
 let f=Fixture::new();let entered=Arc::new(Semaphore::new(0));let release=Arc::new(Semaphore::new(0));
 let g=Arc::new(DefaultGateway::new(f.ports.clone(),f.runners(),Arc::new(PauseCatalog{entered:entered.clone(),release:release.clone()}),vec![Arc::new(Allow)],f.clock.clone(),true).unwrap());
 let mut bob=caller();bob.principal="bob".into();let mut b=request("bob-msg");b.message.context_id=Some(SessionId::new(scope(),"shared-context"));
 let task=tokio::spawn({let g=g.clone();async move{g.submit(bob,b).await}});
 entered.acquire().await.unwrap().forget();
 let mut a=request("alice-msg");a.message.context_id=Some(SessionId::new(scope(),"shared-context"));let alice=g.submit(caller(),a).await.unwrap();
 release.add_permits(1);let second=task.await.unwrap();
 assert!(matches!(second,Err(RuntimeError::AccessDenied)),"second caller was admitted into Alice's context: {:?}; first={:?}",second,alice.session_id);
}

#[tokio::test]
async fn same_caller_concurrent_first_messages_and_exact_retries_work() {
 let f=Fixture::new();let g=barrier_gateway(&f);
 let mut a=request("first");a.message.context_id=Some(SessionId::new(scope(),"shared"));
 let mut b=request("second");b.message.context_id=a.message.context_id.clone();
 let (one,two)=tokio::join!(g.submit(caller(),a.clone()),g.submit(caller(),b.clone()));
 let one=one.unwrap();let two=two.unwrap();assert_ne!(one.task_id,two.task_id);
 assert_eq!(g.submit(caller(),a).await.unwrap().task_id,one.task_id);
 assert_eq!(g.submit(caller(),b).await.unwrap().task_id,two.task_id);
 assert_eq!(f.ports.inbox.read_batch(&one.session_id,0,100).await.unwrap().len(),2);
}

struct BarrierCatalog(tokio::sync::Barrier);
#[async_trait]
impl AgentCatalog for BarrierCatalog {
 async fn resolve(&self,c:&CallerContext,t:&ExecutionTarget,s:&SessionId)->RuntimeResult<SessionSpec>{self.0.wait().await;Catalog.resolve(c,t,s).await}
}
fn barrier_gateway(f:&Fixture)->DefaultGateway {
 DefaultGateway::new(f.ports.clone(),f.runners(),Arc::new(BarrierCatalog(tokio::sync::Barrier::new(2))),vec![Arc::new(Allow)],f.clock.clone(),true).unwrap()
}
#[tokio::test]
async fn concurrent_identical_first_retry_creates_one_receipt_task_and_input() {
 let f=Fixture::new();let g=barrier_gateway(&f);let request=request("same-first-message");
 let (one,two)=tokio::join!(g.submit(caller(),request.clone()),g.submit(caller(),request));
 let one=one.unwrap();assert_eq!(one,two.unwrap());
 assert_eq!(f.ports.inbox.read_batch(&one.session_id,0,100).await.unwrap().len(),1);
}
