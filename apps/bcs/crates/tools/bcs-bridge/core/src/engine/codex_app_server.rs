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
#[cfg(test)]
use std::sync::Arc;
use std::path::PathBuf;

use bcs_protocol::stream::StreamEvent;
use serde_json::{Value, json};
use tokio::sync::mpsc::Sender;
use futures::StreamExt;
use tokio_util::sync::CancellationToken;

use crate::engine::codex_transport::CodexConnection;
use crate::engine::{Engine, TurnError, TurnOutcome, TurnRequest};
use crate::sse;

#[path = "codex_tool_events.rs"]
mod tool_events;
use tool_events::{ToolEvents, map_tool_output_delta, map_mcp_progress};
#[path = "codex_questions.rs"]
mod questions;

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
/// `on-request` (bridge default) allows approval requests. `never` closes the
/// approval channel and denies operations that require approval.
///
/// Verified against a live cfuse codex 0.161 app-server (2026-10-18): the wire
/// enum accepts `never | on-request | untrusted | granular`; the legacy
/// `on-failure` value is rejected outright ("unknown variant"), so the bridge
/// whitelist never offers it. `granular` exists upstream but stays unexposed
/// until its per-command semantics are reviewed.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub enum CodexApprovalPolicy {
    Never,
    #[default]
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
/// - `user`: a human reviewer via BCS HITL (requires the codex HITL
///   wiring; not yet available in this driver).
/// - `auto` (default): cfuse `auto_review`, the engine's automatic reviewer,
///   which can approve or reject requests without human involvement.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub enum CodexApprovalReview {
    User,
    #[default]
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
/// Defaults: `workspace-write` sandbox, outbound network allowed,
/// `on-request` approvals with the engine automatic reviewer, and no reasoning
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
            approval_policy: CodexApprovalPolicy::OnRequest, approval_review: CodexApprovalReview::Auto,
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

    pub(super) fn parse(options: &toml::Table) -> Result<Self, String> {
        let mut parsed = Self::default();
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
        // Preserve explicit never-only configurations: no reviewer is needed.
        if !parsed.approval_policy.asks_approvals() && !options.contains_key(Self::KEY_APPROVAL_REVIEW) {
            parsed.approval_review = CodexApprovalReview::User;
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
                "bot uses engine automatic approval review (auto_review); the reviewer may approve or reject escalation requests");
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
/// User questions route to BCN interactions. Human execution approval requests
/// remain unsupported; the default automatic reviewer handles execution approvals.
/// The sandbox/network posture is configurable per
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
        Self::with_options(bin, prefix_args, CodexOptions::default(), Default::default())
    }

    pub(super) fn with_options(bin: PathBuf, prefix_args: Vec<String>, options: CodexOptions,
        transport: super::codex_owned::NativeTransport) -> Self {
        Self { bin, prefix_args, desktop: false, socket: None, options,
            native: super::codex_persistent::NativeServer::new(transport) }
    }

    /// Connect over Unix WebSocket; never start, restart or stop the shared server.
    /// Desktop threads are explicitly pinned to the engine's historical
    /// bridge-side posture — read-only sandbox, no network, approvalPolicy
    /// never, user reviewer — regardless of the standalone Codex engine's
    /// own configured defaults. This keeps codex-desktop author-managed:
    /// the desktop user drives their own app-server instance; this bridge
    /// must not widen it as a side effect of newer standalone options.
    pub fn desktop(socket: PathBuf) -> Self {
        Self { bin: PathBuf::new(), prefix_args: Vec::new(), desktop: true, socket: Some(socket),
            options: CodexOptions {
                sandbox: CodexSandbox::ReadOnly,
                network_access: false,
                approval_policy: CodexApprovalPolicy::Never,
                approval_review: CodexApprovalReview::User,
                reasoning_effort: None,
            },
            native: Default::default() }
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

    pub(super) fn launch_args(&self, listen: &str) -> Vec<String> {
        let mut args = self.prefix_args.clone();
        args.extend(["app-server".into(), "--listen".into(), listen.into()]);
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

pub use super::codex_factory::CodexAppServerFactory;

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
            self.native.connect(self, &self.bin, &req, &abort).await?
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
        } else {
            match start.await {
                Ok(turn) => turn,
                Err(error) => {
                    // Cancellation before the start ACK still owns a pending turn.
                    // Wait for its interruption or server shutdown before returning.
                    cli.kill().await;
                    return Err(error);
                }
            }
        };
        let mut turn_id = extract_turn_id(&turn).ok_or_else(|| {
            TurnError::Protocol(format!("app-server turn response missing turn.id: {turn}"))
        })?;

        let mut deltas = String::new();
        let mut tools = ToolEvents::default();
        let mut questions = questions::Questions::new(&req, events.clone());
        // Same-dumpgarnered thinking snapshots were previously concatenated
        // per delta into `thinking` and re-sent inside every event; that
        // made retention quadratic (N² copies of the same tail). Emit only
        // the fresh fragment (mirrors the Claude driver), retaining linear
        // thinking history.
        loop {
            let value = if let Some(completed) = questions.ready_completion() {
                if let Some(prompt) = questions.take_followup() {
                    let mut params = turn_start_params(&req, &thread_id, self.options);
                    params["input"] = json!([{"type":"text", "text":prompt}]);
                    let turn = rpc_call(&mut cli, &mut next_id, &mut backlog, "turn/start", params, &abort).await?;
                    turn_id = extract_turn_id(&turn).ok_or_else(|| TurnError::Protocol("answer turn lacks id".into()))?;
                    deltas.clear();
                    tools = ToolEvents::default();
                    continue;
                }
                completed
            } else { tokio::select! {
                _ = abort.cancelled() => {
                    self.abort_turn(&mut cli, &thread_id, &turn_id).await?;
                    return Err(TurnError::Aborted);
                }
                _ = events.closed() => {
                    self.abort_turn(&mut cli, &thread_id, &turn_id).await?;
                    return Err(TurnError::Aborted);
                }
                Some((iid, resolution)) = questions.waits.next(), if !questions.waits.is_empty() => {
                    questions.answer(&mut cli, &iid, resolution).await?;
                    continue;
                }
                value = next_message(&mut cli, &mut backlog) => value?,
            }};

            if is_server_request(&value) {
                if value["method"] == "item/tool/requestUserInput"
                    && matches_turn(&value, &thread_id, &turn_id, true) {
                    questions.request(&mut cli, &value).await?;
                } else {
                    respond_server_request(&mut cli, &value).await?;
                }
                continue;
            }
            if value["method"] == "serverRequest/resolved"
                && value["params"]["threadId"] == thread_id {
                questions.engine_resolved(&value["params"]["requestId"]);
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
                    if let Some(delta) = extract_delta(&value)
                        && send_event(&events, sse::agent_thinking(&req.run_id, Some(delta), None)).await
                    {
                        self.abort_turn(&mut cli, &thread_id, &turn_id).await?;
                        return Err(TurnError::Aborted);
                    }
                }
                "item/started" => {
                    if let Some(event) = tools.started(&value, &req.run_id, &req.cwd)
                        && send_event(&events, event).await {
                        self.abort_turn(&mut cli, &thread_id, &turn_id).await?;
                        return Err(TurnError::Aborted);
                    }
                }
                "item/completed" => {
                    if let Some(item) = value.pointer("/params/item") { questions.observe_item(item).await?; }
                    if let Some(event) = tools.completed(&value, &req.run_id, &req.cwd)
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
                    // Some app-server tools only expose completion in the final snapshot.
                    for event in tools.finish(&value, &req.run_id, &req.cwd) {
                        if send_event(&events, event).await {
                            return Err(TurnError::Aborted);
                        }
                    }
                    questions.observe_completion(&value).await?;
                    if questions.has_async_work() {
                        questions.completion = Some(value);
                        continue;
                    }
                    // The completed turn's own structured items are the
                    // authoritative final message — a preceding streamed
                    // `agentMessage/delta` may only be progress commentary
                    // (e.g. "Checking the PR...") or a partial prefix of the
                    // real answer. Prefer the authoritative snapshot; fall
                    // back to accumulated deltas only when the snapshot
                    // carries no answer (e.g. legacy engines that stream
                    // everything).
                    let final_text = completed_text(&value).unwrap_or(deltas);
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
        if !is_server_request(&value) && value.get("id").and_then(Value::as_u64) == Some(id) {
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
#[cfg(target_os = "linux")]
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
/// Wire contract (verified live from bridge-side engine trace, 2026-10-18):
/// the completed turn arrives as 
/// `{threadId, turn:{items:[...]}}` with no `last_agent_message`, and the
/// final answer lives in `params.turn.items[]` where `type == "agentMessage"`
/// (typically `phase: "final_answer"`). Every turn that streams its answer
/// still takes the earlier delta branch first — this path only matters when
/// the engine delivers the answer as one whole message at turn end without
/// streaming deltas. The engine journal uses a `last_agent_message` field in
/// its own rollout files, so we also honor it as a legacy fallback, ordered
/// after the wire-native shape.
fn completed_text(value: &Value) -> Option<String> {
    let params = value.get("params")?;
    let mut fallback: Option<String> = None;
    if let Some(items) = params.pointer("/turn/items").and_then(Value::as_array) {
        for item in items.iter().rev() {
            if item.get("type").and_then(Value::as_str) != Some("agentMessage") { continue; }
            let Some(text) = item.get("text").and_then(Value::as_str).filter(|t| !t.trim().is_empty()) else { continue; };
            if item.get("phase").and_then(Value::as_str) == Some("final_answer") {
                return Some(text.to_owned());
            }
            if fallback.is_none() { fallback = Some(text.to_owned()); }
        }
    }
    fallback.or_else(|| {
        params.get("last_agent_message")
            .and_then(Value::as_str)
            .filter(|text| !text.trim().is_empty())
            .map(str::to_string)
    })
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
#[path = "codex_app_server_tests.rs"]
mod tests;
