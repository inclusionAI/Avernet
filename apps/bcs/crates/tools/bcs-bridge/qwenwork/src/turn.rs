use crate::{transport::State, Input, Peer, QwenWork, QUEUE_SIZE};
use async_trait::async_trait;
use bcs_bridge_core::{
    engine::{is_valid_engine_session_id, Engine, TurnError, TurnOutcome, TurnRequest},
    sse,
};
use bcs_protocol::stream::{StreamEvent, ToolData, ToolPhase};
use serde_json::{json, Value};
use std::{
    collections::{HashMap, HashSet},
    sync::{atomic::Ordering, Arc},
    time::Duration,
};
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;

struct Registration {
    state: Arc<State>,
    request: String,
}
impl Drop for Registration {
    fn drop(&mut self) {
        self.state.remove(&self.request);
    }
}

fn callback(state: &State, request: &str, conversation: &str, prompt: &str) -> Value {
    json!({"cmd":"aibot_msg_callback", "headers":{"req_id":request}, "body":{
        "msgid":request,"aibotid":state.options.bot_id,"chatid":conversation,"chattype":"single",
        "from":{"userid":conversation},"msgtype":"text","text":{"content":prompt},
        "create_time":bcs_protocol::now_ms()/1000}})
}

async fn send(peer: &Peer, frame: Value) -> Result<(), TurnError> {
    tokio::time::timeout(Duration::from_secs(5), peer.tx.send(frame))
        .await
        .map_err(|_| TurnError::Protocol("QwenWork send queue stalled".into()))?
        .map_err(|_| TurnError::EngineExited("QwenWork channel disconnected".into()))
}

async fn connected(state: &State, abort: &CancellationToken) -> Result<Peer, TurnError> {
    let wait = async {
        loop {
            let notified = state.ready.notified();
            tokio::pin!(notified);
            notified.as_mut().enable();
            if let Some(peer) = state.lock().peer.clone() {
                return Ok(peer);
            }
            notified.await;
        }
    };
    tokio::select! {
        biased;
        _ = abort.cancelled() => Err(TurnError::Aborted),
        result = tokio::time::timeout(Duration::from_secs(10), wait) =>
            result.unwrap_or_else(|_| Err(TurnError::EngineExited("QwenWork channel not connected; check WS configuration".into()))),
    }
}

#[async_trait]
impl Engine for QwenWork {
    async fn run_turn(
        &self,
        req: TurnRequest,
        events: mpsc::Sender<StreamEvent>,
        abort: CancellationToken,
    ) -> Result<TurnOutcome, TurnError> {
        if self.state.faulted.load(Ordering::SeqCst) {
            return Err(TurnError::EngineExited(
                "QwenWork execution state uncertain; stop the task in QwenWork and restart bridge"
                    .into(),
            ));
        }
        if req.model.is_some() || req.permission_mode.is_some() {
            return Err(TurnError::Protocol(
                "QwenWork model and permissions are configured in the desktop app".into(),
            ));
        }
        let command = req
            .prompt
            .split_whitespace()
            .next()
            .map(str::to_ascii_lowercase);
        if matches!(
            command.as_deref(),
            Some("/stop" | "/new" | "/bind" | "/unbind" | "/compact")
        ) {
            return Err(TurnError::Protocol(
                "QwenWork session control commands cannot be used as turn prompts".into(),
            ));
        }
        let conversation = req
            .engine_session_id
            .clone()
            .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
        if !is_valid_engine_session_id(&conversation) {
            return Err(TurnError::Protocol(
                "invalid QwenWork conversation id".into(),
            ));
        }
        let peer = connected(&self.state, &abort).await?;
        let request = uuid::Uuid::new_v4().to_string();
        let (tx, mut rx) = mpsc::channel(QUEUE_SIZE);
        self.state
            .add(&request, &conversation, tx, &req.prompt)
            .map_err(TurnError::Protocol)?;
        let _registration = Registration {
            state: self.state.clone(),
            request: request.clone(),
        };
        req.session_observer
            .established(&conversation)
            .await
            .map_err(TurnError::SessionStorage)?;
        if abort.is_cancelled() || events.is_closed() {
            return Err(TurnError::Aborted);
        }
        send(
            &peer,
            callback(&self.state, &request, &conversation, &req.prompt),
        )
        .await?;
        let result = collect(&req, &self.state, &conversation, &mut rx, &events, &abort, &peer.closed).await;
        req.interactions.invalidate_run(&req.run_id, json!({"decision":"deny"}));
        match result {
            Ok(text) => Ok(TurnOutcome {
                engine_session_id: Some(conversation),
                final_text: Some(text),
            }),
            Err(error) => {
                // Do not release the bridge's session slot until QwenWork has
                // handled /stop. A transport ACK alone does not prove task stop.
                if stop(&self.state, &peer, &conversation).await.is_err() {
                    self.state.faulted.store(true, Ordering::SeqCst);
                    return Err(TurnError::EngineExited("QwenWork cancellation unconfirmed; stop task in the desktop app and restart bridge".into()));
                }
                Err(error)
            }
        }
    }
}

async fn stop(state: &Arc<State>, peer: &Peer, conversation: &str) -> Result<(), TurnError> {
    if peer.closed.is_cancelled() {
        return Err(TurnError::EngineExited(
            "QwenWork channel disconnected".into(),
        ));
    }
    // Keep a distinct req_id, so the stop reply never becomes the original turn's text.
    let request = uuid::Uuid::new_v4().to_string();
    let (tx, mut rx) = mpsc::channel(QUEUE_SIZE);
    state.lock().active.insert(
        request.clone(),
        crate::Active {
            conversation: conversation.into(),
            prompt: "/stop".into(),
            request_set: None,
            tx,
        },
    );
    let _registration = Registration {
        state: state.clone(),
        request: request.clone(),
    };
    send(peer, callback(state, &request, conversation, "/stop")).await?;
    let wait = async {
        loop {
            tokio::select! {
                _ = peer.closed.cancelled() => return Err(TurnError::EngineExited("QwenWork channel disconnected".into())),
                input = rx.recv() => match input {
                    Some(Input::Snapshot { finished: true, text }) if text.starts_with('✅') || text.starts_with('ℹ') => return Ok(()),
                    Some(_) => {},
                    None => return Err(TurnError::EngineExited("QwenWork stop reply missing".into())),
                }
            }
        }
    };
    tokio::time::timeout(Duration::from_secs(10), wait)
        .await
        .map_err(|_| TurnError::Protocol("QwenWork stop confirmation timed out".into()))?
}

pub(crate) async fn emit(
    events: &mpsc::Sender<StreamEvent>,
    event: StreamEvent,
    abort: &CancellationToken,
    closed: &CancellationToken,
) -> Result<(), TurnError> {
    tokio::select! {
        biased;
        _ = abort.cancelled() => Err(TurnError::Aborted),
        _ = closed.cancelled() => Err(TurnError::EngineExited("QwenWork channel disconnected".into())),
        result = events.send(event) => result.map_err(|_| TurnError::Aborted),
    }
}

async fn collect(
    req: &TurnRequest,
    state: &State,
    conversation: &str,
    rx: &mut mpsc::Receiver<Input>,
    events: &mpsc::Sender<StreamEvent>,
    abort: &CancellationToken,
    closed: &CancellationToken,
) -> Result<String, TurnError> {
    let run = &req.run_id;
    let mut text = String::new();
    let mut revised = false;
    let mut finished = false;
    let mut lifecycle = crate::lifecycle::Lifecycle::default();
    let mut seen = HashSet::new();
    let mut pending = HashSet::new();
    // Only wrapped MCP calls need an identity override. Keep the start's name
    // until its matching result; never infer a privileged tool from output.
    let mut wrapped_tools = HashMap::new();
    let mut early_results: HashMap<String, Value> = HashMap::new();
    let mut early_bytes = 0usize;
    let mut completion_deadline = None;
    let mut answered: HashMap<String, Value> = HashMap::new();
    loop {
        if finished && lifecycle.complete() && pending.is_empty() && early_results.is_empty() {
            return Ok(text);
        }
        let completion = async {
            match completion_deadline {
                Some(deadline) => tokio::time::sleep_until(deadline).await,
                None => std::future::pending().await,
            }
        };
        let input = tokio::select! {
            biased;
            _ = abort.cancelled() => return Err(TurnError::Aborted),
            _ = events.closed() => return Err(TurnError::Aborted),
            _ = closed.cancelled() => return Err(TurnError::EngineExited("QwenWork channel disconnected".into())),
            _ = completion => return Err(TurnError::Protocol("QwenWork completion incomplete: missing text final, QueryEnd or tool result".into())),
            input = rx.recv() => input.ok_or_else(|| TurnError::EngineExited("QwenWork event queue closed".into()))?,
        };
        match input {
            Input::Question { hook, reply } => {
                if !crate::questions::permitted(&hook["tool_input"]) {
                    let _ = reply.send(crate::questions::deny("Invalid or secret question form cannot be forwarded"));
                    return Err(TurnError::Protocol("Invalid or secret AskUserQuestion form".into()));
                }
                let id = hook["tool_use_id"].as_str().filter(|s| !s.is_empty())
                    .ok_or_else(|| TurnError::Protocol("Question Hook missing tool_use_id".into()))?.to_owned();
                if let Some(response) = answered.get(&id) {
                    let _ = reply.send(response.clone());
                    continue;
                }
                if answered.len() >= 128 {
                    let _ = reply.send(crate::questions::deny("Question limit reached"));
                    return Err(TurnError::Protocol("QwenWork question limit reached".into()));
                }
                if seen.insert((id.clone(), true)) {
                    pending.insert(id.clone());
                    emit(events, tool(run, &hook, ToolPhase::Start), abort, closed).await?;
                }
                let response = crate::questions::answer(req, &hook, events, abort, closed, reply, (state, conversation)).await?;
                answered.insert(id, response);
            }
            Input::Snapshot {
                text: next,
                finished: done,
            } => {
                if next == "💭 思考中…" || next == "💭 思考中..." {
                    continue;
                }
                if !revised {
                    if let Some(delta) = next.strip_prefix(&text) {
                        if !delta.is_empty() {
                            emit(events, sse::chat_delta(run, delta), abort, closed).await?;
                        }
                    } else {
                        revised = true;
                    }
                }
                text = next;
                finished |= done;
            }
            Input::Hook(mut hook) => {
                let name = hook["hook_event_name"].as_str().unwrap_or_default();
                if lifecycle.handle(&hook)? {
                    // Stop is advisory: the SDK can continue after Stop hooks.
                } else {
                    let id = hook
                        .get("tool_use_id")
                        .and_then(Value::as_str)
                        .filter(|id| !id.is_empty())
                        .ok_or_else(|| {
                            TurnError::Protocol("QwenWork tool Hook missing tool_use_id".into())
                        })?
                        .to_owned();
                    let start = name == "PreToolUse";
                    if !start
                        && hook
                            .get(if name == "PostToolUseFailure" {
                                "error"
                            } else {
                                "tool_response"
                            })
                            .is_none()
                    {
                        return Err(TurnError::Protocol(
                            "QwenWork result Hook missing output".into(),
                        ));
                    }
                    if !seen.insert((id.clone(), start)) {
                        continue;
                    }
                    if seen.len() > 8192 {
                        return Err(TurnError::Protocol(
                            "QwenWork tool event limit reached".into(),
                        ));
                    }
                    if start {
                        if let Some(name) = unwrap_mcp_call(&mut hook) {
                            wrapped_tools.insert(id.clone(), name);
                        }
                        pending.insert(id.clone());
                        emit(events, tool(run, &hook, ToolPhase::Start), abort, closed).await?;
                        if let Some(mut result) = early_results.remove(&id) {
                            early_bytes = early_bytes.saturating_sub(
                                serde_json::to_vec(&result)
                                    .map_err(|e| TurnError::Protocol(e.to_string()))?
                                    .len(),
                            );
                            if let Some(name) = wrapped_tools.remove(&id) {
                                result["tool_name"] = name.into();
                            }
                            emit(events, tool(run, &result, ToolPhase::Result), abort, closed)
                                .await?;
                            pending.remove(&id);
                        }
                    } else if pending.remove(&id) {
                        if let Some(name) = wrapped_tools.remove(&id) {
                            hook["tool_name"] = name.into();
                        }
                        emit(events, tool(run, &hook, ToolPhase::Result), abort, closed).await?;
                    } else {
                        early_bytes += serde_json::to_vec(&hook)
                            .map_err(|e| TurnError::Protocol(e.to_string()))?
                            .len();
                        if early_bytes > 32 * 1024 * 1024 {
                            return Err(TurnError::Protocol(
                                "QwenWork pending tool results exceed 32 MiB".into(),
                            ));
                        }
                        early_results.insert(id, hook);
                    }
                }
            }
        }
        if lifecycle.complete() && completion_deadline.is_none() {
            completion_deadline = Some(tokio::time::Instant::now() + Duration::from_secs(10));
        }
    }
}

pub(super) fn unwrap_mcp_call(hook: &mut Value) -> Option<String> {
    if hook.get("tool_name").and_then(Value::as_str) != Some("mcp__qw-builtin__qwenwork_mcp_tool_call") {
        return None;
    }
    let input = hook.get("tool_input")?;
    let name = input.get("toolName")?.as_str()?;
    let (server, tool) = name.strip_prefix("mcp__")?.split_once("__")?;
    if name.len() > 1024 || server.is_empty() || tool.is_empty()
        || !name.bytes().all(|c| c.is_ascii_alphanumeric() || b"_-.:".contains(&c)) {
        return None;
    }
    let arguments = input.get("arguments")?.as_object()?;
    let name = name.to_owned();
    let arguments = Value::Object(arguments.clone());
    hook["tool_name"] = name.clone().into();
    hook["tool_input"] = arguments;
    Some(name)
}

fn tool(run: &str, hook: &Value, phase: ToolPhase) -> StreamEvent {
    let failed = hook.get("hook_event_name").and_then(Value::as_str) == Some("PostToolUseFailure");
    sse::agent_tool(
        run,
        ToolData {
            phase,
            name: hook
                .get("tool_name")
                .and_then(Value::as_str)
                .map(str::to_owned),
            tool_call_id: hook
                .get("tool_use_id")
                .and_then(Value::as_str)
                .map(str::to_owned),
            args: if phase == ToolPhase::Start {
                hook.get("tool_input").cloned()
            } else {
                None
            },
            result: if phase == ToolPhase::Result {
                hook.get(if failed { "error" } else { "tool_response" })
                    .cloned()
            } else {
                None
            },
            is_error: (phase == ToolPhase::Result).then_some(failed),
            exit_code: None,
            duration_ms: None,
            cwd: None,
            partial_result: None,
        },
    )
}
