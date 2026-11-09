#[path="../../../../application/bcn-runtime-app/tests/common/mod.rs"]
mod runtime;
mod common;
use runtime::{Fixture,caller,group};
use bcn_runtime_api::application::*;
use bcn_runtime_model::*;
use bcn_api_http::*;
use axum::{Router,body::{Body,to_bytes},http::Request};
use http_body_util::BodyExt;
use std::sync::Arc;
use serde_json::{json,Value};
use tower::ServiceExt;
struct Auth;
#[async_trait::async_trait]
impl Authenticator for Auth{async fn authenticate(&self,_:&axum::http::HeaderMap)->RuntimeResult<CallerContext>{Ok(caller())}}
fn app(g:Arc<dyn GatewayService>,target:ExecutionTarget)->Router{
 let card=serde_json::from_value(json!({"name":"Local","version":"1","supportedInterfaces":[],"capabilities":{},"skills":[]})).unwrap();
 router(ApiState{gateway:g,auth:Arc::new(Auth),target,card})
}
async fn response(app:Router,method:&str,params:Value,cursor:Option<&str>)->axum::response::Response{
 let mut req=Request::builder().method("POST").uri("/").header("A2A-Version","1.0").header("content-type","application/json");
 if let Some(c)=cursor{req=req.header("last-event-id",c)}
 app.oneshot(req.body(Body::from(json!({"jsonrpc":"2.0","id":3,"method":method,"params":params}).to_string())).unwrap()).await.unwrap()
}
async fn json_response(app:Router,method:&str,params:Value)->Value{
 serde_json::from_slice(&to_bytes(response(app,method,params,None).await.into_body(),2*1024*1024).await.unwrap()).unwrap()
}
async fn first_event(mut body:Body)->Value{
 let bytes=body.frame().await.unwrap().unwrap().into_data().unwrap();
 let text=std::str::from_utf8(&bytes).unwrap();
 serde_json::from_str(text.lines().find_map(|l|l.strip_prefix("data: ")).unwrap()).unwrap()
}
#[tokio::test]
async fn two_waits_specific_reply_and_late_get_subscribe_preserve_remaining_question(){
 let f=Fixture::new();let g=Arc::new(group::gateway(&f,json!({"a":group::human(),"b":group::human()})));let w=f.worker("w");
 let task=group::start_parallel(&f,&g,&w,group::group_request("wire-questions")).await;
 let app=app(g.clone(),task.target.clone());let params=json!({"id":task.task_id.value});
 let first=json_response(app.clone(),"GetTask",params.clone()).await;
 let questions=first["result"]["status"]["message"]["parts"].as_array().unwrap();assert_eq!(questions.len(),2);
 let chosen=questions[0]["metadata"][mapping::INTERACTION].clone();
 let reply=json!({"message":{"messageId":"answer","role":"ROLE_USER","taskId":task.task_id.value,"contextId":task.session_id.value,"parts":[{"text":"yes"}],"metadata":{mapping::INTERACTION:chosen}},"configuration":{"returnImmediately":true}});
 assert!(json_response(app.clone(),"SendMessage",reply).await.get("result").is_some());
 for _ in 0..4{w.step(&task.session_id).await.unwrap();w.tick().await.unwrap();}
 let next=json_response(app.clone(),"GetTask",params.clone()).await;
 assert_eq!(next["result"]["status"]["message"]["parts"],json!([questions[1].clone()]));
 let snapshot=first_event(response(app,"SubscribeToTask",params,None).await.into_body()).await;
 assert_eq!(snapshot["result"]["task"],next["result"]);
}
#[tokio::test]
async fn initial_snapshot_precedes_progress_and_expired_cursor_recovers_authoritative_terminal_artifact(){
 let f=Fixture::new();let g=Arc::new(f.gateway());let w=f.worker("w");
 let task=g.submit(caller(),runtime::request("stream")).await.unwrap();
 let app=app(g.clone(),task.target.clone());
 let response=response(app.clone(),"SubscribeToTask",json!({"id":task.task_id.value}),None).await;
 let mut body=response.into_body();
 let frame=body.frame().await.unwrap().unwrap().into_data().unwrap();
 let initial=std::str::from_utf8(&frame).unwrap();assert!(initial.contains("TASK_STATE_SUBMITTED"));
 let c=f.connector("c",Arc::new(runtime::Transport::new(vec![Ok(AgentEvent::Delta{text:"intermediate".into()}),Ok(AgentEvent::Completed{output:runtime::text("authoritative")})])));
 f.pump(&w,&c,20).await;
 let bytes=to_bytes(body,2*1024*1024).await.unwrap();let raw=std::str::from_utf8(&bytes).unwrap();
 let events=raw.lines().filter_map(|l|l.strip_prefix("data: ")).map(|l|serde_json::from_str::<Value>(l).unwrap()).collect::<Vec<_>>();
 let artifact=events.iter().find_map(|e|e["result"].get("artifactUpdate")).unwrap();
 assert_eq!(artifact["artifact"]["parts"],json!([{"text":"authoritative"}]));assert_eq!(artifact["append"],false);assert_eq!(artifact["lastChunk"],true);
 assert_eq!(events.last().unwrap()["result"]["statusUpdate"]["status"]["state"],"TASK_STATE_COMPLETED");
 let done=json_response(app,"GetTask",json!({"id":task.task_id.value})).await;
 assert_eq!(done["result"]["artifacts"][0]["parts"],artifact["artifact"]["parts"]);
}
#[tokio::test]
async fn send_message_waits_until_input_required_but_immediate_returns_submitted(){
 let f=Fixture::new();let g=Arc::new(f.gateway());let w=f.worker("w");let app=app(g.clone(),runtime::request("x").target);
 let immediate=json_response(app.clone(),"SendMessage",common::send()).await;
 assert_eq!(immediate["result"]["task"]["status"]["state"],"TASK_STATE_SUBMITTED");
 let mut send=common::send();send["message"]["messageId"]=json!("blocking");send["configuration"]["returnImmediately"]=json!(false);
 let pending=json_response(app.clone(),"SendMessage",send);tokio::pin!(pending);
 assert!(tokio::time::timeout(std::time::Duration::from_millis(20),&mut pending).await.is_err());
 let task=g.list(caller(),TaskQuery{target:None,session_id:None,cursor:None,limit:100}).await.unwrap().items.into_iter().find(|t|t.task_id.value!=immediate["result"]["task"]["id"].as_str().unwrap()).unwrap();
 let c=f.connector("c",Arc::new(runtime::Transport::new(vec![Ok(AgentEvent::InputRequired{handle:runtime::handle(),prompt:runtime::text("Question?")})])));
 w.step(&task.session_id).await.unwrap();
 for _ in 0..10{c.tick().await.unwrap();w.step(&task.session_id).await.unwrap();}
 let result=tokio::time::timeout(std::time::Duration::from_secs(2),pending).await.unwrap();
 assert_eq!(result["result"]["task"]["status"]["state"],"TASK_STATE_INPUT_REQUIRED");
 assert_eq!(result["result"]["task"]["status"]["message"]["parts"][0]["text"],"Question?");
}
#[tokio::test]
async fn route_bound_list_has_exact_totals_and_order_across_pages(){
 let f=Fixture::new();let g=Arc::new(f.gateway());
 let first=g.submit(caller(),runtime::request("older")).await.unwrap();f.clock.set(2000);
 let second=g.submit(caller(),runtime::request("newer")).await.unwrap();
 let route=app(g,first.target.clone());
 let p=json_response(route.clone(),"ListTasks",json!({"pageSize":1})).await;
 assert_eq!(p["result"]["totalSize"],2);assert_eq!(p["result"]["tasks"][0]["id"],second.task_id.value);
 assert_eq!(p["result"]["tasks"][0]["status"]["timestamp"],"1970-01-01T00:00:02Z");
 let p=json_response(route,"ListTasks",json!({"pageSize":1,"pageToken":p["result"]["nextPageToken"]})).await;
 assert_eq!(p["result"]["totalSize"],2);assert_eq!(p["result"]["tasks"][0]["id"],first.task_id.value);assert_eq!(p["result"]["nextPageToken"],"");
}
#[tokio::test]
async fn blocking_reply_retry_returns_remaining_questions_after_its_durable_interaction_resolves(){
 use futures::StreamExt;
 for explicit in [true,false]{
  let f=Fixture::new();let g=Arc::new(group::gateway(&f,json!({"a":group::human(),"b":group::human()})));let w=f.worker("w");
  let task=if explicit{group::start_parallel(&f,&g,&w,group::group_request("parallel")).await}else{let t=g.submit(caller(),group::group_request("entry")).await.unwrap();w.step(&t.session_id).await.unwrap();t};
  let before=g.get(caller(),task.task_id.clone()).await.unwrap();let chosen=before.active_interactions[0].interaction_id.clone();
  let route=app(g.clone(),task.target.clone());
  let mut reply=json!({"message":{"messageId":"blocking-answer","role":"ROLE_USER","taskId":task.task_id.value,"contextId":task.session_id.value,"parts":[{"text":"yes"}]}});
  if explicit{reply["message"]["metadata"]=json!({mapping::INTERACTION:chosen});}
  let pending=json_response(route.clone(),"SendMessage",reply.clone());tokio::pin!(pending);
  assert!(tokio::time::timeout(std::time::Duration::from_millis(20),&mut pending).await.is_err(),"fresh answer must wait for its own interaction to resolve");
  for _ in 0..4{w.step(&task.session_id).await.unwrap();w.tick().await.unwrap();}
  let first=tokio::time::timeout(std::time::Duration::from_secs(2),pending).await.unwrap();
  let remaining=&first["result"]["task"]["status"]["message"]["parts"];
  assert_eq!(remaining.as_array().unwrap().len(),if explicit{1}else{2});
  let mut live=g.subscribe(caller(),task.task_id.clone(),None).await.unwrap();assert!(live.next().await.is_some());
  assert!(tokio::time::timeout(std::time::Duration::from_millis(20),live.next()).await.is_err(),"actual subscription remains open and idle");
  let retry=tokio::time::timeout(std::time::Duration::from_millis(100),json_response(route,"SendMessage",reply)).await.expect("consumed reply retry must not wait for somebody else's question");
  assert_eq!(&retry["result"]["task"]["status"]["message"]["parts"],remaining);
 }
}
async fn resume_response(route:Router,id:&str,cursor:&str,negotiated:bool)->axum::response::Response{
 let mut request=Request::builder().method("POST").uri("/").header("A2A-Version","1.0").header("Last-Event-ID",cursor);
 if negotiated{request=request.header("A2A-Extensions",format!("urn:other, {}",mapping::STREAM));}
 route.oneshot(request.body(Body::from(json!({"jsonrpc":"2.0","id":1,"method":"SubscribeToTask","params":{"id":id}}).to_string())).unwrap()).await.unwrap()
}
#[tokio::test]
async fn cursor_replay_requires_negotiation_and_standard_reconnect_starts_current_task(){
 use bcn_runtime_api::port::repo::*;
 let f=Fixture::new();let g=Arc::new(f.gateway());let task=g.submit(caller(),runtime::request("cursor")).await.unwrap();let w=f.worker("w");w.step(&task.session_id).await.unwrap();
 let owner=runtime::node("connector");let command=f.repo.receive(&runtime::scope(),&owner,CommandConsumerKind::Connector,None,10).await.unwrap().items.remove(0);
 let d=f.repo.claim(&command.command_id,owner).await.unwrap();let d=f.repo.mark_may_have_sent(&d.fence,d.revision).await.unwrap();
 let current=f.repo.read_snapshot(&task.task_id).await.unwrap().unwrap().cursor;
 let base=f.repo.publish_reset(&d.fence,ConnectorProjection{task_id:task.task_id.clone(),output:runtime::text("cached"),expected_cursor:current},EventId::new(runtime::scope(),"base")).await.unwrap();
 f.repo.append(&d.fence,TaskEvent{id:EventId::new(runtime::scope(),"delta"),task_id:task.task_id.clone(),cursor:base.clone(),kind:TaskEventKind::Delta{text:" + new".into()}}).await.unwrap();
 let route=app(g,task.target);let cursor=format!("{}:{}",base.epoch,base.sequence);
 let standard=resume_response(route.clone(),&task.task_id.value,&cursor,false).await;assert!(standard.headers().get("a2a-extensions").is_none());
 let mut body=standard.into_body();let frame=body.frame().await.unwrap().unwrap().into_data().unwrap();
 let text=std::str::from_utf8(&frame).unwrap();let v:Value=serde_json::from_str(text.lines().find_map(|l|l.strip_prefix("data: ")).unwrap()).unwrap();
 assert_eq!(v["result"]["task"]["artifacts"][0]["parts"],json!([{"text":"cached + new"}]));
 assert!(tokio::time::timeout(std::time::Duration::from_millis(20),body.frame()).await.is_err(),"current snapshot must not be followed by duplicate old Delta");
 let resumed=resume_response(route.clone(),&task.task_id.value,&cursor,true).await;assert_eq!(resumed.headers()["a2a-extensions"],mapping::STREAM);
 let event=first_event(resumed.into_body()).await;assert!(event["result"].get("task").is_none());assert_eq!(event["result"]["artifactUpdate"]["append"],true);
 assert_eq!(format!("cached{}",event["result"]["artifactUpdate"]["artifact"]["parts"][0]["text"].as_str().unwrap()),"cached + new");
 let expired=resume_response(route,&task.task_id.value,"0:0",true).await;assert_eq!(expired.headers()["a2a-extensions"],mapping::STREAM);
 let event=first_event(expired.into_body()).await;assert_eq!(event["result"]["task"]["artifacts"][0]["parts"],json!([{"text":"cached + new"}]));
}
