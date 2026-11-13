mod common;
use common::*;
use async_trait::async_trait;
use bcn_runtime_api::{application::*,port::{*,repo::*}};
use bcn_runtime_app::{DefaultConnector,DefaultGateway};
use bcn_runtime_model::*;
use std::sync::{Arc,atomic::{AtomicU64,Ordering}};
struct UnaryCatalog;
#[async_trait]
impl AgentCatalog for UnaryCatalog {
    async fn resolve(&self,c:&CallerContext,t:&ExecutionTarget,s:&SessionId)->RuntimeResult<SessionSpec> {
        let mut spec=Catalog.resolve(c,t,s).await?;spec.bindings.values_mut().next().unwrap().capabilities.streaming=false;Ok(spec)
    }
}
struct Unary {snapshot:ExecutionSnapshot,sends:AtomicU64,queries:AtomicU64,cancels:AtomicU64}
#[async_trait]
impl AgentConnector for Unary {
    fn capabilities(&self)->ConnectorCapabilities {let mut c=binding().capabilities;c.streaming=false;c}
    async fn send_message(&self,_:AgentSendRequest)->ConnectorResult<AgentSendResponse> {
        self.sends.fetch_add(1,Ordering::SeqCst);Ok(AgentSendResponse::Task{snapshot:self.snapshot.clone()})
    }
    async fn cancel_task(&self,r:AgentCancelRequest)->ConnectorResult<CancelOutcome> {
        assert_eq!(r.handle,handle());self.cancels.fetch_add(1,Ordering::SeqCst);Ok(CancelOutcome::Unknown)
    }
    async fn get_task(&self,h:ExecutionHandle)->ConnectorResult<ExecutionSnapshot> {
        assert_eq!(h,handle());self.queries.fetch_add(1,Ordering::SeqCst);Ok(self.snapshot.clone())
    }
}
fn failed_snapshot(terminal:bool)->ExecutionSnapshot {
    let mut value=serde_json::to_value(Transport::new(vec![]).query.lock().unwrap().clone()).unwrap();
    value["status"]=serde_json::json!(if terminal {"completed"} else {"working"});
    value["output"]=serde_json::json!([]);value["observation_error"]=serde_json::json!("execution_unknown");
    serde_json::from_value(value).unwrap()
}
#[tokio::test]
async fn unary_and_recovered_snapshot_errors_save_handle_and_preserve_termination_and_partial() {
    for recovered in [false,true] {for terminal in [false,true] {
        let f=Fixture::new();f.results.down.store(true,Ordering::SeqCst);
        let g=DefaultGateway::new(f.ports.clone(),f.runners(),Arc::new(UnaryCatalog),vec![Arc::new(Allow)],f.clock.clone(),true).unwrap();
        let first=g.submit(caller(),request("observation-first")).await.unwrap();
        let mut queued=request("observation-second");queued.message.context_id=Some(first.session_id.clone());
        let second=g.submit(caller(),queued).await.unwrap();let w=f.worker("w");w.step(&first.session_id).await.unwrap();
        let command=f.repo.receive(&scope(),&node(if recovered{"old"}else{"c"}),CommandConsumerKind::Connector,None,1).await.unwrap().items.remove(0);
        let EffectIntent::SendAgent{request:send,..}=&command.effect else {panic!("expected send")};
        let dispatch_id=send.dispatch_id.clone();
        if recovered {
            let record=f.repo.claim(&command.command_id,node("old")).await.unwrap();
            let record=f.repo.mark_may_have_sent(&record.fence,record.revision).await.unwrap();
            let record=f.repo.save_handle(&record.fence,record.revision,handle()).await.unwrap();
            f.repo.ack(&command.command_id,&node("old")).await.unwrap();
            let cursor=f.repo.read_snapshot(&first.task_id).await.unwrap().unwrap().cursor;
            f.repo.publish_reset(&record.fence,ConnectorProjection{task_id:first.task_id.clone(),output:text("persisted partial"),expected_cursor:cursor},EventId::new(scope(),"partial")).await.unwrap();
        }
        let transport=Arc::new(Unary{snapshot:failed_snapshot(terminal),sends:AtomicU64::new(0),queries:AtomicU64::new(0),cancels:AtomicU64::new(0)});
        let mut b=binding();b.capabilities.streaming=false;
        let c=DefaultConnector::new(node("c"),f.ports.clone(),vec![(b,transport.clone())],f.clock.clone()).unwrap();
        f.pump(&w,&c,16).await;
        let record=f.repo.read(&dispatch_id).await.unwrap().unwrap();assert_eq!(record.handle,Some(handle()),"handle saved before failure receipt");
        let cp=f.repo.load(&first.session_id).await.unwrap().unwrap();assert_eq!(cp.state.context_blocked,!terminal);
        assert_eq!(g.get(caller(),first.task_id.clone()).await.unwrap().status,TaskStatus::PendingResult);
        let event=match record.receipt.unwrap(){DispatchReceipt::Agent{event}=>event,_=>panic!("agent receipt")};
        let output=if terminal {let AgentEvent::Snapshot{snapshot}=event else {panic!("terminal evidence lost")};assert_eq!(snapshot.status,TaskStatus::Failed);assert_eq!(snapshot.handle,handle());snapshot.output}
            else {let AgentEvent::ObservationFailed{output,..}=event else {panic!("remote termination fabricated")};output};
        assert_eq!(output,if recovered{text("persisted partial")}else{vec![]});
        assert_eq!(transport.sends.load(Ordering::SeqCst),u64::from(!recovered));
        assert_eq!(transport.queries.load(Ordering::SeqCst),u64::from(recovered));
        assert_eq!(transport.cancels.load(Ordering::SeqCst),u64::from(!terminal));
        assert_ne!(g.get(caller(),second.task_id).await.unwrap().status,TaskStatus::Completed);
        f.results.down.store(false,Ordering::SeqCst);
        // Lifecycle::backoff caps retry_at at now + 5000 ms. Advance that known
        // window, execute the retained result command, then reduce ResultSaved.
        f.clock.set(Clock::now_ms(f.clock.as_ref())+5000);
        w.tick().await.unwrap();w.step(&first.session_id).await.unwrap();
        let done=g.get(caller(),first.task_id).await.unwrap();assert_eq!(done.status,TaskStatus::Failed);assert_eq!(done.error,Some(if terminal{RuntimeError::Unavailable("terminal observation failed".into())}else{RuntimeError::ExecutionUnknown}));
        if terminal {
            let commands=f.repo.receive(&scope(),&node("inspect"),CommandConsumerKind::Connector,None,10).await.unwrap().items;
            let next=commands.iter().find_map(|c|match &c.effect{EffectIntent::SendAgent{request,..}=>Some(request),_=>None}).expect("next Direct task dispatched");
            let mut context=handle();context.remote_task_id=None;assert_eq!(next.handle,Some(context));
        }
    }}
}

struct BadMessage {with_context:bool,sends:AtomicU64}
#[async_trait]
impl AgentConnector for BadMessage {
    fn capabilities(&self)->ConnectorCapabilities {let mut c=binding().capabilities;c.streaming=false;c}
    async fn send_message(&self,_:AgentSendRequest)->ConnectorResult<AgentSendResponse> {
        self.sends.fetch_add(1,Ordering::SeqCst);
        let mut value=serde_json::json!({"kind":"message","parts":text("known partial"),"observation_error":"execution_unknown"});
        if self.with_context {let mut h=handle();h.remote_task_id=None;value["handle"]=serde_json::to_value(h).unwrap();}
        Ok(serde_json::from_value(value).unwrap())
    }
}
#[tokio::test]
async fn direct_message_projection_errors_are_terminal_with_or_without_remote_context() {
    for with_context in [false,true] {
        let f=Fixture::new();let g=DefaultGateway::new(f.ports.clone(),f.runners(),Arc::new(UnaryCatalog),vec![Arc::new(Allow)],f.clock.clone(),true).unwrap();
        let task=g.submit(caller(),request("bad-message")).await.unwrap();let w=f.worker("w");w.step(&task.session_id).await.unwrap();
        let transport=Arc::new(BadMessage{with_context,sends:AtomicU64::new(0)});let mut b=binding();b.capabilities.streaming=false;
        let c=DefaultConnector::new(node("c"),f.ports.clone(),vec![(b,transport.clone())],f.clock.clone()).unwrap();
        f.pump(&w,&c,8).await;
        let done=g.get(caller(),task.task_id).await.unwrap();assert_eq!(done.status,TaskStatus::Failed);assert_eq!(done.error,Some(RuntimeError::Unavailable("terminal observation failed".into())));assert_eq!(done.output,text("known partial"));
        let cp=f.repo.load(&task.session_id).await.unwrap().unwrap();assert!(!cp.state.context_blocked);
        if with_context {let h=cp.state.direct_context.unwrap();assert_eq!(h.remote_context_id.as_deref(),Some("conversation"));assert!(h.remote_task_id.is_none());}
        assert_eq!(transport.sends.load(Ordering::SeqCst),1);
    }
}

#[tokio::test]
async fn unary_input_prompt_and_partial_output_survive_together() {
    for prompt in [None,Some("Which account?"),Some("")] {
        let f=Fixture::new();
        let g=DefaultGateway::new(f.ports.clone(),f.runners(),Arc::new(UnaryCatalog),vec![Arc::new(Allow)],f.clock.clone(),true).unwrap();
        let task=g.submit(caller(),request("prompt")).await.unwrap();let w=f.worker("w");
        let mut value=serde_json::to_value(failed_snapshot(false)).unwrap();
        value["status"]=serde_json::json!("input_required");value["observation_error"]=serde_json::Value::Null;
        value["output"]=serde_json::to_value(text("partial artifact")).unwrap();
        if let Some(prompt)=prompt {value["input_prompt"]=serde_json::to_value(if prompt.is_empty(){vec![]}else{text(prompt)}).unwrap();}
        let transport=Arc::new(Unary{snapshot:serde_json::from_value(value).unwrap(),sends:AtomicU64::new(0),queries:AtomicU64::new(0),cancels:AtomicU64::new(0)});
        let mut b=binding();b.capabilities.streaming=false;
        let c=DefaultConnector::new(node("c"),f.ports.clone(),vec![(b,transport.clone())],f.clock.clone()).unwrap();
        f.pump(&w,&c,8).await;
        let view=g.get(caller(),task.task_id.clone()).await.unwrap();assert_eq!(view.status,TaskStatus::InputRequired);assert_eq!(view.output,text("partial artifact"));
        let cursor=f.repo.read_snapshot(&task.task_id).await.unwrap().unwrap().cursor;
        let events=f.repo.read_after(&task.task_id,Some(EventCursor{epoch:cursor.epoch,sequence:0}),100).await.unwrap();
        assert!(events.events.iter().any(|e|matches!(&e.kind,TaskEventKind::Domain{event:DomainEvent{kind:DomainEventKind::InputRequired{prompt:p,..},..}} if p==prompt.unwrap_or("partial artifact"))));
        assert_eq!(transport.sends.load(Ordering::SeqCst),1);assert_eq!(transport.cancels.load(Ordering::SeqCst),0);
    }
}

#[tokio::test]
async fn input_prompt_validation_does_not_clear_prior_partial() {
    for mode in ["question","oversized","legacy-empty"] {
        let oversized=mode=="oversized";
        let f=Fixture::new();let g=f.gateway();let task=g.submit(caller(),request("prompt-bounds")).await.unwrap();let w=f.worker("w");
        let mut value=serde_json::to_value(failed_snapshot(false)).unwrap();
        value["status"]=serde_json::json!("input_required");value["observation_error"]=serde_json::Value::Null;
        if mode!="legacy-empty" {value["input_prompt"]=serde_json::to_value(text(&if oversized{"q".repeat(65536)}else{"Question?".into()})).unwrap();}
        let transport=Arc::new(Transport::new(vec![Ok(AgentEvent::Accepted{handle:handle()}),Ok(AgentEvent::Delta{text:"prior partial".into()}),Ok(AgentEvent::Snapshot{snapshot:serde_json::from_value(value).unwrap()})]));
        let c=f.connector("c",transport.clone());f.pump(&w,&c,12).await;
        let done=g.get(caller(),task.task_id.clone()).await.unwrap();assert_eq!(done.output,text("prior partial"));
        if mode=="legacy-empty" {
            let cursor=f.repo.read_snapshot(&task.task_id).await.unwrap().unwrap().cursor;
            let events=f.repo.read_after(&task.task_id,Some(EventCursor{epoch:cursor.epoch,sequence:0}),100).await.unwrap();
            assert!(events.events.iter().any(|e|matches!(&e.kind,TaskEventKind::Domain{event:DomainEvent{kind:DomainEventKind::InputRequired{prompt,..},..}} if prompt.is_empty())));
        }
        assert_eq!(done.status,if oversized{TaskStatus::Failed}else{TaskStatus::InputRequired});
        if oversized {assert_eq!(done.error,Some(RuntimeError::CapacityExceeded));}
        let cp=f.repo.load(&task.session_id).await.unwrap().unwrap();assert_eq!(cp.state.context_blocked,oversized);
        let mut expected=handle();if oversized{expected.remote_task_id=None;}
        assert_eq!(cp.state.direct_context,Some(expected));
        assert_eq!(transport.sends.load(Ordering::SeqCst),1);
    }
}
