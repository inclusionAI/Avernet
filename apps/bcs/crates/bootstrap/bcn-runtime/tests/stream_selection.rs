mod common;
use common::*;
use bcn_runtime::*;
use bcn_runtime_api::{application::*,port::*};
use bcn_runtime_model::*;
use async_trait::async_trait;
use std::{sync::{Arc,Mutex},collections::HashMap};
use futures::{channel::mpsc::{unbounded,UnboundedSender},StreamExt};
use serde_json::json;
struct Catalog;
#[async_trait]
impl AgentCatalog for Catalog {
 async fn resolve(&self,c:&CallerContext,t:&ExecutionTarget,s:&SessionId)->RuntimeResult<SessionSpec>{
  let mut spec=common::Catalog.resolve(c,t,s).await?;
  spec.definition=Some(serde_json::from_value(json!({"nodes":{
   "pre":{"kind":"bot_task","display_name":"Prep","instruction":"pre","assignee":{"type":"bot_binding","binding":"echo"},"transitions":{"complete":{"targets":["final"]}}},
   "final":{"kind":"bot_task","display_name":"Summary","instruction":"final","assignee":{"type":"bot_binding","binding":"echo"},"final_output":true,"transitions":{}}
  }})).unwrap());Ok(spec)
 }
}
#[derive(Default)]
struct Controlled(Mutex<HashMap<String,UnboundedSender<ConnectorResult<AgentEvent>>>>);
#[async_trait]
impl AgentConnector for Controlled{
 fn capabilities(&self)->ConnectorCapabilities{binding().capabilities}
 async fn send_streaming_message(&self,r:AgentSendRequest)->ConnectorResult<AgentEventStream>{
  let text=r.message.parts.iter().find_map(|p|match p{MessagePart::Text{text} if text=="pre"||text=="final"=>Some(text),_=>None}).unwrap();
  let (tx,rx)=unbounded();self.0.lock().unwrap().insert(text.clone(),tx);Ok(Box::pin(rx))
 }
}
impl Controlled{fn emit(&self,node:&str,event:AgentEvent){self.0.lock().unwrap()[node].unbounded_send(Ok(event)).unwrap();}}
async fn pump(r:&Runtime,s:&SessionId){for _ in 0..12{r.worker.step(s).await.unwrap();r.connector.tick().await.unwrap();r.worker.tick().await.unwrap();}}
#[tokio::test]
async fn builder_freezes_final_tokens_only_but_retains_intermediate_saved_results(){
 let mut cfg=config();let ports=memory_ports(&cfg).unwrap();cfg.storage=StorageSelection::External;
 let control=Arc::new(Controlled::default());let mut components=components();components.catalog=Arc::new(Catalog);components.connectors=vec![(binding(),control.clone())];
 let r=build(cfg,components,Some(ports.clone())).unwrap();
 let task=r.gateway.submit(caller(),request(true,"selected")).await.unwrap();pump(&r,&task.session_id).await;
 control.emit("pre",AgentEvent::Delta{text:"private intermediate".into()});pump(&r,&task.session_id).await;tokio::time::sleep(std::time::Duration::from_millis(220)).await;pump(&r,&task.session_id).await;
 assert!(r.gateway.get(caller(),task.task_id.clone()).await.unwrap().output.is_empty(),"intermediate tokens must be hidden by new runtime default");
 let mut fresh=r.gateway.subscribe(caller(),task.task_id.clone(),None).await.unwrap();
 let TaskEventKind::Reset{snapshot}=fresh.next().await.unwrap().unwrap().kind else{panic!("snapshot")};assert!(snapshot.output.is_empty());
 control.emit("pre",AgentEvent::Completed{output:vec![MessagePart::Text{text:"retained intermediate result".into()}]});pump(&r,&task.session_id).await;
 control.emit("final",AgentEvent::Delta{text:"visible summary tokens".into()});pump(&r,&task.session_id).await;tokio::time::sleep(std::time::Duration::from_millis(220)).await;pump(&r,&task.session_id).await;
 let current=r.gateway.get(caller(),task.task_id.clone()).await.unwrap();assert_eq!(current.output,vec![MessagePart::Text{text:"visible summary tokens".into()}]);
 let mut resumed=r.gateway.subscribe(caller(),task.task_id.clone(),Some(EventCursor{epoch:0,sequence:0})).await.unwrap();
 let TaskEventKind::Reset{snapshot}=resumed.next().await.unwrap().unwrap().kind else{panic!("expired cursor resets")};assert_eq!(snapshot.output,current.output);
 control.emit("final",AgentEvent::Completed{output:vec![MessagePart::Text{text:"saved final summary".into()}]});pump(&r,&task.session_id).await;
 let final_task=r.gateway.get(caller(),task.task_id.clone()).await.unwrap();assert_eq!(final_task.status,TaskStatus::Completed);assert_eq!(final_task.output,vec![MessagePart::Text{text:"saved final summary".into()}]);
 let records=ports.results.read_group_records(&task.task_id,None,100).await.unwrap();
 assert!(records.items.iter().any(|r|r.intent.node_id.as_deref()==Some("pre")&&r.intent.output==vec![MessagePart::Text{text:"retained intermediate result".into()}]));
}
