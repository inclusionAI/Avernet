//! `ClaudeCode` driver: maps Claude Code `stream-json` NDJSON lines to
//! engine-neutral [`StreamEvent`]s and drives one downstream turn over a
//! [`CliSession`].
//!
//! 调用形态（`<prefix_args>` 由 [`ClaudeCodeFactory`] 配置，内置 `claude-code` 为空）：
//!
//! ```text
//! <bin> <prefix_args...> --output-format stream-json --verbose --input-format stream-json
//!       --include-partial-messages --permission-prompt-tool stdio
//!       [--permission-mode <mode>] [--resume <engine_session_id>] [--model <model>]
//! ```
//!
//! 默认先初始化原生 Stop hook，再向 stdin 写一条 user 消息；成功完成需 Stop/result/idle 证据。
//!
//! 事件映射表（cc stream-json → StreamEvent）：
//!
//! | cc 事件 | 映射 |
//! | --- | --- |
//! | `{"type":"system","subtype":"init","session_id":…}` | `CcMap::SessionId` |
//! | `{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":…}}}` | `chat_delta` |
//! | `{"type":"assistant","message":{"content":[{"type":"tool_use","id","name","input"}]}}` | `agent_tool(Start, name, toolCallId=id, args=input)` |
//! | `{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id","content","is_error"?}]}}` | `agent_tool(Result, name=matched start, toolCallId=tool_use_id, isError, result=…)` |
//! | `{"type":"result","subtype":"success","result":…}` | `CcMap::Final(text)` |
//! | `{"type":"result","subtype":_}` 非成功 | `CcMap::Failed(note)` → `TurnError::EngineExited` |
//! | `{"type":"control_request","request":{"subtype":"can_use_tool",…}}` | `CcMap::ControlRequest { request_id, tool_name, input }` → 驱动接线 HITL 交互（register→requested→await resolution→control_response→resolved） |

use std::{path::PathBuf, sync::Arc};

use bcs_protocol::stream::{InteractionKind, InteractionPhase, StreamEvent};
#[cfg(test)]
use bcs_protocol::stream::{ToolData, ToolPhase};
use serde_json::{json, Value};

use crate::engine::cli::CliSession;
use crate::engine::{
    is_valid_engine_session_id, Engine, TurnError, TurnOutcome, TurnRequest,
};
use crate::sse;
#[cfg(test)]
use crate::engine::SessionObserver;

#[path = "claude_code/mapper.rs"]
mod mapper;
#[path = "claude_code/permission.rs"]
mod permission;
use mapper::{CcMap, CcTurnMapper};
use permission::handle_control_request;
use super::claude_completion::{Completion, CompletionMode, trace_line, CAPABILITY, UNAVAILABLE};

/// Claude Code (stream-json) 引擎驱动。
pub struct ClaudeCode {
    bin: PathBuf,
    prefix_args: Vec<String>,
    completion_mode: CompletionMode,
}

impl ClaudeCode {
    /// `prefix_args` are placed before the stream-json arguments, for
    /// launchers that select Claude Code through a subcommand or flag.
    /// Uses `hook_idle` completion; the factory accepts explicit mode overrides.
    pub fn new(bin: PathBuf, prefix_args: Vec<String>) -> Self {
        Self { bin, prefix_args, completion_mode: CompletionMode::default() }
    }
}

/// Factory for [`ClaudeCode`] engines. The default is the built-in
/// `claude-code` engine running `claude`; [`ClaudeCodeFactory::custom`] reuses
/// the driver for a launcher that speaks the same protocol.
pub struct ClaudeCodeFactory {
    id: String,
    default_bin: String,
    prefix_args: Vec<String>,
}

impl ClaudeCodeFactory {
    pub fn custom(id: impl Into<String>, default_bin: impl Into<String>, prefix_args: Vec<String>) -> Self {
        Self { id: id.into(), default_bin: default_bin.into(), prefix_args }
    }
}

impl Default for ClaudeCodeFactory {
    fn default() -> Self { Self::custom("claude-code", "claude", Vec::new()) }
}

impl crate::engine::EngineFactory for ClaudeCodeFactory {
    fn supports_caller_env(&self) -> bool { true }
    fn id(&self) -> &str { &self.id }
    fn default_bin(&self) -> &str { &self.default_bin }
    /// The Claude driver forwards `permission_mode` as its protocol flag.
    fn ignores_permission_mode(&self) -> bool { false }
    fn build(&self, bin: PathBuf, options: &toml::Table) -> Result<Arc<dyn Engine>, String> {
        let completion_mode = CompletionMode::parse(options)?;
        Ok(Arc::new(ClaudeCode { bin, prefix_args: self.prefix_args.clone(), completion_mode }))
    }
}

/// Default sliding idle window for the engine stream. Every received line
/// pushes the turn's "still making progress" deadline this far out; only a
/// stream that stays silent for the whole window fails the turn — with a
/// named error that reports the stalled background count. The window must
/// cover silences that are protocol-required, not stalls: model TTFT spikes
/// on large contexts, and the full runtime of a background task or async
/// agent the engine is waiting on (recorded live: task_started to
/// task_notification the stream emits nothing for the task's whole
/// duration — a 3-minute background wait is 3 minutes of legitimate
/// silence). Ten minutes absorbs those while still surfacing a genuinely
/// wedged stream (2026-10-14/2026-10-16 class) long before the outer run
/// deadline. A shorter window was tried (120s, 2026-10-16) and reverted:
/// it killed healthy turns parked on multi-minute background work. A
/// per-turn `req.idle_timeout` override still wins when set.
const IDLE_WINDOW: std::time::Duration = std::time::Duration::from_secs(600);

#[async_trait::async_trait]
impl Engine for ClaudeCode {
    async fn run_turn(
        &self,
        req: TurnRequest,
        events: tokio::sync::mpsc::Sender<StreamEvent>,
        abort: tokio_util::sync::CancellationToken,
    ) -> Result<TurnOutcome, TurnError> {
        let mut args = self.prefix_args.clone();
        args.extend([
            "--output-format".into(),
            "stream-json".into(),
            "--verbose".into(),
            "--input-format".into(),
            "stream-json".into(),
            "--include-partial-messages".into(),
            "--permission-prompt-tool".into(),
            "stdio".into(),
        ]);
        if let Some(mode) = &req.permission_mode {
            args.push("--permission-mode".into());
            args.push(mode.clone());
        }
        if let Some(sid) = &req.engine_session_id {
            args.push("--resume".into());
            args.push(sid.clone());
        }
        if let Some(model) = &req.model {
            args.push("--model".into());
            args.push(model.clone());
        }

        let mut completion = (self.completion_mode != CompletionMode::Legacy).then(Completion::new);
        let mut env = req.env.clone();
        if completion.is_some() {
            env.push(("CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS".into(), "1".into()));
        }
        let mut cli = CliSession::spawn(&self.bin, &args, &req.cwd, &env, req.trace.clone())
            .await
            .map_err(TurnError::Spawn)?;

        if let Some(evidence) = &completion {
            // Hook payloads include transcript paths and answer text. Redact
            // these control frames before the generic raw trace sees them.
            cli.disable_raw_trace();
            cli.limit_stdout_line(2 * 1024 * 1024);
            let handshake = async {
                cli.write_line(&evidence.initialize().to_string()).await.map_err(TurnError::Io)?;
                loop {
                    let line = cli.next_line().await.map_err(TurnError::Io)?
                        .ok_or_else(|| TurnError::Protocol(CAPABILITY.into()))?;
                    if let Some(trace) = &req.trace { trace.record_raw(&trace_line(&line)); }
                    let v: Value = serde_json::from_str(&line).map_err(|_| TurnError::Protocol(CAPABILITY.into()))?;
                    if evidence.initialized(&v).map_err(|e| TurnError::Protocol(e.into()))? { return Ok::<(), TurnError>(()); }
                }
            };
            let initialized = tokio::select! {
                biased;
                _ = abort.cancelled() => return Err(TurnError::Aborted),
                r = tokio::time::timeout(req.idle_timeout.unwrap_or(std::time::Duration::from_secs(30)).min(std::time::Duration::from_secs(30)), handshake) => {
                    r.map_err(|_| TurnError::Protocol(CAPABILITY.into())).and_then(|r| r)
                }
            };
            if let Err(error) = initialized {
                if self.completion_mode == CompletionMode::HookIdle { return Err(error); }
                // No business input has been sent. Restart without observation
                // if the peer cannot initialize it; never replay a user prompt.
                tracing::warn!(target:"bcs_bridge_core::engine", reason=CAPABILITY, "observation unavailable before prompt; restarting legacy process");
                cli.kill().await;
                if abort.is_cancelled() { return Err(TurnError::Aborted); }
                cli = CliSession::spawn(&self.bin, &args, &req.cwd, &req.env, req.trace.clone()).await.map_err(TurnError::Spawn)?;
                completion = None;
            } else if self.completion_mode == CompletionMode::Observe {
                // The shadow observer must not reject frames legacy accepts.
                cli.limit_stdout_line(usize::MAX);
            }
        }

        // 初始化完成后发送业务输入；legacy 模式跳过初始化。
        let user_msg = serde_json::json!({
            "type": "user",
            "message": {
                "role": "user",
                "content": [{ "type": "text", "text": req.prompt }]
            }
        });
        let user_line = serde_json::to_string(&user_msg)
            .map_err(|e| TurnError::Protocol(format!("encode user message: {e}")))?;
        cli.write_line(&user_line).await.map_err(TurnError::Io)?;

        let mut engine_session_id = req.engine_session_id.clone();
        let mut mapper = CcTurnMapper::new(&req.run_id);
        if self.completion_mode == CompletionMode::HookIdle { mapper.disable_consumption_tracking(); }
        // Sliding idle watchdog: every received line pushes this deadline out.
        // A stream that goes quiet forever — the CLI wedged on something the
        // bridge can't observe — must fail the turn explicitly instead of
        // wedging until the outer run deadline with BCS waiting on a final.
        let idle_window = req.idle_timeout.unwrap_or(IDLE_WINDOW);
        let mut completion_deadline = None;
        let mut idle_deadline = tokio::time::Instant::now() + idle_window;
        loop {
            let completion_timer = async {
                match completion_deadline {
                    Some(deadline) => tokio::time::sleep_until(deadline).await,
                    None => std::future::pending::<()>().await,
                }
            };
            let idle_timer = tokio::time::sleep_until(idle_deadline);
            tokio::select! {
                biased;
                _ = abort.cancelled() => {
                    cli.kill().await;
                    return Err(TurnError::Aborted);
                }
                _ = completion_timer => return Err(TurnError::Protocol(UNAVAILABLE.into())),
                _ = idle_timer => {
                    let pending = mapper.background_pending();
                    cli.kill().await;
                    return Err(TurnError::EngineExited(format!(
                        "stream idle for {idle_window:?} ({pending} pending background task(s))"
                    )));
                }
                line = cli.next_line() => {
                    let Some(line) = line.map_err(|e| {
                        if self.completion_mode == CompletionMode::HookIdle && e.kind() == std::io::ErrorKind::InvalidData {
                            TurnError::Protocol(UNAVAILABLE.into())
                        } else { TurnError::Io(e) }
                    })? else {
                        let pending = mapper.background_pending();
                        let note = if pending > 0 {
                            format!(" with {pending} pending background task(s)")
                        } else {
                            String::new()
                        };
                        return Err(TurnError::EngineExited(format!("stdout EOF before result{note}")));
                    };
                    if abort.is_cancelled() || events.is_closed() { return Err(TurnError::Aborted); }
                    let mut callback_frame = false;
                    if let Some(evidence) = &mut completion {
                        if let Some(trace) = &req.trace { trace.record_raw(&trace_line(&line)); }
                        let evidence_result = (|| {
                            if line.len() > 2 * 1024 * 1024 { return Err(UNAVAILABLE); }
                            let v: Value = serde_json::from_str(&line).map_err(|_| UNAVAILABLE)?;
                            callback_frame = v["type"] == "control_request" && v["request"]["subtype"] == "hook_callback";
                            let reply = evidence.callback(&v)?;
                            let ready = evidence.event(&v)?;
                            Ok((reply, ready))
                        })();
                        match evidence_result {
                            Ok((reply, ready)) => {
                                if let Some(reply) = reply {
                                    write_completion_reply(&mut cli, &abort, &reply).await?;
                                }
                                if let Some(text) = ready {
                                    if self.completion_mode == CompletionMode::HookIdle {
                                        if abort.is_cancelled() || events.is_closed() { return Err(TurnError::Aborted); }
                                        return Ok(TurnOutcome { engine_session_id, final_text: Some(text) });
                                    }
                                }
                            }
                            Err(reason) => {
                                tracing::warn!(target:"bcs_bridge_core::engine", mode=?self.completion_mode, reason, "completion_evidence_rejected");
                                if self.completion_mode == CompletionMode::HookIdle { return Err(TurnError::Protocol(reason.into())); }
                                // Observation cannot veto or postpone a legacy result.
                                // Still acknowledge callbacks so the observer never blocks Stop.
                                if callback_frame {
                                    let v: Value = serde_json::from_str(&line).map_err(|_| TurnError::Protocol(UNAVAILABLE.into()))?;
                                    let reply = json!({"type":"control_response","response":{"subtype":"success","request_id":v["request_id"],"response":{}}});
                                    write_completion_reply(&mut cli, &abort, &reply).await?;
                                }
                            }
                        }
                    }
                    if self.completion_mode == CompletionMode::HookIdle {
                        if completion.as_ref().is_some_and(Completion::awaiting_idle) {
                            completion_deadline.get_or_insert_with(|| tokio::time::Instant::now()
                                + idle_window.min(std::time::Duration::from_secs(5)));
                        } else { completion_deadline = None; }
                    }
                    // Callback retries are not progress and must not extend the watchdog.
                    if !callback_frame { idle_deadline = tokio::time::Instant::now() + idle_window; }
                    match mapper.map_line(&line) {
                        CcMap::SessionId(s) => {
                            if !is_valid_engine_session_id(&s) {
                                return Err(TurnError::Protocol("cc supplied invalid session id".into()));
                            }
                            req.session_observer.established(&s).await.map_err(TurnError::SessionStorage)?;
                            engine_session_id = Some(s);
                        }
                        CcMap::Events(evs) => for ev in evs {
                            if events.send(ev).await.is_err() {
                                cli.kill().await;
                                return Err(TurnError::Aborted);
                            }
                        },
                        CcMap::Final(text) => {
                            if self.completion_mode == CompletionMode::HookIdle { continue; }
                            if mapper.background_pending() > 0 {
                                // Intermediate result: background work launched this
                                // turn is still pending, so the CLI keeps the turn
                                // alive — it auto-wakes the agent on each
                                // task_notification and emits another result once
                                // the work is done. Its text already reached BCS
                                // through the streamed deltas; swallow this result
                                // and keep reading. The run deadline bounds the wait.
                                continue;
                            }
                            return Ok(TurnOutcome { engine_session_id, final_text: Some(text) });
                        }
                        CcMap::BackgroundStarted { .. }
                        | CcMap::BackgroundEnqueued { .. }
                        | CcMap::BackgroundDelivered { .. } => {
                            // Consumed into mapper tracking inside map_line.
                        }
                        CcMap::Failed(note) => {
                            cli.kill().await;
                            return Err(TurnError::EngineExited(note));
                        }
                        CcMap::ControlRequest { request_id, tool_name, input } => {
                            handle_control_request(
                                &mut cli,
                                &events,
                                &abort,
                                &req,
                                request_id,
                                tool_name,
                                input,
                            )
                            .await?;
                            // The watchdog measures ENGINE silence, but the
                            // await above parked on a human decision for an
                            // unbounded time during which engine silence is
                            // protocol-required. Restart the window: the moment
                            // the approval lands, the engine owes output again.
                            idle_deadline = tokio::time::Instant::now() + idle_window;
                        }
                        CcMap::Ignore | CcMap::Malformed => {}
                    }
                }
            }
        }
    }
}


async fn write_completion_reply(cli: &mut CliSession, abort: &tokio_util::sync::CancellationToken, reply: &Value) -> Result<(), TurnError> {
    let line = reply.to_string();
    tokio::select! {
        biased;
        _ = abort.cancelled() => Err(TurnError::Aborted),
        result = tokio::time::timeout(std::time::Duration::from_secs(5), cli.write_line(&line)) => {
            result.map_err(|_| TurnError::Protocol(UNAVAILABLE.into()))?.map_err(TurnError::Io)
        }
    }
}

#[cfg(test)]
#[path = "claude_code/tests.rs"]
mod tests;
