//! Actual transport/application seam, with bounded local fixtures (no live agents).
#[path="../../../application/bcn-runtime-app/tests/common/mod.rs"] mod common;
#[path="../../../external-clients/bcn-agent-connectors/tests/common/mod.rs"] mod wire;
use common::*;
use bcn_runtime_api::{application::*,port::*};
use bcn_runtime_model::*;
use std::sync::{Arc,Mutex};
use serde_json::{json,Value};
use axum::{http::{Method,Uri},body::Bytes,response::IntoResponse};

struct RecoveryCatalog(bool);
#[async_trait::async_trait]
impl AgentCatalog for RecoveryCatalog {
 async fn resolve(&self,c:&CallerContext,t:&ExecutionTarget,s:&SessionId)->RuntimeResult<SessionSpec>{
  let mut spec=Catalog.resolve(c,t,s).await?;spec.bindings.get_mut("agent").unwrap().capabilities.query=self.0;Ok(spec)
 }
}
async fn recovery(protocol:&str,reset:bool,query:bool) {
 let f=Fixture::new();let g=bcn_runtime_app::DefaultGateway::new(f.ports.clone(),f.runners(),Arc::new(RecoveryCatalog(query)),vec![Arc::new(Allow)],f.clock.clone(),true).unwrap();let w=f.worker("w");
 let calls=Arc::new(Mutex::new(Vec::<String>::new()));let server_calls=calls.clone();let rpc=protocol=="JSONRPC";
 let handler=move |method:Method,uri:Uri,body:Bytes|{let calls=server_calls.clone();async move{
  let req:Value=serde_json::from_slice(&body).unwrap_or(Value::Null);
  let op=if rpc {req["method"].as_str().unwrap().to_owned()} else {format!("{method} {}",uri.path())};
  calls.lock().unwrap().push(op.clone());
  let completed=json!({"id":"remote","contextId":"conversation","status":{"state":"TASK_STATE_COMPLETED"},"artifacts":[{"artifactId":"a","parts":[{"text":"query recovered result"}]}]});
  if op=="GetTask" || method==Method::GET {
   return axum::Json(if rpc {json!({"jsonrpc":"2.0","id":req["id"],"result":completed})}else{completed}).into_response();
  }
  if op=="SubscribeToTask" || uri.path().ends_with(":subscribe") {
   let completed=json!({"task":completed});
   let event=if rpc {json!({"jsonrpc":"2.0","id":req["id"],"result":completed})}else{completed};
   return axum::response::Response::builder().header("content-type","text/event-stream").body(axum::body::Body::from(format!("data: {event}\n\n"))).unwrap();
  }
  let working=json!({"task":{"id":"remote","contextId":"conversation","status":{"state":"TASK_STATE_WORKING"},"artifacts":[{"artifactId":"a","parts":[{"text":"partial"}]}]}});
  let event=if rpc {json!({"jsonrpc":"2.0","id":req["id"],"result":working})}else{working};
  let data=format!("data: {event}\n\n");
  let stream=futures::stream::unfold(0,move|n|{let data=data.clone();async move{
   match n {0=>Some((Ok::<_,std::io::Error>(data.into_bytes()),1)),1 if reset=>{tokio::time::sleep(std::time::Duration::from_millis(30)).await;Some((Err(std::io::Error::other("fixture connection reset")),2))},_=>None}
  }});
  axum::response::Response::builder().header("content-type","text/event-stream").body(axum::body::Body::from_stream(stream)).unwrap()
 }};
 let listener=tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();let url=format!("http://{}",listener.local_addr().unwrap());
 let server=tokio::spawn(async move{axum::serve(listener,axum::Router::new().fallback(axum::routing::any(handler))).await.unwrap();});
 let mut b=binding();b.capabilities.query=query;
 let transport=Arc::new(bcn_agent_connectors::a2a::A2aConnector::new(b.clone(),wire::card(url,protocol),wire::http()).unwrap());
 let c=bcn_runtime_app::DefaultConnector::new(node("c"),f.ports.clone(),vec![(b,transport)],f.clock.clone()).unwrap();let task=g.submit(caller(),request("eof")).await.unwrap();w.step(&task.session_id).await.unwrap();
 let mut observed_partial=false;
 for _ in 0..220 {
  c.tick().await.unwrap();tokio::time::sleep(std::time::Duration::from_millis(5)).await;w.step(&task.session_id).await.unwrap();w.tick().await.unwrap();
  let view=g.get(caller(),task.task_id.clone()).await.unwrap();
  observed_partial|=view.output==text("partial") && !view.status.is_terminal();
  f.clock.set(Clock::now_ms(f.clock.as_ref())+50);if view.status.is_terminal(){break;}
 }
 let result=g.get(caller(),task.task_id.clone()).await.unwrap();server.abort();
 assert_eq!(result.status,TaskStatus::Completed,"{protocol} reset={reset}: {result:?}; calls={:?}",calls.lock().unwrap());
 assert_eq!(result.output,text("query recovered result"));assert!(observed_partial);
 let calls=calls.lock().unwrap();
 assert_eq!(calls.iter().filter(|s|s.as_str()=="SendStreamingMessage"||s.as_str()=="POST /message:stream").count(),1,"{calls:?}");
 assert!(calls.iter().any(|s|if query{s=="GetTask"||s=="GET /tasks/remote"}else{s=="SubscribeToTask"||s=="POST /tasks/remote:subscribe"}),"{calls:?}");
 assert!(!calls.iter().any(|s|s.to_lowercase().contains("cancel")),"{calls:?}");
 drop(calls);
 let saved=f.ports.results.read_group_records(&task.task_id,None,100).await.unwrap();
 assert!(saved.items.iter().any(|r|r.intent.status==TaskStatus::Completed && r.intent.output==text("query recovered result")));
}
#[tokio::test] async fn http_eof_recovers_known_handle(){recovery("HTTP+JSON",false,true).await;}
#[tokio::test] async fn http_reset_recovers_known_handle(){recovery("HTTP+JSON",true,true).await;}
#[tokio::test] async fn jsonrpc_eof_recovers_known_handle(){recovery("JSONRPC",false,true).await;}
#[tokio::test] async fn jsonrpc_reset_recovers_known_handle(){recovery("JSONRPC",true,true).await;}

#[tokio::test] async fn http_eof_recovers_by_subscription(){recovery("HTTP+JSON",false,false).await;}
#[tokio::test] async fn jsonrpc_eof_recovers_by_subscription(){recovery("JSONRPC",false,false).await;}
