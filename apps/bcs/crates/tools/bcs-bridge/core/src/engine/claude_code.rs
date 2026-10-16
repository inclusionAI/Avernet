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
//! 启动后立刻向 stdin 写一条 user 消息（claude stream-json 输入格式）。
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

use std::{collections::{HashMap, HashSet, VecDeque}, path::PathBuf, sync::Arc};

use bcs_protocol::stream::{AgentData, InteractionKind, InteractionPhase, StreamEvent, ToolData, ToolPhase};
use serde_json::{json, Value};

use crate::engine::cli::CliSession;
use crate::engine::{
    is_valid_engine_session_id, Engine, SessionObserver, TurnError, TurnOutcome, TurnRequest,
};
use crate::sse;

/// Claude Code (stream-json) 引擎驱动。
pub struct ClaudeCode {
    bin: PathBuf,
    prefix_args: Vec<String>,
}

impl ClaudeCode {
    /// `prefix_args` are placed before the stream-json arguments, for
    /// launchers that select Claude Code through a subcommand or flag.
    pub fn new(bin: PathBuf, prefix_args: Vec<String>) -> Self {
        Self { bin, prefix_args }
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
    fn id(&self) -> &str { &self.id }
    fn default_bin(&self) -> &str { &self.default_bin }
    fn build(&self, bin: PathBuf, options: &toml::Table) -> Result<Arc<dyn Engine>, String> {
        crate::engine::reject_engine_options(options)?;
        Ok(Arc::new(ClaudeCode::new(bin, self.prefix_args.clone())))
    }
}

/// 单行 claude `stream-json` NDJSON 的映射结果。
///
/// `SessionId` 携带 `system/init` 的引擎内 session id；
/// `Final` 携带成功 `result` 的最终助手文本；
/// `Failed` 携带非成功 `result` 或无效权限请求的经净化退出原因（调用方上抛为
/// `TurnError::EngineExited`）；`Events` 是该行产出的 [`StreamEvent`]；
/// `Ignore` 标记“JSON 合法但未识别”；`Malformed` 标记“非法 JSON”；
/// `ControlRequest` 携带 `can_use_tool` 的 request_id/tool_name/input，由
/// `run_turn` 接线为 HITL 交互（Task 12）；
/// `BackgroundStarted`/`BackgroundDelivered` 携带后台任务生命周期（启动/投递）
/// （`system/task_started` / `system/task_notification`）的工具调用 id
/// （及通知的 status），由 `run_turn` 维护 pending 后台任务集合——
/// `result` 仅在该集合为空时才算 turn 终态：CLI 会在每个后台任务完成后
/// 自动唤醒主 agent 并再发一个 `result`。
#[derive(Debug)]
pub(crate) enum CcMap {
    Events(Vec<StreamEvent>),
    SessionId(String),
    Final(String),
    Failed(String),
    Ignore,
    Malformed,
    /// Engine permission request (`can_use_tool`). Fields surfaced to the driver
    /// for the interaction roundtrip; `request_id` is engine-native and stays
    /// inside the driver (never sent to BCS as an interactionId).
    ControlRequest {
        request_id: String,
        tool_name: String,
        input: Value,
    },
    /// A background task was launched (`system/task_started`), keyed by the
    /// tool call that launched it; `task_id` (when present) lets a later
    /// notification that omits `tool_use_id` still resolve this call.
    BackgroundStarted { task_id: Option<String>, tool_use_id: String },
    /// A terminal `system/task_notification` — enqueue time, NOT delivery:
    /// the task stopped, and the CLI has queued the notification it will
    /// hand the model later. Recorded so the wake that delivers it can
    /// release exactly one wait; see `CcTurnMapper::enqueued`.
    BackgroundEnqueued { task_id: Option<String>, tool_use_id: Option<String>, status: String },
    /// A background-task completion **delivered to the model** — a user-type
    /// line whose plain-string content is the `<task-notification>` payload
    /// the CLI injects when it wakes the agent. Only a delivered notification
    /// releases the wait: the enqueue-side `system/task_notification` line
    /// fires when the task finishes, but the CLI defers delivery while the
    /// model is mid-round, so a `result` between enqueue and delivery would
    /// otherwise cut the turn short with undelivered results still queued
    /// (observed in production: two finders' notifications were enqueued
    /// mid-round, the model's "still waiting" round-end read as final, and
    /// the queued deliveries died with the reaped process tree).
    /// `tool_use_id` is absent in some CLI versions — `task_id` correlation
    /// from `task_started` covers them.
    BackgroundDelivered { task_id: Option<String>, tool_use_id: Option<String>, status: String },
}

/// Claude tool results carry only an ID. Keep their complete start names within
/// one run (including parallel calls), never in shared engine/session state.
struct CcTurnMapper<'a> {
    run_id: &'a str,
    // None marks an ambiguous or missing name; don't guess a coordination tool.
    pending_tools: HashMap<String, Option<String>>,
    /// Tool call ids that launched async background work this turn. A `result`
    /// line only finalizes the turn once this is empty — and entries are
    /// released only by DELIVERED notifications (the `<task-notification>`
    /// user message the CLI injects when it wakes the agent), not by the
    /// enqueue-time `system/task_notification` line: the CLI queues
    /// notifications that complete mid-round, so a result can land while
    /// completed-but-undelivered work is still queued behind the model's
    /// current round.
    background: HashSet<String>,
    /// `task_id -> tool_use_id` from `task_started` lines; lets a
    /// `task_notification` that omits `tool_use_id` (recorded in some CLI
    /// versions) still release the right wait.
    task_ids: HashMap<String, String>,
    /// Terminal notifications queued inside the CLI, awaiting their
    /// auto-wake. Engines differ in how a completion reaches the model:
    /// some surface the delivery itself as a user `<task-notification>`
    /// line (handled by [`CcMap::BackgroundDelivered`]); others stay
    /// silent on stdout and instead begin each auto-wake round with a
    /// `system/init` line — and hand the model exactly ONE queued
    /// notification per wake. This queue mirrors the CLI's, so each wake
    /// releases exactly one wait whoever fronts it: the release gate is
    /// the pending COUNT, and an identity swap between popped and
    /// actually-delivered entries is invisible to it. A delivered user
    /// line cancels its alias here so the two signals can never
    /// double-release one wait.
    enqueued: VecDeque<CcQueuedNotice>,
}

/// One queued terminal notification awaiting delivery, as recorded from
/// the enqueue-time `system/task_notification` line.
struct CcQueuedNotice {
    task_id: Option<String>,
    tool_use_id: Option<String>,
}

impl<'a> CcTurnMapper<'a> {
    fn new(run_id: &'a str) -> Self {
        Self {
            run_id,
            pending_tools: HashMap::new(),
            background: HashSet::new(),
            task_ids: HashMap::new(),
            enqueued: VecDeque::new(),
        }
    }

    /// Number of background tasks still pending completion.
    fn background_pending(&self) -> usize {
        self.background.len()
    }

    /// An auto-wake round opened (`system/init`): the CLI hands the model
    /// exactly ONE queued notification per wake. Pop the queue front and
    /// release the wait it resolves. The gate is the pending COUNT — if the
    /// popped entry and the actually-delivered notification trade places
    /// (queue order is an arrival-time assumption), the swap is invisible:
    /// one wake, one release, however the names line up. An empty queue
    /// (startup round, a wake for nothing queued yet) releases nothing; a
    /// popped entry this run can't attribute (unknown ids) is consumed
    /// anyway — the CLI spent this wake, and its slot must not survive to
    /// steal a later release.
    fn consume_wake(&mut self) {
        let Some(notice) = self.enqueued.pop_front() else { return };
        let _ = self.finish_background(
            notice.task_id.as_deref(),
            notice.tool_use_id.as_deref(),
            "completed",
        );
    }

    /// Remove the first queue entry matching a delivered notification. Each key
    /// is compared only when the caller actually supplies it: a `None` argument
    /// matches nothing, so a tool-result delivery (which carries only a
    /// tool_use_id) cannot remove another task's notice merely because that
    /// notice also lacks a task_id. The delivery already released its wait
    /// directly, and the wake that ends up carrying its alias must not empty-pop
    /// a second consumer out of a pending set that has already drained.
    fn cancel_enqueued_alias(&mut self, task_id: Option<&str>, tool_use_id: Option<&str>) {
        if let Some(i) = self.enqueued.iter().position(|notice| {
            (tool_use_id.is_some() && notice.tool_use_id.as_deref() == tool_use_id)
                || (task_id.is_some() && notice.task_id.as_deref() == task_id)
        }) {
            self.enqueued.remove(i);
        }
    }

    /// Track a background launch. Only called on evidence the work actually
    /// started (`task_started`, or the async-launch receipt): a
    /// `run_in_background: true` request that is denied or fails validation
    /// never emits either, and eagerly registering it would wedge the turn
    /// on work that will never notify.
    fn register_background(&mut self, tool_use_id: &str, task_id: Option<&str>) {
        if !tool_use_id.trim().is_empty() {
            self.background.insert(tool_use_id.to_owned());
            if let Some(task_id) = non_empty(task_id) {
                self.task_ids.insert(task_id.to_owned(), tool_use_id.to_owned());
            }
        }
    }

    /// Release a background wait on a terminal `task_notification` status;
    /// `running`/`pending` progress reports must keep the wait. Resolves the
    /// waiting call by `tool_use_id`, falling back to the `task_id`
    /// correlation for notifications that omit it.
    /// Returns true when a pending wait was found and released; false for
    /// unknown ids (a stale delivery the caller should mark downstream).
    fn finish_background(&mut self, task_id: Option<&str>, tool_use_id: Option<&str>, status: &str) -> bool {
        if !terminal_task_status(status) {
            return false;
        }
        let released = tool_use_id
            .map(str::to_owned)
            .or_else(|| task_id.and_then(|id| self.task_ids.get(id).cloned()));
        match released.filter(|id| !id.trim().is_empty()) {
            Some(released) => self.background.remove(&released),
            None => false,
        }
    }

    fn map_line(&mut self, line: &str) -> CcMap {
        let mapped = map_cc_line(line, self.run_id);
        match mapped {
            // Background lifecycle lines are consumed into tracking state here;
            // run_turn only reads background_pending() to gate the wait.
            CcMap::BackgroundStarted { task_id, tool_use_id } => {
                self.register_background(&tool_use_id, task_id.as_deref());
                CcMap::Ignore
            }
            CcMap::BackgroundEnqueued { task_id, tool_use_id, .. } => {
                self.enqueued.push_back(CcQueuedNotice { task_id, tool_use_id });
                CcMap::Ignore
            }
            CcMap::BackgroundDelivered { task_id, tool_use_id, status } => {
                let consumed = self.finish_background(task_id.as_deref(), tool_use_id.as_deref(), &status);
                // Either signal drains the same delivery: cancel the queued
                // alias so a later wake cannot pop the spent slot and strand
                // the notification that wake actually delivered.
                self.cancel_enqueued_alias(task_id.as_deref(), tool_use_id.as_deref());
                if !consumed {
                    // This delivery targets a task this run never launched:
                    // the CLI is replaying a notification undelivered by a
                    // previous (killed) turn. Forward it downstream with a
                    // visible marker so BCS consumers can tell a recycled
                    // result from a fresh one.
                    let text = format!(
                        "<task-notification>{}{}",
                        task_id.map(|t| format!(" task_id={t}")).unwrap_or_default(),
                        STALE_DELIVERY_MARKER,
                    );
                    return CcMap::Events(vec![sse::chat_delta(self.run_id, &text)]);
                }
                CcMap::Ignore
            }
            // A wake round opens with system/init: release one queued wait before
            // the id passes through for session persistence.
            CcMap::SessionId(s) => {
                self.consume_wake();
                CcMap::SessionId(s)
            }
            CcMap::Events(mut events) => {
                // Belt-and-braces launch coverage for engines that omit the
                // task lifecycle lines: the async-launch receipt is a
                // whole-result prefix from a launch-capable tool (matching it
                // in arbitrary tool output — e.g. reading this source — would
                // phantom-register a wait that never notifies).
                for event in &mut events {
                    let StreamEvent::Agent(agent) = event else { continue; };
                    let AgentData::Tool(tool) = &mut agent.data else { continue; };
                    let Some(id) = tool.tool_call_id.as_deref().filter(|id| !id.trim().is_empty()).map(str::to_owned) else {
                        continue;
                    };
                    match tool.phase {
                        ToolPhase::Start => {
                            let name = tool.name.clone().filter(|name| !name.trim().is_empty());
                            self.pending_tools.entry(id)
                                .and_modify(|existing| {
                                    if existing != &name {
                                        *existing = None;
                                    }
                                })
                                .or_insert(name);
                        }
                        ToolPhase::Result => {
                            let name = self.pending_tools.remove(&id).flatten();
                            if matches!(name.as_deref(), Some("Bash") | Some("Task") | Some("Agent"))
                                && async_launch_receipt(&tool.result)
                            {
                                // Old protocol: this result is the launch RECEIPT
                                // ("Command running in background …"); the task is
                                // still running, so register the wait.
                                self.register_background(&id, None);
                            } else if self.background.contains(&id) {
                                // Claude Code 2.1.199 auto-backgrounds a long
                                // foreground call and delivers its real output as
                                // an ordinary tool_result carrying the SAME id —
                                // never a `<task-notification>` line or a wake.
                                // That tool_result IS the delivery, so release the
                                // wait here (and cancel the enqueue-time alias so a
                                // later wake cannot pop a spent slot and drain a
                                // different task's still-pending wait). Without
                                // this the run's `result` is swallowed forever and
                                // the turn hangs until the idle watchdog.
                                self.finish_background(None, Some(&id), "completed");
                                self.cancel_enqueued_alias(None, Some(&id));
                            }
                            // Consume once: an orphan/repeated result must not inherit
                            // a different call's name or derive it from result content.
                            tool.name = name;
                        }
                        _ => {}
                    }
                }
                CcMap::Events(events)
            }
            other => other,
        }
    }
}

/// Marker appended to a delivered `<task-notification>` when its tool call id
/// does not belong to this run — i.e. the CLI is replaying a notification
/// that a previous (killed) run left undelivered in the session queue. The
/// marker makes the replay visible to BCS consumers and downstream viewers
/// so a recycled result doesn't read as a fresh response.
const STALE_DELIVERY_MARKER: &str =
    "\n--------- replayed from a previous run (background task completed before this turn started) -------";

/// Whether a tool result is the async-launch receipt of a background task
/// (Agent/Task: "Async agent launched successfully"; background Bash:
/// "Command running in background") rather than a completed tool call.
fn async_launch_receipt(result: &Option<Value>) -> bool {
    let Some(result) = result else { return false };
    match result {
        Value::String(text) => receipt_text(text),
        // Block-array content carries receipts as single text blocks.
        Value::Array(blocks) => blocks.iter().any(|b| {
            b.get("text").and_then(Value::as_str).map(receipt_text).unwrap_or(false)
        }),
        _ => false,
    }
}

fn receipt_text(text: &str) -> bool {
    // Receipts ARE the whole result text; a mere mention inside other output
    // (this source, a log line) must not read as a launch.
    let text = text.trim();
    text.starts_with("Async agent launched successfully")
        || text.starts_with("Command running in background with ID:")
}

/// 把一行 claude `stream-json` NDJSON 映射为引擎中立的 [`CcMap`]。
///
/// 纯函数（无 IO），便于用录制 fixture 做单元测试；按上方映射表逐类分派。
pub(crate) fn map_cc_line(line: &str, run_id: &str) -> CcMap {
    let value: Value = match serde_json::from_str(line) {
        Ok(v) => v,
        Err(_) => return CcMap::Malformed,
    };
    let ty = match value.get("type").and_then(|v| v.as_str()) {
        Some(t) => t,
        None => return CcMap::Ignore,
    };
    match ty {
        "system" => map_system(&value),
        "stream_event" => map_stream_event(&value, run_id),
        "assistant" => map_assistant(&value, run_id),
        "user" => map_user(&value, run_id),
        "result" => map_result(&value),
        "control_request" => map_control_request(&value, run_id),
        _ => CcMap::Ignore,
    }
}

fn map_system(v: &Value) -> CcMap {
    let subtype = v.get("subtype").and_then(|x| x.as_str()).unwrap_or("");
    match subtype {
        "init" => match v.get("session_id").and_then(|x| x.as_str()) {
            Some(s) if !s.is_empty() => CcMap::SessionId(s.to_string()),
            _ => CcMap::Ignore,
        },
        // Background-task lifecycle (recorded from a live capture).
        "task_started" => {
            let tool_use_id = non_empty(v.get("tool_use_id").and_then(|x| x.as_str()));
            match tool_use_id {
                Some(id) => CcMap::BackgroundStarted {
                    task_id: non_empty(v.get("task_id").and_then(|x| x.as_str())).map(str::to_string),
                    tool_use_id: id.to_string(),
                },
                None => CcMap::Ignore,
            }
        }
        // `task_notification` fires when the task finishes — enqueue time,
        // NOT delivery time. The CLI queues the notification and delivers it
        // to the model later: some CLI versions surface that delivery as a
        // user `<task-notification>` line (map_user parses it); others hand
        // the model exactly one queued notification per auto-wake round,
        // opened by a new `system/init` line. Both signals gate the same
        // release, so the enqueue is recorded — never a release on its own
        // (deliveries can land while the model is mid-round; a result then
        // must stay intermediate or completed-but-undelivered work dies
        // with the turn).
        "task_notification" => {
            let status = v.get("status").and_then(|x| x.as_str()).unwrap_or("");
            if !terminal_task_status(status) {
                return CcMap::Ignore;
            }
            // Queue entries mirror the CLI's own queue membership — unknown
            // ids included: the slot still occupies a wake that must not pop
            // a legitimate wait.
            CcMap::BackgroundEnqueued {
                task_id: non_empty(v.get("task_id").and_then(|x| x.as_str())).map(str::to_string),
                tool_use_id: non_empty(v.get("tool_use_id").and_then(|x| x.as_str())).map(str::to_string),
                status: status.to_string(),
            }
        }
        _ => CcMap::Ignore,
    }
}

fn non_empty(s: Option<&str>) -> Option<&str> {
    s.filter(|s| !s.trim().is_empty())
}

/// Whether a task status is terminal: only terminal statuses release a
/// background wait. `running`/`pending` progress reports must keep it.
fn terminal_task_status(status: &str) -> bool {
    matches!(status, "completed" | "failed" | "stopped" | "killed")
}

fn map_stream_event(v: &Value, run_id: &str) -> CcMap {
    let event = match v.get("event") {
        Some(e) => e,
        None => return CcMap::Ignore,
    };
    let etype = event.get("type").and_then(|x| x.as_str()).unwrap_or("");
    if etype != "content_block_delta" {
        return CcMap::Ignore;
    }
    let delta = match event.get("delta") {
        Some(d) => d,
        None => return CcMap::Ignore,
    };
    let dtype = delta.get("type").and_then(|x| x.as_str()).unwrap_or("");
    match dtype {
        "text_delta" => match delta.get("text").and_then(|x| x.as_str()) {
            Some(text) => CcMap::Events(vec![sse::chat_delta(run_id, text)]),
            None => CcMap::Ignore,
        },
        "thinking_delta" => match delta.get("thinking").and_then(|x| x.as_str()) {
            Some(text) if !text.is_empty() => CcMap::Events(vec![sse::agent_thinking(
                run_id,
                Some(text.to_string()),
                None,
            )]),
            _ => CcMap::Ignore,
        },
        _ => CcMap::Ignore,
    }
}

fn map_assistant(v: &Value, run_id: &str) -> CcMap {
    let content = match v
        .get("message")
        .and_then(|m| m.get("content"))
        .and_then(|c| c.as_array())
    {
        Some(arr) => arr,
        None => return CcMap::Ignore,
    };
    let mut events = Vec::new();
    for item in content {
        let itype = item.get("type").and_then(|x| x.as_str()).unwrap_or("");
        if itype != "tool_use" {
            continue;
        }
        let id = item.get("id").and_then(|x| x.as_str()).unwrap_or("");
        let name = item.get("name").and_then(|x| x.as_str()).unwrap_or("");
        let input = item.get("input").cloned().unwrap_or(Value::Null);
        events.push(sse::agent_tool(
            run_id,
            ToolData {
                phase: ToolPhase::Start,
                name: Some(name.to_string()),
                tool_call_id: Some(id.to_string()),
                is_error: None,
                exit_code: None,
                duration_ms: None,
                cwd: None,
                args: Some(input),
                result: None,
                partial_result: None,
            },
        ));
    }
    if events.is_empty() {
        CcMap::Ignore
    } else {
        CcMap::Events(events)
    }
}

/// Whether a tool result's `content` (plain string or block array) starts with
/// our answered-form signature, marking it as a denial carrying answers
/// rather than a genuine tool failure.
fn tool_result_carries_mark(content: &Value) -> bool {
    match content {
        Value::String(text) => text.starts_with(ANSWERED_ASK_USER_MARK),
        Value::Array(blocks) => blocks.iter().any(|block| {
            block.get("text").and_then(Value::as_str).is_some_and(|text| text.starts_with(ANSWERED_ASK_USER_MARK))
        }),
        _ => false,
    }
}

/// Parse a delivered `<task-notification>` user message: `(task_id,
/// tool_use_id, status)` when the whole plain-string content is the
/// notification payload. `tool_use_id` is omitted by some CLI versions — the
/// caller resolves those through the `task_id` correlation `task_started`
/// recorded. A missing `<status>` cannot release the wait (the release rule
/// is terminal-status-driven), so such payloads read as not-a-notification.
fn delivered_notification(content: &str) -> Option<(Option<&str>, Option<&str>, String)> {
    let text = content.trim();
    if !text.starts_with("<task-notification>") {
        return None;
    }
    let status = xml_field(text, "status")?;
    Some((
        xml_field(text, "task-id"),
        xml_field(text, "tool-use-id"),
        status.to_string(),
    ))
}

/// Extract `<tag>…</tag>` inner text; None when the tag is absent or empty.
fn xml_field<'a>(text: &'a str, tag: &str) -> Option<&'a str> {
    let open = format!("<{tag}>");
    let close = format!("</{tag}>");
    let start = text.find(&open)? + open.len();
    let end = text[start..].find(&close)? + start;
    non_empty(Some(&text[start..end]))
}

fn map_user(v: &Value, run_id: &str) -> CcMap {
    // A delivered background-task notification arrives as PLAIN-STRING
    // content (the `<task-notification>` payload the CLI injects to wake the
    // agent). String-only is load-bearing: tool results ride in block
    // arrays, so a tool result that merely echoes notification text — a CR
    // reading this source, a grep of a task output file — can never
    // impersonate a delivery.
    if let Some(content) = v.get("message").and_then(|m| m.get("content")).and_then(|c| c.as_str()) {
        if let Some((task_id, tool_use_id, status)) = delivered_notification(content) {
            return CcMap::BackgroundDelivered {
                task_id: task_id.map(str::to_string),
                tool_use_id: tool_use_id.map(str::to_string),
                status,
            };
        }
        return CcMap::Ignore;
    }
    let content = match v
        .get("message")
        .and_then(|m| m.get("content"))
        .and_then(|c| c.as_array())
    {
        Some(arr) => arr,
        None => return CcMap::Ignore,
    };
    let mut events = Vec::new();
    for item in content {
        let itype = item.get("type").and_then(|x| x.as_str()).unwrap_or("");
        if itype != "tool_result" {
            continue;
        }
        let tool_use_id = item.get("tool_use_id").and_then(|x| x.as_str()).unwrap_or("");
        let content_val = item.get("content").cloned().unwrap_or(Value::Null);
        // Claude's optional is_error is omitted on success. Preserve explicit
        // failures; invalid flag types remain unknown, not successful.
        // https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls
        let mut is_error = match item.get("is_error") {
            None => Some(false),
            Some(value) => value.as_bool(),
        };
        // A submitted ask_user form rides back as a denied tool execution
        // (the only message-carrying permission response). The CLI flags that
        // result as an error; lift the flag for exactly our own answer text
        // so downstream consumers see a normal answered tool row.
        if is_error == Some(true) && tool_result_carries_mark(&content_val) {
            is_error = Some(false);
        }
        events.push(sse::agent_tool(
            run_id,
            ToolData {
                phase: ToolPhase::Result,
                name: None,
                tool_call_id: Some(tool_use_id.to_string()),
                is_error,
                exit_code: None,
                duration_ms: None,
                cwd: None,
                args: None,
                result: Some(content_val),
                partial_result: None,
            },
        ));
    }
    if events.is_empty() {
        CcMap::Ignore
    } else {
        CcMap::Events(events)
    }
}

fn map_result(v: &Value) -> CcMap {
    let subtype = v.get("subtype").and_then(|x| x.as_str()).unwrap_or("");
    if v.get("is_error").and_then(Value::as_bool) == Some(true) {
        return CcMap::Failed("engine_result_error".into());
    }
    if subtype == "success" {
        let text = v.get("result").and_then(|x| x.as_str()).unwrap_or("").to_string();
        return CcMap::Final(text);
    }
    // 非成功 result：上抛为引擎退出。净化——只保留引擎错误类别标识符，
    // 绝不把原始 payload（可能含敏感细节）灌进错误消息。
    CcMap::Failed(sanitize_result_note(subtype))
}

/// 只保留引擎错误类别（有限标识符：字母数字/下划线/连字符），剥掉其它。
fn sanitize_result_note(subtype: &str) -> String {
    let clean: String = subtype
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '_' || *c == '-')
        .collect();
    if clean.is_empty() {
        "engine returned non-success result".to_string()
    } else {
        format!("engine result subtype: {clean}")
    }
}

/// `control_request` 映射：`can_use_tool` 提取为 [`CcMap::ControlRequest`]，
/// 由 `run_turn` 接线 HITL 交互（register→requested→await→control_response
/// →resolved）。`can_use_tool` 以外的子类型忽略；绝不发 `stream:"approval"`
/// （spec §2 禁止旧 approval 用于新接入）。
fn map_control_request(v: &Value, _run_id: &str) -> CcMap {
    let req = match v.get("request") {
        Some(r) => r,
        None => return CcMap::Ignore,
    };
    let subtype = req.get("subtype").and_then(|x| x.as_str()).unwrap_or("");
    if subtype != "can_use_tool" {
        return CcMap::Ignore;
    }
    // Claude correlates control replies with the envelope's request_id, not
    // a field inside request. Invalid requests must fail instead of creating
    // an interaction that the engine can never resolve.
    let Some(request_id) = v.get("request_id").and_then(Value::as_str)
        .filter(|id| !id.trim().is_empty()) else {
        return CcMap::Failed("can_use_tool requires a non-empty request_id".into());
    };
    let Some(tool_name) = req.get("tool_name").and_then(Value::as_str)
        .filter(|name| !name.trim().is_empty()) else {
        return CcMap::Failed("can_use_tool requires a non-empty tool_name".into());
    };
    let Some(input) = req.get("input").filter(|input| input.is_object()) else {
        return CcMap::Failed("can_use_tool requires an input object".into());
    };
    CcMap::ControlRequest {
        request_id: request_id.to_string(), tool_name: tool_name.to_string(), input: input.clone(),
    }
}

/// Build the kind-specific `extra` payload for the `interaction/requested` SSE
/// event (per spec §5.2).
///
/// - exec (`tool_name != "AskUserQuestion"`): `{title, options}` plus either
///   `command` (when `input.command` is a non-empty string) or `description`
///   (otherwise). BCS validates that a present `command` must be a non-empty
///   string — a present-but-null/empty command drops the interaction and parks
///   the run forever, so a missing/non-string command MUST NOT emit the key;
///   we synthesize a human-readable `description` instead. The fixed options
///   `[allow-once, deny]` are always present.
/// - ask_user (`AskUserQuestion`): `{questions}` from `input.questions[]` —
///   questionId = `header` fallback `question_N`, options `label → {label,
///   value=label}` (cc has no separate value; baas-fallback parity).
fn build_requested_extra(tool_name: &str, input: &Value) -> Value {
    if tool_name == "AskUserQuestion" {
        json!({ "questions": map_ask_user_questions(input) })
    } else {
        // Claude Code ends plan mode through the ExitPlanMode tool: the run asks
        // the human to confirm moving from read-only planning into execution.
        // Present that as a readable confirmation rather than a bare tool name;
        // the tool's `input.plan` already carries the proposal the approver reads.
        let plan_mode_exit = matches!(tool_name, "ExitPlanMode" | "exit_plan_mode");
        let title = if plan_mode_exit { "Exit plan mode" } else { tool_name };
        // Only emit `command` when it is a non-empty string; otherwise omit the
        // key (BCS rejects present-but-null/empty command) and synthesize a
        // human-readable `description` so the exec interaction still carries
        // context for the approver.
        let command = input
            .get("command")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty());
        let mut extra = json!({
            "title": title,
            "options": [
                { "decision": "allow-once", "label": "Allow once" },
                { "decision": "deny", "label": "Deny" },
            ],
        });
        match command {
            Some(cmd) => extra["command"] = json!(cmd),
            None => {
                let plan = plan_mode_exit
                    .then(|| input.get("plan").and_then(Value::as_str))
                    .flatten()
                    .filter(|s: &&str| !s.trim().is_empty());
                let description = plan
                    .map(str::to_string)
                    .unwrap_or_else(|| synthesize_exec_description(input));
                extra["description"] = json!(description);
            }
        }
        extra
    }
}

/// Synthesize a human-readable `description` for an exec interaction whose tool
/// input carries no usable `command` string. Prefer `path`/`file_path` (the
/// common Read/Edit/Grep shapes); fall back to a compact JSON rendering of the
/// input, truncated to 200 chars on a UTF-8 character boundary (never byte-slice
/// — the input may contain multi-byte text). `serde_json::to_string` on a
/// `Value` cannot fail, but `unwrap_or_default` keeps this panic-free.
fn synthesize_exec_description(input: &Value) -> String {
    if let Some(p) = input
        .get("path")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
    {
        return p.to_string();
    }
    if let Some(p) = input
        .get("file_path")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
    {
        return p.to_string();
    }
    const MAX_LEN: usize = 200;
    let compact = serde_json::to_string(input).unwrap_or_default();
    match compact.char_indices().nth(MAX_LEN) {
        Some((idx, _)) => compact[..idx].to_string(),
        None => compact,
    }
}

/// Map cc `AskUserQuestion` `input.questions[]` to BCN questions. See
/// [`build_requested_extra`]: `questionId` 取 `header`，缺省回落到
/// `question_N`（1-based 索引）；`options[].value` 以 `label` 回填（cc 无独立
/// value，对齐 baas fallback 策略）；`multiSelect` 透传。**注意**：含
/// `secret`/`isSecret` 标记的问题在本函数之前已被 [`ask_user_has_secret`]
/// 拦截并应答 deny，因此不会走到本映射。
fn map_ask_user_questions(input: &Value) -> Value {
    let Some(questions) = input.get("questions").and_then(|q| q.as_array()) else {
        return json!([]);
    };
    let out: Vec<Value> = questions
        .iter()
        .enumerate()
        .map(|(i, q)| {
            let question_id = q
                .get("header")
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
                .map(str::to_string)
                .unwrap_or_else(|| format!("question_{}", i + 1));
            let question = q.get("question").and_then(|v| v.as_str()).unwrap_or("");
            let options = q
                .get("options")
                .and_then(|o| o.as_array())
                .map(|arr| {
                    arr.iter()
                        .map(|o| {
                            let label = o.get("label").and_then(|v| v.as_str()).unwrap_or("");
                            json!({ "label": label, "value": label })
                        })
                        .collect::<Vec<_>>()
                })
                .unwrap_or_default();
            let mut obj = json!({
                "questionId": question_id,
                "question": question,
                "options": options,
            });
            if let Some(ms) = q.get("multiSelect").and_then(|v| v.as_bool()) {
                obj["multiSelect"] = json!(ms);
            }
            obj
        })
        .collect();
    json!(out)
}

/// Refuse conversion when any `AskUserQuestion` question carries a `secret`
/// (spec §5.2): BCS interaction cannot ferry secret answers — the driver
/// answers the engine `deny` directly and emits no interaction. Checks both
/// the question-level `secret`/`isSecret` boolean and (defensively) a
/// whole-call `isSecret` flag.
fn ask_user_has_secret(input: &Value) -> bool {
    if input.get("secret").and_then(|v| v.as_bool()) == Some(true)
        || input.get("isSecret").and_then(|v| v.as_bool()) == Some(true)
    {
        return true;
    }
    let Some(questions) = input.get("questions").and_then(|q| q.as_array()) else {
        return false;
    };
    questions.iter().any(|q| {
        q.get("secret").and_then(|v| v.as_bool()) == Some(true)
            || q.get("isSecret").and_then(|v| v.as_bool()) == Some(true)
    })
}

/// Map a BCS resolution value to the cc control_response `behavior`
/// (`"allow"`/`"deny"`).
///
/// For exec, only the offered `allow-once` decision maps to `allow`.
/// Any other value, including `allow_once`, maps to `deny`; decision strings
/// are exact wire values, not aliases. We never infer allow from an unknown
/// decision.
///
/// ask_user follows the BCS workbench contract (`bcs-interaction`
/// `validate_ask_user_resolution`): the only actions it sends are
/// `submit` (with a questionId -> {values:[...]} answers object; BCS has
/// already validated one entry per question, where empty `values` is an
/// explicit skip) and `cancel`. cc v1 has no answers channel, so both
/// collapse to allow/deny (`spec §5.2`); `submit` allows, everything else
/// denies.
fn resolution_to_behavior(resolution: &Value) -> &'static str {
    if let Some(decision) = resolution["decision"].as_str() {
        return match decision {
            "allow-once" => "allow",
            _ => "deny",
        };
    }
    match resolution["action"].as_str() {
        // A valid submission answers every question; BCS rejects an empty
        // answers map (and every values entry) before the delivery here.
        Some("submit") if resolution["answers"].as_object().is_some_and(|a| !a.is_empty()) => "allow",
        _ => "deny",
    }
}

/// A submitted ask_user form is denied while its `message` carries the human's
/// completed answers, phrased so the model adopts them and continues without
/// re-asking.
///
/// The stream-json permission protocol has NO answers channel for an allowed
/// AskUserQuestion: the CLI validates `updatedInput` against the tool schema
/// (rejecting the workbench's `{question -> {values}}` shape outright) and,
/// when validation passes, still executes the tool as an unanswered headless
/// form — the model then sees "The user did not answer the questions" and
/// falls back to re-asking in plain chat text. The one part of a denial that
/// does reach the model is its message, rendered into the tool result.
///
/// Denial is cosmetically an error to the CLI (`is_error: true` on the tool
/// result). [`ANSWERED_ASK_USER_MARK`] prefixes this message so the
/// transcription below lifts that flag for exactly these results — the model
/// reads the answers, the workbench sees a normal tool row.
const ANSWERED_ASK_USER_MARK: &str = "The user answered this question card";
const ANSWERED_ASK_USER_PREFIX: &str = "The user answered this question card; adopt these answers and continue the task without asking again:";

fn answered_ask_user_response(request_id: &str, resolution: &Value) -> Value {
    let message = if let Some(answers) = resolution.get("answers").and_then(Value::as_object) {
        let mut lines = String::from(ANSWERED_ASK_USER_PREFIX);
        for (question, answer) in answers {
            let values = answer
                .get("values")
                .and_then(Value::as_array)
                .map(|values| {
                    values
                        .iter()
                        .filter_map(Value::as_str)
                        .collect::<Vec<_>>()
                        .join("; ")
                })
                .unwrap_or_default();
            if values.is_empty() {
                lines.push_str(&format!("\n- {question}: (unanswered)"));
            } else {
                lines.push_str(&format!("\n- {question}: {values}"));
            }
        }
        lines
    } else {
        "The user submitted the question card without answers; continue with the task."
            .to_owned()
    };
    json!({
        "type": "control_response",
        "response": { "subtype": "success", "request_id": request_id,
                      "response": { "behavior": "deny", "message": message } }
    })
}

/// A successful control exchange may carry either an allow or a deny decision.
/// Claude requires the outer subtype and a message on the deny branch;
/// updatedInput belongs only to an allow response.
fn permission_control_response(request_id: &str, behavior: &str, input: &Value) -> Value {
    let permission = if behavior == "allow" {
        json!({ "behavior": "allow", "updatedInput": input })
    } else {
        json!({ "behavior": "deny", "message": "Tool use was not approved." })
    };
    json!({
        "type": "control_response",
        "response": { "subtype": "success", "request_id": request_id,
                      "response": permission }
    })
}

/// Drive one `can_use_tool` control request as a HITL interaction (Task 12):
///
/// 1. (ask_user only) refuse secret-marked questions — answer the engine `deny`
///    immediately and log; no interaction is emitted (spec §5.2).
/// 2. register a pending interaction with the registry → mint interactionId.
/// 3. emit `interaction/requested` (exec: title/command/options; ask_user:
///    questions).
/// 4. await the BCS resolution (or abort → deny fallback).
/// 5. write the cc `control_response` back to the engine's control channel
///    (`behavior` allow/deny; `updatedInput` only on allow).
/// 6. emit `interaction/resolved`.
///
/// Returns `Ok(())` so the `run_turn` loop continues to the next line; an IO
/// failure writing the control_response is returned as `TurnError::Io`, and a
/// failed `requested` send (BCS disconnect) aborts the engine.
async fn handle_control_request(
    cli: &mut CliSession,
    events: &tokio::sync::mpsc::Sender<StreamEvent>,
    abort: &tokio_util::sync::CancellationToken,
    req: &TurnRequest,
    request_id: String,
    tool_name: String,
    input: Value,
) -> Result<(), TurnError> {
    let kind = if tool_name == "AskUserQuestion" {
        InteractionKind::AskUser
    } else {
        InteractionKind::Exec
    };
    // 1. Secret-marked ask_user questions are refused (spec §5.2): the engine
    //    gets deny and no interaction is emitted to BCS.
    if kind == InteractionKind::AskUser && ask_user_has_secret(&input) {
        tracing::warn!(
            target: "bcs_bridge_core",
            request_id = %request_id, tool = %tool_name,
            "AskUserQuestion with secret-marked question refused; answering deny"
        );
        let deny = permission_control_response(&request_id, "deny", &input);
        cli.write_line(&deny.to_string()).await.map_err(TurnError::Io)?;
        return Ok(());
    }
    // 2-3. Register + emit requested.
    let (iid, resolution_rx) = req.interactions.register(&req.run_id, kind, request_id.clone());
    let requested = build_requested_extra(&tool_name, &input);
    if events
        .send(sse::interaction_event(
            &req.run_id,
            InteractionPhase::Requested,
            kind,
            &iid,
            requested,
        ))
        .await
        .is_err()
    {
        cli.kill().await;
        return Err(TurnError::Aborted);
    }
    // 4. Await the BCS resolution; abort or a dropped sender recovers to deny
    //    so the driver never blocks on a dead interaction.
    let resolution = tokio::select! {
        _ = abort.cancelled() => json!({ "decision": "deny" }),
        r = resolution_rx => r.unwrap_or_else(|_| json!({ "decision": "deny" })),
    };
    // 5. Write the control_response with the mapped behavior. An allowed
    //    AskUserQuestion carries the submitted answers in its updatedInput:
    //    the engine reads input.answers as the completed human form, so the
    //    turn continues with the chosen values instead of waiting on a
    //    headless prompt nobody can answer.
    let behavior = resolution_to_behavior(&resolution);
    let response = if kind == InteractionKind::AskUser && behavior == "allow" {
        answered_ask_user_response(&request_id, &resolution)
    } else {
        permission_control_response(&request_id, behavior, &input)
    };
    cli.write_line(&response.to_string()).await.map_err(TurnError::Io)?;
    // 6. Emit resolved (best-effort — BCS may have disconnected post-request).
    // v1 limitation: ask_user resolutions carry no `decision` key, so the
    // resolved event's `decision` is null for ask_user (consistent with the
    // no-answers-channel collapse above).
    let _ = events
        .send(sse::interaction_event(
            &req.run_id,
            InteractionPhase::Resolved,
            kind,
            &iid,
            json!({ "decision": resolution["decision"].clone() }),
        ))
        .await;
    Ok(())
}

/// Default sliding idle window for the engine stream. Every received line
/// pushes the turn's "still making progress" deadline this far out; only a
/// stream that stays silent for the whole window fails the turn — with a
/// named error that reports the stalled background count. Two minutes stays
/// clear of every legitimate silence (model TTFT spikes, a non-streaming
/// foreground tool before it auto-backgrounds) while surfacing a wedged
/// stream (2026-10-14/2026-10-16: quiet with pending work while BCS waited
/// on a final that never came) in minutes rather than only at the outer run
/// deadline. A per-turn `req.idle_timeout` override still wins when set.
const IDLE_WINDOW: std::time::Duration = std::time::Duration::from_secs(120);

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

        let mut cli = CliSession::spawn(&self.bin, &args, &req.cwd, &[], req.trace.clone())
            .await
            .map_err(TurnError::Spawn)?;

        // 启动后立刻向 stdin 写一条 user 消息（claude stream-json 输入格式）。
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
        // Sliding idle watchdog: every received line pushes this deadline out.
        // A stream that goes quiet forever — the CLI wedged on something the
        // bridge can't observe — must fail the turn explicitly instead of
        // wedging until the outer run deadline with BCS waiting on a final.
        let idle_window = req.idle_timeout.unwrap_or(IDLE_WINDOW);
        let mut idle_deadline = tokio::time::Instant::now() + idle_window;
        loop {
            let idle_timer = tokio::time::sleep_until(idle_deadline);
            tokio::select! {
                _ = abort.cancelled() => {
                    cli.kill().await;
                    return Err(TurnError::Aborted);
                }
                _ = idle_timer => {
                    let pending = mapper.background_pending();
                    cli.kill().await;
                    return Err(TurnError::EngineExited(format!(
                        "stream idle for {idle_window:?} ({pending} pending background task(s))"
                    )));
                }
                line = cli.next_line() => {
                    idle_deadline = tokio::time::Instant::now() + idle_window;
                    let Some(line) = line.map_err(TurnError::Io)? else {
                        let pending = mapper.background_pending();
                        let note = if pending > 0 {
                            format!(" with {pending} pending background task(s)")
                        } else {
                            String::new()
                        };
                        return Err(TurnError::EngineExited(format!("stdout EOF before result{note}")));
                    };
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

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_protocol::stream::{AgentData, ChatState, StreamEvent};

    #[test]
    fn answered_ask_user_response_delivers_answers_as_denial_message() {
        use serde_json::json;
        let resolution = json!({"action":"submit","answers":{
            "q1":{"values":["静态"]},
            "q2":{"values":["a","b"]},
            "q3":{"values":[]}
        }});
        let response = answered_ask_user_response("req-1", &resolution);
        assert_eq!(response["type"], "control_response");
        assert_eq!(response["response"]["subtype"], "success");
        assert_eq!(response["response"]["request_id"], "req-1");
        let message = response["response"]["response"]["message"].as_str().unwrap();
        assert!(response["response"]["response"]["behavior"] == "deny",
            "the message-carrying answer rides on a denial");
        assert!(message.starts_with(ANSWERED_ASK_USER_MARK), "signed for transcription lifting");
        assert!(message.contains("- q1: 静态"));
        assert!(message.contains("- q2: a; b"), "multi-select answers are joined");
        assert!(message.contains("- q3: (unanswered)"), "an explicit skip stays visible");
        // Without an answers object the message still guides the model onward.
        let response = answered_ask_user_response("req-2", &json!({"action":"submit"}));
        assert!(response["response"]["response"]["message"]
            .as_str().unwrap().contains("without answers"));
    }

    #[test]
    fn answered_ask_user_tool_result_lifts_the_error_flag() {
        // The CLI renders a denial as {content: our message, is_error: true};
        // the transcription surfaces exactly these as a normal answered row.
        let marked = json!({"type":"user","message":{"content":[
            {"type":"tool_result","tool_use_id":"t1",
             "content":"The user answered this question card; adopt these answers:\n- q1: 静态",
             "is_error":true}]}}).to_string();
        match map_cc_line(&marked, "run-1") {
            CcMap::Events(events) => match &events[0] {
                StreamEvent::Agent(agent) => match &agent.data {
                    AgentData::Tool(tool) => {
                        assert_eq!(tool.is_error, Some(false), "our answer text is not a failure");
                        assert_eq!(tool.tool_call_id.as_deref(), Some("t1"));
                    }
                    other => panic!("expected tool event, got {other:?}"),
                },
                other => panic!("expected agent event, got {other:?}"),
            },
            other => panic!("expected events, got {other:?}"),
        }
        // A genuine failure retains its error flag.
        let failed = json!({"type":"user","message":{"content":[
            {"type":"tool_result","tool_use_id":"t2",
             "content":"command exited 1","is_error":true}]}}).to_string();
        match map_cc_line(&failed, "run-1") {
            CcMap::Events(events) => match &events[0] {
                StreamEvent::Agent(agent) => match &agent.data {
                    AgentData::Tool(tool) => assert_eq!(tool.is_error, Some(true)),
                    other => panic!("expected tool event, got {other:?}"),
                },
                other => panic!("expected agent event, got {other:?}"),
            },
            other => panic!("expected events, got {other:?}"),
        }
    }

    #[test]
    fn maps_cc_ndjson_turn() {
        let lines: Vec<String> = std::fs::read_to_string(
            concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/cc_turn.ndjson"))
            .unwrap().lines().map(str::to_string).collect();
        let mut session_id = None;
        let mut deltas = String::new();
        let mut tools = 0;
        let mut final_text = None;
        for line in &lines {
            match map_cc_line(line, "r-1") {
                CcMap::SessionId(s) => session_id = Some(s),
                CcMap::Events(events) => for ev in events {
                    match ev {
                        StreamEvent::Chat(c) if c.state == ChatState::Delta =>
                            deltas.push_str(&c.delta_text.unwrap()),
                        StreamEvent::Agent(_) => tools += 1,
                        _ => {}
                    }
                },
                CcMap::Final(text) => final_text = Some(text),
                CcMap::Failed(_) => {}
                CcMap::ControlRequest { .. } => {}
                CcMap::BackgroundStarted { .. }
                | CcMap::BackgroundEnqueued { .. }
                | CcMap::BackgroundDelivered { .. } => {}
                CcMap::Ignore | CcMap::Malformed => {}
            }
        }
        assert_eq!(session_id.as_deref(), Some("cc-sess-1"));
        assert_eq!(deltas, "正在分析");
        assert_eq!(tools, 2);
        assert_eq!(final_text.as_deref(), Some("完成了"));
    }

    #[test]
    fn malformed_json_is_malformed() {
        assert!(matches!(map_cc_line("not json", "r-1"), CcMap::Malformed));
        assert!(matches!(map_cc_line("{", "r-1"), CcMap::Malformed));
    }

    #[test]
    fn unrecognized_type_is_ignore() {
        let line = r#"{"type":"mystery","payload":42}"#;
        assert!(matches!(map_cc_line(line, "r-1"), CcMap::Ignore));
    }

    #[test]
    fn json_without_type_is_ignore() {
        let line = r#"{"hello":"world"}"#;
        assert!(matches!(map_cc_line(line, "r-1"), CcMap::Ignore));
    }

    #[test]
    fn non_success_result_is_failed_with_sanitized_note() {
        // error subtype 标识符被保留（引擎错误类别，非用户数据）。
        let line = r#"{"type":"result","subtype":"error_max_cycles","result":"secret detail"}"#;
        match map_cc_line(line, "r-1") {
            CcMap::Failed(note) => {
                assert!(note.contains("error_max_cycles"), "note: {note}");
                // 不携带原始 result payload。
                assert!(!note.contains("secret detail"), "note leaked payload: {note}");
            }
            other => panic!("expected Failed, got {other:?}"),
        }
    }

    #[test]
    fn empty_result_text_success_maps_to_empty_final() {
        let line = r#"{"type":"result","subtype":"success","result":""}"#;
        match map_cc_line(line, "r-1") {
            CcMap::Final(text) => assert_eq!(text, ""),
            other => panic!("expected Final, got {other:?}"),
        }
    }

    #[test]
    fn error_flag_does_not_acknowledge_a_successful_turn() {
        let line = r#"{"type":"result","subtype":"success","is_error":true,"result":"private upstream error"}"#;
        match map_cc_line(line, "r-1") {
            CcMap::Failed(note) => assert!(!note.contains("private upstream error")),
            other => panic!("an error result must not consume pending injects: {other:?}"),
        }
    }

    #[test]
    fn control_request_can_use_tool_maps_to_control_request() {
        // 不再发占位 thinking（Task 9）——Task 12 接线为 ControlRequest 载体，
        // 由 run_turn 驱动 HITL 交互；绝不发 `stream:"approval"`（spec §2）。
        let line = r#"{"type":"control_request","request_id":"req-7","request":{"subtype":"can_use_tool","tool_name":"Bash","input":{"command":"ls"}}}"#;
        match map_cc_line(line, "r-1") {
            CcMap::ControlRequest { request_id, tool_name, input } => {
                assert_eq!(request_id, "req-7");
                assert_eq!(tool_name, "Bash");
                assert_eq!(input["command"], json!("ls"));
            }
            other => panic!("expected ControlRequest, got {other:?}"),
        }
    }

    #[test]
    fn invalid_permission_requests_fail_instead_of_parking_an_unresolvable_interaction() {
        let valid = json!({
            "type": "control_request", "request_id": "req-1",
            "request": { "subtype": "can_use_tool", "tool_name": "Bash",
                         "input": { "command": "ls" } }
        });
        for invalid_id in [Value::Null, json!(""), json!(" "), json!(7)] {
            let mut request = valid.clone();
            request["request_id"] = invalid_id;
            // A nested ID must never substitute for the engine's top-level ID.
            request["request"]["request_id"] = json!("nested-id");
            assert!(matches!(map_cc_line(&request.to_string(), "r-1"), CcMap::Failed(_)));
        }
        for (field, value) in [("tool_name", json!("")), ("tool_name", Value::Null),
                               ("input", Value::Null), ("input", json!([]))] {
            let mut request = valid.clone();
            request["request"][field] = value;
            assert!(matches!(map_cc_line(&request.to_string(), "r-1"), CcMap::Failed(_)));
        }
    }

    #[test]
    fn build_requested_extra_plan_mode_exit_reads_as_confirmation_with_plan_text() {
        // The plan-mode exit gate shows a human title and the proposed plan
        // verbatim instead of the tool name plus a truncated JSON rendering.
        let extra = build_requested_extra("ExitPlanMode", &json!({"plan":"1. 找接口\n2. 写迁移"}));
        assert_eq!(extra["title"], json!("Exit plan mode"));
        assert_eq!(extra["description"], json!("1. 找接口\n2. 写迁移"));
        assert!(extra.get("command").is_none());
        assert_eq!(extra["options"][0]["decision"], json!("allow-once"));
        assert_eq!(extra["options"][1]["decision"], json!("deny"));
        // Without plan text the generic exec description fallback still applies.
        let extra = build_requested_extra("ExitPlanMode", &json!({}));
        assert_eq!(extra["title"], json!("Exit plan mode"));
        assert!(extra["description"].is_string());
        // The snake_case spelling hits the same branch.
        let extra = build_requested_extra("exit_plan_mode", &json!({"plan":"步骤"}));
        assert_eq!(extra["title"], json!("Exit plan mode"));
        assert_eq!(extra["description"], json!("步骤"));
    }

    #[test]
    fn build_requested_extra_exec_carries_title_command_and_fixed_options() {
        let extra = build_requested_extra("Bash", &json!({"command":"ls -la"}));
        assert_eq!(extra["title"], json!("Bash"));
        assert_eq!(extra["command"], json!("ls -la"));
        assert_eq!(extra["options"][0]["decision"], json!("allow-once"));
        assert_eq!(extra["options"][0]["label"], json!("Allow once"));
        assert_eq!(extra["options"][1]["decision"], json!("deny"));
        assert_eq!(extra["options"][1]["label"], json!("Deny"));
    }

    #[test]
    fn build_requested_extra_exec_omits_command_when_missing() {
        // BCS rejects a present-but-null `command` (interaction dropped → run
        // parks forever), so when input has no `command` string we MUST NOT emit
        // the key; we synthesize a `description` from `path`/`file_path` instead.
        let extra = build_requested_extra("Read", &json!({"path":"/a"}));
        assert_eq!(extra["title"], json!("Read"));
        assert!(
            extra.get("command").is_none(),
            "command key must be absent when input has no command string"
        );
        assert_eq!(extra["description"], json!("/a"), "description synthesized from path");
    }

    #[test]
    fn build_requested_extra_exec_emits_bcs_valid_shape() {
        // BCS-side validation rule for what we emit on an exec `requested` extra:
        // if the `command` key is present it must be a non-empty string, and
        // `options` must be a non-empty array where each option has a string
        // `decision` and `label`. Holds for every shape we build.
        fn assert_bcs_exec_valid(extra: &Value) {
            if let Some(cmd) = extra.get("command") {
                assert!(cmd.is_string(), "command must be a string when present: {extra}");
                let s = cmd.as_str().unwrap();
                assert!(!s.is_empty(), "command must be non-empty when present: {extra}");
            }
            let options = extra
                .get("options")
                .and_then(|o| o.as_array())
                .expect("options must be a non-empty array");
            assert!(!options.is_empty(), "options must be non-empty: {extra}");
            for o in options {
                assert!(
                    o.get("decision").and_then(|v| v.as_str()).is_some(),
                    "each option needs a string decision: {extra}"
                );
                assert!(
                    o.get("label").and_then(|v| v.as_str()).is_some(),
                    "each option needs a string label: {extra}"
                );
            }
        }

        // Non-empty string command → command key present.
        assert_bcs_exec_valid(&build_requested_extra("Bash", &json!({"command":"ls -la"})));
        // Missing command → description shape (no command key).
        assert_bcs_exec_valid(&build_requested_extra("Read", &json!({"path":"/a"})));
        // Empty-string command → treated as no command (description shape).
        assert_bcs_exec_valid(&build_requested_extra("Bash", &json!({"command":""})));
        // Null command → treated as no command (description shape).
        assert_bcs_exec_valid(&build_requested_extra("Bash", &json!({"command":null})));
        // No path/file_path either → description falls back to compact JSON.
        let extra = build_requested_extra("Bash", &json!({"foo":"bar"}));
        assert!(extra.get("command").is_none(), "no command key when command absent");
        assert_bcs_exec_valid(&extra);
        assert!(extra["description"].as_str().unwrap().contains("bar"));
    }

    #[test]
    fn synthesize_exec_description_prefers_path_then_falls_back() {
        // path wins over file_path and JSON fallback.
        assert_eq!(synthesize_exec_description(&json!({"path":"/a/b","file_path":"/c"})), "/a/b");
        // file_path used when path absent.
        assert_eq!(synthesize_exec_description(&json!({"file_path":"/c"})), "/c");
        // empty path string falls through to file_path, then JSON.
        assert_eq!(synthesize_exec_description(&json!({"path":"","file_path":"/c"})), "/c");
        // empty path + no file_path → JSON fallback keeps the empty `path` key.
        assert_eq!(synthesize_exec_description(&json!({"path":""})), r#"{"path":""}"#);
        assert_eq!(synthesize_exec_description(&json!({})), "{}");
        // UTF-8 safe truncation at 200 chars on multibyte text (no mid-char slice).
        let big = json!({ "k": "你".repeat(300) });
        let compact = serde_json::to_string(&big).unwrap();
        let desc = synthesize_exec_description(&big);
        assert!(desc.chars().count() <= 200, "desc must be at most 200 chars: {}", desc.chars().count());
        // desc is a char-boundary prefix of the compact JSON — a multi-byte char
        // is never split (Rust str invariant + `char_indices` truncation).
        assert!(compact.starts_with(&desc), "desc must be a char-boundary prefix: {desc:?}");
    }

    #[test]
    fn build_requested_extra_ask_user_maps_questions_label_to_value() {
        let input = json!({
            "questions": [
                { "header": "lang", "question": "Pick a language", "multiSelect": false,
                  "options": [ {"label":"Rust"}, {"label":"Go"} ] },
                { "question": "Free text?", "options": [ {"label":"yes"} ] },
            ]
        });
        let extra = build_requested_extra("AskUserQuestion", &input);
        let qs = &extra["questions"];
        assert_eq!(qs[0]["questionId"], json!("lang"), "header → questionId");
        assert_eq!(qs[0]["question"], json!("Pick a language"));
        assert_eq!(qs[0]["multiSelect"], json!(false));
        assert_eq!(qs[0]["options"][0], json!({"label":"Rust","value":"Rust"}));
        // 缺 header → question_N（1-based）
        assert_eq!(qs[1]["questionId"], json!("question_2"));
        assert_eq!(qs[1]["options"][0], json!({"label":"yes","value":"yes"}));
    }

    #[test]
    fn ask_user_question_with_secret_at_question_is_refused() {
        let input = json!({"questions":[{"secret":true,"question":"pwd"}]});
        assert!(ask_user_has_secret(&input));
    }

    #[test]
    fn ask_user_question_with_is_secret_at_call_level_is_refused() {
        let input = json!({"isSecret":true,"questions":[{"question":"pwd"}]});
        assert!(ask_user_has_secret(&input));
    }

    #[test]
    fn ask_user_question_without_secret_is_not_refused() {
        let input = json!({"questions":[{"question":"name","options":[{"label":"a"}]}]});
        assert!(!ask_user_has_secret(&input));
    }

    #[test]
    fn resolution_to_behavior_maps_exec_decisions_and_ask_user_actions() {
        // exec: only the offered allow-once decision → allow; everything else (deny,
        // unrecognized/garbage, a bare `allow` not in the allowlist) → deny.
        // Conservative: never infer allow from an unknown decision.
        assert_eq!(resolution_to_behavior(&json!({"decision":"allow-once"})), "allow");
        for decision in ["allow_once", "allow_session", "allow_persistent", "allow_always", "allow-always"] {
            assert_eq!(resolution_to_behavior(&json!({"decision":decision})), "deny");
        }
        assert_eq!(resolution_to_behavior(&json!({"decision":"deny"})), "deny");
        assert_eq!(
            resolution_to_behavior(&json!({"decision":"yes"})),
            "deny",
            "unknown decision → deny"
        );
        assert_eq!(
            resolution_to_behavior(&json!({"decision":"allow"})),
            "deny",
            "bare 'allow' (not in allowlist) → deny"
        );
        assert_eq!(resolution_to_behavior(&json!({"decision":"garbage"})), "deny");
        // ask_user follows the BCS workbench contract: submit with a
        // questionId -> {values} object allows (empty values entries are
        // explicit skips BCS already accepted); cancel and anything else deny.
        assert_eq!(
            resolution_to_behavior(&json!({
                "action":"submit",
                "answers":{"技术栈":{"values":[]},"规模频率":{"values":["大规模长期爬取"]}}
            })),
            "allow",
            "a real workbench submission, including an unanswered (empty) question"
        );
        assert_eq!(
            resolution_to_behavior(&json!({"action":"submit","answers":{}})),
            "deny",
            "submit without an answers object is not a valid submission"
        );
        assert_eq!(
            resolution_to_behavior(&json!({"action":"submit"})),
            "deny",
            "missing answers → deny"
        );
        assert_eq!(resolution_to_behavior(&json!({"action":"cancel"})), "deny");
        assert_eq!(
            resolution_to_behavior(&json!({"action":"answer","answers":[{"q":"a"}]})),
            "deny",
            "the never-shipped v1 shape must not silently allow"
        );
        assert_eq!(resolution_to_behavior(&json!(null)), "deny", "unknown shape → deny");
    }

    #[test]
    fn non_can_use_tool_control_request_is_ignore() {
        let line = r#"{"type":"control_request","request":{"subtype":"other"}}"#;
        assert!(matches!(map_cc_line(line, "r-1"), CcMap::Ignore));
    }

    #[test]
    fn assistant_tool_use_carries_id_name_and_input() {
        let line = r#"{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_9","name":"Read","input":{"path":"/a"}}]}}"#;
        match map_cc_line(line, "r-9") {
            CcMap::Events(evs) => {
                assert_eq!(evs.len(), 1);
                match &evs[0] {
                    StreamEvent::Agent(a) => match &a.data {
                        AgentData::Tool(t) => {
                            assert_eq!(t.phase, ToolPhase::Start);
                            assert_eq!(t.name.as_deref(), Some("Read"));
                            assert_eq!(t.tool_call_id.as_deref(), Some("toolu_9"));
                            assert_eq!(t.args.as_ref().unwrap()["path"], serde_json::json!("/a"));
                        }
                        other => panic!("expected Tool, got {other:?}"),
                    },
                    other => panic!("expected Agent(Tool), got {other:?}"),
                }
            }
            other => panic!("expected Events, got {other:?}"),
        }
    }

    #[test]
    fn user_tool_result_carries_tool_use_id_and_content_zero_loss() {
        let line = r#"{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"toolu_9","content":[{"type":"text","text":"ok"}]}]}}"#;
        match map_cc_line(line, "r-9") {
            CcMap::Events(evs) => {
                assert_eq!(evs.len(), 1);
                match &evs[0] {
                    StreamEvent::Agent(a) => match &a.data {
                        AgentData::Tool(t) => {
                            assert_eq!(t.phase, ToolPhase::Result);
                            assert_eq!(t.tool_call_id.as_deref(), Some("toolu_9"));
                            assert_eq!(t.result.as_ref().unwrap()[0]["text"], serde_json::json!("ok"));
                        }
                        other => panic!("expected Tool, got {other:?}"),
                    },
                    other => panic!("expected Agent(Tool), got {other:?}"),
                }
            }
            other => panic!("expected Events, got {other:?}"),
        }
    }

    #[test]
    fn assistant_without_tool_use_is_ignore() {
        let line = r#"{"type":"assistant","message":{"content":[{"type":"text","text":"hi"}]}}"#;
        // 文本块由 stream_event 的 deltas 承载；assistant 非工具块不重复映射。
        assert!(matches!(map_cc_line(line, "r-1"), CcMap::Ignore));
    }

    #[test]
    fn user_tool_result_preserves_error_flags_and_defaults_only_when_absent() {
        for (flag, expected) in [
            (None, Some(false)),
            (Some(json!(false)), Some(false)),
            (Some(json!(true)), Some(true)),
            (Some(json!(null)), None),
            (Some(json!("false")), None),
            (Some(json!(0)), None),
        ] {
            let mut item = json!({"type":"tool_result","tool_use_id":"toolu_9","content":"ok"});
            if let Some(flag) = &flag {
                item["is_error"] = flag.clone();
            }
            let line = json!({"type":"user","message":{"content":[item]}}).to_string();
            let CcMap::Events(events) = map_cc_line(&line, "r-9") else {
                panic!("expected tool result");
            };
            let StreamEvent::Agent(agent) = &events[0] else { panic!("expected agent"); };
            let AgentData::Tool(tool) = &agent.data else {
                panic!("expected tool");
            };
            assert_eq!(tool.is_error, expected, "source is_error: {flag:?}");
            assert_eq!(tool.result, Some(json!("ok")));
        }
    }

    fn mapped_tools(mapper: &mut CcTurnMapper<'_>, line: Value) -> Vec<ToolData> {
        let CcMap::Events(events) = mapper.map_line(&line.to_string()) else {
            panic!("expected tool events");
        };
        events.into_iter().map(|event| {
            let StreamEvent::Agent(agent) = event else { panic!("expected agent"); };
            let AgentData::Tool(tool) = agent.data else {
                panic!("expected tool");
            };
            tool
        }).collect()
    }

    fn tool_start(id: &str, name: &str) -> Value {
        json!({"type":"assistant","message":{"content":[
            {"type":"tool_use","id":id,"name":name,"input":{}}
        ]}})
    }

    fn tool_result(id: &str) -> Value {
        json!({"type":"user","message":{"content":[
            {"type":"tool_result","tool_use_id":id,"content":{"tool":"untrusted-name"}}
        ]}})
    }

    #[test]
    fn tool_names_are_isolated_between_runs_even_when_ids_are_reused() {
        let mut first = CcTurnMapper::new("run-1");
        let mut second = CcTurnMapper::new("run-2");
        mapped_tools(&mut first, tool_start("same-id", "first-tool"));
        assert_eq!(mapped_tools(&mut second, tool_result("same-id"))[0].name, None);
        mapped_tools(&mut second, tool_start("same-id", "second-tool"));
        assert_eq!(mapped_tools(&mut first, tool_result("same-id"))[0].name.as_deref(), Some("first-tool"));
        assert_eq!(mapped_tools(&mut second, tool_result("same-id"))[0].name.as_deref(), Some("second-tool"));
    }

    #[test]
    fn orphan_and_duplicate_results_do_not_inherit_another_tool_name() {
        let mut mapper = CcTurnMapper::new("run-1");
        mapped_tools(&mut mapper, tool_start("known-id", "known-tool"));
        assert_eq!(mapped_tools(&mut mapper, tool_result("unknown-id"))[0].name, None);
        assert_eq!(mapped_tools(&mut mapper, tool_result("known-id"))[0].name.as_deref(), Some("known-tool"));
        assert_eq!(mapped_tools(&mut mapper, tool_result("known-id"))[0].name, None);
    }

    #[test]
    fn conflicting_tool_names_for_an_id_are_not_guessed() {
        let mut mapper = CcTurnMapper::new("run-1");
        mapped_tools(&mut mapper, tool_start("same-id", "first-tool"));
        mapped_tools(&mut mapper, tool_start("same-id", "second-tool"));
        mapped_tools(&mut mapper, tool_start("same-id", "first-tool"));
        assert_eq!(mapped_tools(&mut mapper, tool_result("same-id"))[0].name, None);
    }

    #[test]
    fn repeated_matching_starts_preserve_the_name_but_blank_fields_do_not() {
        let mut mapper = CcTurnMapper::new("run-1");
        mapped_tools(&mut mapper, tool_start("same-id", "known-tool"));
        mapped_tools(&mut mapper, tool_start("same-id", "known-tool"));
        assert_eq!(mapped_tools(&mut mapper, tool_result("same-id"))[0].name.as_deref(), Some("known-tool"));
        for (id, name) in [("", "known-tool"), (" ", "known-tool"), ("id", ""), ("id", " ")] {
            mapped_tools(&mut mapper, tool_start(id, name));
            assert_eq!(mapped_tools(&mut mapper, tool_result(id))[0].name, None);
        }
    }

    #[test]
    fn system_non_init_is_ignore() {
        let line = r#"{"type":"system","subtype":"other","session_id":"s"}"#;
        assert!(matches!(map_cc_line(line, "r-1"), CcMap::Ignore));
    }

    #[test]
    fn stream_event_non_text_delta_is_ignore() {
        let line = r#"{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"input_json_delta","partial_json":"{"}}}"#;
        assert!(matches!(map_cc_line(line, "r-1"), CcMap::Ignore));
    }

    #[test]
    fn stream_event_thinking_delta_emits_thinking_event() {
        let line = r#"{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"分析中"}}}"#;
        match map_cc_line(line, "r-1") {
            CcMap::Events(events) => match &events[0] {
                StreamEvent::Agent(agent) => match &agent.data {
                    AgentData::Thinking(thinking) => {
                        assert_eq!(thinking.delta.as_deref(), Some("分析中"));
                    }
                    other => panic!("expected Thinking, got {other:?}"),
                },
                other => panic!("expected Agent, got {other:?}"),
            },
            other => panic!("expected Events, got {other:?}"),
        }
    }

    // -- 后台任务（async subagent / background bash）的 turn 续接 ------------

    struct NullObserver;
    #[async_trait::async_trait]
    impl SessionObserver for NullObserver {
        async fn established(&self, _: &str) -> Result<(), String> { Ok(()) }
    }

    /// Drive a full `run_turn` against a mock Claude Code script.
    async fn run_mock_cc(script: &str) -> Result<TurnOutcome, TurnError> {
        let engine = ClaudeCode::new(PathBuf::from("bash"), vec![script.to_string()]);
        let req = TurnRequest::new("r-bg", "hi", ".", Arc::new(NullObserver));
        let (tx, _rx) = tokio::sync::mpsc::channel::<StreamEvent>(64);
        engine
            .run_turn(req, tx, tokio_util::sync::CancellationToken::new())
            .await
    }

    #[test]
    fn task_lifecycle_lines_map_to_tracking_variants() {
        // Shapes recorded from a live CLI capture. task_started carries the
        // ids the mapper registers by; heavy task_notification system lines
        // are INERT (enqueue-time only — release happens on delivery);
        // delivered notifications arrive as user lines with plain-string
        // `<task-notification>` content.
        let started = r#"{"type":"system","subtype":"task_started","task_id":"t1","tool_use_id":"toolu_1","task_type":"local_agent"}"#;
        match map_cc_line(started, "r-1") {
            CcMap::BackgroundStarted { task_id, tool_use_id } => {
                assert_eq!(task_id.as_deref(), Some("t1"));
                assert_eq!(tool_use_id, "toolu_1");
            }
            other => panic!("expected BackgroundStarted, got {other:?}"),
        }
        // Enqueue-time terminal lines record the queued notification — a slot
        // in the wake queue, never a release on their own (the CLI defers
        // delivery while the model is mid-round; production 2026-10-14:
        // results cut the turn with two enqueued-but-undelivered finders).
        let enq = r#"{"type":"system","subtype":"task_notification","task_id":"t1","tool_use_id":"toolu_1","status":"completed","summary":"done"}"#;
        match map_cc_line(enq, "r-1") {
            CcMap::BackgroundEnqueued { task_id, tool_use_id, status } => {
                assert_eq!(task_id.as_deref(), Some("t1"));
                assert_eq!(tool_use_id.as_deref(), Some("toolu_1"));
                assert_eq!(status, "completed");
            }
            other => panic!("expected BackgroundEnqueued, got {other:?}"),
        }
        // A terminal notice without ids still occupies a future wake, so it
        // enqueues too; wake-side resolution handles the missing ids.
        let anonymous = r#"{"type":"system","subtype":"task_notification","status":"completed"}"#;
        match map_cc_line(anonymous, "r-1") {
            CcMap::BackgroundEnqueued { task_id, tool_use_id, .. } => {
                assert_eq!(task_id, None);
                assert_eq!(tool_use_id, None);
            }
            other => panic!("expected anonymous BackgroundEnqueued, got {other:?}"),
        }
        // Non-terminal statuses and id-less task_started stay inert.
        for inert in [
            r#"{"type":"system","subtype":"task_notification","task_id":"t1","tool_use_id":"toolu_1","status":"running"}"#,
            r#"{"type":"system","subtype":"task_started","task_id":"t2"}"#,
        ] {
            assert!(matches!(map_cc_line(inert, "r-1"), CcMap::Ignore), "must be inert: {inert}");
        }
        // The delivered notification: user line, plain-string content,
        // fields extracted for the delivery-driven release.
        let delivered = json!({
            "type": "user",
            "message": {"role": "user", "content":
                "<task-notification>\n<task-id>t1</task-id>\n<tool-use-id>toolu_1</tool-use-id>\n<status>completed</status>\n<summary>done</summary>\n</task-notification>"}
        }).to_string();
        match map_cc_line(&delivered, "r-1") {
            CcMap::BackgroundDelivered { task_id, tool_use_id, status } => {
                assert_eq!(task_id.as_deref(), Some("t1"));
                assert_eq!(tool_use_id.as_deref(), Some("toolu_1"));
                assert_eq!(status, "completed");
            }
            other => panic!("expected BackgroundDelivered, got {other:?}"),
        }
        // Legacy delivery without <tool-use-id>: task_id rides for correlation.
        let legacy = json!({
            "type": "user",
            "message": {"role": "user", "content":
                "<task-notification>\n<task-id>t1</task-id>\n<status>completed</status>\n</task-notification>"}
        }).to_string();
        match map_cc_line(&legacy, "r-1") {
            CcMap::BackgroundDelivered { task_id, tool_use_id, status } => {
                assert_eq!(task_id.as_deref(), Some("t1"));
                assert_eq!(tool_use_id, None);
                assert_eq!(status, "completed");
            }
            other => panic!("expected BackgroundDelivered, got {other:?}"),
        }
        // A delivered payload without <status> cannot release (the release
        // rule is terminal-status-driven): reads as not-a-notification.
        let statusless = json!({
            "type": "user",
            "message": {"role": "user", "content":
                "<task-notification>\n<task-id>t1</task-id>\n<tool-use-id>toolu_1</tool-use-id>\n</task-notification>"}
        }).to_string();
        assert!(matches!(map_cc_line(&statusless, "r-1"), CcMap::Ignore));
    }

    #[test]
    fn enqueue_side_notification_does_not_release_only_delivery_does() {
        // The wait releases on the delivered user `<task-notification>`, never
        // on the enqueue-time system line — and plain-string content only,
        // so tool results echoing notification text (a CR reading this very
        // source) can never impersonate a delivery.
        let mut mapper = CcTurnMapper::new("r-1");
        let started = r#"{"type":"system","subtype":"task_started","task_id":"t-bg","tool_use_id":"toolu_bg","task_type":"local_agent"}"#;
        assert!(matches!(mapper.map_line(started), CcMap::Ignore));
        assert_eq!(mapper.background_pending(), 1);
        let enqueued = r#"{"type":"system","subtype":"task_notification","task_id":"t-bg","tool_use_id":"toolu_bg","status":"completed"}"#;
        assert!(matches!(mapper.map_line(enqueued), CcMap::Ignore));
        assert_eq!(mapper.background_pending(), 1, "enqueue must keep the wait");
        let delivered = json!({
            "type": "user",
            "message": {"role": "user", "content":
                "<task-notification>\n<task-id>t-bg</task-id>\n<tool-use-id>toolu_bg</tool-use-id>\n<status>completed</status>\n</task-notification>"}
        }).to_string();
        assert!(matches!(mapper.map_line(&delivered), CcMap::Ignore));
        assert_eq!(mapper.background_pending(), 0, "delivery releases");
    }

    #[test]
    fn notification_text_in_tool_results_does_not_read_as_delivery() {
        // Foreground tool output that carries the notification payload — a CR
        // reading this fixture, a grep over an tasks/ directory — rides in
        // block arrays; only plain-string user content is a delivery.
        let mut mapper = CcTurnMapper::new("r-1");
        let started = r#"{"type":"system","subtype":"task_started","task_id":"t-bg","tool_use_id":"toolu_e","task_type":"local_agent"}"#;
        assert!(matches!(mapper.map_line(started), CcMap::Ignore));
        let read_start = r#"{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_r","name":"Read","input":{"path":"mock fixture"}}]}}"#;
        assert!(matches!(mapper.map_line(read_start), CcMap::Events(_)));
        let echo = json!({
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_r", "is_error": false, "content": [
                {"type": "text", "text": "<task-notification>\n<task-id>t-bg</task-id>\n<tool-use-id>toolu_e</tool-use-id>\n<status>completed</status>\n</task-notification>"}
            ]}]}
        }).to_string();
        assert!(matches!(mapper.map_line(&echo), CcMap::Events(_)), "tool_result array must map as events");
        assert_eq!(mapper.background_pending(), 1, "tool output must not release the wait");
    }

    #[test]
    fn inline_tool_result_for_a_backgrounded_id_releases_the_wait() {
        // Claude Code 2.1.199 auto-backgrounds a long-running foreground Bash
        // (no run_in_background from the model): it emits task_started, then an
        // enqueue-time task_notification, then delivers the real output as an
        // ORDINARY block-array tool_result carrying the SAME tool_use_id — and
        // never surfaces a `<task-notification>` user line nor a `system/init`
        // wake. Recorded live (repro6): sleep 25 → task_started(toolu),
        // task_notification(completed), user tool_result(id=toolu), result.
        // The delivery IS that tool_result, so a result for a registered
        // background id must release the wait — otherwise the run's `result`
        // is swallowed forever and the turn hangs until the idle watchdog.
        let mut mapper = CcTurnMapper::new("r-1");
        let start = r#"{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_bg","name":"Bash","input":{"command":"sleep 25; echo DONE","timeout":120000}}]}}"#;
        assert!(matches!(mapper.map_line(start), CcMap::Events(_)));
        let started = r#"{"type":"system","subtype":"task_started","task_id":"t-bg","tool_use_id":"toolu_bg","task_type":"local_bash"}"#;
        assert!(matches!(mapper.map_line(started), CcMap::Ignore));
        assert_eq!(mapper.background_pending(), 1, "task_started registers the wait");
        let enqueued = r#"{"type":"system","subtype":"task_notification","task_id":"t-bg","tool_use_id":"toolu_bg","status":"completed"}"#;
        assert!(matches!(mapper.map_line(enqueued), CcMap::Ignore));
        assert_eq!(mapper.background_pending(), 1, "enqueue alone must not release");
        // The real output, delivered inline as an ordinary tool_result with the
        // backgrounded id. This is the delivery this engine actually surfaces.
        let delivered = json!({
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_bg", "is_error": false, "content": [
                {"type": "text", "text": "DONE"}
            ]}]}
        }).to_string();
        assert!(matches!(mapper.map_line(&delivered), CcMap::Events(_)), "still forwarded as a tool event");
        assert_eq!(mapper.background_pending(), 0, "inline delivery of a backgrounded id releases the wait");
    }

    #[test]
    fn inline_delivery_cancels_its_wake_queue_alias() {
        // The enqueue-time task_notification parked an alias in the wake queue.
        // Once the inline tool_result releases the wait directly, that alias is
        // spent: a later system/init wake must not pop it and drain a DIFFERENT
        // task's still-pending wait.
        let mut mapper = CcTurnMapper::new("r-1");
        for (t, u) in [("t-x", "toolu_x"), ("t-y", "toolu_y")] {
            let started = format!(
                r#"{{"type":"system","subtype":"task_started","task_id":"{t}","tool_use_id":"{u}","task_type":"local_bash"}}"#
            );
            assert!(matches!(mapper.map_line(&started), CcMap::Ignore));
            let noticed = format!(
                r#"{{"type":"system","subtype":"task_notification","task_id":"{t}","tool_use_id":"{u}","status":"completed"}}"#
            );
            assert!(matches!(mapper.map_line(&noticed), CcMap::Ignore));
        }
        assert_eq!(mapper.background_pending(), 2);
        // x delivered inline (ordinary tool_result) — releases x and cancels
        // its queued alias.
        let delivered_x = json!({
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_x", "is_error": false, "content": "x done"}]}
        }).to_string();
        assert!(matches!(mapper.map_line(&delivered_x), CcMap::Events(_)));
        assert_eq!(mapper.background_pending(), 1, "x released by its inline delivery");
        // A wake must release y — impossible if x's spent alias still fronts it.
        let wake = r#"{"type":"system","subtype":"init","session_id":"cc-sess-wake"}"#;
        assert!(matches!(mapper.map_line(wake), CcMap::SessionId(_)));
        assert_eq!(mapper.background_pending(), 0, "wake releases y, not x's spent alias");
    }

    #[test]
    fn inline_delivery_cancels_the_matching_alias_not_a_taskidless_one() {
        // Two auto-backgrounded tasks. Task A's task_notification OMITS task_id
        // (some CLI recordings do), so its queued alias is {task_id: None, …}.
        // When B is delivered inline, cancelling B's alias must key on B's own
        // id — it must NOT match A's notice merely because A's task_id is also
        // absent. Otherwise the following wake pops B's spent slot, A's wait is
        // stranded, and the turn hangs until the idle watchdog.
        let mut mapper = CcTurnMapper::new("r-1");
        for u in ["toolu_a", "toolu_b"] {
            let started = format!(
                r#"{{"type":"system","subtype":"task_started","tool_use_id":"{u}","task_type":"local_bash"}}"#
            );
            assert!(matches!(mapper.map_line(&started), CcMap::Ignore));
        }
        assert_eq!(mapper.background_pending(), 2);
        // A's completion notification omits task_id; B's carries both keys.
        let notice_a = r#"{"type":"system","subtype":"task_notification","tool_use_id":"toolu_a","status":"completed"}"#;
        assert!(matches!(mapper.map_line(notice_a), CcMap::Ignore));
        let notice_b = r#"{"type":"system","subtype":"task_notification","task_id":"t-b","tool_use_id":"toolu_b","status":"completed"}"#;
        assert!(matches!(mapper.map_line(notice_b), CcMap::Ignore));
        assert_eq!(mapper.background_pending(), 2, "enqueue alone must not release");
        // B delivered inline — releases B and must cancel B's alias, not A's.
        let delivered_b = json!({
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_b", "is_error": false, "content": "b done"}]}
        }).to_string();
        assert!(matches!(mapper.map_line(&delivered_b), CcMap::Events(_)));
        assert_eq!(mapper.background_pending(), 1, "B released by its inline delivery");
        // The wake must release A through A's still-queued alias. If B's cancel
        // wrongly removed A's alias, the wake pops B's spent slot and A hangs.
        let wake = r#"{"type":"system","subtype":"init","session_id":"cc-sess-wake"}"#;
        assert!(matches!(mapper.map_line(wake), CcMap::SessionId(_)));
        assert_eq!(mapper.background_pending(), 0, "wake releases A, not B's spent alias");
    }

    #[test]
    fn background_launch_requires_evidence_not_just_the_request() {
        // A `run_in_background: true` request alone is not a launch: a denied or
        // invalid call never emits task_started or a receipt, and registering
        // it would wedge the turn on work that will never notify.
        let mut mapper = CcTurnMapper::new("r-1");
        let start = r#"{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_d","name":"Bash","input":{"command":"sleep 1","run_in_background":true}}]}}"#;
        assert!(matches!(mapper.map_line(start), CcMap::Events(_)));
        assert_eq!(mapper.background_pending(), 0, "request alone must not register");
        // The authoritative launch evidence does.
        let launched = r#"{"type":"system","subtype":"task_started","task_id":"t1","tool_use_id":"toolu_d","task_type":"local_bash"}"#;
        assert!(matches!(mapper.map_line(launched), CcMap::Ignore));
        assert_eq!(mapper.background_pending(), 1);
    }

    #[test]
    fn receipt_registration_requires_initiating_tool_and_whole_text_prefix() {
        // Receipt markers seen inside other tools' output — CRs reading this
        // very source, grep over logs — must not read as background launches.
        let mut mapper = CcTurnMapper::new("r-1");
        let read_start = r#"{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_r","name":"Read","input":{"path":"claude_code.rs"}}]}}"#;
        assert!(matches!(mapper.map_line(read_start), CcMap::Events(_)));
        let read_result = r#"{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"toolu_r","content":"core/src/engine/claude_code.rs: text.contains(\"Async agent launched successfully\")"}]}}"#;
        assert!(matches!(mapper.map_line(read_result), CcMap::Events(_)));
        assert_eq!(mapper.background_pending(), 0, "substring in a Read result must not register");
        // A Bash result that merely mentions the sentence mid-text: excluded.
        let bash_start = r#"{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_g","name":"Bash","input":{"command":"grep receipt src"}}]}}"#;
        assert!(matches!(mapper.map_line(bash_start), CcMap::Events(_)));
        let grep_result = r#"{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"toolu_g","content":"logs: text.contains(\"Command running in background\")"}]}}"#;
        assert!(matches!(mapper.map_line(grep_result), CcMap::Events(_)));
        assert_eq!(mapper.background_pending(), 0, "mid-text mention must not register");
        // The recorded receipt shape — the whole result, from an initiating
        // tool — registers.
        let bg_start = r#"{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_b","name":"Bash","input":{"command":"sleep 1","run_in_background":true}}]}}"#;
        assert!(matches!(mapper.map_line(bg_start), CcMap::Events(_)));
        let receipt = r#"{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"toolu_b","content":"Command running in background with ID: bp1. Output is being written to: /tmp/x. You will be notified when it completes.","is_error":false}]}}"#;
        assert!(matches!(mapper.map_line(receipt), CcMap::Events(_)));
        assert_eq!(mapper.background_pending(), 1);
    }

    #[test]
    fn task_id_correlation_releases_waits_from_deliveries_without_tool_id() {
        let mut mapper = CcTurnMapper::new("r-1");
        let started = r#"{"type":"system","subtype":"task_started","task_id":"t-legacy","tool_use_id":"toolu_leg","task_type":"local_agent"}"#;
        assert!(matches!(mapper.map_line(started), CcMap::Ignore));
        assert_eq!(mapper.background_pending(), 1);
        // Recorded pre-0.3.269 shape: the delivered notification omits
        // <tool-use-id>; task_id must release the correlated wait.
        let legacy_delivered = json!({
            "type": "user",
            "message": {"role": "user", "content":
                "<task-notification>\n<task-id>t-legacy</task-id>\n<status>completed</status>\n</task-notification>"}
        }).to_string();
        assert!(matches!(mapper.map_line(&legacy_delivered), CcMap::Ignore));
        assert_eq!(mapper.background_pending(), 0, "task_id must release the correlated wait");
    }

    #[test]
    fn wake_inits_release_one_enqueued_notification_each() {
        // Real-capture protocol (2026-10-14 production + lab): this CLI
        // never surfaces the delivered <task-notification> on stdout —
        // completions arrive as enqueue-time system lines, and each
        // auto-wake round (a new system/init) hands the model exactly ONE
        // queued notification. Draining one wait per wake is the ONLY way
        // this pending set can ever empty, so it is the only way the turn
        // can ever finalize.
        let mut mapper = CcTurnMapper::new("r-1");
        for (t, u) in [("t-a", "toolu_a"), ("t-b", "toolu_b"), ("t-c", "toolu_c")] {
            let started = format!(
                r#"{{"type":"system","subtype":"task_started","task_id":"{t}","tool_use_id":"{u}","task_type":"local_agent"}}"#
            );
            assert!(matches!(mapper.map_line(&started), CcMap::Ignore));
        }
        assert_eq!(mapper.background_pending(), 3);
        for (t, u) in [("t-a", "toolu_a"), ("t-b", "toolu_b"), ("t-c", "toolu_c")] {
            let noticed = format!(
                r#"{{"type":"system","subtype":"task_notification","task_id":"{t}","tool_use_id":"{u}","status":"completed"}}"#
            );
            assert!(matches!(mapper.map_line(&noticed), CcMap::Ignore));
            assert_eq!(mapper.background_pending(), 3, "enqueue must keep the wait");
        }
        // Three wakes deliver the three queued notifications — one each. C
        // was enqueued before B's wake yet delivered a round later, so the
        // pops must not race ahead of arrivals: one release per wake, no
        // more, whoever fronts the queue.
        for expected in [2, 1, 0] {
            let wake = r#"{"type":"system","subtype":"init","session_id":"cc-sess-wake"}"#;
            assert!(matches!(mapper.map_line(wake), CcMap::SessionId(_)));
            assert_eq!(mapper.background_pending(), expected, "one wake releases exactly one");
        }
        // A fourth wake (round running with nothing left queued) must be a
        // no-op — it can neither release a wait that does not exist nor
        // fabricate a stale-marker event.
        let wake = r#"{"type":"system","subtype":"init","session_id":"cc-sess-wake"}"#;
        assert!(matches!(mapper.map_line(wake), CcMap::SessionId(_)));
        assert_eq!(mapper.background_pending(), 0);
    }

    #[test]
    fn delivered_user_line_cancels_its_alias_in_the_wake_queue() {
        // Engines that DO surface deliveries (user <task-notification> lines)
        // resolve their wait directly — and must cancel the queued alias from
        // the enqueue-time system line, or the following wake pops the spent
        // alias instead, and the notification that wake actually delivered is
        // left owning a wait that stays pending forever.
        let mut mapper = CcTurnMapper::new("r-1");
        for (t, u) in [("t-x", "toolu_x"), ("t-y", "toolu_y")] {
            let started = format!(
                r#"{{"type":"system","subtype":"task_started","task_id":"{t}","tool_use_id":"{u}","task_type":"local_agent"}}"#
            );
            assert!(matches!(mapper.map_line(&started), CcMap::Ignore));
        }
        assert_eq!(mapper.background_pending(), 2);
        for (t, u) in [("t-x", "toolu_x"), ("t-y", "toolu_y")] {
            let noticed = format!(
                r#"{{"type":"system","subtype":"task_notification","task_id":"{t}","tool_use_id":"{u}","status":"completed"}}"#
            );
            assert!(matches!(mapper.map_line(&noticed), CcMap::Ignore));
            assert_eq!(mapper.background_pending(), 2, "enqueue must keep the wait");
        }
        // x is delivered as a user line (CLI versions that surface the
        // delivery on stdout): releases x directly, cancels its alias.
        let delivered = json!({
            "type": "user",
            "message": {"role": "user", "content":
                "<task-notification>\n<task-id>t-x</task-id>\n<tool-use-id>toolu_x</tool-use-id>\n<status>completed</status>\n</task-notification>"}
        }).to_string();
        assert!(matches!(mapper.map_line(&delivered), CcMap::Ignore));
        assert_eq!(mapper.background_pending(), 1, "user line releases its own wait");
        // The wake that delivers y must release y — impossible while x's
        // spent alias still fronts the queue.
        let wake = r#"{"type":"system","subtype":"init","session_id":"cc-sess-wake"}"#;
        assert!(matches!(mapper.map_line(wake), CcMap::SessionId(_)));
        assert_eq!(
            mapper.background_pending(), 0,
            "wake must release y, not pop x's spent alias"
        );
    }

    #[test]
    fn stale_delivery_for_unknown_task_emits_marked_event() {
        // A notification whose tool call id was never registered this run
        // (a replay from a previous killed turn) must not be silent: forward
        // it downstream with a visible marker.
        let mut mapper = CcTurnMapper::new("r-1");
        let stale = json!({
            "type": "user",
            "message": {"role": "user", "content":
                "<task-notification>\n<task-id>t-stale</task-id>\n<tool-use-id>toolu_old</tool-use-id>\n<status>completed</status>\n<summary>old finder result</summary>\n</task-notification>"}
        }).to_string();
        match mapper.map_line(&stale) {
            CcMap::Events(events) => {
                let StreamEvent::Chat(chat) = &events[0] else { panic!("expected chat delta") };
                let text = chat.delta_text.as_deref().unwrap_or("");
                assert!(text.contains(STALE_DELIVERY_MARKER), "must carry the marker: {text}");
                assert!(text.contains("t-stale"), "must surface the task id: {text}");
            }
            other => panic!("expected marked event, got {other:?}"),
        }
        assert_eq!(mapper.background_pending(), 0, "stale delivery must not register");
    }

    #[tokio::test]
    async fn turn_waits_for_pending_background_tasks_before_finalizing() {
        // A `result` that lands while a background task is still pending is
        // intermediate: the CLI stays alive, auto-wakes on the
        // task_notification, and ends the turn again with the real final text.
        let script = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixtures/mock_cc_background_continuation.sh"
        );
        let outcome = run_mock_cc(script).await.expect("turn must end on the second result");
        assert_eq!(outcome.final_text.as_deref(), Some("final answer after background agent"));
        assert_eq!(outcome.engine_session_id.as_deref(), Some("cc-sess-bg"));
    }

    #[tokio::test]
    async fn turn_finalizes_when_background_result_is_delivered_inline() {
        // The 2026-10-16 incident, end to end: an auto-backgrounded command
        // whose completion the engine delivers as an ordinary tool_result (not
        // a `<task-notification>` line, not a wake). The inline delivery
        // releases the wait, so the single `result` finalizes the turn instead
        // of being swallowed until the idle watchdog kills the engine.
        let script = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixtures/mock_cc_bg_inline_delivery.sh"
        );
        let outcome = run_mock_cc(script)
            .await
            .expect("inline delivery must release the wait so result finalizes");
        assert_eq!(outcome.final_text.as_deref(), Some("the background command printed DONE"));
        assert_eq!(outcome.engine_session_id.as_deref(), Some("cc-sess-inline"));
    }

    #[tokio::test]
    async fn engine_eof_with_pending_background_tasks_reads_as_explicit_error() {
        // Stdout EOF while background work is pending must not read as a
        // successful turn with the intermediate result as its final text.
        let script = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixtures/mock_cc_bg_pending_eof.sh"
        );
        let error = run_mock_cc(script)
            .await
            .expect_err("EOF with a pending background task must not read as success");
        let text = error.to_string();
        assert!(text.contains("pending background task"), "unexpected error: {text}");
    }

    #[tokio::test]
    async fn denied_background_launch_does_not_wedge_the_turn() {
        // A run_in_background request refused before launch emits no
        // task_started and no receipt; the turn must finalize on its single
        // result instead of swallowing it until the deadline.
        let script = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixtures/mock_cc_bg_denied_launch.sh"
        );
        let outcome = run_mock_cc(script).await.expect("denied launch must not wedge");
        assert_eq!(outcome.final_text.as_deref(), Some("not launched"));
    }

    #[tokio::test]
    async fn notification_without_tool_id_still_releases_the_wait() {
        // Pre-0.3.269 CLI recordings omit tool_use_id from the delivered
        // notification; the task_id correlation from task_started must
        // release the wait.
        let script = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixtures/mock_cc_bg_taskid_notification.sh"
        );
        let outcome = run_mock_cc(script).await.expect("legacy notification must release");
        assert_eq!(outcome.final_text.as_deref(), Some("released via task_id"));
    }

    #[tokio::test]
    async fn result_landing_between_enqueue_and_delivery_stays_intermediate() {
        // Production incident 2026-10-14 replay: two finders completed
        // mid-round (enqueue-time system lines only), the model ended its
        // round with "still waiting for B/C", and the queued deliveries had
        // not landed. That round's result MUST be swallowed — not finalized —
        // and the turn must end on the final round's result after every
        // delivery has landed.
        let script = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixtures/mock_cc_bg_late_delivered_notifications.sh"
        );
        let outcome = run_mock_cc(script)
            .await
            .expect("turn must run until the queued deliveries landed");
        assert_eq!(outcome.final_text.as_deref(), Some("summary table for all three finders"));
    }

    #[tokio::test]
    async fn enqueue_only_protocol_finalizes_through_wake_rounds() {
        // Second production incident 2026-10-14 (session e6f0d46d): every
        // event crossed to BCS — launches, progress, all four results'
        // streamed texts — yet the turn never sent a final: the engine
        // never emits the user <task-notification> lines the release logic
        // was waiting on, so the pending set could never drain and every
        // result stayed swallowed. With wake-driven release the same
        // protocol must run to the real final result.
        let script = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixtures/mock_cc_bg_enqueue_wakes.sh"
        );
        let outcome = tokio::time::timeout(
            std::time::Duration::from_secs(5),
            run_mock_cc(script),
        )
        .await
        .expect("turn must not wedge on an un-drainable pending set")
        .expect("turn must finalize on the last round's result");
        assert_eq!(outcome.final_text.as_deref(), Some("summary table for all three finders"));
    }

    #[tokio::test]
    async fn idle_stream_with_pending_background_fails_explicitly() {
        // The hang shape the same incident left behind: the engine goes
        // permanently silent with a pending background task and neither a
        // result nor a notification will ever come. The sliding idle
        // watchdog must end the turn with a named error that reports the
        // stalled background count — not wedge until the run deadline with
        // BCS waiting on a final that never arrives.
        let script = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixtures/mock_cc_bg_idle_forever.sh"
        );
        let mut req = TurnRequest::new("r-idle", "hi", ".", Arc::new(NullObserver));
        req.idle_timeout = Some(std::time::Duration::from_millis(300));
        let engine = ClaudeCode::new(PathBuf::from("bash"), vec![script.to_string()]);
        let (tx, _rx) = tokio::sync::mpsc::channel::<StreamEvent>(64);
        let turn = engine.run_turn(req, tx, tokio_util::sync::CancellationToken::new());
        let error = tokio::time::timeout(std::time::Duration::from_secs(5), turn)
            .await
            .expect("watchdog must fire, not wedge")
            .expect_err("a quiet stream must fail the turn");
        let text = error.to_string();
        assert!(text.contains("idle"), "must name the idle watchdog: {text}");
        assert!(
            text.contains("1 pending background task"),
            "must report the stalled background count: {text}"
        );
    }
}
