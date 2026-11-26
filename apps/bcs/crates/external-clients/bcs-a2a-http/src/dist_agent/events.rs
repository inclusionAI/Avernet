//! A2A stream responses → the Connector's remote events, and the payload of
//! each event kind. Used for the stream of a send, for `subscribe`, and (via
//! `task_event`) for the snapshot a `query` returns.
//!
//! | A2A event | Kind | Payload |
//! | --- | --- | --- |
//! | artifact update | `Delta` | `artifact`, `append`, `last_chunk` |
//! | working / submitted status, or a message, with a new tools-extension record | `Tool` | `tool` |
//! | working / submitted status, or a message, with any other message | `Thinking` | `message` |
//! | a whole non-terminal task | `Snapshot` | `task` (history left out) |
//! | working / submitted status without content | nothing | — |
//! | `input-required` / `auth-required` | `InputRequired` / `AuthRequired` | `state`, optional `message` |
//! | `completed` | `Final` | `state`, optional `message`, `artifacts` (whole task only) |
//! | `failed`, `canceled`, `rejected` | `Error` | `state`, `code`, optional `message` |
//!
//! Rows for a state apply to a status update and to a whole task alike. A
//! direct message answer to a send (no task) never reaches this mapper as
//! the first event: `send` turns it into `SendOutcome::Completed` with
//! `{"state": "completed", "message": ...}`. A message later in a stream is
//! mapped like a working status message.
//!
//! Objects under `message`, `artifact` and `task` are the SDK's own JSON.
//! `state` and `code` are our words; remote free text appears only inside
//! `message`. The interaction id of a question is a placeholder: the
//! Connector's rules derive the real one.
//!
//! The `state` word is the remote A2A task state in our spelling
//! (`completed`, `failed`, `canceled`, `rejected`, `input_required`,
//! `auth_required`). The Connector reads it rather than the payload's other
//! content: it is part of a key event's business digest (the digest covers
//! identity and state, not the payload bytes, so a later holder that re-reads
//! the task reports the same key event), and for an `Error` event it decides
//! whether the call ends `Canceled` or `Failed`. Example `Error` payload:
//!
//! ```text
//! {"state": "canceled", "code": "agent_canceled", "message": {...}}
//! ```

use a2a::{StreamResponse, Task, TaskState, TaskStatus as RemoteStatus};
use bcs_dist_model::{AgentEventKind, InteractionId, Payload, RemoteTaskRef, TaskStatus};
use bcs_service_api::port::dist_agent::RemoteEvent;
use serde_json::{Value, json};

/// Turns stream responses of one remote task into remote events.
pub(super) struct Mapper {
    /// Tool-record decoder; remembers the records already seen in this
    /// stream, so a repeated record is recognised.
    tools: crate::tools::Tools,
}

/// Placeholder interaction id of a question; see the module doc.
fn question() -> InteractionId {
    InteractionId(String::new())
}

/// The status message as JSON, if any.
fn message_of(status: &RemoteStatus) -> Option<Value> {
    status.message.as_ref().and_then(|message| serde_json::to_value(message).ok())
}

/// Kind and payload a task state implies; `None` for a state without content
/// of its own (submitted, working, unspecified). `artifacts` is kept only for
/// `completed`.
fn state_event(state: &TaskState, message: Option<Value>, artifacts: Option<Value>) -> Option<(AgentEventKind, Value)> {
    let mut payload = json!({});
    if let Some(message) = message {
        payload["message"] = message;
    }
    let (kind, word, code) = match state {
        TaskState::Completed => (AgentEventKind::Final, "completed", None),
        TaskState::Failed => (AgentEventKind::Error, "failed", Some("agent_failed")),
        TaskState::Canceled => (AgentEventKind::Error, "canceled", Some("agent_canceled")),
        TaskState::Rejected => (AgentEventKind::Error, "rejected", Some("agent_rejected")),
        TaskState::InputRequired => (AgentEventKind::InputRequired { interaction: question() }, "input_required", None),
        TaskState::AuthRequired => (AgentEventKind::AuthRequired { interaction: question() }, "auth_required", None),
        TaskState::Submitted | TaskState::Working | TaskState::Unspecified => return None,
    };
    payload["state"] = json!(word);
    if let Some(code) = code {
        payload["code"] = json!(code);
    }
    if let (TaskState::Completed, Some(artifacts)) = (state, artifacts) {
        payload["artifacts"] = artifacts;
    }
    Some((kind, payload))
}

/// A whole task as an event: its state when that is terminal or a question,
/// otherwise a snapshot.
///
/// The `Snapshot` fallback covers a task that is still submitted or working:
/// there is no state event to report, so the task itself (without its
/// message history) is the payload, as `{"task": {...}}`. `query` uses this payload as its snapshot.
pub(super) fn task_event(task: &Task) -> (AgentEventKind, Value) {
    let artifacts = task.artifacts.as_ref().and_then(|a| serde_json::to_value(a).ok());
    state_event(&task.status.state, message_of(&task.status), artifacts).unwrap_or_else(|| {
        let mut snapshot = serde_json::to_value(task).unwrap_or(Value::Null);
        if let Some(object) = snapshot.as_object_mut() {
            object.remove("history");
        }
        (AgentEventKind::Snapshot, json!({ "task": snapshot }))
    })
}

/// Our task status for a remote state.
pub(super) fn status_of(state: &TaskState) -> TaskStatus {
    match state {
        TaskState::Submitted => TaskStatus::Submitted,
        TaskState::Working | TaskState::Unspecified => TaskStatus::Working,
        TaskState::InputRequired => TaskStatus::InputRequired,
        TaskState::AuthRequired => TaskStatus::AuthRequired,
        TaskState::Completed => TaskStatus::Completed,
        TaskState::Failed => TaskStatus::Failed,
        TaskState::Canceled => TaskStatus::Canceled,
        TaskState::Rejected => TaskStatus::Rejected,
    }
}

/// The remote task reference; an empty context id is no context id.
pub(super) fn remote_of(task_id: &str, context_id: &str) -> RemoteTaskRef {
    RemoteTaskRef {
        task_id: task_id.to_owned(),
        context_id: Some(context_id.to_owned()).filter(|context| !context.is_empty()),
    }
}

impl Mapper {
    /// `v1_negotiated` / `v2_negotiated`: whether tools v1 or v2 were negotiated with
    /// this agent; when not, tool records are dropped.
    pub fn new(v1_negotiated: bool, v2_negotiated: bool) -> Self {
        Self {
            tools: crate::tools::Tools::with_versions(v1_negotiated, v2_negotiated),
        }
    }

    /// The events one stream response stands for (usually one, sometimes none
    /// or several tool records).
    pub fn map(&mut self, response: StreamResponse) -> Vec<RemoteEvent> {
        let event = |remote: RemoteTaskRef, (kind, payload): (AgentEventKind, Value)| RemoteEvent {
            remote,
            // The bindings expose no event id or replay position, so events
            // carry none and `subscribe` cannot resume from a position.
            event_id: None,
            cursor: None,
            kind,
            payload: Payload::Inline(payload),
        };
        match response {
            StreamResponse::Task(task) => {
                vec![event(remote_of(&task.id, &task.context_id), task_event(&task))]
            }
            StreamResponse::ArtifactUpdate(update) => {
                let payload = json!({
                    "artifact": serde_json::to_value(&update.artifact).unwrap_or(Value::Null),
                    "append": update.append.unwrap_or(false),
                    "last_chunk": update.last_chunk.unwrap_or(false),
                });
                vec![event(remote_of(&update.task_id, &update.context_id), (AgentEventKind::Delta, payload))]
            }
            StreamResponse::StatusUpdate(update) => {
                let remote = remote_of(&update.task_id, &update.context_id);
                if let Some(state) = state_event(&update.status.state, message_of(&update.status), None) {
                    return vec![event(remote, state)];
                }
                let Some(message) = &update.status.message else {
                    return Vec::new();
                };
                self.message_events(message)
                    .into_iter()
                    .map(|content| event(remote.clone(), content))
                    .collect()
            }
            StreamResponse::Message(message) => {
                let remote = RemoteTaskRef {
                    task_id: message.task_id.clone().unwrap_or_default(),
                    context_id: message.context_id.clone(),
                };
                self.message_events(&message)
                    .into_iter()
                    .map(|content| event(remote.clone(), content))
                    .collect()
            }
        }
    }

    /// A message during work: tool records when it carries the negotiated
    /// tools extension, otherwise the agent thinking aloud.
    ///
    /// The tools extension (`crate::tools::EXTENSION`) is our own A2A
    /// extension: an agent reports tool calls as data parts of a status
    /// message whose metadata holds the extension URI as key with
    /// `{"version": "1"}` as value (the marker). It is observation only.
    /// - New records: one `Tool` event each, payload
    ///   `{"tool": {"phase", "toolCallId", "name", "args" | "result" + "isError"}}`.
    /// - A marked message with no new record (all repeats): no event.
    /// - Malformed, conflicting, from a non-agent role, or not negotiated:
    ///   dropped (debug log), never relayed.
    /// - No marker: one `Thinking` event with the whole message.
    fn message_events(&mut self, message: &a2a::Message) -> Vec<(AgentEventKind, Value)> {
        let raw = serde_json::to_value(message).unwrap_or(Value::Null);
        let tool_message = raw.get("metadata").and_then(|m| m.get(crate::tools::EXTENSION)).is_some()
            || message.extensions.as_ref().is_some_and(|exts| exts.iter().any(|uri| uri == crate::tools::EXTENSION_V2));
        match self.tools.decode(message) {
            Ok(records) if !records.is_empty() => records
                .into_iter()
                .map(|record| (AgentEventKind::Tool, json!({ "tool": record["data"] })))
                .collect(),
            // A tool message with nothing new (a repeated record): no event.
            // It must not be relayed as thinking, it carries tool payload.
            Ok(_) if tool_message => Vec::new(),
            Ok(_) if raw.is_null() => Vec::new(),
            Ok(_) => vec![(AgentEventKind::Thinking, json!({ "message": raw }))],
            // An unnegotiated or malformed tool record is dropped, not relayed.
            Err(reason) => {
                tracing::debug!(reason, "tool record ignored");
                Vec::new()
            }
        }
    }
}
