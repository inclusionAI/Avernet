mod common;
use common::*;
use serde_json::json;
use std::sync::atomic::Ordering;
#[tokio::test]
async fn missing_empty_and_unsupported_versions_fail_before_admission(){
 let (app,g)=app();
 for version in [None,Some(""),Some("0.3"),Some("2.0")] {
  let (_,r)=call(app.clone(),"SendMessage",send(),version,true).await;
  assert_eq!(r["error"]["code"],-32009);
 }
 assert_eq!(g.submits.load(Ordering::SeqCst),0);
 for version in ["1.0","1.0.7"] { let (_,r)=call(app.clone(),"SendMessage",send(),Some(version),true).await;assert_eq!(r["result"]["task"]["id"],"task"); }
 assert_eq!(g.submits.load(Ordering::SeqCst),2);
}
#[tokio::test]
async fn six_methods_use_typed_a2a_models_and_streaming_binding(){
 let (app,_)=app();
 for (method,params) in [("SendMessage",send()),("GetTask",json!({"id":"task"})),("ListTasks",json!({})),("CancelTask",json!({"id":"task"}))] {
  let (status,r)=call(app.clone(),method,params,Some("1.0"),true).await;
  assert_eq!(status,200); assert!(r.get("result").is_some(),"{method}: {r}");
 }
 for (method,params) in [("SendStreamingMessage",send()),("SubscribeToTask",json!({"id":"task"}))] {
  let (status,r)=call(app.clone(),method,params,Some("1.0"),true).await;
  assert_eq!(status,200); assert!(r["raw"].as_str().unwrap().contains("\"task\""));
 }
}
#[tokio::test]
async fn tenant_auth_and_media_checks_precede_admission(){
 let (app,g)=app();
 assert_eq!(call(app.clone(),"SendMessage",send(),Some("1.0"),false).await.0,401);
 let mut p=send();p["tenant"]=json!("other");assert!(call(app.clone(),"SendMessage",p,Some("1.0"),true).await.1.get("error").is_some());
 let mut p=send();p["message"]["parts"]=json!([{"data":{"x":1}}]);assert_eq!(call(app.clone(),"SendMessage",p,Some("1.0"),true).await.1["error"]["code"],-32005);
 let mut p=send();p["configuration"]["acceptedOutputModes"]=json!(["image/png"]);assert_eq!(call(app.clone(),"SendMessage",p,Some("1.0"),true).await.1["error"]["code"],-32005);
 assert_eq!(g.submits.load(Ordering::SeqCst),0);
}
#[tokio::test]
async fn known_optional_capabilities_have_protocol_specific_errors(){
 let (app,_)=app();
 for method in ["CreateTaskPushNotificationConfig","GetTaskPushNotificationConfig","ListTaskPushNotificationConfigs","DeleteTaskPushNotificationConfig"] {
  assert_eq!(call(app.clone(),method,json!({}),Some("1.0"),true).await.1["error"]["code"],-32003);
 }
 assert_eq!(call(app.clone(),"GetExtendedAgentCard",json!({}),Some("1.0"),true).await.1["error"]["code"],-32004);
 assert_eq!(call(app,"InventedMethod",json!({}),Some("1.0"),true).await.1["error"]["code"],-32601);
}
#[tokio::test]
async fn bodies_are_bounded_and_terminal_subscriptions_are_errors(){
 let (app,g)=app();
 let mut p=send();p["message"]["parts"]=json!([{"text":"x".repeat(bcn_api_http::MAX_BODY_BYTES)}]);
 assert_eq!(call(app.clone(),"SendMessage",p,Some("1.0"),true).await.0,413);
 assert_eq!(g.submits.load(Ordering::SeqCst),0);
 g.task.lock().unwrap().status=bcn_runtime_model::TaskStatus::Completed;
 assert_eq!(call(app,"SubscribeToTask",json!({"id":"task"}),Some("1.0"),true).await.1["error"]["code"],-32004);
}
#[tokio::test]
async fn parallel_resets_replace_progress_and_terminal_artifacts_reconstruct_get_task(){
 use bcn_runtime_model::*;
 let (app,g)=app();let mut partial=view();partial.status=TaskStatus::Working;
 partial.output=vec![MessagePart::Text{text:"node-a".into()}];
 let mut parallel=partial.clone();parallel.output=vec![MessagePart::Text{text:"node-a updated".into()},MessagePart::Text{text:"node-b".into()}];
 let mut terminal=view();terminal.status=TaskStatus::Completed;terminal.output=vec![MessagePart::Text{text:"authoritative joined result".into()}];
 *g.terminal.lock().unwrap()=Some(terminal.clone());
 let kinds=vec![TaskEventKind::Reset{snapshot:partial},TaskEventKind::Domain{event:DomainEvent{event_id:EventId::new(scope(),"working"),task_id:terminal.task_id.clone(),kind:DomainEventKind::TaskStatusChanged{status:TaskStatus::Working}}},TaskEventKind::Delta{text:" tail".into()},TaskEventKind::Reset{snapshot:parallel},TaskEventKind::Domain{event:DomainEvent{event_id:EventId::new(scope(),"terminal"),task_id:terminal.task_id.clone(),kind:DomainEventKind::TaskStatusChanged{status:TaskStatus::Completed}}}];
 *g.events.lock().unwrap()=Some(kinds.into_iter().enumerate().map(|(n,kind)|TaskEvent{id:EventId::new(scope(),format!("e{n}")),task_id:terminal.task_id.clone(),cursor:EventCursor{epoch:1,sequence:n as u64},kind}).collect());
 let (_,response)=call(app.clone(),"SubscribeToTask",json!({"id":"task"}),Some("1.0"),true).await;
 let mut output=Vec::<a2a::Part>::new();let mut status=None;
 for line in response["raw"].as_str().unwrap().lines().filter_map(|s|s.strip_prefix("data: ")){
  let rpc:a2a::JsonRpcResponse=serde_json::from_str(line).unwrap();
  match serde_json::from_value::<a2a::StreamResponse>(rpc.result.unwrap()).unwrap(){
   a2a::StreamResponse::Task(task)=>{output=task.artifacts.unwrap_or_default().into_iter().flat_map(|a|a.parts).collect();status=Some(task.status.state)},
   a2a::StreamResponse::ArtifactUpdate(event)=>{if event.append==Some(true){output.extend(event.artifact.parts)}else{output=event.artifact.parts}},
   a2a::StreamResponse::StatusUpdate(event)=>{if event.status.state==a2a::TaskState::Working{assert!(event.status.timestamp.is_none());}else{assert!(event.status.timestamp.is_some());}status=Some(event.status.state)},_=>panic!("unexpected message")
  }
 }
 let (_,queried)=call(app,"GetTask",json!({"id":"task"}),Some("1.0"),true).await;
 let task:a2a::Task=serde_json::from_value(queried["result"].clone()).unwrap();
 assert_eq!(output,task.artifacts.unwrap()[0].parts);assert_eq!(status,Some(task.status.state));
}
#[tokio::test]
async fn blocking_interruption_waits_for_durable_question_not_status_alone(){
 use bcn_runtime_model::*;
 let (app,g)=app();g.task.lock().unwrap().status=TaskStatus::InputRequired;
 let interaction=InteractionId::new(scope(),"question");
 let mut ready=view();ready.status=TaskStatus::InputRequired;ready.active_interactions=vec![ActiveInteraction{interaction_id:interaction.clone(),prompt:"Which?".into()}];
 *g.terminal.lock().unwrap()=Some(ready);
 *g.events.lock().unwrap()=Some(vec![TaskEvent{id:EventId::new(scope(),"question"),task_id:view().task_id,cursor:EventCursor{epoch:1,sequence:1},kind:TaskEventKind::Domain{event:DomainEvent{event_id:EventId::new(scope(),"question"),task_id:view().task_id,kind:DomainEventKind::InputRequired{interaction_id:interaction,prompt:"Which?".into()}}}}]);
 let mut request=send();request["configuration"]["returnImmediately"]=json!(false);
 let (_,r)=call(app,"SendMessage",request,Some("1.0"),true).await;
 assert_eq!(r["result"]["task"]["status"]["message"]["parts"][0]["text"],"Which?");
}
#[tokio::test]
async fn discovery_card_advertises_only_implemented_modes_and_extensions(){
 use tower::ServiceExt;
 let (app,_)=app();
 let response=app.oneshot(axum::http::Request::builder().uri("/.well-known/agent-card.json").body(axum::body::Body::empty()).unwrap()).await.unwrap();
 assert_eq!(response.status(),200);
 let card:serde_json::Value=serde_json::from_slice(&axum::body::to_bytes(response.into_body(),65536).await.unwrap()).unwrap();
 assert_eq!(card["defaultInputModes"],json!(["text/plain"]));assert_eq!(card["defaultOutputModes"],json!(["text/plain"]));
 assert_eq!(card["skills"][0]["inputModes"],json!(["text/plain"]));assert_eq!(card["skills"][0]["outputModes"],json!(["text/plain"]));
 assert_eq!(card["capabilities"]["extendedAgentCard"],false);
 assert_eq!(card["capabilities"]["extensions"].as_array().unwrap().len(),2);
}

#[tokio::test]
async fn list_defaults_omit_artifacts_and_expose_matching_status_timestamp() {
 let (app,g)=app();{let mut t=g.task.lock().unwrap();t.output=vec![bcn_runtime_model::MessagePart::Text{text:"saved".into()}];t.updated_at_ms=1234;}
 let (_,v)=call(app.clone(),"ListTasks",json!({}),Some("1.0"),true).await;
 assert_eq!(v["result"]["pageSize"],50);
 assert_eq!(v["result"]["totalSize"],42);
 assert!(v["result"]["tasks"][0].get("artifacts").is_none());
 assert_eq!(v["result"]["tasks"][0]["status"]["timestamp"],"1970-01-01T00:00:01.234Z");
 let (_,v)=call(app,"ListTasks",json!({"includeArtifacts":true}),Some("1.0"),true).await;
 assert_eq!(v["result"]["tasks"][0]["artifacts"][0]["parts"][0]["text"],"saved");
}
