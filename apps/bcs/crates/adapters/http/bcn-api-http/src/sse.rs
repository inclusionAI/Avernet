use crate::{ApiState,mapping};
use a2a::{JsonRpcId,JsonRpcResponse,StreamResponse};
use axum::response::{IntoResponse,Response,Sse,sse::Event};
use bcn_runtime_api::application::EventStream;
use bcn_runtime_model::*;
use futures::StreamExt;
use std::{collections::VecDeque,convert::Infallible};
pub fn cursor(value:&str)->Result<EventCursor,a2a::A2AError>{
    let (epoch,sequence)=value.split_once(':').ok_or_else(||a2a::A2AError::invalid_params("cursor must be epoch:sequence"))?;
    Ok(EventCursor{epoch:epoch.parse().map_err(|_|a2a::A2AError::invalid_params("invalid cursor epoch"))?,sequence:sequence.parse().map_err(|_|a2a::A2AError::invalid_params("invalid cursor sequence"))?})
}
fn encode(id:&JsonRpcId,response:StreamResponse,cursor:Option<&EventCursor>)->Event {
    let rpc=JsonRpcResponse::success(id.clone(),serde_json::to_value(response).expect("protocol serialization"));
    let event=Event::default().data(serde_json::to_string(&rpc).expect("protocol serialization"));
    if let Some(c)=cursor {event.id(format!("{}:{}",c.epoch,c.sequence))}else{event}
}
fn replacement(id:&JsonRpcId,view:&TaskView,cursor:Option<&EventCursor>,clear_output:bool)->VecDeque<Event>{
    let task=mapping::task(view);
    let mut queue=VecDeque::new();
    if !view.output.is_empty() || clear_output{
        // An empty Text Part clears previously streamed text; an Artifact may not have zero parts.
        let artifact=if view.output.is_empty(){mapping::artifact(&[MessagePart::Text{text:String::new()}])}else{mapping::artifact(&view.output)};
        queue.push_back(encode(id,StreamResponse::ArtifactUpdate(a2a::TaskArtifactUpdateEvent{task_id:task.id.clone(),context_id:task.context_id.clone(),artifact,append:Some(false),last_chunk:Some(view.status.is_terminal()),metadata:None}),None));
    }
    queue.push_back(encode(id,StreamResponse::StatusUpdate(a2a::TaskStatusUpdateEvent{task_id:task.id,context_id:task.context_id,status:task.status,metadata:task.metadata}),cursor));
    queue
}
pub fn terminal(id:JsonRpcId,view:TaskView)->Response{
    let mut queue=VecDeque::from([encode(&id,StreamResponse::Task(mapping::task(&view)),None)]);
    queue.extend(replacement(&id,&view,None,false));
    Sse::new(futures::stream::iter(queue.into_iter().map(Ok::<_,Infallible>))).into_response()
}
pub fn response(state:ApiState,ctx:CallerContext,id:JsonRpcId,view:TaskView,stream:EventStream)->Response{
    let stream=futures::stream::unfold((stream,view,VecDeque::new(),false,false),move |(mut stream,mut view,mut queue,mut done,mut started)|{
        let state=state.clone();let ctx=ctx.clone();let id=id.clone();
        async move { loop {
            if let Some(e)=queue.pop_front(){return Some((Ok::<_,Infallible>(e),(stream,view,queue,done,started)))}
            if done{return None}
            let event=match stream.next().await {Some(Ok(e))=>e,Some(Err(e))=>{
                let rpc=JsonRpcResponse::error(id.clone(),mapping::error(e).to_jsonrpc_error());
                queue.push_back(Event::default().data(serde_json::to_string(&rpc).expect("protocol serialization")));done=true;continue
            },None=>return None};
            let cursor=event.cursor;
            match event.kind {
                TaskEventKind::Reset{snapshot}=>{
                    let clear_output=!view.output.is_empty() && snapshot.output.is_empty();view=snapshot;
                    if !started {
                        queue.push_back(encode(&id,StreamResponse::Task(mapping::task(&view)),Some(&cursor)));started=true;
                        if view.status.is_terminal(){queue.extend(replacement(&id,&view,Some(&cursor),false));}
                    }else{queue.extend(replacement(&id,&view,Some(&cursor),clear_output));}
                    done=view.status.is_terminal();
                }
                TaskEventKind::Delta{text}=>{
                    started=true;
                    if let Some(MessagePart::Text{text:tail})=view.output.last_mut(){tail.push_str(&text)}else{view.output.push(MessagePart::Text{text:text.clone()})}
                    queue.push_back(encode(&id,StreamResponse::ArtifactUpdate(a2a::TaskArtifactUpdateEvent{task_id:view.task_id.value.clone(),context_id:view.session_id.value.clone(),artifact:mapping::artifact(&[MessagePart::Text{text}]),append:Some(true),last_chunk:Some(false),metadata:None}),Some(&cursor)));
                }
                TaskEventKind::Domain{event}=>{
                    started=true;
                    match event.kind {
                        DomainEventKind::TaskStatusChanged{status}=>view.status=status,
                        DomainEventKind::InputRequired{interaction_id,prompt}=>{
                            view.active_interactions.retain(|i|i.interaction_id!=interaction_id);
                            view.active_interactions.push(ActiveInteraction{interaction_id,prompt});view.status=TaskStatus::InputRequired;
                        }
                        DomainEventKind::InteractionResolved{interaction_id}=>view.active_interactions.retain(|i|i.interaction_id!=interaction_id),
                        _=>{}
                    }
                    if view.status.is_terminal(){
                        match state.gateway.get(ctx.clone(),view.task_id.clone()).await {
                            Ok(authoritative)=>{let clear_output=!view.output.is_empty() && authoritative.output.is_empty();view=authoritative;queue.extend(replacement(&id,&view,Some(&cursor),clear_output));}
                            Err(e)=>{let rpc=JsonRpcResponse::error(id.clone(),mapping::error(e).to_jsonrpc_error());queue.push_back(Event::default().data(serde_json::to_string(&rpc).expect("protocol serialization")));}
                        }
                        done=true;
                    }else{
                        // Domain deltas carry no publication time; do not reuse an older snapshot timestamp.
                        let mut task=mapping::task(&view);task.status.timestamp=None;queue.push_back(encode(&id,StreamResponse::StatusUpdate(a2a::TaskStatusUpdateEvent{task_id:task.id,context_id:task.context_id,status:task.status,metadata:task.metadata}),Some(&cursor)));
                    }
                }
            }
        }}
    });
    Sse::new(stream).keep_alive(axum::response::sse::KeepAlive::default()).into_response()
}
