use async_trait::async_trait;
use std::{sync::{Arc,Mutex},collections::BTreeMap};
use bcn_runtime::{management::*,manifest::*};
use bcn_runtime_model::*;
use bcn_runtime_api::port::*;
use bcs_service_api::{ServiceResult,port::repo::{*,a2a_bot::*,bot_provider::*}};
struct Cards;
#[async_trait]
impl A2aBotRepoPort for Cards {
 async fn get(&self,_:&str)->ServiceResult<Option<A2aBotRecord>>{Ok(None)}
 async fn cards_for_bots(&self,_:&[String])->ServiceResult<BTreeMap<String,serde_json::Value>>{Ok(BTreeMap::new())}
 async fn discovery_cards(&self,_:&[String],_:Option<&str>,_:&[String])->ServiceResult<BTreeMap<String,Option<serde_json::Value>>>{Ok(BTreeMap::new())}
 async fn availability_many(&self,_:&[String])->ServiceResult<Vec<A2aBotAvailability>>{Ok(vec![])}
 async fn compare_and_set(&self,_:&A2aBotRecord,_:u64)->ServiceResult<bool>{Ok(false)}
 async fn replace(&self,_:&A2aBotRecord)->ServiceResult<A2aBotRecord>{unreachable!()}
 async fn get_by_name(&self,_:&str,_:&str)->ServiceResult<Option<A2aBotRecord>>{Ok(None)}
 async fn refreshable(&self,_:Option<&str>,_:u32)->ServiceResult<Vec<A2aBotRecord>>{Ok(vec![])}
}
fn entry(endpoint:&str)->FrozenTransport {
 let scope=Scope::new("test","tenant","catalog");
 let mut e=FrozenTransport{binding:FrozenTargetBinding{target:AgentId::new(scope,"agent"),binding_id:"binding".into(),binding_version:String::new(),credential_ref:None,capabilities:ConnectorCapabilities{streaming:true,query:false,subscribe:false,input_continuation:false,cancellation:CancellationSupport::Unsupported,idempotency:IdempotencySupport::Unsupported}},route:TransportRoute::Provider{endpoint:endpoint.into(),provider_id:"p".into(),provider_bot_ref:"b".into(),group_id:"g".into(),timeout_ms:1000}};
 e.binding.binding_version=e.version().unwrap();e
}
#[tokio::test]
async fn current_management_route_selects_new_binding_while_history_stays_exact(){
 let fixture_dir=std::env::temp_dir().join(format!("bcn-management-{}",uuid::Uuid::new_v4()));
 let providers=Arc::new(bcs_bot_store::provider::MemoryProviderStore::new());
 providers.insert_provider(bcs_domain::ProviderRecord{provider_id:"p".into(),slug:None,name:"fixture".into(),config:r#"{"downlink":{"enabled":true,"webhook_url":"http://localhost:1234","protocol_version":"2.0","auth_mode":"static_bearer"}}"#.into(),created_by:"alice".into(),owners:"[]".into(),disabled:false,created_at:0,updated_at:0}).await.unwrap();
 let bots=Arc::new(bcs_bot_store::provider::MemoryBotProviderStore::new(Arc::new(bcs_bot_store::MemoryBotRepo::with_base_dir(fixture_dir.clone())),providers.clone()));
 bots.create_provider_bot(bcs_domain::bot_provider::BotProviderRecord{bot_uuid:"agent".into(),provider_id:"p".into(),provider_bot_ref:"b".into(),connection_mode:bcs_domain::bot_provider::BotConnectionMode::Gateway,webhook_url:None,is_deleted:false},Default::default(),"alice","fixture-runtime-token").await.unwrap();
 let old=entry("http://localhost:1234");let new=entry("http://localhost:4321");
 let manifest=Arc::new(TransportManifest{entries:vec![old.clone(),new.clone()]});manifest.validate().unwrap();
 let collaboration=Arc::new(bcs_collaboration_store::MemoryCollaborationStore::new());
 let catalog=ManagementCatalog{ports:ManagementPorts{bots:bots.clone(),providers:providers.clone(),cards:Arc::new(Cards),groups:Arc::new(bcs_group_store::MemoryGroupRepo::new()),bindings:collaboration.clone(),definitions:collaboration},manifest:manifest.clone(),policy:ExecutionPolicy{execution_timeout_ms:60000,human_timeout_ms:60000,judge_available:false,max_pending_tasks:16,max_graph_nodes:32,max_agents:5}};
 let caller=CallerContext{scope:old.binding.target.scope.clone(),principal:"alice".into(),trace_id:None};let session=SessionId::new(caller.scope.clone(),"session");let target=ExecutionTarget::Agent{agent_id:old.binding.target.clone()};
 let frozen=catalog.resolve(&caller,&target,&session).await.unwrap();assert_eq!(frozen.bindings["agent"],old.binding);
 bots.update_provider_webhook("p","agent",Some("http://localhost:4321".into()),1).await.unwrap();
 let current=catalog.resolve(&caller,&target,&SessionId::new(caller.scope.clone(),"new-session")).await.unwrap();assert_eq!(current.bindings["agent"],new.binding);
 assert_eq!(manifest.find(&frozen.bindings["agent"]).unwrap().route,old.route);
 let missing=TransportManifest{entries:vec![new]};assert!(missing.find(&frozen.bindings["agent"]).is_err());
 let mut foreign=old.binding.target.clone();foreign.scope.tenant="foreign".into();
 assert!(matches!(catalog.resolve(&caller,&ExecutionTarget::Agent{agent_id:foreign},&session).await,Err(RuntimeError::AccessDenied)));
 let definition:bcs_domain::CollaborationDefinition=serde_json::from_value(serde_json::json!({"id":"definition","version":1,"name":"fixture","participants":{"agent":{"bot_id":"agent"}},"runtime":{"kind":"state_machine","state_machine":{"nodes":{"work":{"kind":"bot_task","display_name":"Work","instruction":"work","assignee":{"type":"bot_binding","binding":"agent"},"final_output":true}}}}})).unwrap();
 catalog.ports.definitions.upsert(definition).await.unwrap();
 catalog.ports.groups.upsert(bcs_domain::Group::new("group","agent",vec![bcs_domain::Participant::bot("agent",bcs_domain::ParticipantRole::Worker)])).await.unwrap();
 let mut binding=bcs_domain::GroupRuntimeBinding{group_id:"group".into(),group_version:1,default_definition:Some(bcs_domain::CollaborationDefinitionRef{id:"definition".into(),version:1}),participant_bindings:BTreeMap::new(),auto_start_on_service_invocation:false};
 catalog.ports.bindings.upsert(binding.clone()).await.unwrap();
 let group_target=ExecutionTarget::Group{group_id:GroupId::new(caller.scope.clone(),"group")};
 let group=catalog.resolve(&caller,&group_target,&session).await.unwrap();assert_eq!(group.kind,RunnerKind::StructuredGroup);assert_eq!(group.bindings["agent"],current.bindings["agent"]);
 binding.group_version=2;catalog.ports.bindings.upsert(binding).await.unwrap();
 assert!(catalog.resolve(&caller,&group_target,&session).await.is_err(),"mismatched group version cannot freeze mixed management state");
 let mut provider=providers.get_provider("p").await.unwrap().unwrap();provider.config=r#"{"downlink":{"enabled":false,"webhook_url":"http://localhost:4321","protocol_version":"2.0","auth_mode":"static_bearer"}}"#.into();
 providers.update_provider_metadata("p",None,Some(&provider.config),None,2).await.unwrap();
 assert!(catalog.resolve(&caller,&target,&session).await.is_err(),"disabled current transport must not match retained history");
 std::fs::remove_dir_all(fixture_dir).unwrap();

}
struct Llm { requests:Mutex<Vec<bcs_llm_api::LlmChatCompletionRequest>>,reply:String }
#[async_trait]
impl bcs_llm_api::LlmChatCompletionPort for Llm {
 async fn complete(&self,r:bcs_llm_api::LlmChatCompletionRequest)->Result<bcs_llm_api::LlmChatCompletionResponse,bcs_llm_api::LlmError>{self.requests.lock().unwrap().push(r);Ok(bcs_llm_api::LlmChatCompletionResponse{content:self.reply.clone(),raw:serde_json::Value::Null})}
}
#[tokio::test]
async fn judge_uses_existing_llm_service_and_rejects_unknown_outcomes(){
 for choice in ["approved","invented"]{
  let llm=Arc::new(Llm{requests:Mutex::new(vec![]),reply:serde_json::json!({"outcome":choice,"reason":"evidence","confidence":1.0,"checked_criteria":[],"retry_instruction":""}).to_string()});
  let judge=ManagementJudge(Arc::new(bcs_judge::LlmJudgeService::new(llm.clone(),"fixture-model".into())));
  let scope=Scope::new("test","tenant","judge");let task=TaskId::new(scope.clone(),"task");
  let saved=SavedResult{intent:ResultIntent{result_id:ResultId::new(scope.clone(),"saved"),task_id:task.clone(),node_id:Some("before".into()),attempt:1,status:TaskStatus::Completed,output:vec![MessagePart::Text{text:"persisted predecessor".into()}],error:None},saved_at_ms:1};
  let result=judge.execute(JudgeRequest{operation_id:OperationId::new(scope,"op"),task_id:task,node_id:"judge".into(),prompt:"frozen criterion".into(),choices:vec!["approved".into()],inputs:BTreeMap::from([("before".into(),saved)])}).await;
  assert_eq!(result.is_ok(),choice=="approved");let requests=llm.requests.lock().unwrap();assert_eq!(requests.len(),1);
  let prompt=serde_json::to_string(&requests[0]).unwrap();assert!(prompt.contains("persisted predecessor")&&prompt.contains("frozen criterion"));
 }
}
