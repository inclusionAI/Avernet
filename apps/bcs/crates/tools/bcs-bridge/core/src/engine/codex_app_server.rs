//! `CodexAppServer` driver: runs `codex app-server` over the Codex JSON-RPC
//! stdio protocol.
//!
//! Invocation (`<prefix_args>` is configured by [`CodexAppServerFactory`] and
//! empty for the built-in `codex` engine):
//!
//! ```text
//! <bin> <prefix_args...> app-server --listen stdio://
//!   initialize
//!   thread/start | thread/resume
//!   turn/start
//!   item/agentMessage/delta ...
//!   turn/completed
//! ```
//!
//! Unlike `codex exec --json`, app-server emits assistant text as small delta
//! notifications. The driver forwards those notifications immediately as
//! engine-neutral `chat_delta` events and uses `turn/completed` only as the
//! terminal boundary.

use std::collections::VecDeque;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use bcs_protocol::stream::{StreamEvent, ToolData, ToolPhase};
use serde_json::{Value, json};
use tokio::sync::mpsc::Sender;
use tokio_util::sync::CancellationToken;

use crate::engine::cli::CliSession;
use crate::engine::{Engine, TurnError, TurnOutcome, TurnRequest};
use crate::sse;

/// Codex app-server JSON-RPC driver.
///
/// Known limitation: threads run with `approvalPolicy: never`, a read-only
/// sandbox and no network access, and app-server requests are rejected rather
/// than routed to HITL interactions.
pub struct CodexAppServer {
    bin: PathBuf,
    prefix_args: Vec<String>,
}

impl CodexAppServer {
    /// `prefix_args` are placed before `app-server`, for launchers that select
    /// Codex through a subcommand or flag.
    pub fn new(bin: PathBuf, prefix_args: Vec<String>) -> Self {
        Self { bin, prefix_args }
    }
}

/// Factory for [`CodexAppServer`] engines. The default is the built-in
/// `codex` engine running `codex`; [`CodexAppServerFactory::custom`] reuses
/// the driver for a launcher that speaks the same protocol.
pub struct CodexAppServerFactory {
    id: String,
    default_bin: String,
    prefix_args: Vec<String>,
}

impl CodexAppServerFactory {
    pub fn custom(id: impl Into<String>, default_bin: impl Into<String>, prefix_args: Vec<String>) -> Self {
        Self { id: id.into(), default_bin: default_bin.into(), prefix_args }
    }
}

impl Default for CodexAppServerFactory {
    fn default() -> Self { Self::custom("codex", "codex", Vec::new()) }
}

impl crate::engine::EngineFactory for CodexAppServerFactory {
    fn id(&self) -> &str { &self.id }
    fn default_bin(&self) -> &str { &self.default_bin }
    fn build(&self, bin: PathBuf, options: &toml::Table) -> Result<Arc<dyn Engine>, String> {
        crate::engine::reject_engine_options(options)?;
        Ok(Arc::new(CodexAppServer::new(bin, self.prefix_args.clone())))
    }
}

#[async_trait::async_trait]
impl Engine for CodexAppServer {
    async fn run_turn(
        &self,
        req: TurnRequest,
        events: Sender<StreamEvent>,
        abort: CancellationToken,
    ) -> Result<TurnOutcome, TurnError> {
        let mut args = self.prefix_args.clone();
        args.extend([
            "app-server".to_string(),
            "--listen".to_string(),
            "stdio://".to_string(),
        ]);
        let mut cli = CliSession::spawn(&self.bin, &args, &req.cwd, &[], req.trace.clone())
            .await
            .map_err(TurnError::Spawn)?;

        let mut next_id = 1_u64;
        let mut backlog = VecDeque::new();

        rpc_call(
            &mut cli,
            &mut next_id,
            &mut backlog,
            "initialize",
            json!({
                "clientInfo": {
                    "name": "bridge-provider",
                    "version": env!("CARGO_PKG_VERSION"),
                },
                "capabilities": null,
            }),
            &abort,
        )
        .await?;
        send_notification(&mut cli, "initialized", Value::Null).await?;

        let thread = if let Some(session_id) = &req.engine_session_id {
            rpc_call(
                &mut cli,
                &mut next_id,
                &mut backlog,
                "thread/resume",
                thread_resume_params(&req, session_id),
                &abort,
            )
            .await?
        } else {
            rpc_call(
                &mut cli,
                &mut next_id,
                &mut backlog,
                "thread/start",
                thread_start_params(&req),
                &abort,
            )
            .await?
        };
        let thread_id = extract_thread_id(&thread).ok_or_else(|| {
            TurnError::Protocol(format!("app-server thread response missing thread.id: {thread}"))
        })?;
        if !crate::engine::is_valid_engine_session_id(&thread_id) {
            return Err(TurnError::Protocol("app-server supplied invalid thread id".into()));
        }
        req.session_observer.established(&thread_id).await.map_err(TurnError::SessionStorage)?;

        let turn = rpc_call(
            &mut cli,
            &mut next_id,
            &mut backlog,
            "turn/start",
            turn_start_params(&req, &thread_id),
            &abort,
        )
        .await?;
        let turn_id = extract_turn_id(&turn).ok_or_else(|| {
            TurnError::Protocol(format!("app-server turn response missing turn.id: {turn}"))
        })?;

        let mut deltas = String::new();
        let mut thinking = String::new();
        loop {
            let value = tokio::select! {
                _ = abort.cancelled() => {
                    cli.kill().await;
                    return Err(TurnError::Aborted);
                }
                value = next_message(&mut cli, &mut backlog) => value?,
            };

            if is_server_request(&value) {
                respond_server_request(&mut cli, &value).await?;
                continue;
            }
            if !matches_turn(&value, &thread_id, &turn_id) {
                continue;
            }

            match value.get("method").and_then(Value::as_str).unwrap_or_default() {
                "item/agentMessage/delta" | "agent/output_chunk" => {
                    if let Some(delta) = extract_delta(&value) {
                        deltas.push_str(&delta);
                        if send_event(&events, sse::chat_delta(&req.run_id, &delta)).await {
                            cli.kill().await;
                            return Err(TurnError::Aborted);
                        }
                    }
                }
                "item/reasoning/textDelta" | "item/reasoning/summaryTextDelta" => {
                    if let Some(delta) = extract_delta(&value) {
                        thinking.push_str(&delta);
                        if send_event(
                            &events,
                            sse::agent_thinking(
                                &req.run_id,
                                Some(delta),
                                Some(thinking.clone()),
                            ),
                        )
                        .await
                        {
                            cli.kill().await;
                            return Err(TurnError::Aborted);
                        }
                    }
                }
                "item/started" => {
                    if let Some(event) = map_item_started(&value, &req.run_id, &req.cwd) {
                        if send_event(&events, event).await {
                            return Err(TurnError::Aborted);
                        }
                    }
                }
                "item/completed" => {
                    if let Some(event) = map_item_completed(&value, &req.run_id, &req.cwd) {
                        if send_event(&events, event).await {
                            return Err(TurnError::Aborted);
                        }
                    }
                }
                "item/commandExecution/outputDelta" | "item/fileChange/outputDelta" => {
                    if let Some(event) = map_tool_output_delta(&value, &req.run_id) {
                        if send_event(&events, event).await {
                            return Err(TurnError::Aborted);
                        }
                    }
                }
                "item/mcpToolCall/progress" => {
                    if let Some(event) = map_mcp_progress(&value, &req.run_id) {
                        if send_event(&events, event).await {
                            return Err(TurnError::Aborted);
                        }
                    }
                }
                "turn/completed" | "agent/turn_completed" => {
                    if let Some(message) = completed_failure_message(&value) {
                        return Err(TurnError::EngineExited(message));
                    }
                    let final_text = if deltas.is_empty() {
                        completed_text(&value).unwrap_or_default()
                    } else {
                        deltas
                    };
                    cli.close_stdin();
                    return Ok(TurnOutcome {
                        engine_session_id: Some(thread_id),
                        final_text: Some(final_text),
                    });
                }
                "agent/turn_failed" => {
                    return Err(TurnError::EngineExited(
                        turn_failure_message(&value),
                    ));
                }
                _ => {}
            }
        }
    }
}

/// Send one converted event and return `true` when the downstream BCS sender
/// has gone away. The caller kills the engine and ends the turn in that case.
async fn send_event(
    events: &Sender<StreamEvent>,
    event: StreamEvent,
) -> bool {
    events.send(event).await.is_err()
}

fn map_item_started(value: &Value, run_id: &str, cwd: &Path) -> Option<StreamEvent> {
    let item = value.get("params")?.get("item")?;
    let item_type = item.get("type").and_then(Value::as_str)?;
    let tool_call_id = item.get("id").and_then(Value::as_str)?.to_string();
    let name = tool_item_name(item, item_type)?;
    Some(sse::agent_tool(
        run_id,
        ToolData {
            phase: ToolPhase::Start,
            name: Some(name),
            tool_call_id: Some(tool_call_id),
            is_error: None,
            exit_code: None,
            duration_ms: None,
            cwd: Some(item_string(item, "cwd").unwrap_or_else(|| cwd.to_string_lossy().into_owned())),
            args: Some(tool_item_args(item, item_type, cwd)),
            result: None,
            partial_result: None,
        },
    ))
}

fn map_item_completed(value: &Value, run_id: &str, cwd: &Path) -> Option<StreamEvent> {
    let item = value.get("params")?.get("item")?;
    let item_type = item.get("type").and_then(Value::as_str)?;
    let tool_call_id = item.get("id").and_then(Value::as_str)?.to_string();
    let status = item.get("status").and_then(Value::as_str);
    let is_error = status.is_some_and(|value| value != "completed")
        || item
            .get("exitCode")
            .and_then(Value::as_i64)
            .is_some_and(|value| value != 0)
        || item.get("success").and_then(Value::as_bool) == Some(false);
    let name = tool_item_name(item, item_type)?;
    let result = if item_type == "commandExecution" {
        item.get("aggregatedOutput")
            .cloned()
            .unwrap_or(Value::String(String::new()))
    } else {
        item.get("result")
            .cloned()
            .or_else(|| item.get("contentItems").cloned())
            .or_else(|| item.get("error").cloned())
            .unwrap_or(Value::Null)
    };
    Some(sse::agent_tool(
        run_id,
        ToolData {
            phase: ToolPhase::Result,
            name: Some(name),
            tool_call_id: Some(tool_call_id),
            is_error: Some(is_error),
            exit_code: item.get("exitCode").and_then(Value::as_i64),
            duration_ms: item.get("durationMs").and_then(Value::as_u64),
            cwd: item_string(item, "cwd"),
            args: Some(tool_item_args(item, item_type, cwd)),
            result: Some(result),
            partial_result: None,
        },
    ))
}

fn map_tool_output_delta(value: &Value, run_id: &str) -> Option<StreamEvent> {
    let params = value.get("params")?;
    let delta = params.get("delta").and_then(Value::as_str)?;
    Some(sse::agent_tool(
        run_id,
        ToolData {
            phase: ToolPhase::Update,
            name: None,
            tool_call_id: params
                .get("itemId")
                .and_then(Value::as_str)
                .map(str::to_string),
            is_error: Some(false),
            exit_code: None,
            duration_ms: None,
            cwd: None,
            args: None,
            result: None,
            partial_result: Some(Value::String(delta.to_string())),
        },
    ))
}

fn map_mcp_progress(value: &Value, run_id: &str) -> Option<StreamEvent> {
    let params = value.get("params")?;
    let partial = params
        .get("message")
        .cloned()
        .or_else(|| params.get("delta").cloned())
        .unwrap_or_else(|| params.clone());
    Some(sse::agent_tool(
        run_id,
        ToolData {
            phase: ToolPhase::Update,
            name: None,
            tool_call_id: params
                .get("itemId")
                .and_then(Value::as_str)
                .map(str::to_string),
            is_error: Some(false),
            exit_code: None,
            duration_ms: None,
            cwd: None,
            args: None,
            result: None,
            partial_result: Some(partial),
        },
    ))
}

fn item_string(item: &Value, key: &str) -> Option<String> {
    item.get(key).and_then(Value::as_str).map(str::to_string)
}

fn mcp_tool_name(item: &Value) -> String {
    let server = item_string(item, "server").unwrap_or_else(|| "mcp".into());
    let tool = item_string(item, "tool").unwrap_or_else(|| "tool".into());
    format!("mcp__{server}__{tool}")
}

fn tool_item_name(item: &Value, item_type: &str) -> Option<String> {
    match item_type {
        "commandExecution" => Some("Bash".into()),
        "mcpToolCall" => Some(mcp_tool_name(item)),
        "dynamicToolCall" => Some("dynamicTool".into()),
        "fileChange" => Some("FileChange".into()),
        "webSearch" => Some("WebSearch".into()),
        _ => None,
    }
}

fn tool_item_args(item: &Value, item_type: &str, cwd: &Path) -> Value {
    match item_type {
        "commandExecution" => json!({
            "command": item.get("command").cloned().unwrap_or(Value::Null),
            "cwd": item.get("cwd").cloned().unwrap_or_else(|| json!(cwd)),
        }),
        "mcpToolCall" | "dynamicToolCall" => item
            .get("arguments")
            .cloned()
            .unwrap_or(Value::Null),
        _ => item.clone(),
    }
}

async fn rpc_call(
    cli: &mut CliSession,
    next_id: &mut u64,
    backlog: &mut VecDeque<Value>,
    method: &str,
    params: Value,
    abort: &CancellationToken,
) -> Result<Value, TurnError> {
    let id = *next_id;
    *next_id = next_id.saturating_add(1);
    let request = json!({
        "jsonrpc": "2.0",
        "id": id,
        "method": method,
        "params": params,
    });
    send_line(cli, &request).await?;

    // Notifications may arrive before the response we are waiting for (the
    // real Codex app-server emits config/status notifications around
    // thread/start). Look for the response in the backlog without letting an
    // unrelated notification at the front prevent us from reading stdout.
    if let Some(index) = backlog
        .iter()
        .position(|value| value.get("id").and_then(Value::as_u64) == Some(id))
    {
        let Some(value) = backlog.remove(index) else {
            return Err(TurnError::Protocol(
                "app-server response backlog changed while resolving RPC response".into(),
            ));
        };
        return decode_rpc_response(value, method);
    }

    loop {
        // Read the live stdout stream directly here. `next_message` consumes
        // the notification backlog first, which is correct while driving the
        // turn but would repeatedly return the same unrelated notification
        // while this RPC call is waiting for a later response.
        let value = tokio::select! {
            _ = abort.cancelled() => return Err(TurnError::Aborted),
            value = next_live_message(cli) => value?,
        };
        if value.get("id").and_then(Value::as_u64) == Some(id) {
            return decode_rpc_response(value, method);
        }
        if value.get("method").is_some() {
            backlog.push_back(value);
        }
    }
}

fn decode_rpc_response(value: Value, method: &str) -> Result<Value, TurnError> {
    if let Some(error) = value.get("error") {
        let message = error
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("app-server RPC failed");
        return Err(TurnError::EngineExited(format!(
            "{method}: {}",
            sanitize_message(message)
        )));
    }
    Ok(value.get("result").cloned().unwrap_or(Value::Null))
}

async fn send_notification(
    cli: &mut CliSession,
    method: &str,
    params: Value,
) -> Result<(), TurnError> {
    send_line(
        cli,
        &json!({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }),
    )
    .await
}

async fn send_line(cli: &mut CliSession, value: &Value) -> Result<(), TurnError> {
    let line = serde_json::to_string(value)
        .map_err(|error| TurnError::Protocol(format!("encode app-server request: {error}")))?;
    cli.write_line(&line).await.map_err(TurnError::Io)
}

async fn next_message(
    cli: &mut CliSession,
    backlog: &mut VecDeque<Value>,
) -> Result<Value, TurnError> {
    if let Some(value) = backlog.pop_front() {
        return Ok(value);
    }
    next_live_message(cli).await
}

async fn next_live_message(cli: &mut CliSession) -> Result<Value, TurnError> {
    let Some(line) = cli.next_line().await.map_err(TurnError::Io)? else {
        return Err(TurnError::EngineExited(
            "app-server stdout EOF before result".into(),
        ));
    };
    serde_json::from_str(&line)
        .map_err(|error| TurnError::Protocol(format!("parse app-server JSON: {error}")))
}

async fn respond_server_request(cli: &mut CliSession, request: &Value) -> Result<(), TurnError> {
    let id = request.get("id").cloned().unwrap_or(Value::Null);
    send_line(
        cli,
        &json!({
            "jsonrpc": "2.0",
            "id": id,
            "error": {
                "code": -32000,
                "message": "bridge-provider does not support app-server server requests",
            },
        }),
    )
    .await
}

fn thread_start_params(req: &TurnRequest) -> Value {
    let mut params = json!({
        "cwd": req.cwd,
        "approvalPolicy": "never",
        "approvalsReviewer": "user",
        "sandbox": "read-only",
        "ephemeral": false,
        "threadSource": "bridge-provider",
    });
    if let Some(model) = req.model.as_deref().filter(|model| !model.is_empty()) {
        params["model"] = json!(model);
    }
    params
}

fn thread_resume_params(req: &TurnRequest, thread_id: &str) -> Value {
    let mut params = json!({
        "threadId": thread_id,
        "cwd": req.cwd,
        "approvalPolicy": "never",
        "approvalsReviewer": "user",
        "sandbox": "read-only",
    });
    if let Some(model) = req.model.as_deref().filter(|model| !model.is_empty()) {
        params["model"] = json!(model);
    }
    params
}

fn turn_start_params(req: &TurnRequest, thread_id: &str) -> Value {
    json!({
        "threadId": thread_id,
        "clientUserMessageId": req.run_id,
        "input": [{
            "type": "text",
            "text": req.prompt,
            "text_elements": [],
        }],
        "cwd": req.cwd,
        "approvalPolicy": "never",
        "approvalsReviewer": "user",
        "sandboxPolicy": {
            "type": "readOnly",
            "networkAccess": false,
        },
    })
}

fn extract_thread_id(value: &Value) -> Option<String> {
    value
        .pointer("/thread/id")
        .and_then(Value::as_str)
        .or_else(|| value.get("threadId").and_then(Value::as_str))
        .map(str::to_string)
}

fn extract_turn_id(value: &Value) -> Option<String> {
    value
        .pointer("/turn/id")
        .and_then(Value::as_str)
        .or_else(|| value.get("turnId").and_then(Value::as_str))
        .map(str::to_string)
}

fn matches_turn(value: &Value, thread_id: &str, turn_id: &str) -> bool {
    let Some(params) = value.get("params") else {
        return false;
    };
    let thread_matches = params
        .get("threadId")
        .and_then(Value::as_str)
        .is_none_or(|value| value == thread_id);
    let turn_matches = params
        .get("turnId")
        .and_then(Value::as_str)
        .is_none_or(|value| value == turn_id);
    thread_matches && turn_matches
}

fn is_server_request(value: &Value) -> bool {
    value.get("id").is_some()
        && value.get("method").is_some()
        && value.get("result").is_none()
        && value.get("error").is_none()
}

fn extract_delta(value: &Value) -> Option<String> {
    let params = value.get("params")?;
    params
        .get("delta")
        .and_then(Value::as_str)
        .or_else(|| params.get("text").and_then(Value::as_str))
        .or_else(|| {
            params
                .get("delta")
                .and_then(|delta| delta.get("text"))
                .and_then(Value::as_str)
        })
        .map(str::to_string)
}

fn completed_text(value: &Value) -> Option<String> {
    let params = value.get("params")?;
    params
        .get("text")
        .and_then(Value::as_str)
        .or_else(|| params.get("finalText").and_then(Value::as_str))
        .or_else(|| params.get("final_text").and_then(Value::as_str))
        .map(str::to_string)
}

fn completed_failure_message(value: &Value) -> Option<String> {
    let turn = value.pointer("/params/turn")?;
    if turn.get("status").and_then(Value::as_str) != Some("failed") {
        return None;
    }
    let message = turn
        .pointer("/error/message")
        .and_then(Value::as_str)
        .unwrap_or("app-server turn failed");
    Some(sanitize_message(message))
}

fn turn_failure_message(value: &Value) -> String {
    let message = value
        .pointer("/params/error/message")
        .and_then(Value::as_str)
        .or_else(|| value.pointer("/params/message").and_then(Value::as_str))
        .unwrap_or("app-server turn failed");
    sanitize_message(message)
}

fn sanitize_message(message: &str) -> String {
    const MAX_LEN: usize = 256;
    let clean: String = message.chars().filter(|c| !c.is_control()).collect();
    match clean.char_indices().nth(MAX_LEN) {
        Some((index, _)) => clean[..index].to_string(),
        None => clean,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_app_server_ids() {
        assert_eq!(
            extract_thread_id(&json!({"thread": {"id": "t-1"}})).as_deref(),
            Some("t-1")
        );
        assert_eq!(
            extract_turn_id(&json!({"turn": {"id": "turn-1"}})).as_deref(),
            Some("turn-1")
        );
    }

    #[test]
    fn extracts_streaming_delta_shapes() {
        assert_eq!(
            extract_delta(&json!({"params": {"delta": "hello"}})).as_deref(),
            Some("hello")
        );
        assert_eq!(
            extract_delta(&json!({"params": {"text": "world"}})).as_deref(),
            Some("world")
        );
    }

    #[test]
    fn matches_only_the_active_turn() {
        let value = json!({
            "params": {"threadId": "t-1", "turnId": "turn-1"}
        });
        assert!(matches_turn(&value, "t-1", "turn-1"));
        assert!(!matches_turn(&value, "t-2", "turn-1"));
        assert!(!matches_turn(&value, "t-1", "turn-2"));
    }
}
