//! Pure fold of bridge run events into an A2A Task and its update events.
//!
//! Result vs. progress (spec §3.7): the conclusion is the `response` artifact;
//! progress narration, tools and thinking ride on WORKING status updates.
//! Engines that tag deltas with a message phase (codex `commentary` /
//! `final_answer`) are separated while streaming; others stream every delta
//! into the artifact and the authoritative final text replaces it at the end.
use bcs_a2a_protocol::{meta, Artifact, Message, Metadata, Part, Role, StreamResponse, Task,
    TaskArtifactUpdateEvent, TaskState, TaskStatus, TaskStatusUpdateEvent};
use bcs_protocol::stream::{AgentData, ChatEvent, ChatState, InteractionKind, InteractionPhase, StreamEvent};
use serde_json::{json, Value};

pub const RESPONSE_ARTIFACT_ID: &str = "response";
pub const PENDING_INTERACTIONS: &str = meta::PENDING_INTERACTIONS;
/// Bound on `Task.history`; the oldest narration is dropped first.
const MAX_HISTORY: usize = 256;

pub struct Projection {
    task: Task,
    context_id: String,
    artifact_started: bool,
    artifact_text: String,
    commentary: Option<(String, String)>,
    pending: Vec<(String, InteractionKind, Value)>,
    message_seq: u64,
}

impl Projection {
    pub fn new(task_id: &str, context_id: &str, user_message: Message) -> Self {
        let mut status = TaskStatus::new(TaskState::Submitted);
        status.timestamp = Some(now_iso());
        let task = Task { id: task_id.into(), context_id: Some(context_id.into()), status,
            artifacts: Vec::new(), history: vec![user_message], metadata: None };
        Self { task, context_id: context_id.into(), artifact_started: false, artifact_text: String::new(),
            commentary: None, pending: Vec::new(), message_seq: 0 }
    }

    /// Rebuild from a persisted snapshot (read-only use: GetTask after restart).
    pub fn from_snapshot(task: Task) -> Self {
        let context_id = task.context_id.clone().unwrap_or_default();
        let artifact_text = task.artifacts.iter().find(|a| a.artifact_id == RESPONSE_ARTIFACT_ID).map(Artifact::text).unwrap_or_default();
        Self { artifact_started: !artifact_text.is_empty(), artifact_text, task, context_id,
            commentary: None, pending: Vec::new(), message_seq: 0 }
    }

    pub fn task(&self) -> &Task { &self.task }
    pub fn state(&self) -> TaskState { self.task.status.state }
    pub fn pending_interactions(&self) -> Vec<String> { self.pending.iter().map(|(id, _, _)| id.clone()).collect() }

    pub fn pending_interaction(&self, id: &str) -> Option<(InteractionKind, Value)> {
        self.pending.iter().find(|(pending, _, _)| pending == id).map(|(_, kind, raw)| (*kind, raw.clone()))
    }

    /// The initial snapshot followed by the transition to WORKING.
    pub fn start(&mut self) -> Vec<StreamResponse> {
        let initial = StreamResponse::Task(self.task.clone());
        let working = self.set_status(TaskState::Working, None, None);
        vec![initial, working]
    }

    /// Fold one run event; returns the A2A events it produces, in order.
    pub fn apply(&mut self, event: &StreamEvent) -> Vec<StreamResponse> {
        if self.state().is_terminal() { return Vec::new(); }
        match event {
            StreamEvent::Chat(chat) => self.apply_chat(chat),
            StreamEvent::Agent(agent) => {
                let (kind, frame) = match &agent.data {
                    AgentData::Tool(data) => ("tool", serde_json::to_value(data).unwrap_or(Value::Null)),
                    AgentData::Thinking(data) => ("thinking", serde_json::to_value(data).unwrap_or(Value::Null)),
                    AgentData::Lifecycle(data) => ("lifecycle", serde_json::to_value(data).unwrap_or(Value::Null)),
                    _ => return Vec::new(),
                };
                let mut out = self.flush_commentary();
                out.push(self.progress(metadata([(meta::KIND, json!(kind)), (meta::ENGINE_FRAME, frame)])));
                out
            }
            StreamEvent::Interaction(interaction) => {
                let mut out = self.flush_commentary();
                let kind = interaction_kind(interaction.kind);
                match interaction.phase {
                    InteractionPhase::Requested => {
                        if !self.pending.iter().any(|(id, _, _)| id == &interaction.interaction_id) {
                            self.pending.push((interaction.interaction_id.clone(), interaction.kind, interaction.raw.clone()));
                        }
                        self.sync_pending_metadata();
                        let prompt = self.agent_message(interaction_prompt(interaction.kind, &interaction.raw));
                        self.push_history(prompt.clone());
                        out.push(self.set_status(TaskState::InputRequired, Some(prompt), Some(metadata([
                            (meta::KIND, json!("interaction.requested")),
                            (meta::INTERACTION_ID, json!(interaction.interaction_id)),
                            (meta::INTERACTION_KIND, json!(kind)),
                            (meta::ENGINE_FRAME, interaction.raw.clone()),
                        ]))));
                    }
                    InteractionPhase::Resolved => {
                        self.pending.retain(|(id, _, _)| id != &interaction.interaction_id);
                        self.sync_pending_metadata();
                        let state = if self.pending.is_empty() { TaskState::Working } else { TaskState::InputRequired };
                        out.push(self.set_status(state, None, Some(metadata([
                            (meta::KIND, json!("interaction.resolved")),
                            (meta::INTERACTION_ID, json!(interaction.interaction_id)),
                            (meta::INTERACTION_KIND, json!(kind)),
                            (meta::ENGINE_FRAME, interaction.raw.clone()),
                        ]))));
                    }
                }
                out
            }
            StreamEvent::Ping { .. } | StreamEvent::Unknown { .. } => Vec::new(),
        }
    }

    /// The run ended without a terminal chat event.
    pub fn finish_without_terminal(&mut self, reason: &str) -> Vec<StreamResponse> {
        if self.state().is_terminal() { return Vec::new(); }
        self.fail(reason, "runtime_error")
    }

    fn apply_chat(&mut self, chat: &ChatEvent) -> Vec<StreamResponse> {
        match chat.state {
            ChatState::Delta => {
                let Some(delta) = chat.delta_text.as_deref().filter(|d| !d.is_empty()) else { return Vec::new() };
                match crate::sse::delta_segment(chat) {
                    Some((item_id, Some("commentary"))) => {
                        let mut out = match &self.commentary {
                            Some((current, _)) if current != item_id => self.flush_commentary(),
                            _ => Vec::new(),
                        };
                        self.commentary.get_or_insert_with(|| (item_id.to_owned(), String::new())).1.push_str(delta);
                        out.push(self.progress(metadata([(meta::KIND, json!("commentary.delta")),
                            (meta::ITEM_ID, json!(item_id)), (meta::DELTA, json!(delta))])));
                        out
                    }
                    _ => {
                        let mut out = self.flush_commentary();
                        out.push(self.append_artifact(delta));
                        out
                    }
                }
            }
            ChatState::Final => {
                let mut out = self.flush_commentary();
                let text = chat.message.as_ref().map(message_text).filter(|t| !t.is_empty())
                    .unwrap_or_else(|| self.artifact_text.clone());
                out.push(self.replace_artifact(text));
                out.push(self.set_status(TaskState::Completed, None, None));
                out.push(StreamResponse::Task(self.task.clone()));
                out
            }
            ChatState::Error => {
                let mut out = self.flush_commentary();
                out.extend(self.fail(chat.error_message.as_deref().unwrap_or("agent run failed"),
                    chat.error_kind.as_deref().unwrap_or("runtime_error")));
                out
            }
            ChatState::Aborted => {
                let mut out = self.flush_commentary();
                let reason = chat.stop_reason.clone().unwrap_or_else(|| "user_cancelled".into());
                out.push(self.set_status(TaskState::Canceled, None, Some(metadata([("stopReason", json!(reason))]))));
                out.push(StreamResponse::Task(self.task.clone()));
                out
            }
        }
    }

    fn fail(&mut self, message: &str, code: &str) -> Vec<StreamResponse> {
        let status_message = self.agent_message(message.to_owned());
        let update = self.set_status(TaskState::Failed, Some(status_message), Some(metadata([
            (meta::ERROR_CODE, json!(code)), (meta::ERROR_MESSAGE, json!(message))])));
        vec![update, StreamResponse::Task(self.task.clone())]
    }

    fn flush_commentary(&mut self) -> Vec<StreamResponse> {
        let Some((item_id, text)) = self.commentary.take() else { return Vec::new() };
        if text.trim().is_empty() { return Vec::new(); }
        let message = self.agent_message(text);
        self.push_history(message.clone());
        let state = self.state();
        vec![self.set_status(state, Some(message), Some(metadata([(meta::KIND, json!("commentary")), (meta::ITEM_ID, json!(item_id))])))]
    }

    /// A WORKING-family progress update that leaves the snapshot's status message intact.
    fn progress(&self, metadata: Metadata) -> StreamResponse {
        StreamResponse::StatusUpdate(TaskStatusUpdateEvent { task_id: self.task.id.clone(), context_id: self.context_id.clone(),
            status: TaskStatus { state: self.state(), message: None, timestamp: Some(now_iso()) }, metadata: Some(metadata) })
    }

    fn set_status(&mut self, state: TaskState, message: Option<Message>, metadata: Option<Metadata>) -> StreamResponse {
        let status = TaskStatus { state, message, timestamp: Some(now_iso()) };
        self.task.status = status.clone();
        StreamResponse::StatusUpdate(TaskStatusUpdateEvent { task_id: self.task.id.clone(), context_id: self.context_id.clone(), status, metadata })
    }

    fn append_artifact(&mut self, delta: &str) -> StreamResponse {
        let append = self.artifact_started;
        self.artifact_started = true;
        self.artifact_text.push_str(delta);
        self.store_artifact();
        self.artifact_update(Part::text(delta), append, false)
    }

    fn replace_artifact(&mut self, text: String) -> StreamResponse {
        self.artifact_started = true;
        self.artifact_text = text.clone();
        self.store_artifact();
        self.artifact_update(Part::text(text), false, true)
    }

    fn store_artifact(&mut self) {
        let artifact = Artifact { artifact_id: RESPONSE_ARTIFACT_ID.into(), name: Some("response".into()), description: None,
            parts: vec![Part::text(self.artifact_text.clone())], metadata: None, extensions: Vec::new() };
        match self.task.artifacts.iter_mut().find(|a| a.artifact_id == RESPONSE_ARTIFACT_ID) {
            Some(existing) => *existing = artifact,
            None => self.task.artifacts.push(artifact),
        }
    }

    fn artifact_update(&self, part: Part, append: bool, last_chunk: bool) -> StreamResponse {
        StreamResponse::ArtifactUpdate(TaskArtifactUpdateEvent { task_id: self.task.id.clone(), context_id: self.context_id.clone(),
            artifact: Artifact { artifact_id: RESPONSE_ARTIFACT_ID.into(), name: Some("response".into()), description: None,
                parts: vec![part], metadata: None, extensions: Vec::new() },
            append, last_chunk, metadata: None })
    }

    fn agent_message(&mut self, text: String) -> Message {
        self.message_seq += 1;
        let mut message = Message::new(format!("{}-m{}", self.task.id, self.message_seq), Role::Agent, vec![Part::text(text)]);
        message.context_id = Some(self.context_id.clone());
        message.task_id = Some(self.task.id.clone());
        message
    }

    /// Mirror pending interactions into `Task.metadata.pendingInteractions` so
    /// polling clients and snapshot-based resumes see structured HITL requests.
    fn sync_pending_metadata(&mut self) {
        let metadata = self.task.metadata.get_or_insert_with(Default::default);
        if self.pending.is_empty() {
            metadata.remove(PENDING_INTERACTIONS);
        } else {
            metadata.insert(PENDING_INTERACTIONS.into(), Value::Array(self.pending.iter().map(|(id, kind, raw)| json!({
                meta::INTERACTION_ID: id, meta::INTERACTION_KIND: interaction_kind(*kind), meta::ENGINE_FRAME: raw,
            })).collect()));
        }
        if metadata.is_empty() { self.task.metadata = None; }
    }

    /// Record a client message (e.g. an interaction answer) in history.
    pub fn push_history(&mut self, message: Message) {
        self.task.history.push(message);
        if self.task.history.len() > MAX_HISTORY {
            let overflow = self.task.history.len() - MAX_HISTORY;
            self.task.history.drain(1..=overflow);
        }
    }
}

/// Return `task` trimmed to the client's `historyLength` (None keeps all).
pub fn with_history_length(mut task: Task, history_length: Option<i32>) -> Task {
    if let Some(limit) = history_length {
        let keep = usize::try_from(limit.max(0)).unwrap_or(0);
        let len = task.history.len();
        if len > keep { task.history.drain(..len - keep); }
    }
    task
}

pub fn interaction_kind(kind: InteractionKind) -> &'static str {
    match kind {
        InteractionKind::Exec => "exec",
        InteractionKind::AskUser => "ask_user",
        InteractionKind::ModeSwitch => "mode_switch",
    }
}

/// Human-readable question for clients that do not understand the extension.
fn interaction_prompt(kind: InteractionKind, raw: &Value) -> String {
    match kind {
        InteractionKind::AskUser => {
            let questions = raw.get("questions").and_then(Value::as_array).cloned().unwrap_or_default();
            let mut lines = vec!["Input required:".to_owned()];
            for question in &questions {
                let text = question.get("question").and_then(Value::as_str).filter(|t| !t.is_empty())
                    .or_else(|| question.get("header").and_then(Value::as_str))
                    .unwrap_or("question");
                let options: Vec<&str> = question.get("options").and_then(Value::as_array).into_iter().flatten()
                    .filter_map(|o| o.get("label").and_then(Value::as_str)).collect();
                if options.is_empty() { lines.push(format!("- {text}")); } else { lines.push(format!("- {text} [{}]", options.join(" | "))); }
            }
            lines.join("\n")
        }
        InteractionKind::Exec | InteractionKind::ModeSwitch => {
            let title = raw.get("title").and_then(Value::as_str).unwrap_or("Approval required");
            let detail = raw.get("command").or_else(|| raw.get("description")).and_then(Value::as_str).unwrap_or("");
            let options: Vec<&str> = raw.get("options").and_then(Value::as_array).into_iter().flatten()
                .filter_map(|o| o.get("decision").and_then(Value::as_str)).collect();
            let mut text = format!("Approval required: {title}");
            if !detail.is_empty() { text.push('\n'); text.push_str(detail); }
            if !options.is_empty() { text.push_str(&format!("\nReply with one of: {}", options.join(", "))); }
            text
        }
    }
}

fn message_text(message: &Value) -> String {
    message.get("content").and_then(Value::as_array).into_iter().flatten()
        .filter_map(|item| item.get("text").and_then(Value::as_str)).collect::<Vec<_>>().join("\n")
}

fn metadata<const N: usize>(entries: [(&str, Value); N]) -> Metadata {
    entries.into_iter().map(|(k, v)| (k.to_owned(), v)).collect()
}

pub fn now_iso() -> String {
    chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true)
}

#[cfg(test)]
#[path = "projection_tests.rs"]
mod tests;
