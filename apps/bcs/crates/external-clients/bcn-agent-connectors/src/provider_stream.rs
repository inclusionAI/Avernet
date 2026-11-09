//! Bounded incremental bytes -> shared Provider frames -> neutral observations.
use bcn_runtime_model::*;
use bcn_runtime_api::port::AgentEventStream;
use bcs_protocol::stream::{parse_stream_event,StreamEvent,ChatState,AgentData,
    sse::{parse_sse_block,SeqDedup,SeqDecision}};
use futures::StreamExt;
use std::{collections::VecDeque,time::Duration};
use crate::errors::*;

const MAX_FRAME_BYTES:usize=256*1024;
const MAX_STREAM_BYTES:usize=8*1024*1024;
#[derive(Default)]
struct Decoder {buffer:Vec<u8>,block:String,frame_bytes:usize,started:bool}
impl Decoder {
    fn next(&mut self,eof:bool)->Result<Option<bcs_protocol::stream::sse::SseFrame>,()> {
        loop {
            let Some(end)=self.buffer.iter().position(|b|matches!(b,b'\r'|b'\n')) else {
                if self.frame_bytes.saturating_add(self.buffer.len())>MAX_FRAME_BYTES {return Err(());}return Ok(None);
            };
            let separator=if self.buffer[end]==b'\r' {
                if end+1==self.buffer.len() && !eof {return Ok(None);}if self.buffer.get(end+1)==Some(&b'\n'){2}else{1}
            }else{1};
            self.frame_bytes=self.frame_bytes.saturating_add(end+separator);
            if self.frame_bytes>MAX_FRAME_BYTES {return Err(());}
            let bytes=self.buffer.drain(..end).collect();self.buffer.drain(..separator);
            let mut line=String::from_utf8(bytes).map_err(|_|())?;
            if !self.started {line=line.trim_start_matches('\u{feff}').into();self.started=true;}
            if line.is_empty() {
                self.frame_bytes=0;let block=std::mem::take(&mut self.block);
                if let Some(frame)=parse_sse_block(&block) {return Ok(Some(frame));}
            }else{self.block.push_str(&line);self.block.push('\n');}
        }
    }
}
pub(crate) async fn body(mut response:reqwest::Response,timeout_ms:u64)->Result<Vec<u8>,()> {
    tokio::time::timeout(Duration::from_millis(timeout_ms),async {
        let mut bytes=vec![];
        while let Some(chunk)=response.chunk().await.map_err(|_|())? {
            if bytes.len().saturating_add(chunk.len())>MAX_FRAME_BYTES{return Err(());}bytes.extend_from_slice(&chunk);
        }
        Ok(bytes)
    }).await.map_err(|_|())?
}
struct State {
    binding:FrozenTargetBinding,context:String,response:reqwest::Response,timeout_ms:u64,
    decoder:Decoder,eof:bool,received:usize,seq:SeqDedup,handle:Option<ExecutionHandle>,text:String,pending:VecDeque<AgentEvent>,done:bool,
}
pub(crate) fn stream(binding:FrozenTargetBinding,context:String,response:reqwest::Response,timeout_ms:u64)->AgentEventStream {
    let state=State{binding,context,response,timeout_ms,decoder:Decoder::default(),eof:false,received:0,seq:SeqDedup::default(),handle:None,text:String::new(),pending:VecDeque::new(),done:false};
    futures::stream::unfold(state,|mut s|async move {
        loop {
            if let Some(event)=s.pending.pop_front(){return Some((Ok(event),s));}
            if s.done{return None;}
            match s.decoder.next(s.eof) {
                Ok(Some(frame))=>{if s.apply(&frame.event,&frame.data).is_err(){s.fail();}continue;}
                Err(_)=>{s.fail();continue;}
                Ok(None)=>{}
            }
            if s.eof {s.fail();continue;}
            match tokio::time::timeout(Duration::from_millis(s.timeout_ms),s.response.chunk()).await {
                Ok(Ok(Some(chunk)))=>{
                    s.received=s.received.saturating_add(chunk.len());
                    if s.received>MAX_STREAM_BYTES {s.fail();}else{s.decoder.buffer.extend_from_slice(&chunk);}
                }
                Ok(Ok(None))=>s.eof=true,
                _=>s.fail(),
            }
        }
    }).boxed()
}
impl State {
    fn fail(&mut self){self.done=true;self.pending.push_back(observation(&self.text));}
    fn identity(&mut self,run:&str,context:Option<&str>)->Result<(),()> {
        if run.is_empty() || context.is_some_and(|c|c!=self.context) {return Err(());}
        if let Some(h)=&self.handle {if h.remote_task_id.as_deref()!=Some(run){return Err(());}}
        else {
            let handle=ExecutionHandle{target:self.binding.target.clone(),binding_version:self.binding.binding_version.clone(),
                remote_task_id:Some(run.into()),remote_context_id:Some(self.context.clone())};
            self.handle=Some(handle.clone());self.pending.push_back(AgentEvent::Accepted{handle});
        }
        Ok(())
    }
    fn apply(&mut self,event:&str,data:&str)->Result<(),()> {
        if event=="ping" {return Ok(());}
        let raw=serde_json::from_str(data).map_err(|_|())?;
        let event=parse_stream_event(event,raw);
        let (run,context,seq)=match &event {
            StreamEvent::Chat(e)=>(e.run_id.as_str(),e.session_id.as_deref(),e.seq),
            StreamEvent::Agent(e)=>(e.run_id.as_str(),e.session_id.as_deref(),e.seq),
            StreamEvent::Interaction(e)=>(e.run_id.as_str(),e.session_id.as_deref(),e.seq),
            _=>return Err(()),
        };
        self.identity(run,context)?;
        match self.seq.accept(seq) {SeqDecision::Duplicate=>return Ok(()),SeqDecision::Gap(_)=>return Err(()),SeqDecision::Accept=>{}}
        match event {
            StreamEvent::Chat(chat)=>{
                let full=(||->Result<Option<String>,()> {
                    match &chat.message {
                        Some(message)=>{
                            let blocks=message.get("content").and_then(serde_json::Value::as_array).ok_or(())?;
                            let mut text=String::new();
                            for block in blocks {
                                if block.get("type").and_then(serde_json::Value::as_str).is_some_and(|t|t!="text") {return Err(());}
                                text.push_str(block.get("text").and_then(serde_json::Value::as_str).ok_or(())?);
                            }
                            Ok(Some(text))
                        }
                        None=>Ok(chat.content),
                    }
                })();
                let full=match full {
                    Ok(full)=>full,
                    Err(())=>{
                        // Identity, ordering and typed terminal state are already
                        // validated; unsupported content cannot erase termination.
                        let error=RuntimeError::Unsupported("Provider output content".into());
                        self.done=true;
                        self.pending.push_back(if chat.state==ChatState::Delta {AgentEvent::ObservationFailed{error,output:output(&self.text)}}
                            else {AgentEvent::FailedWithOutput{error,output:output(&self.text)}});
                        return Ok(());
                    }
                };
                let mut next=self.text.clone();
                match chat.state {
                    ChatState::Delta=>next.push_str(chat.delta_text.as_deref().or(full.as_deref()).unwrap_or("")),
                    ChatState::Final=>if let Some(full)=full.filter(|s|!s.is_empty()){next=full;},
                    _=>{}
                }
                if !fits(&output(&next)) {
                    self.done=true;
                    self.pending.push_back(if chat.state==ChatState::Delta {AgentEvent::ObservationFailed{error:RuntimeError::CapacityExceeded,output:output(&self.text)}}
                        else {AgentEvent::FailedWithOutput{error:RuntimeError::CapacityExceeded,output:output(&self.text)}});
                    return Ok(());
                }
                match chat.state {
                    ChatState::Delta=>{let suffix=next[self.text.len()..].to_string();if !suffix.is_empty(){self.pending.push_back(AgentEvent::Delta{text:suffix});}}
                    ChatState::Final=>{self.done=true;self.pending.push_back(AgentEvent::Completed{output:output(&next)});}
                    ChatState::Error|ChatState::Aborted=>{
                        self.done=true;self.pending.push_back(AgentEvent::Snapshot{snapshot:ExecutionSnapshot{observation_error:None,
                            handle:self.handle.clone().ok_or(())?,status:if chat.state==ChatState::Aborted{TaskStatus::Cancelled}else{TaskStatus::Failed},
                            output:output(&next),error:(chat.state==ChatState::Error).then(||RuntimeError::Unavailable("remote task failed".into()))}});
                    }
                }
                self.text=next;
            }
            StreamEvent::Agent(agent)=>match agent.data {
                AgentData::Error{..}=>{self.done=true;self.pending.push_back(AgentEvent::FailedWithOutput{error:RuntimeError::Unavailable("remote task failed".into()),output:output(&self.text)});}
                AgentData::Approval(_)|AgentData::Unknown{..}=>return Err(()),
                _=>{},
            },
            // No input-continuation contract is negotiated for Provider in this MVP.
            StreamEvent::Interaction(_)=>return Err(()),
            _=>return Err(()),
        }
        Ok(())
    }
}
