//! Projection reuses the existing A2A artifact/identity state machine.
use a2a::{StreamResponse, TaskState};
use bcn_runtime_api::port::AgentEventStream;
use bcn_runtime_model::*;
use bcs_a2a_http::projection::{Projection, Output};
use futures::{StreamExt, stream::BoxStream};
use std::collections::VecDeque;
use crate::errors::*;

const MAX_OBSERVATION_BYTES:usize=8*1024*1024;

fn handle(binding:&FrozenTargetBinding, task:Option<String>, context:Option<String>) -> ExecutionHandle {
    ExecutionHandle{target:binding.target.clone(),binding_version:binding.binding_version.clone(),remote_task_id:task,remote_context_id:context}
}
fn matches_expected(actual:&ExecutionHandle, expected:Option<&ExecutionHandle>) -> bool {
    expected.is_none_or(|h|h.remote_task_id.as_ref().is_none_or(|id|Some(id)==actual.remote_task_id.as_ref())
        && h.remote_context_id.as_ref().is_none_or(|id|Some(id)==actual.remote_context_id.as_ref()))
}
pub(crate) fn message(binding:&FrozenTargetBinding, message:a2a::Message, expected:Option<&ExecutionHandle>) -> ConnectorResult<AgentSendResponse> {
    if message.role!=a2a::Role::Agent || message.task_id.is_some()
        || message.context_id.as_ref().is_some_and(String::is_empty) { return Err(uncertain()); }
    let h=handle(binding,None,message.context_id);
    if !matches_expected(&h,expected) {return Err(uncertain());}
    let (parts,observation_error)=project_parts(message.parts);
    Ok(AgentSendResponse::Message{parts,handle:h.remote_context_id.as_ref().map(|_|h.clone()),observation_error})
}
fn project_parts(parts:impl IntoIterator<Item=a2a::Part>)->(Vec<MessagePart>,Option<RuntimeError>) {
    let mut output=vec![];
    for part in parts {
        let a2a::PartContent::Text(text)=part.content else {
            return (output,Some(RuntimeError::Unsupported("A2A output part".into())));
        };
        let mut next=output.clone();next.push(MessagePart::Text{text});
        if !fits(&next) {return (output,Some(RuntimeError::CapacityExceeded));}
        output=next;
    }
    (output,None)
}

fn status(state:TaskState) -> ConnectorResult<TaskStatus> {
    Ok(match state {
        TaskState::Submitted=>TaskStatus::Submitted,TaskState::Working=>TaskStatus::Working,
        TaskState::Completed=>TaskStatus::Completed,TaskState::Failed|TaskState::Rejected=>TaskStatus::Failed,
        TaskState::Canceled=>TaskStatus::Cancelled,TaskState::InputRequired=>TaskStatus::InputRequired,
        TaskState::AuthRequired=>TaskStatus::AuthRequired,TaskState::Unspecified=>return Err(uncertain()),
    })
}
pub(crate) fn snapshot(binding:&FrozenTargetBinding, task:a2a::Task, expected:Option<&ExecutionHandle>) -> ConnectorResult<ExecutionSnapshot> {
    let h=handle(binding,Some(task.id.clone()),Some(task.context_id.clone()));
    if task.id.is_empty() || task.context_id.is_empty() || !matches_expected(&h,expected) {return Err(uncertain());}
    let (state,state_error)=match status(task.status.state.clone()) {
        Ok(state)=>(state,None),Err(_)=>(TaskStatus::Working,Some(RuntimeError::ExecutionUnknown)),
    };
    let (parts,part_error)=project_parts(task.artifacts.into_iter().flatten().flat_map(|a|a.parts));
    let observation_error=state_error.or(part_error);
    Ok(ExecutionSnapshot{handle:h,status:state,output:parts,observation_error,
        error:(state==TaskStatus::Failed).then(||RuntimeError::Unavailable("remote task failed".into()))})
}
struct State {
    binding:FrozenTargetBinding, expected:Option<ExecutionHandle>, projection:Projection,
    wire:BoxStream<'static,Result<StreamResponse,a2a::A2AError>>, handle:Option<ExecutionHandle>,
    text:String, pending:VecDeque<AgentEvent>, done:bool, observed_bytes:usize,
}
pub(crate) fn stream(binding:FrozenTargetBinding, expected:Option<ExecutionHandle>, wire:BoxStream<'static,Result<StreamResponse,a2a::A2AError>>) -> AgentEventStream {
    let state=State{binding,expected,projection:Projection::default(),wire,handle:None,text:String::new(),pending:VecDeque::new(),done:false,observed_bytes:0};
    futures::stream::unfold(state,|mut s|async move {
        loop {
            if let Some(event)=s.pending.pop_front() { return Some((Ok(event),s)); }
            if s.done {return None;}
            let Some(Ok(event))=s.wire.next().await else {s.done=true;return Some((Ok(observation(&s.text)),s));};
            if s.apply(event).is_err() {s.done=true;s.pending.push_back(observation(&s.text));}
        }
    }).boxed()
}
impl State {
    fn apply(&mut self,event:StreamResponse) -> ConnectorResult<()> {
        // A valid remote handle is independently useful even if the same Task's
        // output cannot be projected. Save it before reporting observation loss.
        if let StreamResponse::Task(task)=&event {
            let h=handle(&self.binding,Some(task.id.clone()),Some(task.context_id.clone()));
            if task.id.is_empty() || task.context_id.is_empty() || !matches_expected(&h,self.expected.as_ref())
                || self.handle.as_ref().is_some_and(|known|known!=&h) {return Err(uncertain());}
            if self.handle.is_none() {
                self.handle=Some(h.clone());self.pending.push_back(AgentEvent::Accepted{handle:h});
            }
        }
        // Bound retained projection metadata too: empty artifacts can grow the
        // projection without growing visible output. This is one observation budget.
        self.observed_bytes=self.observed_bytes.saturating_add(serde_json::to_vec(&event).map_err(|_|uncertain())?.len());
        if self.observed_bytes>MAX_OBSERVATION_BYTES {return Err(uncertain());}
        if let StreamResponse::Message(reply)=&event {
            if self.handle.is_some() {return Err(uncertain());}
            let AgentSendResponse::Message{parts,handle,observation_error}=message(&self.binding,reply.clone(),self.expected.as_ref())? else {return Err(uncertain());};
            if let Some(handle)=handle {self.pending.push_back(AgentEvent::Accepted{handle});}
            self.pending.push_back(match observation_error {
                Some(error)=>AgentEvent::FailedWithOutput{error,output:parts},
                None=>AgentEvent::Completed{output:parts},
            });
            self.done=true;return Ok(());
        }
        let (next,outputs)=match self.projection.stage(event.clone()) {
            Ok(staged)=>staged,
            Err(_)=>{
                if let StreamResponse::Task(task)=event {
                    let snapshot=snapshot(&self.binding,task,self.expected.as_ref())?;
                    let parts=if snapshot.output.is_empty(){output(&self.text)}else{snapshot.output};
                    let error=snapshot.observation_error.unwrap_or_else(||RuntimeError::Unsupported("A2A output projection".into()));
                    self.pending.push_back(if snapshot.status.is_terminal(){AgentEvent::FailedWithOutput{error,output:parts}}
                        else{AgentEvent::ObservationFailed{error,output:parts}});
                    self.done=true;return Ok(());
                }
                return Err(uncertain());
            }
        };
        let mut text=self.text.clone();
        let mut pending=VecDeque::new();
        let mut prompt=vec![];
        for event in outputs {
            match event {
                Output::Identity{task_id,context_id}=>{
                    let h=handle(&self.binding,Some(task_id),Some(context_id));
                    if !matches_expected(&h,self.expected.as_ref()) {return Err(uncertain());}
                    self.handle=Some(h);
                }
                Output::Delta(delta)=>{text.push_str(&delta);pending.push_back(AgentEvent::Delta{text:delta});}
                Output::StatusMessage(message)=>{
                    for p in message.parts {match p.content {a2a::PartContent::Text(text)=>prompt.push(MessagePart::Text{text}),_=>return Err(uncertain())}}
                }
                Output::Status(state)=>{
                    let status=status(state)?;
                    let h=self.handle.clone().ok_or_else(uncertain)?;
                    let parts=output(&text);
                    let bounded=fits(&parts) && fits(&prompt);
                    if !bounded {
                        self.done=true;
                        self.pending.push_back(if status.is_terminal() {AgentEvent::FailedWithOutput{error:RuntimeError::CapacityExceeded,output:output(&self.text)}}
                            else {AgentEvent::ObservationFailed{error:RuntimeError::CapacityExceeded,output:output(&self.text)}});
                        return Ok(());
                    }
                    match status {
                        TaskStatus::Completed=>{pending.push_back(AgentEvent::Completed{output:parts});self.done=true;}
                        TaskStatus::Failed|TaskStatus::Cancelled=>{
                            pending.push_back(AgentEvent::Snapshot{snapshot:ExecutionSnapshot{observation_error:None,handle:h,status,output:parts,
                                error:(status==TaskStatus::Failed).then(||RuntimeError::Unavailable("remote task failed".into()))}});self.done=true;
                        }
                        TaskStatus::InputRequired=>{pending.push_back(AgentEvent::InputRequired{handle:h,prompt:prompt.clone()});self.done=true;}
                        TaskStatus::AuthRequired=>{pending.push_back(AgentEvent::AuthRequired{handle:h});self.done=true;}
                        _=>{}
                    }
                }
                Output::MessageFinal{text:final_text,context_id}=>{
                    let h=handle(&self.binding,None,context_id);
                    if !matches_expected(&h,self.expected.as_ref()) {return Err(uncertain());}
                    if h.remote_context_id.is_some() {self.pending.push_back(AgentEvent::Accepted{handle:h});}
                    self.done=true;
                    if fits(&output(&final_text)) {text=final_text;pending.push_back(AgentEvent::Completed{output:output(&text)});}
                    else {pending.push_back(AgentEvent::FailedWithOutput{error:RuntimeError::CapacityExceeded,output:output(&self.text)});}
                }
                Output::Tool(_)=>return Err(uncertain()),
            }
        }
        if !fits(&output(&text)) {self.done=true;self.pending.push_back(AgentEvent::ObservationFailed{error:RuntimeError::CapacityExceeded,output:output(&self.text)});return Ok(());}
        self.text=text;self.projection=next;self.pending.extend(pending);Ok(())
    }
}
