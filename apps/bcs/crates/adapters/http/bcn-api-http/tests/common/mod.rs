#![allow(dead_code)]
use async_trait::async_trait;
use bcn_api_http::*;
use bcn_runtime_api::application::*;
use bcn_runtime_model::*;
use axum::{Router, http::{HeaderMap, Request}, body::{Body, to_bytes}};
use std::sync::{Arc, Mutex, atomic::{AtomicUsize, Ordering}};
use serde_json::{Value, json};
use tower::ServiceExt;
pub fn scope() -> Scope { Scope::new("local", "tenant", "bcn-v1") }
pub fn target() -> ExecutionTarget { ExecutionTarget::Agent { agent_id: AgentId::new(scope(),"agent") } }
pub fn view() -> TaskView { TaskView { active_interactions: vec![], task_id: TaskId::new(scope(),"task"), session_id:SessionId::new(scope(),"context"),target:target(),status:TaskStatus::Submitted,output:vec![],error:None,created_at_ms:0,updated_at_ms:0 } }
pub struct Auth;
#[async_trait]
impl Authenticator for Auth {
 async fn authenticate(&self, h:&HeaderMap)->RuntimeResult<CallerContext> {
  if h.get("authorization").and_then(|v|v.to_str().ok())!=Some("Bearer local") { return Err(RuntimeError::AccessDenied) }
  Ok(CallerContext {scope:scope(), principal:"alice".into(),trace_id:None})
 }
}
pub struct Gateway {pub submits:AtomicUsize,pub task:Mutex<TaskView>,pub requests:Mutex<Vec<SubmitRequest>>,pub terminal:Mutex<Option<TaskView>>,pub events:Mutex<Option<Vec<TaskEvent>>>}
#[async_trait]
impl GatewayService for Gateway {
 async fn submit(&self,_:CallerContext,r:SubmitRequest)->RuntimeResult<TaskView>{ self.submits.fetch_add(1,Ordering::SeqCst);self.requests.lock().unwrap().push(r);Ok(self.task.lock().unwrap().clone()) }
 async fn get(&self,_:CallerContext,_:TaskId)->RuntimeResult<TaskView>{Ok(if self.events.lock().unwrap().is_some(){self.task.lock().unwrap().clone()}else{self.terminal.lock().unwrap().clone().unwrap_or_else(||self.task.lock().unwrap().clone())})}
 async fn list(&self,_:CallerContext,_:TaskQuery)->RuntimeResult<TaskPage>{Ok(TaskPage{total_size:42,items:vec![self.task.lock().unwrap().clone()],next_cursor:None})}
 async fn cancel(&self,_:CallerContext,_:CancelCommand)->RuntimeResult<TaskView>{Ok(self.task.lock().unwrap().clone())}
 async fn subscribe(&self,_:CallerContext,_:TaskId,_:Option<EventCursor>)->RuntimeResult<EventStream>{
  let snapshot=self.task.lock().unwrap().clone();
  if snapshot.status.is_terminal(){return Err(RuntimeError::Conflict("terminal".into()))}
  if let Some(events)=self.events.lock().unwrap().take(){return Ok(Box::pin(futures::stream::iter(events.into_iter().map(Ok))))}
  Ok(Box::pin(futures::stream::iter(vec![Ok(TaskEvent{id:EventId::new(scope(),"event"),task_id:snapshot.task_id.clone(),cursor:EventCursor{epoch:1,sequence:0},kind:TaskEventKind::Reset{snapshot}})])))
 }
}
pub fn app()->(Router,Arc<Gateway>){
 let gateway=Arc::new(Gateway{submits:AtomicUsize::new(0),task:Mutex::new(view()),requests:Mutex::new(vec![]),terminal:Mutex::new(None),events:Mutex::new(None)});
 let card: a2a::AgentCard=serde_json::from_value(json!({"name":"BCN","version":"1","supportedInterfaces":[],"capabilities":{"streaming":true,"extendedAgentCard":true},"defaultInputModes":["text/plain"],"defaultOutputModes":["text/plain"],"skills":[{"id":"s","name":"s","description":"s","tags":[],"inputModes":["image/png"],"outputModes":["image/png"]}]})).unwrap();
 (router(ApiState{gateway:gateway.clone(),auth:Arc::new(Auth),target:target(),card}),gateway)
}
pub fn send()->Value{json!({"message":{"messageId":"m","role":"ROLE_USER","parts":[{"text":"hello"}]},"configuration":{"returnImmediately":true}})}
pub async fn call(app:Router,method:&str,params:Value,version:Option<&str>,auth:bool)->(u16,Value){
 let mut req=Request::builder().method("POST").uri("/").header("content-type","application/json");
 if let Some(v)=version {req=req.header("a2a-version",v)}
 if auth {req=req.header("authorization","Bearer local")}
 let response=app.oneshot(req.body(Body::from(json!({"jsonrpc":"2.0","id":1,"method":method,"params":params}).to_string())).unwrap()).await.unwrap();
 let status=response.status().as_u16();let bytes=to_bytes(response.into_body(),2*1024*1024).await.unwrap();
 (status,serde_json::from_slice(&bytes).unwrap_or_else(|_|json!({"raw":String::from_utf8_lossy(&bytes)})))
}
