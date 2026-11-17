//! A2A stream responses → the Connector's remote events, and the payload of
//! each event kind.
//!
//! | A2A event | Kind | Payload |
//! | --- | --- | --- |
//! | artifact update | `Delta` | `artifact`, `append`, `last_chunk` |
//! | working status with a tools-extension record | `Tool` | `tool` |
//! | working status with another message | `Thinking` | `message` |
//! | a whole non-terminal task | `Snapshot` | `task` (history left out) |
//! | working / submitted status without content | nothing | — |
//! | `input-required` / `auth-required` | `InputRequired` / `AuthRequired` | `state`, `message` |
//! | `completed`, or a direct message answer | `Final` | `state`, optional `message`, `artifacts` |
//! | `failed`, `canceled`, `rejected` | `Error` | `state`, `code`, optional `message` |
//!
//! Objects under `message`, `artifact` and `task` are the SDK's own JSON.
//! `state` and `code` are our words; remote free text appears only inside
//! `message`. The interaction id of a question is a placeholder: the
//! Connector's rules derive the real one.

use a2a::{StreamResponse, Task, TaskState, TaskStatus as RemoteStatus};
use bcs_dist_model::{AgentEventKind, InteractionId, Payload, RemoteTaskRef, TaskStatus};
use bcs_service_api::port::dist_agent::RemoteEvent;
use serde_json::{Value, json};

/// Turns stream responses of one remote task into remote events.
pub(super) struct Mapper {
    tools: crate::tools::Tools,
}

fn question() -> InteractionId {
    InteractionId(String::new())
}

fn message_of(status: &RemoteStatus) -> Option<Value> {
    status.message.as_ref().and_then(|message| serde_json::to_value(message).ok())
}

/// Kind and payload a task state implies; `None` for a state without content.
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

pub(super) fn remote_of(task_id: &str, context_id: &str) -> RemoteTaskRef {
    RemoteTaskRef {
        task_id: task_id.to_owned(),
        context_id: Some(context_id.to_owned()).filter(|context| !context.is_empty()),
    }
}

impl Mapper {
    pub fn new(tools_negotiated: bool) -> Self {
        Self {
            tools: crate::tools::Tools::new(tools_negotiated),
        }
    }

    /// The events one stream response stands for (usually one, sometimes none
    /// or several tool records).
    pub fn map(&mut self, response: StreamResponse) -> Vec<RemoteEvent> {
        let event = |remote: RemoteTaskRef, (kind, payload): (AgentEventKind, Value)| RemoteEvent {
            remote,
            // The SSE decoder exposes no event id or replay position.
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
    fn message_events(&mut self, message: &a2a::Message) -> Vec<(AgentEventKind, Value)> {
        match self.tools.decode(message) {
            Ok(records) if !records.is_empty() => records
                .into_iter()
                .map(|record| (AgentEventKind::Tool, json!({ "tool": record["data"] })))
                .collect(),
            Ok(_) => match serde_json::to_value(message) {
                Ok(message) => vec![(AgentEventKind::Thinking, json!({ "message": message }))],
                Err(_) => Vec::new(),
            },
            // An unnegotiated or malformed tool record is dropped, not relayed.
            Err(reason) => {
                tracing::debug!(reason, "tool record ignored");
                Vec::new()
            }
        }
    }
}
