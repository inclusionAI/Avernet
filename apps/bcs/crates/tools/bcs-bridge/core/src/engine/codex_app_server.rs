//! `CodexAppServer` driver: runs `codex app-server` over the Codex JSON-RPC
//! stdio protocol.
//!
//! The owned stdio server stays alive across turns. Loaded threads use
//! `turn/start` directly; `thread/resume` is used after a Bridge/server restart.
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

use crate::engine::codex_transport::CodexConnection;
use crate::engine::{Engine, TurnError, TurnOutcome, TurnRequest};
use crate::sse;

/// Codex engine sandbox mode, forwarded into every `thread/*` and`turn/start`
/// wire parameter. `workspace-write` is the bridge default: the bot works
/// within its configured working directory, matching the cfuse interactive
/// (TUI) capability surface instead of the legacy read-only lockdown.
///
/// The wire serialization normalizes to the engine's recorded form
/// (kebab-case, echo of the rollout `turn_context.sandbox_policy.type`).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub enum CodexSandbox {
    #[default]
    WorkspaceWrite,
    ReadOnly,
    DangerFullAccess,
}

// Wire values as the codex app-server records them in rollout context.
const SANDBOX_READ_ONLY: &str = "read-only";
const SANDBOX_WORKSPACE_WRITE: &str = "workspace-write";
const SANDBOX_DANGER_FULL_ACCESS: &str = "danger-full-access";

// `turn/start` sandboxPolicy.type is a strict camelCase enum on the wire; the
// server rejects kebab-case ("unknown variant ..., expected one of
// `dangerFullAccess`, `readOnly`, `externalSandbox`, `workspaceWrite`").
const SANDBOX_TURN_READ_ONLY: &str = "readOnly";
const SANDBOX_TURN_WORKSPACE_WRITE: &str = "workspaceWrite";
const SANDBOX_TURN_DANGER_FULL_ACCESS: &str = "dangerFullAccess";

impl CodexSandbox {
    fn parse(value: &str) -> Option<Self> {
        match value {
            SANDBOX_READ_ONLY => Some(Self::ReadOnly),
            SANDBOX_WORKSPACE_WRITE => Some(Self::WorkspaceWrite),
            SANDBOX_DANGER_FULL_ACCESS => Some(Self::DangerFullAccess),
            _ => None,
        }
    }
    /// Kebab-case form used by `thread/start` / `thread/resume` `sandbox`
    /// (accepted there and echoed back in rollout turn_context records).
    fn wire(self) -> &'static str {
        match self {
            Self::ReadOnly => SANDBOX_READ_ONLY,
            Self::WorkspaceWrite => SANDBOX_WORKSPACE_WRITE,
            Self::DangerFullAccess => SANDBOX_DANGER_FULL_ACCESS,
        }
    }

    /// CamelCase form required by `turn/start` `sandboxPolicy.type`; the
    /// server-side enum rejects kebab variants at request time.
    fn turn_wire(self) -> &'static str {
        match self {
            Self::ReadOnly => SANDBOX_TURN_READ_ONLY,
            Self::WorkspaceWrite => SANDBOX_TURN_WORKSPACE_WRITE,
            Self::DangerFullAccess => SANDBOX_TURN_DANGER_FULL_ACCESS,
        }
    }
}

/// Codex approval policy, decides whether the engine may request approvals at all.
/// `never` (bridge default) means no approval channel: operations outside the
/// sandbox are denied outright instead of prompting.
///
/// Verified against a live cfuse codex 0.161 app-server (2026-10-18): the wire
/// enum accepts `never | on-request | untrusted | granular`; the legacy
/// `on-failure` value is rejected outright ("unknown variant"), so the bridge
/// whitelist never offers it. `granular` exists upstream but stays unexposed
/// until its per-command semantics are reviewed.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub enum CodexApprovalPolicy {
    #[default]
    Never,
    OnRequest,
    Untrusted,
}

impl CodexApprovalPolicy {
    fn parse(value: &str) -> Option<Self> {
        match value {
            "never" => Some(Self::Never),
            "on-request" => Some(Self::OnRequest),
            "untrusted" => Some(Self::Untrusted),
            _ => None,
        }
    }
    fn wire(self) -> &'static str {
        match self {
            Self::Never => "never",
            Self::OnRequest => "on-request",
            Self::Untrusted => "untrusted",
        }
    }
    fn asks_approvals(self) -> bool { !matches!(self, Self::Never) }
}

/// Who answers codex approval requests when `approval_policy` enables them.
/// - `user` (default): a human reviewer via BCS HITL (requires the codex HITL
///   wiring; not yet available in this driver).
/// - `auto`: cfuse `auto_review`, the engine's programmatic policy engine
///   (TUI parity: escalations are approved without human involvement).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub enum CodexApprovalReview {
    #[default]
    User,
    Auto,
}

impl CodexApprovalReview {
    fn parse(value: &str) -> Option<Self> {
        match value {
            "user" => Some(Self::User),
            "auto" => Some(Self::Auto),
            _ => None,
        }
    }
    fn wire(self) -> &'static str {
        match self {
            Self::User => "user",
            Self::Auto => "auto_review",
        }
    }
}

/// Per-bot Codex engine policy, parsed from `bot.engine_options`.
/// Defaults (policy decision 2026-10-18): `workspace-write` sandbox,
/// outbound network allowed, approvals never requested, and no reasoning
/// effort override (the engine's config-level default applies).
#[derive(Clone, Copy, Debug)]
pub struct CodexOptions {
    pub sandbox: CodexSandbox,
    pub network_access: bool,
    pub approval_policy: CodexApprovalPolicy,
    pub approval_review: CodexApprovalReview,
    /// `thread.*`/`turn.start` `effort`; None leaves the engine's own default
    /// (cfuse config.toml `model_reasoning_effort`, currently `xhigh`).
    pub reasoning_effort: Option<&'static str>,
}

impl Default for CodexOptions {
    fn default() -> Self {
        Self { sandbox: CodexSandbox::WorkspaceWrite, network_access: true,
            approval_policy: CodexApprovalPolicy::Never, approval_review: CodexApprovalReview::User,
            reasoning_effort: None }
    }
}

impl CodexOptions {
    const KEY_SANDBOX: &'static str = "sandbox";
    const KEY_NETWORK_ACCESS: &'static str = "network_access";
    const KEY_APPROVAL_POLICY: &'static str = "approval_policy";
    const KEY_APPROVAL_REVIEW: &'static str = "approval_review";
    const KEY_REASONING_EFFORT: &'static str = "reasoning_effort";

    // `effort` wire values, mirroring the cfuse model catalog reasoning levels.
    const EFFORTS: [&'static str; 5] = ["minimal", "low", "medium", "high", "xhigh"];

    fn parse(options: &toml::Table) -> Result<Self, String> {
        let mut parsed = Self { sandbox: CodexSandbox::WorkspaceWrite,
            network_access: true, approval_policy: CodexApprovalPolicy::Never,
            approval_review: CodexApprovalReview::User, reasoning_effort: None };
        for (key, value) in options {
            match (key.as_str(), value) {
                (Self::KEY_SANDBOX, toml::Value::String(sandbox)) => {
                    parsed.sandbox = CodexSandbox::parse(sandbox).ok_or_else(||
                        format!("engine_options.sandbox must be one of '{SANDBOX_READ_ONLY}' | '{SANDBOX_WORKSPACE_WRITE}' | '{SANDBOX_DANGER_FULL_ACCESS}'"))?;
                }
                (Self::KEY_NETWORK_ACCESS, toml::Value::Boolean(network)) => parsed.network_access = *network,
                (Self::KEY_APPROVAL_POLICY, toml::Value::String(policy)) => {
                    parsed.approval_policy = CodexApprovalPolicy::parse(policy).ok_or_else(||
                        format!("engine_options.approval_policy must be one of 'never' | 'on-request' | 'untrusted'"))?;
                }
                (Self::KEY_APPROVAL_REVIEW, toml::Value::String(review)) => {
                    parsed.approval_review = CodexApprovalReview::parse(review).ok_or_else(||
                        format!("engine_options.approval_review must be one of 'user' | 'auto'"))?;
                }
                (Self::KEY_REASONING_EFFORT, toml::Value::String(effort)) => {
                    let trimmed = effort.trim();
                    parsed.reasoning_effort = Some(Self::EFFORTS.iter().copied().find(|candidate| *candidate == trimmed)
                        .ok_or_else(|| format!("engine_options.reasoning_effort must be one of {}",
                            Self::EFFORTS.map(|e| format!("'{e}'")).join(" | ")))?);
                }
                _ => return Err(format!("unsupported engine_options key '{key}'")),
            }
        }
        match (parsed.approval_policy.asks_approvals(), parsed.approval_review) {
            // `never` closes the approval channel entirely; an auto reviewer
            // never fires. The combo usually means the author wanted engine-side
            // auto-approvals — fail fast instead of silently keeping the denial.
            (false, CodexApprovalReview::Auto) => return Err("engine_options.approval_review='auto' requires approval_policy 'on-request' or 'untrusted'; with 'never' escalations are denied without a reviewer".into()),
            // Human approval via BCS HITL needs codex→interaction routing that this
            // driver does not implement yet; asking would hit the -32000 rejector.
            (true, CodexApprovalReview::User) => return Err("engine_options.approval_review='user' with a non-'never' approval_policy requires codex HITL routing, which this driver does not implement yet; use approval_policy='never' or approval_review='auto'".into()),
            _ => {}
        }
        if parsed.sandbox == CodexSandbox::DangerFullAccess && options.contains_key(Self::KEY_NETWORK_ACCESS) {
            tracing::warn!(target: "bcs_bridge_core::engine",
                "engine_options.network_access is a no-op with sandbox='danger-full-access'; the sandbox already permits everything");
        }
        if parsed.approval_review == CodexApprovalReview::Auto {
            tracing::warn!(target: "bcs_bridge_core::engine",
                "bot grants escalated unsandboxed execution via engine auto-review (cfuse auto_review); escalations may leave the declared sandbox without human approval");
        }
        tracing::warn!(target: "bcs_bridge_core::engine",
            sandbox = parsed.sandbox.wire(), network_access = parsed.network_access,
            approval_policy = parsed.approval_policy.wire(), approvals_reviewer = parsed.approval_review.wire(),
            reasoning_effort = parsed.reasoning_effort.unwrap_or("(engine default)"),
            "bot policy posture (codex engine)");
        Ok(parsed)
    }

    fn wire_thread_sandbox(self) -> &'static str { self.sandbox.wire() }
    fn wire_approval_policy(self) -> &'static str { self.approval_policy.wire() }
    fn wire_approvals_reviewer(self) -> &'static str { self.approval_review.wire() }
    fn wire_reasoning_effort(self) -> Option<&'static str> { self.reasoning_effort }
    fn wire_sandbox_policy(self) -> Value {
        match self.sandbox {
            // danger-full-access disables sandboxing entirely; network is inherent.
            CodexSandbox::DangerFullAccess => json!({"type": CodexSandbox::DangerFullAccess.turn_wire()}),
            CodexSandbox::ReadOnly => json!({"type": CodexSandbox::ReadOnly.turn_wire(), "networkAccess": self.network_access}),
            CodexSandbox::WorkspaceWrite => json!({"type": CodexSandbox::WorkspaceWrite.turn_wire(), "networkAccess": self.network_access}),
        }
    }
}

/// Codex app-server JSON-RPC driver.
///
/// Known limitation: threads default to `approvalPolicy: never` with no HITL
/// approval routing (app-server server requests are rejected rather than routed
/// to interactions), while the sandbox/network posture is now configurable per
/// bot via `bot.engine_options` (see [`CodexOptions`]; default
/// `workspace-write` + `network_access: true`), replacing the previous hardcoded
/// read-only/no-network lockdown.
pub struct CodexAppServer {
    bin: PathBuf,
    prefix_args: Vec<String>,
    desktop: bool,
    socket: Option<PathBuf>,
    options: CodexOptions,
    native: super::codex_persistent::NativeServer,
}

impl CodexAppServer {
    /// `prefix_args` are placed before `app-server`, for launchers that select
    /// Codex through a subcommand or flag.
    pub fn new(bin: PathBuf, prefix_args: Vec<String>) -> Self {
        Self::with_options(bin, prefix_args, CodexOptions::default())
    }

    fn with_options(bin: PathBuf, prefix_args: Vec<String>, options: CodexOptions) -> Self {
        Self { bin, prefix_args, desktop: false, socket: None, options, native: Default::default() }
    }

    /// Connect over Unix WebSocket; never start, restart or stop the shared server.
    pub fn desktop(socket: PathBuf) -> Self {
        Self { bin: PathBuf::new(), prefix_args: Vec::new(), desktop: true, socket: Some(socket),
            options: CodexOptions::default(), native: Default::default() }
    }

    pub(super) async fn initialize(&self, cli: &mut CodexConnection, next_id: &mut u64,
        backlog: &mut VecDeque<Value>, abort: &CancellationToken) -> Result<(), TurnError> {
        let initialization = rpc_call(cli, next_id, backlog, "initialize", json!({
            "clientInfo": { "name": "bridge-provider", "version": env!("CARGO_PKG_VERSION") },
            "capabilities": null,
        }), abort);
        let result = if self.desktop {
            tokio::time::timeout(std::time::Duration::from_secs(5), initialization).await
                .map_err(|_| TurnError::Protocol("Codex desktop detection timed out during initialization".into()))?
        } else { initialization.await }?;
        if self.desktop && result.get("userAgent").and_then(Value::as_str).is_none_or(str::is_empty) {
            return Err(TurnError::Protocol("Selected desktop endpoint did not return a Codex initialization response; no task was created".into()));
        }
        send_notification(cli, "initialized", Value::Null).await
    }

    fn launch_args(&self) -> Vec<String> {
        let mut args = self.prefix_args.clone();
        args.extend(["app-server".into(), "--listen".into(), "stdio://".into()]);
        args
    }

    /// Warn when Codex has no sandbox launcher for a sandboxed mode, on Linux:
    /// exec commands run through bubblewrap (`bwrap` on PATH or a bundled
    /// `codex-resources/bwrap` beside the engine binary). The engine itself
    /// reports the failure per command at turn time; this warning makes it
    /// visible at start instead. Other platforms use other launchers (e.g.
    /// macOS sandbox-exec) and need a different probe.
    #[cfg(target_os = "linux")]
    fn warn_sandbox_host(&self) {
        if let Some(parent) = self.bin.parent()
            && parent.join("codex-resources").join("bwrap").exists() { return; }
        if executable_on_path("bwrap") { return; }
        tracing::warn!(target: "bcs_bridge_core::engine",
            sandbox = self.options.sandbox.wire(), engine_bin = %self.bin.display(),
            "codex sandbox unavailable: bubblewrap is missing on this host; every sandboxed exec_command will panic at runtime. \
             Install bubblewrap (yum/apt install bubblewrap) or set engine_options {{ sandbox = 'danger-full-access' }} for a sandbox-less bot");
    }

    #[cfg(not(target_os = "linux"))]
    fn warn_sandbox_host(&self) {}

    async fn abort_turn(&self, cli: &mut CodexConnection, thread_id: &str, turn_id: &str) -> Result<(), TurnError> {
        if self.desktop {
            let mut id = 1_000_000;
            let mut backlog = VecDeque::new();
            let cancellation = CancellationToken::new();
            let interrupted = tokio::time::timeout(std::time::Duration::from_secs(5), rpc_call(
                cli, &mut id, &mut backlog, "turn/interrupt",
                json!({"threadId": thread_id, "turnId": turn_id}), &cancellation,
            )).await;
            cli.kill().await;
            interrupted.map_err(|_| TurnError::Protocol("Desktop turn interruption was not acknowledged; inspect the saved thread".into()))??;
            return Ok(());
        }
        cli.kill().await;
        Ok(())
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
        Ok(Arc::new(CodexAppServer::with_options(bin, self.prefix_args.clone(), CodexOptions::parse(options)?)))
    }
}

#[async_trait::async_trait]
impl Engine for CodexAppServer {
    async fn shutdown(&self) { self.native.shutdown().await; }

    async fn preflight(&self) -> Result<(), TurnError> {
        if !self.desktop {
            // The Codex Linux sandbox executes every exec_command through bwrap
            // (or the bundled codex-resources/bwrap next to the engine binary).
            // Without it every command panics at runtime — warn loudly here, in
            // lieu of failing business startup on hosts that cannot provide it.
            if self.options.sandbox != CodexSandbox::DangerFullAccess {
                self.warn_sandbox_host();
            }
            return Ok(());
        }
        let socket = self.socket.as_deref().ok_or_else(|| TurnError::Protocol("Missing desktop socket".into()))?;
        let mut connection = CodexConnection::connect(socket, None).await?;
        let result = self.initialize(&mut connection, &mut 1, &mut VecDeque::new(), &CancellationToken::new()).await;
        connection.kill().await;
        result
    }
    async fn run_turn(
        &self,
        req: TurnRequest,
        events: Sender<StreamEvent>,
        abort: CancellationToken,
    ) -> Result<TurnOutcome, TurnError> {
        if abort.is_cancelled() { return Err(TurnError::Aborted); }
        let (mut cli, loaded) = if self.desktop {
            let socket = self.socket.as_deref().ok_or_else(|| TurnError::Protocol("Missing desktop socket".into()))?;
            (CodexConnection::connect(socket, req.trace.clone()).await?, false)
        } else {
            self.native.connect(self, &self.bin, &self.launch_args(), &req, &abort).await?
        };

        let mut next_id = 1_u64;
        let mut backlog = VecDeque::new();

        if self.desktop {
            self.initialize(&mut cli, &mut next_id, &mut backlog, &abort)
                .await.map_err(|error| if !matches!(error, TurnError::Aborted) {
                    TurnError::EngineExited("Cannot initialize the existing Codex app-server; check the desktop Unix WebSocket listener. No standalone server was started".into())
                } else { error })?;
        }
        let thread = if loaded {
            json!({"thread": {"id": req.engine_session_id}})
        } else if let Some(session_id) = &req.engine_session_id {
            rpc_call(
                &mut cli,
                &mut next_id,
                &mut backlog,
                "thread/resume",
                thread_resume_params(&req, session_id, self.options),
                &abort,
            )
            .await?
        } else {
            rpc_call(
                &mut cli,
                &mut next_id,
                &mut backlog,
                "thread/start",
                thread_start_params(&req, self.options),
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

        // A shared server survives proxy disconnects. Obtain the turn id before
        // observing cancellation, so cancellation can target this exact turn.
        let start_abort = if self.desktop { CancellationToken::new() } else { abort.clone() };
        if abort.is_cancelled() { return Err(TurnError::Aborted); }
        let start = rpc_call(
            &mut cli,
            &mut next_id,
            &mut backlog,
            "turn/start",
            turn_start_params(&req, &thread_id, self.options),
            &start_abort,
        );
        let turn = if self.desktop {
            tokio::time::timeout(std::time::Duration::from_secs(30), start).await
                .map_err(|_| TurnError::Protocol("turn/start timed out; outcome may be unknown, inspect the saved Codex thread before retrying".into()))??
        } else { start.await? };
        let turn_id = extract_turn_id(&turn).ok_or_else(|| {
            TurnError::Protocol(format!("app-server turn response missing turn.id: {turn}"))
        })?;

        let mut deltas = String::new();
        let mut thinking = String::new();
        loop {
            let value = tokio::select! {
                _ = abort.cancelled() => {
                    self.abort_turn(&mut cli, &thread_id, &turn_id).await?;
                    return Err(TurnError::Aborted);
                }
                _ = events.closed() => {
                    self.abort_turn(&mut cli, &thread_id, &turn_id).await?;
                    return Err(TurnError::Aborted);
                }
                value = next_message(&mut cli, &mut backlog) => value?,
            };

            if is_server_request(&value) {
                respond_server_request(&mut cli, &value).await?;
                continue;
            }
            if !matches_turn(&value, &thread_id, &turn_id, self.desktop) {
                continue;
            }

            match value.get("method").and_then(Value::as_str).unwrap_or_default() {
                "item/agentMessage/delta" | "agent/output_chunk" => {
                    if let Some(delta) = extract_delta(&value) {
                        deltas.push_str(&delta);
                        if send_event(&events, sse::chat_delta(&req.run_id, &delta)).await {
                            self.abort_turn(&mut cli, &thread_id, &turn_id).await?;
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
                            self.abort_turn(&mut cli, &thread_id, &turn_id).await?;
                            return Err(TurnError::Aborted);
                        }
                    }
                }
                "item/started" => {
                    if let Some(event) = map_item_started(&value, &req.run_id, &req.cwd)
                        && send_event(&events, event).await {
                        self.abort_turn(&mut cli, &thread_id, &turn_id).await?;
                        return Err(TurnError::Aborted);
                    }
                }
                "item/completed" => {
                    if let Some(event) = map_item_completed(&value, &req.run_id, &req.cwd)
                        && send_event(&events, event).await {
                        self.abort_turn(&mut cli, &thread_id, &turn_id).await?;
                        return Err(TurnError::Aborted);
                    }
                }
                "item/commandExecution/outputDelta" | "item/fileChange/outputDelta" => {
                    if let Some(event) = map_tool_output_delta(&value, &req.run_id)
                        && send_event(&events, event).await {
                        self.abort_turn(&mut cli, &thread_id, &turn_id).await?;
                        return Err(TurnError::Aborted);
                    }
                }
                "item/mcpToolCall/progress" => {
                    if let Some(event) = map_mcp_progress(&value, &req.run_id)
                        && send_event(&events, event).await {
                        self.abort_turn(&mut cli, &thread_id, &turn_id).await?;
                        return Err(TurnError::Aborted);
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
                    cli.finish().await;
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
    cli: &mut CodexConnection,
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
    cli: &mut CodexConnection,
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

async fn send_line(cli: &mut CodexConnection, value: &Value) -> Result<(), TurnError> {
    let line = serde_json::to_string(value)
        .map_err(|error| TurnError::Protocol(format!("encode app-server request: {error}")))?;
    cli.write_line(&line).await.map_err(TurnError::Io)
}

async fn next_message(
    cli: &mut CodexConnection,
    backlog: &mut VecDeque<Value>,
) -> Result<Value, TurnError> {
    if let Some(value) = backlog.pop_front() {
        return Ok(value);
    }
    next_live_message(cli).await
}

async fn next_live_message(cli: &mut CodexConnection) -> Result<Value, TurnError> {
    let Some(line) = cli.next_line().await.map_err(TurnError::Io)? else {
        return Err(TurnError::EngineExited(
            "app-server stdout EOF before result".into(),
        ));
    };
    serde_json::from_str(&line)
        .map_err(|error| TurnError::Protocol(format!("parse app-server JSON: {error}")))
}

async fn respond_server_request(cli: &mut CodexConnection, request: &Value) -> Result<(), TurnError> {
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

fn with_reasoning_effort(mut params: Value, options: CodexOptions) -> Value {
    if let Some(effort) = options.wire_reasoning_effort() {
        params["effort"] = json!(effort);
    }
    params
}

fn thread_start_params(req: &TurnRequest, options: CodexOptions) -> Value {
    let mut params = json!({
        "cwd": req.cwd,
        "approvalPolicy": options.wire_approval_policy(),
        "approvalsReviewer": options.wire_approvals_reviewer(),
        "sandbox": options.wire_thread_sandbox(),
        "ephemeral": false,
        "threadSource": "bridge-provider",
    });
    if let Some(model) = req.model.as_deref().filter(|model| !model.is_empty()) {
        params["model"] = json!(model);
    }
    with_reasoning_effort(params, options)
}

fn thread_resume_params(req: &TurnRequest, thread_id: &str, options: CodexOptions) -> Value {
    let mut params = json!({
        "threadId": thread_id,
        "cwd": req.cwd,
        "approvalPolicy": options.wire_approval_policy(),
        "approvalsReviewer": options.wire_approvals_reviewer(),
        "sandbox": options.wire_thread_sandbox(),
    });
    if let Some(model) = req.model.as_deref().filter(|model| !model.is_empty()) {
        params["model"] = json!(model);
    }
    with_reasoning_effort(params, options)
}

fn turn_start_params(req: &TurnRequest, thread_id: &str, options: CodexOptions) -> Value {
    let mut params = json!({
        "threadId": thread_id,
        "clientUserMessageId": req.run_id,
        "input": [{
            "type": "text",
            "text": req.prompt,
            "text_elements": [],
        }],
        "cwd": req.cwd,
        "approvalPolicy": options.wire_approval_policy(),
        "approvalsReviewer": options.wire_approvals_reviewer(),
        "sandboxPolicy": options.wire_sandbox_policy(),
    });
    if let Some(model) = req.model.as_deref().filter(|model| !model.is_empty()) {
        params["model"] = json!(model);
    }
    with_reasoning_effort(params, options)
}

fn extract_thread_id(value: &Value) -> Option<String> {
    value
        .pointer("/thread/id")
        .and_then(Value::as_str)
        .or_else(|| value.get("threadId").and_then(Value::as_str))
        .map(str::to_string)
}

/// Locate an executable named `name` on PATH without launching it.
#[cfg(unix)]
fn executable_on_path(name: &str) -> bool {
    std::env::var_os("PATH").is_some_and(|paths| std::env::split_paths(&paths).any(|directory| {
        let candidate = directory.join(name);
        std::fs::metadata(&candidate).is_ok_and(|metadata| {
            use std::os::unix::fs::PermissionsExt;
            metadata.is_file() && metadata.permissions().mode() & 0o111 != 0
        })
    }))
}

fn extract_turn_id(value: &Value) -> Option<String> {
    value
        .pointer("/turn/id")
        .and_then(Value::as_str)
        .or_else(|| value.get("turnId").and_then(Value::as_str))
        .map(str::to_string)
}

pub(super) fn matches_turn(value: &Value, thread_id: &str, turn_id: &str, shared: bool) -> bool {
    let Some(params) = value.get("params") else {
        return false;
    };
    let flat = params.get("turnId");
    let nested = match value.get("method").and_then(Value::as_str) {
        Some("turn/completed" | "agent/turn_completed" | "agent/turn_failed") => params.pointer("/turn/id"),
        _ => None,
    };
    if shared {
        // A shared host can broadcast other tasks' notifications. Missing or
        // malformed ownership is never evidence that an event belongs to us.
        if params.get("threadId").and_then(Value::as_str) != Some(thread_id) {
            return false;
        }
        return (flat.is_some() || nested.is_some())
            && flat.into_iter().chain(nested).all(|id| id.as_str() == Some(turn_id));
    }
    let thread_matches = params
        .get("threadId")
        .and_then(Value::as_str)
        .is_none_or(|value| value == thread_id);
    // Dedicated stdio retains identifier-free legacy events, but every supplied
    // turn identifier must match, including completions buffered before the ACK.
    let turn_matches = flat.into_iter().chain(nested)
        .all(|id| id.as_str() == Some(turn_id));
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

/// Extract the final assistant text from a `turn/completed` notification.
///
/// Empirically verified contract (2026-10-18, 438 task_complete payloads
/// across cfuse codex 0.161 and vanilla codex rollouts on this host): the
/// engine always emits the answer under `params.last_agent_message`; the
/// legacy probe keys `text` / `finalText` / `final_text` never occur and are
/// deliberately not read. Turns that instead streamed `agentMessage/delta`
/// never reach this fallback.
fn completed_text(value: &Value) -> Option<String> {
    let params = value.get("params")?;
    params
        .get("last_agent_message")
        .and_then(Value::as_str)
        .filter(|text| !text.trim().is_empty())
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
        for shared in [false, true] {
            assert!(matches_turn(&value, "t-1", "turn-1", shared));
            assert!(!matches_turn(&value, "t-2", "turn-1", shared));
            assert!(!matches_turn(&value, "t-1", "turn-2", shared));
        }
    }

    #[test]
    fn shared_events_require_positive_ownership_but_cli_keeps_legacy_shapes() {
        for method in ["agent/output_chunk", "agent/turn_completed", "agent/turn_failed"] {
            let value = json!({"method":method,"params":{"text":"foreign"}});
            assert!(!matches_turn(&value, "t-1", "turn-1", true));
            assert!(matches_turn(&value, "t-1", "turn-1", false));
        }
        for params in [
            json!({"threadId":"t-1"}),
            json!({"turnId":"turn-1"}),
            json!({"threadId":"t-1","turnId":null}),
            json!({"threadId":null,"turnId":"turn-1"}),
        ] {
            assert!(!matches_turn(&json!({"params":params}), "t-1", "turn-1", true));
        }
    }

    #[test]
    fn completions_check_all_supplied_turn_ids_for_native_and_shared_connections() {
        for shared in [false, true] {
            for method in ["turn/completed", "agent/turn_completed", "agent/turn_failed"] {
                let mut value = json!({"method":method,"params":{
                    "threadId":"t-1","turn":{"id":"turn-1"}}});
                assert!(matches_turn(&value, "t-1", "turn-1", shared));
                assert!(!matches_turn(&value, "t-1", "turn-2", shared));
                value["params"]["turnId"] = json!("other-turn");
                assert!(!matches_turn(&value, "t-1", "turn-1", shared));
                assert!(!matches_turn(&value, "t-1", "other-turn", shared));
                value["params"]["turnId"] = Value::Null;
                assert!(!matches_turn(&value, "t-1", "turn-1", shared));
            }
        }
    }

    fn table(pairs: &[(&str, toml::Value)]) -> toml::Table {
        pairs.iter().cloned().map(|(k, v)| (k.to_owned(), v)).collect()
    }

    #[test]
    fn completed_text_reads_last_agent_message_from_turn_end() {
        let value = json!({"method":"turn/completed","params":{
            "threadId":"t-1","turnId":"turn-1",
            "last_agent_message":"review done"
        }});
        assert_eq!(completed_text(&value).as_deref(), Some("review done"));
        let legacy = json!({"method":"turn/completed","params":{
            "text":"legacy fallback never delivered by this engine",
            "finalText":"also unverified",
            "final_text":"ditto"
        }});
        assert_eq!(completed_text(&legacy), None,
            "speculative legacy keys must stay unread, matching the verified wire contract");
        let empty = json!({"method":"turn/completed","params":{
            "last_agent_message":"   "
        }});
        assert_eq!(completed_text(&empty), None, "whitespace-only tail text is not a real final");
    }

    #[test]
    fn codex_options_default_to_workspace_write_network_never() {
        let options = CodexOptions::parse(&toml::Table::new()).unwrap();
        assert_eq!(options.sandbox, CodexSandbox::WorkspaceWrite);
        assert!(options.network_access);
        assert_eq!(options.approval_policy, CodexApprovalPolicy::Never);
        assert_eq!(options.approval_review, CodexApprovalReview::User);
        assert_eq!(options.wire_thread_sandbox(), "workspace-write");
        assert_eq!(options.wire_approval_policy(), "never");
        assert_eq!(options.wire_approvals_reviewer(), "user");
        assert_eq!(options.wire_sandbox_policy(),
            json!({"type": "workspaceWrite", "networkAccess": true}));
    }

    #[test]
    fn codex_options_parse_each_supported_key_and_reject_others() {
        let options = CodexOptions::parse(&table(&[
            ("sandbox", "read-only".into()),
            ("network_access", false.into()),
            ("approval_policy", "on-request".into()),
            ("approval_review", "auto".into()),
        ])).unwrap();
        assert_eq!(options.sandbox, CodexSandbox::ReadOnly);
        assert!(!options.network_access);
        assert_eq!(options.approval_policy, CodexApprovalPolicy::OnRequest);
        assert_eq!(options.approval_review, CodexApprovalReview::Auto);
        assert_eq!(options.wire_approvals_reviewer(), "auto_review");
        assert_eq!(options.wire_sandbox_policy(),
            json!({"type": "readOnly", "networkAccess": false}));
        for invalid in [
            table(&[("bogus", "x".into())]),
            table(&[("sandbox", "bogus".into())]),
            table(&[("sandbox", 1.into())]),
            table(&[("network_access", "true".into())]),
            table(&[("approval_policy", "auto".into())]),
            table(&[("approval_review", "everyone".into())]),
            table(&[("reasoning_effort", "bogus".into())]),
            table(&[("reasoning_effort", 3.into())]),
        ] {
            assert!(CodexOptions::parse(&invalid).is_err(), "accepted {invalid:?}");
        }
    }

    #[test]
    fn codex_options_reasoning_effort_only_sends_when_set() {
        let options = CodexOptions::parse(&toml::Table::new()).unwrap();
        assert_eq!(options.reasoning_effort, None, "defaults leave the engine-level effort untouched");
        for (value, expected) in [
            ("minimal", "minimal"), ("low", "low"), ("medium", "medium"),
            ("high", "high"), ("xhigh", "xhigh"),
        ] {
            let parsed = CodexOptions::parse(&table(&[("reasoning_effort", value.into())])).unwrap();
            assert_eq!(parsed.reasoning_effort, Some(expected));
        }
        let parsed = CodexOptions::parse(&table(&[("reasoning_effort", " high ".into())])).unwrap();
        assert_eq!(parsed.reasoning_effort, Some("high"), "surrounding whitespace is trimmed");
    }

    #[test]
    fn codex_options_reject_dead_approval_combinations() {
        let auto_never = table(&[("approval_policy", "never".into()),
            ("approval_review", "auto".into())]);
        let error = CodexOptions::parse(&auto_never).unwrap_err();
        assert!(error.contains("approval_review='auto' requires") && error.contains("never"), "{error}");
        let user_asks = table(&[("approval_policy", "on-request".into()),
            ("approval_review", "user".into())]);
        let error = CodexOptions::parse(&user_asks).unwrap_err();
        assert!(error.contains("HITL") && error.contains("does not implement"), "{error}");
        // unset review on a non-never policy defaults to user and must fail the same way
        let unset_review = table(&[("approval_policy", "on-request".into())]);
        let error = CodexOptions::parse(&unset_review).unwrap_err();
        assert!(error.contains("HITL"), "{error}");
        assert!(CodexOptions::parse(&table(&[
            ("approval_policy", "on-request".into()), ("approval_review", "auto".into()),
        ])).is_ok());
    }

    #[test]
    fn codex_options_danger_full_access_wire_omits_network() {
        let options = CodexOptions::parse(&table(&[("sandbox", "danger-full-access".into())])).unwrap();
        assert_eq!(options.sandbox, CodexSandbox::DangerFullAccess);
        assert_eq!(options.wire_sandbox_policy(), json!({"type": "dangerFullAccess"}));
        assert!(CodexOptions::parse(&table(&[
            ("sandbox", "danger-full-access".into()), ("network_access", false.into()),
        ])).is_ok(), "explicit network_access normalizes with a warning");
    }

    fn options_req(cwd: &str) -> TurnRequest {
        struct TestObserver;
        #[async_trait::async_trait]
        impl crate::engine::SessionObserver for TestObserver {
            async fn established(&self, _: &str) -> Result<(), String> { Ok(()) }
        }
        TurnRequest::new("run-1", "prompt", cwd, Arc::new(TestObserver))
    }

    #[test]
    fn wire_params_follow_resolved_options_and_keep_bridge_identifiers() {
        let options = CodexOptions::parse(&toml::Table::new()).unwrap();
        let req = options_req("/tmp/x");
        let thread = thread_start_params(&req, options);
        assert_eq!(thread["sandbox"], "workspace-write");
        assert_eq!(thread["approvalPolicy"], "never");
        assert_eq!(thread["approvalsReviewer"], "user");
        assert_eq!(thread["threadSource"], "bridge-provider");
        assert_eq!(thread["ephemeral"], false);
        assert!(thread.get("model").is_none());
        let resume = thread_resume_params(&req, "t-1", options);
        assert_eq!(resume["sandbox"], "workspace-write");
        assert_eq!(resume["threadId"], "t-1");
        let turn = turn_start_params(&req, "t-1", options);
        assert_eq!(turn["sandboxPolicy"], json!({"type": "workspaceWrite", "networkAccess": true}));
        assert_eq!(turn["approvalPolicy"], "never");
        assert_eq!(turn["clientUserMessageId"], "run-1");
        let read_only = CodexOptions::parse(&table(&[("sandbox", "read-only".into()),
            ("network_access", false.into())])).unwrap();
        let turn = turn_start_params(&req, "t-1", read_only);
        assert_eq!(turn["sandboxPolicy"], json!({"type": "readOnly", "networkAccess": false}));
        let banned = CodexOptions::parse(&table(&[("sandbox", "danger-full-access".into())])).unwrap();
        assert_eq!(turn_start_params(&req, "t-1", banned)["sandboxPolicy"],
            json!({"type": "dangerFullAccess"}));
        assert_eq!(thread_start_params(&req, banned)["sandbox"], "danger-full-access");
    }

    #[test]
    fn wire_params_carry_reasoning_effort_only_when_configured() {
        let defaults = CodexOptions::parse(&toml::Table::new()).unwrap();
        let req = options_req("/tmp/x");
        for params in [thread_start_params(&req, defaults),
            thread_resume_params(&req, "t-1", defaults), turn_start_params(&req, "t-1", defaults)] {
            assert!(params.get("effort").is_none(), "no effort key without an override: {params}");
        }
        let with_effort = CodexOptions::parse(&table(&[
            ("reasoning_effort", "medium".into()),
        ])).unwrap();
        for params in [thread_start_params(&req, with_effort),
            thread_resume_params(&req, "t-1", with_effort), turn_start_params(&req, "t-1", with_effort)] {
            assert_eq!(params["effort"], "medium");
        }
    }
}
