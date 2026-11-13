mod common;
use common::*;
use bcn_runtime::*;
use bcn_runtime_api::application::*;
use bcn_runtime_model::*;
#[tokio::test]
async fn assembled_shared_memory_runs_direct_and_structured_to_saved_result(){
 let runtime=build(config(),components(),None).unwrap();
 for group in [false,true]{
  let task=runtime.gateway.submit(caller(),request(group,if group{"group"}else{"direct"})).await.unwrap();
  for _ in 0..20{runtime.worker.step(&task.session_id).await.unwrap();runtime.connector.tick().await.unwrap();runtime.worker.tick().await.unwrap();}
  let result=runtime.gateway.get(caller(),task.task_id).await.unwrap();
  assert_eq!(result.status,TaskStatus::Completed);assert_eq!(result.output,vec![MessagePart::Text{text:"local result".into()}]);
 }
}
#[tokio::test]
async fn new_session_allowlist_does_not_replace_authorization(){
 let mut c=config();c.allowlist=vec![target(false)];
 let runtime=build(c,components(),None).unwrap();
 assert_eq!(runtime.gateway.submit(caller(),request(true,"denied")).await,Err(RuntimeError::AccessDenied));
 let task=runtime.gateway.submit(caller(),request(false,"accepted")).await.unwrap();
 let mut other=caller();other.principal="bob".into();
 assert_eq!(runtime.gateway.get(other,task.task_id).await,Err(RuntimeError::AccessDenied));
}
#[tokio::test]
async fn changed_allowlist_keeps_existing_session_and_disabled_gate_keeps_reads_and_cancel(){
 let memory=config();let ports=memory_ports(&memory).unwrap();
 let mut first_config=memory.clone();first_config.storage=StorageSelection::External;
 let first=build(first_config.clone(),components(),Some(ports.clone())).unwrap();
 let task=first.gateway.submit(caller(),request(false,"first")).await.unwrap();
 let mut changed=first_config.clone();changed.allowlist=vec![target(true)];
 let changed=build(changed,components(),Some(ports.clone())).unwrap();
 assert_eq!(changed.gateway.get(caller(),task.task_id.clone()).await.unwrap().task_id,task.task_id);
 let mut continuation=request(false,"continuation");continuation.message.context_id=Some(task.session_id.clone());
 changed.gateway.submit(caller(),continuation).await.unwrap();
 assert_eq!(changed.gateway.submit(caller(),request(false,"new-session")).await,Err(RuntimeError::AccessDenied));
 first_config.enabled=false;first_config.allowlist.clear();
 let disabled=build(first_config,components(),Some(ports)).unwrap();
 disabled.gateway.get(caller(),task.task_id.clone()).await.unwrap();
 disabled.gateway.cancel(caller(),CancelCommand{task_id:task.task_id,operation_id:OperationId::new(scope(),"cancel")}).await.unwrap();
 assert!(matches!(disabled.gateway.submit(caller(),request(false,"disabled")).await,Err(RuntimeError::Unavailable(_))));
}
#[test]
fn external_storage_never_falls_back_to_memory_and_connector_scopes_are_checked(){
 let mut c=config();c.profile=RuntimeProfile::Production;c.storage=StorageSelection::External;
 assert!(build(c,components(),None).is_err());
 let mut components=components();components.connectors[0].0.target.scope.tenant="wrong".into();
 assert!(build(config(),components,None).is_err());
}
