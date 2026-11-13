//! In-process local harness with explicit fake AgentConnector; no network services.
use bcn_runtime::*;
use bcn_runtime_model::*;
use bcn_runtime_api::port::*;
use async_trait::async_trait;
use std::{sync::Arc,collections::BTreeMap};
pub fn scope()->Scope{Scope::new("local","tenant","bcn-v1")}
pub fn caller()->CallerContext{CallerContext{scope:scope(),principal:"alice".into(),trace_id:None}}
pub fn binding()->FrozenTargetBinding{FrozenTargetBinding{target:AgentId::new(scope(),"echo"),binding_version:"1".into(),binding_id:"local".into(),credential_ref:None,capabilities:ConnectorCapabilities{streaming:true,query:false,subscribe:false,input_continuation:false,cancellation:CancellationSupport::Unsupported,idempotency:IdempotencySupport::Unsupported}}}
pub fn target(group:bool)->ExecutionTarget{if group{ExecutionTarget::Group{group_id:GroupId::new(scope(),"group")}}else{ExecutionTarget::Agent{agent_id:binding().target}}}
pub fn config()->RuntimeConfig{RuntimeConfig{scope:scope(),profile:RuntimeProfile::Local,storage:StorageSelection::Memory,enabled:true,allowlist:vec![target(false),target(true)],limits:RuntimeDefaults::default()}}
pub struct Catalog;
#[async_trait]
impl AgentCatalog for Catalog{
 async fn resolve(&self,_:&CallerContext,target:&ExecutionTarget,id:&SessionId)->RuntimeResult<SessionSpec>{
  let group=matches!(target,ExecutionTarget::Group{..});
  Ok(SessionSpec {live_output_nodes: None,session_id:id.clone(),target:target.clone(),kind:if group{RunnerKind::StructuredGroup}else{RunnerKind::DirectA2a},
   definition:if group{Some(serde_json::from_value(serde_json::json!({"nodes":{"work":{"kind":"bot_task","display_name":"Echo","instruction":"echo","assignee":{"type":"bot_binding","binding":"echo"},"final_output":true,"transitions":{}}}})).unwrap())}else{None},
   bindings:BTreeMap::from([("echo".into(),binding())]),policy:ExecutionPolicy{execution_timeout_ms:60000,human_timeout_ms:60000,judge_available:false,max_pending_tasks:16,max_graph_nodes:32,max_agents:5}})
 }
}
pub struct Allow;
#[async_trait]
impl AccessPolicy for Allow{async fn authorize(&self,_:&CallerContext,_:AccessAction,_:&ExecutionTarget,_:Option<&SessionId>,_:Option<&TaskId>)->RuntimeResult<()>{Ok(())}}
pub struct NoJudge;
#[async_trait]
impl JudgeExecutor for NoJudge{async fn execute(&self,_:JudgeRequest)->RuntimeResult<String>{Err(RuntimeError::Unsupported("judge".into()))}}
pub struct Echo;
#[async_trait]
impl AgentConnector for Echo{
 fn capabilities(&self)->ConnectorCapabilities{binding().capabilities}
 async fn send_streaming_message(&self,_:AgentSendRequest)->ConnectorResult<AgentEventStream>{Ok(Box::pin(futures::stream::iter(vec![Ok(AgentEvent::Completed{output:vec![MessagePart::Text{text:"local result".into()}]})])))}
}
pub fn components()->Components{Components{catalog:Arc::new(Catalog),policies:vec![Arc::new(Allow)],judge:Arc::new(NoJudge),connectors:vec![(binding(),Arc::new(Echo))]}}
pub fn request(group:bool,id:&str)->SubmitRequest{SubmitRequest{target:target(group),interaction_id:None,message:Message{message_id:MessageId::new(scope(),id),context_id:None,task_id:None,parts:vec![MessagePart::Text{text:"hello".into()}]}}}

#[tokio::main]
async fn main() -> Result<(),Box<dyn std::error::Error>> {
    use bcn_runtime_api::application::GatewayService;
    let runtime=build(config(),components(),None)?;
    for group in [false,true] {
        let task=runtime.gateway.submit(caller(),request(group,if group{"structured"}else{"direct"})).await?;
        for _ in 0..20 {
            runtime.worker.step(&task.session_id).await?;
            runtime.connector.tick().await?;
            runtime.worker.tick().await?;
        }
        let task=runtime.gateway.get(caller(),task.task_id).await?;
        if task.status!=TaskStatus::Completed{return Err("local flow did not complete".into())}
        println!("{}: {:?} {:?}",if group{"structured"}else{"direct"},task.status,task.output);
    }
    Ok(())
}
