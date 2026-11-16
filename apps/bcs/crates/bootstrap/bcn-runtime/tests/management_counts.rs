#![allow(dead_code)]
use async_trait::async_trait;
use std::{sync::{Arc,atomic::{AtomicUsize,Ordering}}};
use bcn_runtime::{management::*};
use bcn_runtime_model::*;
use bcn_runtime_api::port::*;
use bcs_service_api::{ServiceResult,BotCapabilities,port::repo::bot_provider::*};
use bcs_domain::bot_provider::{BotProviderRecord,BotConnectionMode};
pub struct CountingBots {inner:Arc<dyn BotProviderRepoPort>,reads:AtomicUsize}
impl CountingBots {pub fn new(inner:Arc<dyn BotProviderRepoPort>)->Self{Self{inner,reads:AtomicUsize::new(0)}}}
#[async_trait]
impl BotProviderRepoPort for CountingBots {
 async fn get_provider_bot(&self,id:&str)->ServiceResult<Option<BotProviderRecord>>{self.reads.fetch_add(1,Ordering::SeqCst);self.inner.get_provider_bot(id).await}
 async fn validate_provider_bot_name(&self,_:&str,_:&str,_:Option<&str>)->ServiceResult<()>{unreachable!()}
 async fn get_provider_bot_by_ref(&self,_:&str,_:&str)->ServiceResult<Option<BotProviderRecord>>{unreachable!()}
 async fn list_provider_bot_metadata(&self,_:Option<&str>)->ServiceResult<Vec<BotProviderRecord>>{unreachable!()}
 async fn get_connection_mode(&self,_:&str)->ServiceResult<Option<BotConnectionMode>>{unreachable!()}
 async fn attach_provider_bot(&self,_:BotProviderRecord)->ServiceResult<()>{unreachable!()}
 async fn create_provider_bot(&self,_:BotProviderRecord,_:BotCapabilities,_:&str,_:&str)->ServiceResult<()>{unreachable!()}
 async fn update_provider_webhook(&self,_:&str,_:&str,_:Option<String>,_:u64)->ServiceResult<BotProviderRecord>{unreachable!()}
 async fn delete_provider_bot(&self,_:&str,_:&str,_:u64)->ServiceResult<bool>{unreachable!()}
}
fn definition(count:usize,distinct:bool)->bcs_domain::CollaborationDefinition {
 let mut participants=serde_json::Map::new();let mut nodes=serde_json::Map::new();
 for i in 0..count {
  let key=format!("b{i}");let agent=if distinct{format!("agent{i}")}else{"agent".into()};participants.insert(key.clone(),serde_json::json!({"bot_id":agent}));
  let mut node=serde_json::json!({"kind":"bot_task","display_name":"Work","instruction":"work","assignee":{"type":"bot_binding","binding":key},"final_output":i+1==count});
  if i+1<count {node["transitions"]=serde_json::json!({"complete":{"targets":[format!("n{}",i+1)]}});}
  nodes.insert(format!("n{i}"),node);
 }
 serde_json::from_value(serde_json::json!({"id":"definition","version":1,"name":"bounded","participants":participants,"runtime":{"kind":"state_machine","state_machine":{"nodes":nodes}}})).unwrap()
}
pub async fn check_bounds(c:&ManagementCatalog,caller:&CallerContext,target:&ExecutionTarget,session:&SessionId,bots:&CountingBots) {
 // Unused participants (including missing agents) are not runtime dependencies.
 let mut d=definition(1,false);let unused=d.participants["b0"].clone();
 for i in 0..8 {let mut p=unused.clone();p.bot_id=Some(format!("unused{i}"));d.participants.insert(format!("unused{i}"),p);}
 install(c,d).await;bots.reads.store(0,Ordering::SeqCst);
 let spec=c.resolve(caller,target,session).await.unwrap();assert_eq!(spec.bindings.len(),1);assert_eq!(bots.reads.load(Ordering::SeqCst),1);
 // Many graph aliases of one target still resolve it once, up to 32 nodes.
 install(c,definition(32,false)).await;bots.reads.store(0,Ordering::SeqCst);
 assert!(c.resolve(caller,target,session).await.is_ok());assert_eq!(bots.reads.load(Ordering::SeqCst),1);
 let participants=(0..6).map(|i|bcs_domain::Participant::bot(format!("agent{i}"),bcs_domain::ParticipantRole::Worker)).collect();
 c.ports.groups.upsert(bcs_domain::Group::new("group","agent0",participants)).await.unwrap();
 install(c,definition(5,true)).await;bots.reads.store(0,Ordering::SeqCst);
 assert!(c.resolve(caller,target,session).await.is_ok());assert_eq!(bots.reads.load(Ordering::SeqCst),5);
 let mut invalid_judge=definition(1,true);
 if let bcs_domain::CollaborationRuntimeDefinition::StateMachine(sm)=&mut invalid_judge.runtime {
  sm.nodes.get_mut("n0").unwrap().judge=Some(serde_json::from_value(serde_json::json!({"criteria":["check"],"outcomes":["ok"]})).unwrap());
 }
 for d in [definition(6,true),definition(33,false),invalid_judge] {
  install(c,d).await;bots.reads.store(0,Ordering::SeqCst);
  assert!(c.resolve(caller,target,session).await.is_err());assert_eq!(bots.reads.load(Ordering::SeqCst),0,"invalid admission must not resolve agents");
 }
 // Restore management group for the original test's later version checks.
 c.ports.groups.upsert(bcs_domain::Group::new("group","agent",vec![bcs_domain::Participant::bot("agent",bcs_domain::ParticipantRole::Worker)])).await.unwrap();
 install(c,definition(1,false)).await;
}

async fn install(c:&ManagementCatalog,mut d:bcs_domain::CollaborationDefinition) {
 let mut binding=c.ports.bindings.get("group").await.unwrap().unwrap();
 d.version=binding.default_definition.as_ref().unwrap().version+1;
 binding.default_definition=Some(bcs_domain::CollaborationDefinitionRef{id:d.id.clone(),version:d.version});
 c.ports.definitions.upsert(d).await.unwrap();c.ports.bindings.upsert(binding).await.unwrap();
}
