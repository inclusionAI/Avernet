mod common;
use common::*;
use async_trait::async_trait;
use bcn_runtime_api::{application::*,port::{AccessAction,AccessPolicy,repo::TaskQueryStore}};
use bcn_runtime_app::DefaultGateway;
use bcn_runtime_model::*;
use std::sync::{Arc,atomic::{AtomicUsize,Ordering}};
struct Queries(AtomicUsize);
#[async_trait]
impl TaskQueryStore for Queries {
 async fn get_task(&self,_:&CallerContext,_:&TaskId)->RuntimeResult<TaskView>{unreachable!()}
 async fn list_tasks(&self,_:&CallerContext,_:TaskQuery)->RuntimeResult<TaskPage>{self.0.fetch_add(1,Ordering::SeqCst);Ok(TaskPage{items:vec![],next_cursor:None,total_size:42})}
}
struct Policy(bool);
#[async_trait]
impl AccessPolicy for Policy {
 async fn authorize(&self,_:&CallerContext,_:AccessAction,_:&ExecutionTarget,_:Option<&SessionId>,_:Option<&TaskId>)->RuntimeResult<()>{if self.0{Ok(())}else{Err(RuntimeError::AccessDenied)}}
}
#[tokio::test]
async fn collection_denial_and_targetless_default_precede_any_count_read(){
 let f=Fixture::new();let queries=Arc::new(Queries(AtomicUsize::new(0)));let mut ports=f.ports.clone();ports.queries=queries.clone();
 for allow in [true,false]{
  let g=DefaultGateway::new(ports.clone(),f.runners(),Arc::new(Catalog),vec![Arc::new(Policy(allow))],f.clock.clone(),true).unwrap();
  let q=TaskQuery{target:None,session_id:None,cursor:None,limit:1};
  assert!(matches!(g.list(caller(),q).await,Err(RuntimeError::Unsupported(_))));
  if !allow {assert_eq!(g.list(caller(),TaskQuery{target:Some(request("target").target),session_id:None,cursor:None,limit:1}).await,Err(RuntimeError::AccessDenied));}
 }
 assert_eq!(queries.0.load(Ordering::SeqCst),0);
 let g=DefaultGateway::new(ports,f.runners(),Arc::new(Catalog),vec![Arc::new(Policy(true))],f.clock.clone(),true).unwrap();
 assert_eq!(g.list(caller(),TaskQuery{target:Some(request("target").target),session_id:None,cursor:None,limit:1}).await.unwrap().total_size,42);
 assert_eq!(queries.0.load(Ordering::SeqCst),1);
}
struct DenyTask;
#[async_trait]
impl AccessPolicy for DenyTask {
 async fn authorize(&self,_:&CallerContext,_:AccessAction,_:&ExecutionTarget,_:Option<&SessionId>,task:Option<&TaskId>)->RuntimeResult<()>{if task.is_some(){Err(RuntimeError::AccessDenied)}else{Ok(())}}
}
#[tokio::test]
async fn page_task_authorization_still_fails_closed(){
 let f=Fixture::new();let task=f.gateway().submit(caller(),request("private")).await.unwrap();
 let g=DefaultGateway::new(f.ports.clone(),f.runners(),Arc::new(Catalog),vec![Arc::new(DenyTask)],f.clock.clone(),true).unwrap();
 assert_eq!(g.list(caller(),TaskQuery{target:Some(task.target),session_id:None,cursor:None,limit:1}).await,Err(RuntimeError::AccessDenied));
}
