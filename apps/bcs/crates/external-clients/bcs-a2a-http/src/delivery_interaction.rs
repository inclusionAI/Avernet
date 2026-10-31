//! Single-instance input owner. Awaiting humans holds no network subscription.
use super::*;
use bcs_service_api::{InteractionKind,InteractionProviderAck,InteractionProviderCommand,InteractionProviderPort,
    InteractionRequestedOutcome,InteractionService,ManagedMessageDeliveryService,ProviderInteractionRequestedCommand,
    ProviderInteractionResolvedCommand,BotRunScope};
use bcs_service_api::RunBudgetService;
use bcs_service_api::types::run_budget::{RunBudgetLimits,RunBudgetTarget,RunBudgetUpdate};
use crate::structured_exec::{Decision,ExecInput,EXTENSION};

#[derive(Clone)]
pub struct InputRuntime {
    service:Weak<dyn InteractionService>,
    deliveries:Arc<dyn ManagedMessageDeliveryService>,
    pub(super) budgets:Arc<dyn RunBudgetService>,
    pub(super) limits:RunBudgetLimits,
}
impl InputRuntime {
    pub fn new(service:Arc<dyn InteractionService>,deliveries:Arc<dyn ManagedMessageDeliveryService>,
        budgets:Arc<dyn RunBudgetService>,limits:RunBudgetLimits)->Self {
        Self {service:Arc::downgrade(&service),deliveries,budgets,limits}
    }
    fn service(&self)->ServiceResult<Arc<dyn InteractionService>> {
        self.service.upgrade().ok_or_else(||invalid("interaction service stopped"))
    }
}
pub(super) struct Resolution {
    command:InteractionProviderCommand,
    reply:tokio::sync::oneshot::Sender<ServiceResult<InteractionProviderAck>>,
}
pub(super) fn budget_target(record:&A2aRunRecord)->RunBudgetTarget {
    RunBudgetTarget {scope:BotRunScope {bot_id:record.scope.bot_id.clone(),group_id:record.group_id.clone(),session_id:record.scope.session_id.clone()},
        run_id:record.run_id.clone(),expected_deadline_ms:record.deadline_ms}
}
fn interaction_id(input:&ExecInput,tool:&str)->String {
    // Request and tool remain opaque strings; no path or separator ambiguity.
    let digest=Sha256::digest(json!([input.id,tool]).to_string().as_bytes());
    format!("a2a-exec-{}",hex::encode(digest))
}
fn identity(record:&A2aRunRecord)->ServiceResult<(&str,&str)> {
    Ok((record.remote_task_id.as_deref().ok_or_else(||invalid("input Task missing"))?,
        record.context_id.as_deref().ok_or_else(||invalid("input context missing"))?))
}
fn stored_input(record:&A2aRunRecord,at_ms:u64)->ServiceResult<ExecInput> {
    let (task,context)=identity(record)?;
    let message=serde_json::from_value(record.pending_input.clone().ok_or_else(||invalid("no pending input"))?)?;
    ExecInput::decode(&message,task,context,at_ms).map_err(invalid)
}
fn pending(record:&A2aRunRecord)->ServiceResult<ExecInput> {stored_input(record,now_ms())}
fn resolution(input:&ExecInput,command:&InteractionProviderCommand)->ServiceResult<(String,Decision)> {
    if command.kind!=InteractionKind::Exec {return Err(invalid("unsupported input kind"));}
    let tool=input.tools.keys().find(|tool|interaction_id(input,tool)==command.interaction_id)
        .ok_or_else(||invalid("input interaction mismatch"))?.clone();
    let decision=match command.resolution.get("decision").and_then(Value::as_str) {
        Some("allow_once")=>Decision::AllowOnce,Some("deny")=>Decision::Deny,_=>return Err(invalid("unsupported exec decision")),
    };
    if input.accepted.get(&tool).is_some_and(|value|value!=&decision) {return Err(invalid("conflicting exec decision"));}
    Ok((tool,decision))
}

impl A2aHttpTransport {
    pub fn set_input_runtime(&self,runtime:InputRuntime)->ServiceResult<()> {
        if runtime.limits.input_wait_ms==0 || runtime.limits.total_run_ms==0 {return Err(invalid("positive input budgets required"));}
        runtime.service()?;
        let mut slot=self.inner.input.write().map_err(|_|invalid("input runtime lock failed"))?;
        if slot.is_some() {return Err(invalid("input runtime already bound"));}
        *slot=Some(runtime);Ok(())
    }
    pub(super) fn input_runtime(&self)->ServiceResult<InputRuntime> {
        self.inner.input.read().map_err(|_|invalid("input runtime lock failed"))?.clone()
            .ok_or_else(||invalid("Structured Input not configured"))
    }
    pub(super) async fn tracking_lost(&self,record:&A2aRunRecord)->ServiceResult<()> {
        // Minimal standalone transport fixtures have no managed queue. The
        // production composition always binds this lifecycle collaborator.
        let runtime=self.inner.input.read().map_err(|_|invalid("input runtime lock failed"))?.clone();
        if let Some(runtime)=runtime {
            let scope=BotRunScope {bot_id:record.scope.bot_id.clone(),group_id:record.group_id.clone(),session_id:record.scope.session_id.clone()};
            runtime.deliveries.mark_run_transport_unknown(&scope,&record.run_id,now_ms()).await
                .map_err(|_|invalid("uncertain Run persistence failed"))?;
            runtime.service()?.invalidate_run(&record.run_id,"a2a_tracking_lost",now_ms()).await?;
        }
        Ok(())
    }
    pub(super) async fn change_budget(&self,record:&mut A2aRunRecord,update:RunBudgetUpdate,state:A2aRunState)->ServiceResult<()> {
        let change=self.input_runtime()?.budgets.update_deadline(budget_target(record),update,now_ms()).await?;
        let mut next=record.clone();next.budget=Some(change.state);next.deadline_ms=change.deadline_ms;
        self.save(&mut next,state).await?;*record=next;Ok(())
    }
    pub(super) async fn request_input(&self,record:&mut A2aRunRecord,message:a2a::Message)->ServiceResult<()> {
        if !record.extensions.iter().any(|uri|uri==EXTENSION) {return Err(invalid("unnegotiated Structured Input"));}
        let (task,context)=identity(record)?;
        let next=ExecInput::decode(&message,task,context,now_ms()).map_err(invalid)?;
        if record.pending_input.is_some() {
            // Completed input is historical binding data. Its old expiry must
            // not prevent a later confirmation batch after execution resumes.
            // The new request and every actionable reply still validate now.
            let previous=stored_input(record,0)?;
            if previous.id==next.id {
                // A queued native continuation can still expose its old schema
                // briefly after every decision has been durably accepted.
                if previous.accepted.len()==previous.tools.len() {
                    if previous.tools!=next.tools || next.accepted.iter().any(|(id,value)|previous.accepted.get(id)!=Some(value)) {
                        return Err(invalid("completed input binding changed"));
                    }
                    return Ok(());
                }
                previous.check_update(&next).map_err(invalid)?;
            } else if previous.accepted.len()!=previous.tools.len() {return Err(invalid("pending input replaced before completion"));}
        }
        let input=self.input_runtime()?;
        let budget=record.budget.as_ref().ok_or_else(||invalid("input budget missing"))?;
        let update=RunBudgetUpdate::Pause {state:budget.clone(),input_expires_at_ms:next.expires_at_ms};
        let mut updated=record.clone();updated.pending_input=Some(serde_json::to_value(next.message(task,context).map_err(invalid)?)?);
        self.change_budget(&mut updated,update,A2aRunState::InputRequired).await?;*record=updated;
        let target=self.inner.active.lock().map_err(|_|invalid("active map lock failed"))?.get(&record.run_id)
            .map(|active|active.target.clone()).ok_or_else(||invalid("input owner missing"))?;
        let task=record.remote_task_id.clone().ok_or_else(||invalid("input Task missing"))?;
        let service=input.service()?;
        for (tool_id,tool) in &next.tools {
            if next.accepted.contains_key(tool_id) {continue;}
            let id=interaction_id(&next,tool_id);
            let outcome=service.on_provider_requested(ProviderInteractionRequestedCommand {
                bcs_run_id:record.run_id.clone(),provider_run_id:task.clone(),interaction_id:id.clone(),kind:InteractionKind::Exec,
                bcs_session_id:record.scope.session_id.clone(),group_id:record.group_id.clone(),bot_id:record.scope.bot_id.clone(),
                run_deadline_ms:record.deadline_ms,provider_target:target.clone(),provider_bypass_headers:vec![],received_at_ms:now_ms(),
                payload:json!({"runId":record.run_id,"seq":0,"phase":"requested","interactionId":id,"kind":"exec",
                    "command":tool.name,"toolCallId":tool_id,"args":tool.args,"argsSha256":tool.args_sha256,
                    "inputRequestId":next.id,"expiresAtMs":record.deadline_ms,
                    "options":[{"decision":"allow_once","label":"Allow once"},{"decision":"deny","label":"Deny"}]}),
            }).await?;
            if !matches!(outcome,InteractionRequestedOutcome::Stored | InteractionRequestedOutcome::Duplicate) {
                return Err(invalid("input interaction could not be stored"));
            }
        }
        Ok(())
    }

    async fn resolve_pending(&self,record:&mut A2aRunRecord,command:&InteractionProviderCommand)->ServiceResult<Option<a2a::Task>> {
        let input=pending(record)?;let (tool,decision)=resolution(&input,command)?;
        let complete=input.accepted.len()+usize::from(!input.accepted.contains_key(&tool))==input.tools.len();
        if complete {
            let state=record.budget.clone().ok_or_else(||invalid("input budget missing"))?;
            self.change_budget(record,RunBudgetUpdate::Resume {state},A2aRunState::Running).await?;
        }
        let (task,context)=identity(record)?;
        let message_id=format!("a2a-resolution-{}",hex::encode(Sha256::digest(json!([record.run_id,input.id,tool,command.idempotency_key]).to_string().as_bytes())));
        let message=input.reply(&tool,decision.clone(),&message_id,task,context,now_ms()).map_err(invalid)?;
        let request=serde_json::from_value(json!({"message":message,"configuration":{"returnImmediately":true,"acceptedOutputModes":["text/plain"]}}))?;
        let timeout=record.deadline_ms.saturating_sub(now_ms()).min(15_000);
        if timeout==0 {return Err(invalid("input deadline expired"));}
        let response=tokio::time::timeout(Duration::from_millis(timeout),self.client(record).await?.send_message(&request)).await
            .map_err(|_|invalid("input acceptance outcome unknown"))?.map_err(|error|delivery_error::protocol_failure(&error,"input continuation failed"))?;
        let a2a::SendMessageResponse::Task(mut snapshot)=response else {return Err(invalid("input continuation must retain Task"));};
        let mut confirmed=false;
        for attempt in 0..4 {
            if snapshot.id!=task || snapshot.context_id!=context {return Err(invalid("input response identity mismatch"));}
            let raw=serde_json::to_value(&snapshot)?;
            if input.accepted_by_task(&raw["metadata"],&tool,&decision).map_err(invalid)? {confirmed=true;break;}
            if attempt==3 || now_ms()>=record.deadline_ms {break;}
            tokio::time::sleep(Duration::from_millis(100*(attempt+1))).await;
            snapshot=self.snapshot(record).await?;
        }
        if !confirmed || now_ms()>=record.deadline_ms {return Err(invalid("input receipt unconfirmed or execution expired"));}
        let accepted=input.with_receipt(&tool,decision.clone()).map_err(invalid)?;
        record.pending_input=Some(serde_json::to_value(accepted.message(task,context).map_err(invalid)?)?);
        self.save(record,record.state).await?;
        // Resolve before allowing a fast Task terminal to invalidate still
        // pending local cards. Existing management supports this callback racing
        // its acceptance commit. This is decision acceptance, not tool success.
        self.input_runtime()?.service()?.on_provider_resolved(ProviderInteractionResolvedCommand {
            bcs_run_id:record.run_id.clone(),provider_run_id:record.remote_task_id.clone().ok_or_else(||invalid("input Task missing"))?,
            interaction_id:command.interaction_id.clone(),kind:InteractionKind::Exec,received_at_ms:now_ms(),
            payload:json!({"runId":record.run_id,"seq":0,"interactionId":command.interaction_id,"phase":"resolved","kind":"exec",
                "decision":decision.as_str(),"inputRequestId":input.id,"toolCallId":tool,"receipt":"accepted"}),
        }).await?;
        if complete {Ok(Some(snapshot))} else {
            if snapshot.status.state.is_terminal() {return Err(invalid("Task ended before all input decisions"));}
            Ok(None)
        }
    }

    /// None is an explicit stop; a successful last decision returns the Task
    /// calibration to the stream owner. Partial decisions keep waiting locally.
    pub(super) async fn wait_input(&self,record:&mut A2aRunRecord,stop:&mut tokio::sync::watch::Receiver<bool>,
        resolutions:&mut tokio::sync::mpsc::Receiver<Resolution>)->ServiceResult<Option<a2a::Task>> {
        loop {
            let remaining=record.deadline_ms.saturating_sub(now_ms());
            if remaining==0 {return Err(invalid("input wait expired"));}
            let incoming=tokio::select! {
                _=stop.changed()=>return Ok(None),
                value=tokio::time::timeout(Duration::from_millis(remaining),resolutions.recv())=>
                    value.map_err(|_|invalid("input wait expired"))?.ok_or_else(||invalid("input owner closed"))?,
            };
            // A stale/invalid command cannot cancel an otherwise valid wait.
            if let Err(error)=pending(record).and_then(|input|resolution(&input,&incoming.command)) {
                let _=incoming.reply.send(Err(error));continue;
            }
            match self.resolve_pending(record,&incoming.command).await {
                Ok(snapshot)=> {
                    let _=incoming.reply.send(Ok(InteractionProviderAck {ok:true,retryable:Some(false),error:None}));
                    if snapshot.is_some() {return Ok(snapshot);}
                }
                Err(error)=> {
                    let _=incoming.reply.send(Err(invalid("input acceptance unconfirmed; stopping Run")));
                    return Err(error);
                }
            }
        }
    }
}

#[async_trait]
impl InteractionProviderPort for A2aHttpTransport {
    async fn resolve_interaction(&self,command:InteractionProviderCommand)->ServiceResult<InteractionProviderAck> {
        let record=self.inner.runs.get(&command.bcs_run_id).await?.ok_or_else(||invalid("input Run missing"))?;
        let BotDeliveryTarget::A2a {bot_id,provider_id,provider_bot_ref,interface,..}=&command.target else {return Err(invalid("input target mismatch"));};
        if record.state!=A2aRunState::InputRequired || command.bot_id!=record.scope.bot_id || *bot_id!=record.scope.bot_id
            || command.group_id!=record.group_id || command.bcs_session_id!=record.scope.session_id
            || *provider_id!=record.scope.provider_id || *provider_bot_ref!=record.scope.provider_bot_ref
            || interface.url!=record.interface.url || interface.tenant!=record.interface.tenant
            || record.remote_task_id.as_deref()!=Some(command.provider_run_id.as_str()) {
            return Err(invalid("input scope mismatch or Run is not waiting"));
        }
        resolution(&pending(&record)?,&command)?;
        let sender=self.inner.active.lock().map_err(|_|invalid("active map lock failed"))?.get(&record.run_id)
            .map(|active|active.resolutions.clone()).ok_or_else(||invalid("input owner unavailable"))?;
        let (reply,result)=tokio::sync::oneshot::channel();
        sender.try_send(Resolution {command,reply}).map_err(|_|invalid("input owner busy"))?;
        result.await.map_err(|_|invalid("input owner stopped before acceptance"))?
    }
}
