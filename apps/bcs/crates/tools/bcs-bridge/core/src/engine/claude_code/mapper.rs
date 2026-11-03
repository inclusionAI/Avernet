use std::collections::{HashMap, HashSet, VecDeque};
use bcs_protocol::stream::{AgentData, StreamEvent, ToolData, ToolPhase};
use serde_json::Value;
use crate::sse;
use super::permission::ANSWERED_ASK_USER_MARK;

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
    BackgroundEnqueued { task_id: Option<String>, tool_use_id: Option<String> },
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
pub(super) struct CcTurnMapper<'a> {
    run_id: &'a str,
    track_consumption: bool,
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
pub(super) struct CcQueuedNotice {
    task_id: Option<String>,
    tool_use_id: Option<String>,
}

impl<'a> CcTurnMapper<'a> {
    pub(super) fn new(run_id: &'a str) -> Self {
        Self {
            run_id,
            track_consumption: true,
            pending_tools: HashMap::new(),
            background: HashSet::new(),
            task_ids: HashMap::new(),
            enqueued: VecDeque::new(),
        }
    }

    pub(super) fn disable_consumption_tracking(&mut self) { self.track_consumption = false; }

    /// Number of background tasks still pending completion.
    pub(super) fn background_pending(&self) -> usize {
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

    pub(super) fn map_line(&mut self, line: &str) -> CcMap {
        let mapped = map_cc_line(line, self.run_id);
        if !self.track_consumption && matches!(mapped, CcMap::BackgroundStarted { .. } | CcMap::BackgroundEnqueued { .. } | CcMap::BackgroundDelivered { .. }) {
            return CcMap::Ignore;
        }
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
                if self.track_consumption { self.consume_wake(); }
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
                            if self.track_consumption && matches!(name.as_deref(), Some("Bash") | Some("Task") | Some("Agent"))
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
pub(super) const STALE_DELIVERY_MARKER: &str =
    "\n--------- replayed from a previous run (background task completed before this turn started) -------";

/// Whether a tool result is the async-launch receipt of a background task
/// (Agent/Task: "Async agent launched successfully"; background Bash:
/// "Command running in background") rather than a completed tool call.
pub(super) fn async_launch_receipt(result: &Option<Value>) -> bool {
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

pub(super) fn receipt_text(text: &str) -> bool {
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

pub(super) fn map_system(v: &Value) -> CcMap {
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
            }
        }
        _ => CcMap::Ignore,
    }
}

pub(super) fn non_empty(s: Option<&str>) -> Option<&str> {
    s.filter(|s| !s.trim().is_empty())
}

/// Whether a task status is terminal: only terminal statuses release a
/// background wait. `running`/`pending` progress reports must keep it.
pub(super) fn terminal_task_status(status: &str) -> bool {
    matches!(status, "completed" | "failed" | "stopped" | "killed")
}

pub(super) fn map_stream_event(v: &Value, run_id: &str) -> CcMap {
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

pub(super) fn map_assistant(v: &Value, run_id: &str) -> CcMap {
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
pub(super) fn tool_result_carries_mark(content: &Value) -> bool {
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
pub(super) fn delivered_notification(content: &str) -> Option<(Option<&str>, Option<&str>, String)> {
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
pub(super) fn xml_field<'a>(text: &'a str, tag: &str) -> Option<&'a str> {
    let open = format!("<{tag}>");
    let close = format!("</{tag}>");
    let start = text.find(&open)? + open.len();
    let end = text[start..].find(&close)? + start;
    non_empty(Some(&text[start..end]))
}

pub(super) fn map_user(v: &Value, run_id: &str) -> CcMap {
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

pub(super) fn map_result(v: &Value) -> CcMap {
    let subtype = v.get("subtype").and_then(|x| x.as_str()).unwrap_or("");
    let is_error = v.get("is_error").and_then(Value::as_bool) == Some(true);
    if subtype == "success" && !is_error {
        let text = v.get("result").and_then(|x| x.as_str()).unwrap_or("").to_string();
        return CcMap::Final(text);
    }
    // Keep actionable upstream causes without echoing arbitrary error payloads:
    // result/errors can contain credentials, prompts, or provider response bodies.
    // Native CLI failures may expose only api_error_status and prefixed text.
    let cause = v.get("api_error_status").and_then(Value::as_u64).and_then(upstream_status_note)
        .or_else(|| v.get("error").and_then(|error| upstream_error_note(error, 3)))
        .or_else(|| v.get("errors").and_then(Value::as_array)
            .and_then(|errors| errors.iter().take(8).find_map(|error| upstream_error_note(error, 3))))
        .or_else(|| v.get("result").and_then(|error| upstream_error_note(error, 3)));
    if let Some(cause) = cause { return CcMap::Failed(cause.into()); }
    if is_error && matches!(subtype, "success" | "") {
        return CcMap::Failed("engine_result_error".into());
    }
    CcMap::Failed(sanitize_result_note(subtype))
}

/// Only allowlisted, constant summaries cross the BCN boundary. Recognize native
/// error objects and CLI-rendered API errors; never quote their free-form text.
fn upstream_error_note(value: &Value, depth: usize) -> Option<&'static str> {
    if depth == 0 { return None; }
    if let Some(text) = value.as_str() {
        let text = text.trim();
        if let Some(status) = upstream_error_status(text) { return upstream_status_note(status); }
        let message = text.strip_prefix("API Error: ").unwrap_or(text);
        let code = if let Some(rejected) = message.strip_prefix("Request rejected (") {
            rejected.split_once(')')?.0
        } else {
            message.strip_prefix("HTTP ").unwrap_or(message)
                .split(|c: char| c.is_ascii_whitespace() || c == ':').next()?
        };
        if code.len() == 3 {
            if let Some(note) = code.parse().ok().and_then(upstream_status_note) { return Some(note); }
        }
        if text.starts_with('{') && text.len() <= 4096 {
            let object: Value = serde_json::from_str(text).ok()?;
            return upstream_error_note(&object, depth - 1);
        }
        return None;
    }
    let object = value.as_object()?;
    object.get("type").and_then(Value::as_str).and_then(upstream_error_status).and_then(upstream_status_note)
        .or_else(|| object.get("error").and_then(|error| upstream_error_note(error, depth - 1)))
        .or_else(|| object.get("status").and_then(Value::as_u64).and_then(upstream_status_note))
        .or_else(|| object.get("message").and_then(|message| upstream_error_note(message, depth - 1)))
}

fn upstream_error_status(kind: &str) -> Option<u64> {
    match kind {
        "invalid_request_error" => Some(400),
        "authentication_error" => Some(401),
        "permission_error" => Some(403),
        "request_too_large" => Some(413),
        "rate_limit_error" | "rate_limit" => Some(429),
        "api_error" => Some(500),
        "overloaded_error" => Some(529),
        _ => None,
    }
}

fn upstream_status_note(status: u64) -> Option<&'static str> {
    match status {
        400 => Some("upstream invalid_request_error (HTTP 400): model provider rejected the request; check model and input limits"),
        401 => Some("upstream authentication_error (HTTP 401): model provider authentication failed; check credentials"),
        403 => Some("upstream permission_error (HTTP 403): model provider denied access; check model permissions"),
        413 => Some("upstream request_too_large (HTTP 413): model request exceeds the provider size limit"),
        429 => Some("upstream rate_limit_error (HTTP 429): model provider rate limit exceeded; retry later"),
        500 => Some("upstream api_error (HTTP 500): model provider internal error; retry later"),
        503 => Some("upstream service_unavailable (HTTP 503): model provider unavailable; retry later"),
        529 => Some("upstream overloaded_error (HTTP 529): model provider overloaded; retry later"),
        _ => None,
    }
}

/// 只保留引擎错误类别（有限标识符：字母数字/下划线/连字符），剥掉其它。
pub(super) fn sanitize_result_note(subtype: &str) -> String {
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
pub(super) fn map_control_request(v: &Value, _run_id: &str) -> CcMap {
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

