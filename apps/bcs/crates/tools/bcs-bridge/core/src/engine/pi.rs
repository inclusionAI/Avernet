//! Pi-family RPC driver: runs one `pi --mode rpc` (or `omp --mode rpc`)
//! subprocess per turn over the JSONL RPC protocol and maps its session
//! events to engine-neutral [`StreamEvent`]s.
//!
//! Two flavors share this driver because omp is a pi-mono fork that kept the
//! command and event vocabulary but changed the turn lifecycle framing:
//!
//! | | `pi` | `omp` |
//! | --- | --- | --- |
//! | startup | no greeting; the `get_state` response is the handshake | writes a `ready` frame first |
//! | session resume | `--session-id <id>` argv | `open_session {sessionDir}` command over a bridge-owned directory |
//! | prompt completion | `data.disposition`; `handled` finished locally | response `agentInvoked: false` or a later `prompt_result` frame |
//! | turn terminal | `agent_settled` event | `prompt_result` + `session_settled` frames (omp has no `agent_settled`) |
//!
//! Neither flavor emits per-tool-call approval requests in RPC mode, so the
//! driver registers no HITL interactions: `permission_mode` is ignored (the
//! registry warns) and tool reach is narrowed with the engine's own options
//! (`tools`, `no_extensions`) or the host sandbox.
//!
//! Turn lifecycle (one fresh process per turn, mirroring the claude-code
//! driver; a pi RPC process hosts exactly one session and its event stream
//! carries no turn ids, so a shared process could not attribute events to
//! runs):
//!
//! ```text
//! <bin> <prefix_args...> --mode rpc [--model <model>] [--session-id <id>]  (pi)
//!   pi:  get_state -> sessionId
//!   omp: ready -> open_session{sessionDir: <cwd>/.bcs-bridge/omp-sessions/<id>}
//!   prompt -> response (started|queued|handled | agentInvoked)
//!   message_update.text_delta       -> chat_delta
//!   message_update.thinking_delta   -> agent_thinking
//!   tool_execution_start/update/end -> agent_tool
//!   message_end (assistant)         -> authoritative final text
//!   agent_settled | session_settled -> TurnOutcome
//! ```
//!
//! Known limitations: prompts consumed locally by an extension or builtin
//! command (pi `disposition: "handled"`, omp `agentInvoked: false`) complete
//! with an empty final text — their side-channel output is not part of the
//! agent event stream, and omp's `command_output` frames are not bridged.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use bcs_protocol::stream::{StreamEvent, ToolData, ToolPhase};
use serde_json::{Value, json};
use tokio::sync::mpsc::Sender;
use tokio_util::sync::CancellationToken;

use crate::engine::cli::CliSession;
use crate::engine::{Engine, TurnError, TurnOutcome, TurnRequest, is_valid_engine_session_id};
use crate::sse;

/// Which JSONL RPC dialect the engine speaks.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PiFlavor {
    /// Upstream pi coding agent: `agent_settled` terminal event, sessions
    /// resume through the `--session-id` argv flag.
    Pi,
    /// omp (pi-mono fork): `ready` greeting, `prompt_result`/`session_settled`
    /// terminals, sessions bind to a host-keyed directory resumed with the
    /// `open_session` command.
    Omp,
}

/// Per-bot pi engine policy, parsed from `bot.engine_options`.
///
/// Defaults pass no policy flags so the engine's own configuration
/// (`~/.pi/agent/settings.json`, saved project trust) stays authoritative.
/// `approve` exists because RPC mode cannot show the project-trust prompt:
/// with no saved decision and `defaultProjectTrust: "ask"` protected project
/// resources are skipped, which is the safe unattended default.
#[derive(Clone, Debug, Default)]
pub struct PiOptions {
    /// `--tools <list>`: replace the engine's default tool selection.
    tools: Option<String>,
    /// `--approve` / `--no-approve`: explicit project-trust decision.
    approve: Option<bool>,
    /// `--thinking <level>`: reasoning level.
    thinking: Option<String>,
    /// `--no-extensions`: never load discovered extensions.
    no_extensions: bool,
    /// `--session-dir <dir>`: session storage base (pi only; omp binds its
    /// directory through `open_session` instead).
    session_dir: Option<PathBuf>,
    /// `--no-ui`: run extensions headless (omp only).
    no_ui: bool,
}

impl PiOptions {
    const KEY_TOOLS: &'static str = "tools";
    const KEY_APPROVE: &'static str = "approve";
    const KEY_THINKING: &'static str = "thinking";
    const KEY_NO_EXTENSIONS: &'static str = "no_extensions";
    const KEY_SESSION_DIR: &'static str = "session_dir";
    const KEY_NO_UI: &'static str = "no_ui";

    fn parse(options: &toml::Table, flavor: PiFlavor) -> Result<Self, String> {
        let mut parsed = Self::default();
        for (key, value) in options {
            match (key.as_str(), value) {
                (Self::KEY_TOOLS, toml::Value::String(tools)) if !tools.trim().is_empty() => {
                    parsed.tools = Some(tools.clone());
                }
                (Self::KEY_APPROVE, toml::Value::Boolean(approve)) => {
                    parsed.approve = Some(*approve)
                }
                (Self::KEY_THINKING, toml::Value::String(thinking))
                    if !thinking.trim().is_empty() =>
                {
                    parsed.thinking = Some(thinking.trim().to_string());
                }
                (Self::KEY_NO_EXTENSIONS, toml::Value::Boolean(no_extensions)) => {
                    parsed.no_extensions = *no_extensions;
                }
                (Self::KEY_SESSION_DIR, toml::Value::String(dir)) if flavor == PiFlavor::Pi => {
                    if dir.trim().is_empty() {
                        return Err("engine_options.session_dir must be a non-empty path".into());
                    }
                    parsed.session_dir = Some(PathBuf::from(dir));
                }
                (Self::KEY_NO_UI, toml::Value::Boolean(no_ui)) if flavor == PiFlavor::Omp => {
                    parsed.no_ui = *no_ui;
                }
                (Self::KEY_SESSION_DIR, _) if flavor == PiFlavor::Omp => {
                    return Err("engine_options.session_dir is a pi-only key; the omp flavor binds sessions with open_session".into());
                }
                (Self::KEY_NO_UI, _) if flavor == PiFlavor::Pi => {
                    return Err("engine_options.no_ui is an omp-only key".into());
                }
                _ => return Err(format!("unsupported engine_options key '{key}'")),
            }
        }
        Ok(parsed)
    }

    /// Append the policy flags to the spawn argv.
    fn push_args(&self, args: &mut Vec<String>, flavor: PiFlavor) {
        if let Some(tools) = &self.tools {
            args.push("--tools".into());
            args.push(tools.clone());
        }
        match self.approve {
            Some(true) => args.push("--approve".into()),
            Some(false) => args.push("--no-approve".into()),
            None => {}
        }
        if let Some(thinking) = &self.thinking {
            args.push("--thinking".into());
            args.push(thinking.clone());
        }
        if self.no_extensions {
            args.push("--no-extensions".into());
        }
        if flavor == PiFlavor::Omp && self.no_ui {
            args.push("--no-ui".into());
        }
    }
}

/// Pi-family RPC driver. One subprocess per turn; sessions persist in the
/// engine's own session storage and resume across bridge restarts.
pub struct PiEngine {
    bin: PathBuf,
    prefix_args: Vec<String>,
    flavor: PiFlavor,
    options: PiOptions,
}

/// Factory for [`PiEngine`]. The default is the built-in `pi` engine running
/// `pi`; [`PiFactory::omp`] registers the omp dialect under its own id, and
/// [`PiFactory::custom`] reuses either dialect for a launcher that selects
/// the engine through a subcommand or flag.
pub struct PiFactory {
    id: String,
    default_bin: String,
    prefix_args: Vec<String>,
    flavor: PiFlavor,
}

impl PiFactory {
    pub fn custom(
        id: impl Into<String>,
        default_bin: impl Into<String>,
        prefix_args: Vec<String>,
        flavor: PiFlavor,
    ) -> Self {
        Self {
            id: id.into(),
            default_bin: default_bin.into(),
            prefix_args,
            flavor,
        }
    }

    /// The omp (pi-mono fork) dialect under the `omp` engine id.
    pub fn omp() -> Self {
        Self::custom("omp", "omp", Vec::new(), PiFlavor::Omp)
    }
}

impl Default for PiFactory {
    fn default() -> Self {
        Self::custom("pi", "pi", Vec::new(), PiFlavor::Pi)
    }
}

impl crate::engine::EngineFactory for PiFactory {
    fn supports_caller_env(&self) -> bool {
        true
    }
    fn id(&self) -> &str {
        &self.id
    }
    fn default_bin(&self) -> &str {
        &self.default_bin
    }
    fn build(&self, bin: PathBuf, options: &toml::Table) -> Result<Arc<dyn Engine>, String> {
        Ok(Arc::new(PiEngine {
            bin,
            prefix_args: self.prefix_args.clone(),
            flavor: self.flavor,
            options: PiOptions::parse(options, self.flavor)?,
        }))
    }
}

/// Default sliding idle window, mirroring the claude-code driver: a pi agent
/// executing a long tool (e.g. a multi-minute bash call) emits nothing on
/// stdout for the tool's whole duration — only RPC-issued `bash` commands
/// stream `bash_execution_update`. The window must cover that legitimate
/// silence while still surfacing a wedged stream long before the outer run
/// deadline. A per-turn `req.idle_timeout` override still wins.
const IDLE_WINDOW: std::time::Duration = std::time::Duration::from_secs(600);

/// Bound for the startup handshake: omp `ready` frame plus the `get_state`
/// (pi) or `open_session` (omp) response.
const HANDSHAKE_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(30);

/// Bound on waiting for the `abort` command's response (both flavors wait for
/// the session to become idle before responding) before killing the tree.
const ABORT_GRACE: std::time::Duration = std::time::Duration::from_secs(10);

/// Inbound JSONL line cap. omp caps v1 physical frames at 1 MiB and compacts
/// oversized `agent_end` frames by dropping already-streamed messages; pi has
/// no wire cap and repeats every run message inside `agent_end`. 16 MiB
/// bounds a hostile or buggy engine while leaving room for those aggregates.
const MAX_LINE_BYTES: usize = 16 * 1024 * 1024;

/// omp sessions live in bridge-owned directories under the bot's working
/// directory so each BCS session maps to one `open_session` target that
/// survives bridge restarts. The engine session id *is* the directory name.
fn omp_session_dir(cwd: &Path, engine_session_id: &str) -> PathBuf {
    cwd.join(".bcs-bridge")
        .join("omp-sessions")
        .join(engine_session_id)
}

/// A fresh omp session directory name. Valid by construction under both the
/// bridge's `is_valid_engine_session_id` and omp's session-id charset.
fn new_omp_session_id() -> String {
    format!("bcs-{}", uuid::Uuid::new_v4().simple())
}

#[async_trait::async_trait]
impl Engine for PiEngine {
    async fn run_turn(
        &self,
        req: TurnRequest,
        events: Sender<StreamEvent>,
        abort: CancellationToken,
    ) -> Result<TurnOutcome, TurnError> {
        if abort.is_cancelled() {
            return Err(TurnError::Aborted);
        }
        let mut args = self.prefix_args.clone();
        args.extend(["--mode".into(), "rpc".into()]);
        if let Some(model) = &req.model {
            args.push("--model".into());
            args.push(model.clone());
        }
        // pi resumes by exact session id; the id came from the engine and was
        // validated before persistence, so it is safe as an argv value.
        let omp_session_id = match self.flavor {
            PiFlavor::Pi => {
                if let Some(id) = &req.engine_session_id {
                    if !is_valid_engine_session_id(id) {
                        return Err(TurnError::Protocol(format!(
                            "invalid persisted pi session id '{id}'"
                        )));
                    }
                    args.push("--session-id".into());
                    args.push(id.clone());
                }
                if let Some(dir) = &self.options.session_dir {
                    args.push("--session-dir".into());
                    args.push(dir.to_string_lossy().into_owned());
                }
                None
            }
            // omp binds sessions through `open_session` after startup; the
            // persisted id is this bridge's directory name.
            PiFlavor::Omp => {
                let id = match &req.engine_session_id {
                    Some(id) if is_valid_engine_session_id(id) => id.clone(),
                    Some(id) => {
                        return Err(TurnError::Protocol(format!(
                            "invalid persisted omp session id '{id}'"
                        )));
                    }
                    None => new_omp_session_id(),
                };
                let dir = omp_session_dir(&req.cwd, &id);
                std::fs::create_dir_all(&dir).map_err(|error| {
                    TurnError::Protocol(format!(
                        "create omp session dir {}: {error}",
                        dir.display()
                    ))
                })?;
                Some(id)
            }
        };
        self.options.push_args(&mut args, self.flavor);
        let mut cli = CliSession::spawn(&self.bin, &args, &req.cwd, &req.env, req.trace.clone())
            .await
            .map_err(TurnError::Spawn)?;
        cli.limit_stdout_line(MAX_LINE_BYTES);
        if abort.is_cancelled() {
            cli.kill().await;
            return Err(TurnError::Aborted);
        }

        // ---- Handshake. No prompt has been sent, so no agent events can
        // interleave; `await_response` may safely skip unrelated frames.
        if self.flavor == PiFlavor::Omp {
            expect_ready(&mut cli, &abort).await?;
        }
        let session_id = match self.flavor {
            PiFlavor::Pi => {
                let state =
                    rpc_call(&mut cli, "get_state", json!({}), &abort, HANDSHAKE_TIMEOUT).await?;
                let id = state
                    .pointer("/sessionId")
                    .and_then(Value::as_str)
                    .ok_or_else(|| {
                        TurnError::Protocol("get_state response missing sessionId".into())
                    })?
                    .to_string();
                if let Some(expected) = &req.engine_session_id {
                    if id != *expected {
                        return Err(TurnError::Protocol(format!(
                            "pi reopened session '{id}' instead of requested '{expected}'"
                        )));
                    }
                }
                id
            }
            PiFlavor::Omp => {
                let dir = omp_session_dir(
                    &req.cwd,
                    omp_session_id.as_deref().expect("omp session id set"),
                );
                let opened = rpc_call(
                    &mut cli,
                    "open_session",
                    json!({"sessionDir": dir.to_string_lossy()}),
                    &abort,
                    HANDSHAKE_TIMEOUT,
                )
                .await?;
                if opened.get("cancelled").and_then(Value::as_bool) == Some(true) {
                    return Err(TurnError::Protocol(
                        "omp extension canceled the session switch".into(),
                    ));
                }
                // Persist our directory name, not omp's internal session id:
                // `open_session` rebinds by directory, and a fork/branch inside
                // the engine can change the internal id between turns.
                omp_session_id.expect("omp session id set")
            }
        };
        if !is_valid_engine_session_id(&session_id) {
            cli.kill().await;
            return Err(TurnError::Protocol(format!(
                "engine supplied invalid session id '{session_id}'"
            )));
        }
        req.session_observer
            .established(&session_id)
            .await
            .map_err(TurnError::SessionStorage)?;
        if abort.is_cancelled() {
            cli.kill().await;
            return Err(TurnError::Aborted);
        }

        // ---- Prompt. The response is handled inside the event loop: pi
        // accepts a prompt asynchronously and may stream agent events before
        // the acknowledgement line, which a blocking wait would drop.
        let prompt_id = "bridge-prompt".to_string();
        let prompt_command = json!({"id": prompt_id, "type": "prompt", "message": req.prompt});
        cli.write_line(&prompt_command.to_string())
            .await
            .map_err(TurnError::Io)?;

        // ---- Event loop
        let mut state = TurnState::default();
        let idle_window = req.idle_timeout.unwrap_or(IDLE_WINDOW);
        let mut idle_deadline = tokio::time::Instant::now() + idle_window;
        loop {
            tokio::select! {
                biased;
                _ = abort.cancelled() => {
                    abort_engine(&mut cli, &abort).await;
                    return Err(TurnError::Aborted);
                }
                _ = events.closed() => {
                    abort_engine(&mut cli, &abort).await;
                    return Err(TurnError::Aborted);
                }
                _ = tokio::time::sleep_until(idle_deadline) => {
                    cli.kill().await;
                    return Err(TurnError::EngineExited(format!(
                        "stream idle for {idle_window:?} (last event: {})",
                        state.last_event.unwrap_or("none")
                    )));
                }
                line = cli.next_line() => {
                    let Some(line) = line.map_err(TurnError::Io)? else {
                        cli.kill().await;
                        return Err(TurnError::EngineExited(format!(
                            "stdout EOF before the turn settled (last event: {})",
                            state.last_event.unwrap_or("none")
                        )));
                    };
                    if abort.is_cancelled() || events.is_closed() {
                        abort_engine(&mut cli, &abort).await;
                        return Err(TurnError::Aborted);
                    }
                    idle_deadline = tokio::time::Instant::now() + idle_window;
                    let value: Value = match serde_json::from_str(&line) {
                        Ok(value) => value,
                        Err(error) => {
                            cli.kill().await;
                            return Err(TurnError::Protocol(format!("invalid JSONL line: {error}")));
                        }
                    };
                    match handle_frame(&self.flavor, value, &req.run_id, &prompt_id, &mut state, &events, &mut cli, &abort).await? {
                        FrameOutcome::Continue => {}
                        FrameOutcome::Settled { final_text } => {
                            cli.kill().await;
                            return finish(state, session_id, final_text);
                        }
                    }
                }
            }
        }
    }
}

/// Per-turn streaming state.
#[derive(Default)]
struct TurnState {
    /// Final text of the latest assistant message (`message_end` is the
    /// authoritative snapshot; streamed deltas are progress only).
    last_text: String,
    /// Error of the latest assistant message, when its stop reason is
    /// `error`. Cleared by a later successful message (auto-retry).
    last_error: Option<String>,
    /// omp: `prompt_result` reported completion but background work may still
    /// wake the agent; the turn ends on the following `session_settled`.
    omp_awaiting_settled: bool,
    last_event: Option<&'static str>,
}

#[derive(Debug)]
enum FrameOutcome {
    Continue,
    /// The turn ended. `final_text: Some(_)` overrides the accumulated text
    /// (used by locally-handled prompts); `None` uses the authoritative
    /// `message_end` snapshot.
    Settled {
        final_text: Option<String>,
    },
}

/// Classify and map one stdout frame. Pure mapping plus event emission; the
/// process lifecycle stays with the caller.
async fn handle_frame(
    flavor: &PiFlavor,
    value: Value,
    run_id: &str,
    prompt_id: &str,
    state: &mut TurnState,
    events: &Sender<StreamEvent>,
    cli: &mut CliSession,
    abort: &CancellationToken,
) -> Result<FrameOutcome, TurnError> {
    let frame_type = value
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or_default();
    match frame_type {
        "response" => {
            if value.get("id").and_then(Value::as_str) != Some(prompt_id) {
                return Ok(FrameOutcome::Continue);
            }
            if value.get("success").and_then(Value::as_bool) != Some(true) {
                let error = value
                    .get("error")
                    .and_then(Value::as_str)
                    .unwrap_or("unknown error");
                return Err(TurnError::Protocol(format!("prompt rejected: {error}")));
            }
            let data = value.get("data").cloned().unwrap_or(Value::Null);
            let handled = match flavor {
                PiFlavor::Pi => data.get("disposition").and_then(Value::as_str) == Some("handled"),
                // omp acknowledges locally-finished builtin commands with
                // `agentInvoked: false` and sends no `prompt_result` for them.
                PiFlavor::Omp => data.get("agentInvoked").and_then(Value::as_bool) == Some(false),
            };
            if handled {
                return Ok(FrameOutcome::Settled {
                    final_text: Some(String::new()),
                });
            }
            Ok(FrameOutcome::Continue)
        }
        "message_update" => {
            let event = value.get("assistantMessageEvent");
            match event
                .and_then(|event| event.get("type"))
                .and_then(Value::as_str)
            {
                Some("text_delta") => {
                    if let Some(delta) = event
                        .and_then(|event| event.get("delta"))
                        .and_then(Value::as_str)
                    {
                        if send_event(events, sse::chat_delta(run_id, delta)).await {
                            abort_engine(cli, abort).await;
                            return Err(TurnError::Aborted);
                        }
                    }
                }
                Some("thinking_delta") => {
                    if let Some(delta) = event
                        .and_then(|event| event.get("delta"))
                        .and_then(Value::as_str)
                    {
                        if send_event(
                            events,
                            sse::agent_thinking(run_id, Some(delta.to_string()), None),
                        )
                        .await
                        {
                            abort_engine(cli, abort).await;
                            return Err(TurnError::Aborted);
                        }
                    }
                }
                _ => {}
            }
            Ok(FrameOutcome::Continue)
        }
        "message_end" => {
            let message = value.get("message");
            if message
                .and_then(|message| message.get("role"))
                .and_then(Value::as_str)
                == Some("assistant")
            {
                state.last_text = assistant_text(message.unwrap_or(&Value::Null));
                state.last_error = message
                    .and_then(|message| message.get("stopReason"))
                    .and_then(Value::as_str)
                    .filter(|reason| *reason == "error")
                    .and_then(|_| message.and_then(|message| message.get("errorMessage")))
                    .and_then(Value::as_str)
                    .map(str::to_string);
                state.last_event = Some("message_end");
            }
            Ok(FrameOutcome::Continue)
        }
        "tool_execution_start" | "tool_execution_update" | "tool_execution_end" => {
            let phase = match frame_type {
                "tool_execution_start" => ToolPhase::Start,
                "tool_execution_update" => ToolPhase::Update,
                _ => ToolPhase::Result,
            };
            if let Some(event) = tool_event(&value, run_id, phase) {
                // A frame without toolCallId/toolName is skipped, not fatal:
                // the run continues and the final message is authoritative.
                if send_event(events, event).await {
                    abort_engine(cli, abort).await;
                    return Err(TurnError::Aborted);
                }
                state.last_event = Some("tool_execution");
            }
            Ok(FrameOutcome::Continue)
        }
        "auto_retry_end" => {
            if value.get("success").and_then(Value::as_bool) == Some(false) {
                let error = value
                    .get("finalError")
                    .and_then(Value::as_str)
                    .unwrap_or("agent retry failed");
                return Err(TurnError::EngineExited(error.to_string()));
            }
            Ok(FrameOutcome::Continue)
        }
        "agent_settled" if *flavor == PiFlavor::Pi => {
            if value.get("aborted").and_then(Value::as_bool) == Some(true) {
                // The bridge-requested abort path returns before reading this
                // frame, so an in-loop aborted settle is engine-initiated;
                // surface it explicitly instead of closing the stream
                // silently (the run loop stays quiet for unrequested
                // `TurnError::Aborted`).
                return Err(TurnError::EngineExited("pi aborted the run".into()));
            }
            Ok(FrameOutcome::Settled { final_text: None })
        }
        "prompt_result" if *flavor == PiFlavor::Omp => {
            if value.get("id").and_then(Value::as_str) != Some(prompt_id) {
                return Ok(FrameOutcome::Continue);
            }
            state.last_event = Some("prompt_result");
            match value
                .get("status")
                .and_then(Value::as_str)
                .unwrap_or_default()
            {
                "completed" => {
                    if value.get("sessionSettled").and_then(Value::as_bool) == Some(true) {
                        Ok(FrameOutcome::Settled { final_text: None })
                    } else {
                        // Background work (auto-backgrounded bash, async jobs)
                        // may still stream follow-up runs; the turn ends when
                        // omp reports quiescence.
                        state.omp_awaiting_settled = true;
                        Ok(FrameOutcome::Continue)
                    }
                }
                "aborted" => Err(TurnError::EngineExited("omp aborted the prompt".into())),
                status => {
                    let message = value
                        .pointer("/error/message")
                        .and_then(Value::as_str)
                        .map(str::to_string)
                        .unwrap_or_else(|| format!("omp prompt ended with status '{status}'"));
                    Err(TurnError::EngineExited(message))
                }
            }
        }
        "session_settled" if *flavor == PiFlavor::Omp => {
            if state.omp_awaiting_settled {
                Ok(FrameOutcome::Settled { final_text: None })
            } else {
                // Quiescence of pre-prompt activity; not our terminal.
                Ok(FrameOutcome::Continue)
            }
        }
        // Unknown frames (queue_update, compaction_*, rpc_frame_error,
        // available_commands_update, extension_ui_*, ...) are ignored: the
        // protocol is additive and the bridge must stay forward-compatible.
        _ => Ok(FrameOutcome::Continue),
    }
}

/// Resolve the turn from the accumulated state.
fn finish(
    state: TurnState,
    session_id: String,
    final_text: Option<String>,
) -> Result<TurnOutcome, TurnError> {
    if let Some(error) = state.last_error {
        return Err(TurnError::EngineExited(error));
    }
    Ok(TurnOutcome {
        engine_session_id: Some(session_id),
        final_text: Some(final_text.unwrap_or(state.last_text)),
    })
}

/// Concatenate the text blocks of an assistant message.
fn assistant_text(message: &Value) -> String {
    let Some(content) = message.get("content").and_then(Value::as_array) else {
        return String::new();
    };
    let mut text = String::new();
    for block in content {
        if block.get("type").and_then(Value::as_str) == Some("text") {
            if let Some(chunk) = block.get("text").and_then(Value::as_str) {
                if !text.is_empty() {
                    text.push('\n');
                }
                text.push_str(chunk);
            }
        }
    }
    text
}

/// Map one pi tool-execution event to an engine-neutral tool event.
fn tool_event(value: &Value, run_id: &str, phase: ToolPhase) -> Option<StreamEvent> {
    let tool_call_id = value.get("toolCallId").and_then(Value::as_str)?.to_string();
    let name = value.get("toolName").and_then(Value::as_str)?.to_string();
    let is_result = phase == ToolPhase::Result;
    Some(sse::agent_tool(
        run_id,
        ToolData {
            phase,
            name: Some(name),
            tool_call_id: Some(tool_call_id),
            is_error: is_result
                .then(|| value.get("isError").and_then(Value::as_bool))
                .flatten(),
            exit_code: None,
            duration_ms: is_result
                .then(|| value.get("durationMs").and_then(Value::as_u64))
                .flatten(),
            cwd: None,
            args: value.get("args").cloned().filter(|value| !value.is_null()),
            result: is_result.then(|| flatten_tool_result(value.get("result"))),
            partial_result: (!is_result)
                .then(|| value.get("partialResult").cloned())
                .flatten()
                .filter(|value| !value.is_null()),
        },
    ))
}

/// pi tool results carry `content: [{type:"text"|"image", ...}]`. Text blocks
/// concatenate into one string (BCS tool results are single-valued); a result
/// with no text blocks keeps its raw JSON so nothing is silently dropped.
fn flatten_tool_result(result: Option<&Value>) -> Value {
    let Some(result) = result.filter(|result| !result.is_null()) else {
        return Value::String(String::new());
    };
    let Some(blocks) = result.get("content").and_then(Value::as_array) else {
        return result.clone();
    };
    let mut text = String::new();
    for block in blocks {
        if block.get("type").and_then(Value::as_str) == Some("text") {
            if let Some(chunk) = block.get("text").and_then(Value::as_str) {
                if !text.is_empty() {
                    text.push('\n');
                }
                text.push_str(chunk);
            }
        }
    }
    // Blocks without text (e.g. image-only results) keep the raw JSON so the
    // payload is not silently replaced by an empty string.
    if text.is_empty() {
        return result.clone();
    }
    Value::String(text)
}

async fn send_event(events: &Sender<StreamEvent>, event: StreamEvent) -> bool {
    events.send(event).await.is_err()
}

/// Write one handshake command and await its matching response. Only used
/// before any prompt exists, so skipping unrelated frames loses nothing.
async fn rpc_call(
    cli: &mut CliSession,
    command: &str,
    params: Value,
    abort: &CancellationToken,
    timeout: std::time::Duration,
) -> Result<Value, TurnError> {
    let id = format!("bridge-{command}");
    let mut request = json!({"id": id, "type": command});
    if let (Some(object), Some(params)) = (request.as_object_mut(), params.as_object()) {
        for (key, value) in params {
            object.insert(key.clone(), value.clone());
        }
    }
    cli.write_line(&request.to_string())
        .await
        .map_err(TurnError::Io)?;
    await_response(cli, &id, command, abort, timeout).await
}

async fn await_response(
    cli: &mut CliSession,
    id: &str,
    command: &str,
    abort: &CancellationToken,
    timeout: std::time::Duration,
) -> Result<Value, TurnError> {
    let deadline = tokio::time::Instant::now() + timeout;
    loop {
        let line = tokio::select! {
            biased;
            _ = abort.cancelled() => return Err(TurnError::Aborted),
            _ = tokio::time::sleep_until(deadline) => {
                return Err(TurnError::Protocol(format!("'{command}' response timed out after {timeout:?}")));
            }
            line = cli.next_line() => line.map_err(TurnError::Io)?,
        };
        let Some(line) = line else {
            return Err(TurnError::EngineExited(format!(
                "stdout EOF while awaiting '{command}'"
            )));
        };
        let value: Value = match serde_json::from_str(&line) {
            Ok(value) => value,
            Err(error) => return Err(TurnError::Protocol(format!("invalid JSONL line: {error}"))),
        };
        if value.get("type").and_then(Value::as_str) == Some("response")
            && value.get("id").and_then(Value::as_str) == Some(id)
        {
            if value.get("success").and_then(Value::as_bool) != Some(true) {
                let error = value
                    .get("error")
                    .and_then(Value::as_str)
                    .unwrap_or("unknown error");
                return Err(TurnError::Protocol(format!("'{command}' failed: {error}")));
            }
            return Ok(value.get("data").cloned().unwrap_or(Value::Null));
        }
        // Skip frames that are not the response we are waiting for: omp emits
        // `available_commands_update` and session events around startup.
    }
}

/// omp writes one `ready` frame before processing any command.
async fn expect_ready(cli: &mut CliSession, abort: &CancellationToken) -> Result<(), TurnError> {
    let deadline = tokio::time::Instant::now() + HANDSHAKE_TIMEOUT;
    loop {
        let line = tokio::select! {
            biased;
            _ = abort.cancelled() => return Err(TurnError::Aborted),
            _ = tokio::time::sleep_until(deadline) => {
                return Err(TurnError::Protocol("omp ready frame timed out".into()));
            }
            line = cli.next_line() => line.map_err(TurnError::Io)?,
        };
        let Some(line) = line else {
            return Err(TurnError::EngineExited(
                "stdout EOF before the omp ready frame".into(),
            ));
        };
        let value: Value = serde_json::from_str(&line).map_err(|error| {
            TurnError::Protocol(format!("omp ready frame is not JSON: {error}"))
        })?;
        if value.get("type").and_then(Value::as_str) == Some("ready") {
            return Ok(());
        }
    }
}

/// Ask the engine to abort the active operation, then stop the process tree.
/// Both flavors answer the `abort` command only once the session is idle, so
/// its response bounds the wait; the grace timeout covers a wedged engine.
async fn abort_engine(cli: &mut CliSession, abort: &CancellationToken) {
    let abort_command = json!({"id": "bridge-abort", "type": "abort"});
    if cli.write_line(&abort_command.to_string()).await.is_ok() {
        let _ = await_response(cli, "bridge-abort", "abort", abort, ABORT_GRACE).await;
    }
    cli.kill().await;
}

#[cfg(test)]
#[path = "pi_tests.rs"]
mod tests;
