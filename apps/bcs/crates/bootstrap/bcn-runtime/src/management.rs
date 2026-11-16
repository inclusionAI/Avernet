//! Reusable mapping of management contracts to frozen BCN admission and Judge work.
use std::{collections::BTreeMap,sync::Arc};
use async_trait::async_trait;
use bcn_runtime_api::port::*;
use bcn_runtime_model::*;
use bcs_service_api::port::repo::{bot_provider::BotProviderRepoPort,a2a_bot::A2aBotRepoPort};
use bcs_service_api::{port::repo::*,JudgeEvaluatorPort};
use crate::manifest::*;
pub struct ManagementPorts {
    pub bots:Arc<dyn BotProviderRepoPort>,
    pub providers:Arc<dyn ProviderRepoPort>,
    pub cards:Arc<dyn A2aBotRepoPort>,
    pub groups:Arc<dyn GroupRepoPort>,
    pub bindings:Arc<dyn GroupRuntimeBindingRepoPort>,
    pub definitions:Arc<dyn StateMachineDefinitionRepoPort>,
}
/// Explicit deployment grants supplement existing management target existence.
/// Collection and per-task checks use the same grant; query storage separately
/// binds every task/count to the authenticated principal and session.
#[derive(Clone,serde::Serialize,serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Grant { pub principal:String,pub target:ExecutionTarget }
pub struct GrantedAccess { pub scope:Scope,pub grants:Vec<Grant> }
#[async_trait]
impl AccessPolicy for GrantedAccess {
    async fn authorize(&self,ctx:&CallerContext,_:AccessAction,target:&ExecutionTarget,session:Option<&SessionId>,task:Option<&TaskId>)->RuntimeResult<()> {
        let target_scope=match target {ExecutionTarget::Agent{agent_id}=>&agent_id.scope,ExecutionTarget::Group{group_id}=>&group_id.scope};
        if target_scope!=&ctx.scope || ctx.scope!=self.scope || session.is_some_and(|s|s.scope!=ctx.scope) || task.is_some_and(|t|t.scope!=ctx.scope)
            || !self.grants.iter().any(|g|g.principal==ctx.principal && &g.target==target) {return Err(RuntimeError::AccessDenied)}
        Ok(())
    }
}
pub struct ManagementCatalog {pub ports:ManagementPorts,pub manifest:Arc<TransportManifest>,pub policy:ExecutionPolicy}
fn unavailable(_:bcs_service_api::ServiceError)->RuntimeError {RuntimeError::Unavailable("management operation failed".into())}
impl ManagementCatalog {
    async fn agent(&self,id:&AgentId)->RuntimeResult<FrozenTargetBinding> {
        let bot=self.ports.bots.get_provider_bot(&id.value).await.map_err(unavailable)?.ok_or(RuntimeError::NotFound)?;
        if bot.is_deleted {return Err(RuntimeError::NotFound)}
        let provider=self.ports.providers.get_provider(&bot.provider_id).await.map_err(unavailable)?.ok_or(RuntimeError::NotFound)?;
        if provider.disabled {return Err(RuntimeError::AccessDenied)}
        let card=self.ports.cards.get(&id.value).await.map_err(unavailable)?;
        let config:serde_json::Value=serde_json::from_str(&provider.config).map_err(|_|RuntimeError::InvalidInput("management Provider config invalid".into()))?;
        let endpoint=bot.webhook_url.clone().or_else(||config.pointer("/downlink/webhook_url").and_then(|v|v.as_str()).map(str::to_owned));
        if card.is_none() && (bot.connection_mode!=bcs_domain::bot_provider::BotConnectionMode::Gateway
            || config.pointer("/downlink/enabled").and_then(|v|v.as_bool())!=Some(true)
            || config.pointer("/downlink/protocol_version").and_then(|v|v.as_str())!=Some("2.0")
            || config.pointer("/downlink/auth_mode").and_then(|v|v.as_str())!=Some("static_bearer")) {
            return Err(RuntimeError::Unsupported("management transport requires enabled v2 static-bearer Provider route".into()));
        }
        let matches:Vec<_>=self.manifest.entries.iter().filter(|e|e.binding.target==*id).filter(|e|match (&e.route,&card) {
            (TransportRoute::A2a{card:raw},Some(c))=>c.provider_id==bot.provider_id && c.provider_bot_ref==bot.provider_bot_ref && raw==&c.agent_card,
            (TransportRoute::Provider{endpoint:ep,provider_id,provider_bot_ref,..},None)=>endpoint.as_ref()==Some(ep) && provider_id==&bot.provider_id && provider_bot_ref==&bot.provider_bot_ref,
            _=>false,
        }).collect();
        if matches.len()!=1 {return Err(RuntimeError::Conflict("current management route has no unique frozen manifest entry".into()))}
        Ok(matches[0].binding.clone())
    }
}
#[async_trait]
impl AgentCatalog for ManagementCatalog {
    async fn resolve(&self,ctx:&CallerContext,target:&ExecutionTarget,session:&SessionId)->RuntimeResult<SessionSpec> {
        let target_scope=match target {ExecutionTarget::Agent{agent_id}=>&agent_id.scope,ExecutionTarget::Group{group_id}=>&group_id.scope};
        if ctx.scope!=session.scope || target_scope!=&ctx.scope {return Err(RuntimeError::AccessDenied)}
        let mut bindings=BTreeMap::new();
        let (kind,definition)=match target {
            ExecutionTarget::Agent{agent_id}=>{bindings.insert("agent".into(),self.agent(agent_id).await?);(RunnerKind::DirectA2a,None)},
            ExecutionTarget::Group{group_id}=>{
                let group=self.ports.groups.try_get(&group_id.value).await.map_err(unavailable)?.ok_or(RuntimeError::NotFound)?;
                if group.record_status!="active" {return Err(RuntimeError::AccessDenied)}
                let binding=self.ports.bindings.get(&group_id.value).await.map_err(unavailable)?.ok_or(RuntimeError::NotFound)?;
                if binding.group_id!=group.id || binding.group_version!=group.version {return Err(RuntimeError::InvalidInput("management group binding version mismatch".into()))}
                let reference=binding.default_definition.ok_or_else(||RuntimeError::Unsupported("group has no structured definition".into()))?;
                let definition=self.ports.definitions.get(&reference.id,reference.version).await.map_err(unavailable)?.ok_or(RuntimeError::NotFound)?;
                if definition.id!=reference.id || definition.version!=reference.version {return Err(RuntimeError::InvalidInput("management definition version mismatch".into()))}
                let bcs_domain::CollaborationRuntimeDefinition::StateMachine(sm)=definition.runtime else {return Err(RuntimeError::Unsupported("group runtime is not structured".into()))};
                if sm.nodes.len() > 32 {return Err(RuntimeError::CapacityExceeded)}
                let mut targets=BTreeMap::new();
                for node in sm.nodes.values() {
                    if node.kind!=bcs_domain::StateMachineNodeKind::BotTask {continue;}
                    let Some(bcs_domain::StateMachineAssignee::BotBinding{binding:key})=&node.assignee else {
                        return Err(RuntimeError::InvalidInput("bot_task requires bot_binding".into()));
                    };
                    if targets.contains_key(key) {continue;}
                    let participant=definition.participants.get(key).ok_or_else(||RuntimeError::InvalidInput("missing participant".into()))?;
                    let ids=binding.participant_bindings.get(key).map(|b|b.bot_ids.clone()).unwrap_or_else(||participant.bot_id.clone().into_iter().collect());
                    if ids.len()!=1 || !group.participants.iter().any(|p|p.bot_uuid==ids[0]) {return Err(RuntimeError::InvalidInput("group requires one existing agent per binding".into()))}
                    targets.insert(key.clone(),AgentId::new(ctx.scope.clone(),ids[0].clone()));
                }
                bcn_session_runner::validation::validate_graph(&sm,&self.policy,&targets,None)?;
                // Aliases of the same agent share one admission-time route read.
                let mut resolved=BTreeMap::new();
                for (key,id) in targets {
                    if !resolved.contains_key(&id) {resolved.insert(id.clone(),self.agent(&id).await?);}
                    bindings.insert(key,resolved[&id].clone());
                }
                (RunnerKind::StructuredGroup,Some(sm))
            }
        };
        Ok(SessionSpec{session_id:session.clone(),target:target.clone(),kind,definition,bindings,policy:self.policy.clone(),live_output_nodes:None})
    }
}
pub struct ManagementJudge(pub Arc<dyn JudgeEvaluatorPort>);
#[async_trait]
impl JudgeExecutor for ManagementJudge {
    async fn execute(&self,r:JudgeRequest)->RuntimeResult<String> {
        let outputs=r.inputs.iter().map(|(node,result)|bcs_service_api::JudgeArtifact{node_id:node.clone(),text:result.intent.output.iter().filter_map(|p|match p {MessagePart::Text{text}=>Some(text.as_str()),_=>None}).collect::<Vec<_>>().join("\n")}).collect();
        let result=self.0.judge(bcs_service_api::JudgeRequest{run_id:r.operation_id.value,node_id:r.node_id,attempt:1,judge_type:"llm".into(),criteria:vec![r.prompt],allowed_outcomes:r.choices.clone(),input:serde_json::json!({"task":r.task_id}),upstream_outputs:outputs,artifact_text:String::new()}).await.map_err(|_|RuntimeError::Unavailable("configured Judge failed".into()))?;
        if !r.choices.contains(&result.outcome) {return Err(RuntimeError::InvalidInput("Judge returned disallowed outcome".into()))}
        Ok(result.outcome)
    }
}
