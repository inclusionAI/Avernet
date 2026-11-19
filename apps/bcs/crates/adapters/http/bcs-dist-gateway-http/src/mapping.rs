//! Application results as A2A objects.

use crate::content;
use a2a::{StreamResponse, Task, TaskArtifactUpdateEvent, TaskState, TaskStatusUpdateEvent};
use bcs_dist_model::*;
use bcs_service_api::application::dist_gateway::{FormattedOutputBody, FormattedOutputEvent};

pub(crate) fn state(status: TaskStatus) -> TaskState {
    match status {
        TaskStatus::Submitted => TaskState::Submitted,
        TaskStatus::Working => TaskState::Working,
        TaskStatus::InputRequired => TaskState::InputRequired,
        TaskStatus::AuthRequired => TaskState::AuthRequired,
        TaskStatus::Completed => TaskState::Completed,
        TaskStatus::Failed => TaskState::Failed,
        TaskStatus::Canceled => TaskState::Canceled,
        TaskStatus::Rejected => TaskState::Rejected,
    }
}

pub(crate) fn status_filter(state: &TaskState) -> Option<TaskStatus> {
    Some(match state {
        TaskState::Unspecified => return None,
        TaskState::Submitted => TaskStatus::Submitted,
        TaskState::Working => TaskStatus::Working,
        TaskState::InputRequired => TaskStatus::InputRequired,
        TaskState::AuthRequired => TaskStatus::AuthRequired,
        TaskState::Completed => TaskStatus::Completed,
        TaskState::Failed => TaskStatus::Failed,
        TaskState::Canceled => TaskStatus::Canceled,
        TaskState::Rejected => TaskStatus::Rejected,
    })
}

fn terminal(status: TerminalStatus) -> TaskState {
    match status {
        TerminalStatus::Completed => TaskState::Completed,
        TerminalStatus::Failed => TaskState::Failed,
        TerminalStatus::Canceled => TaskState::Canceled,
        TerminalStatus::Rejected => TaskState::Rejected,
    }
}

/// What a returned task includes besides its status.
#[derive(Debug, Clone, Copy)]
pub(crate) struct TaskShape {
    /// `Some(n)`: at most the last `n` messages of the history.
    pub history_length: Option<usize>,
    pub artifacts: bool,
}

impl TaskShape {
    pub(crate) const FULL: Self = Self {
        history_length: None,
        artifacts: true,
    };

    pub(crate) fn history(length: Option<i32>) -> Self {
        Self {
            history_length: length.map(|n| usize::try_from(n).unwrap_or(0)),
            artifacts: true,
        }
    }
}

pub(crate) fn task(view: &TaskView, shape: TaskShape) -> Task {
    let display = content::display(&view.content.snapshot);
    let history = display.history.map(|mut messages| {
        if let Some(keep) = shape.history_length {
            let drop = messages.len().saturating_sub(keep);
            messages.drain(..drop);
        }
        messages
    });
    Task {
        id: view.tid.0.clone(),
        context_id: view.sid.0.clone(),
        status: a2a::TaskStatus {
            state: state(view.content.status),
            message: display.status_message,
            timestamp: None,
        },
        artifacts: display.artifacts.filter(|_| shape.artifacts),
        history,
        metadata: None,
    }
}

fn status_update(sid: &Sid, tid: &Tid, state: TaskState) -> StreamResponse {
    StreamResponse::StatusUpdate(TaskStatusUpdateEvent {
        task_id: tid.0.clone(),
        context_id: sid.0.clone(),
        status: a2a::TaskStatus {
            state,
            message: None,
            timestamp: None,
        },
        metadata: None,
    })
}

/// The A2A event of one output event; `None` when it carries nothing a client
/// can read (its position is still advanced by later events).
pub(crate) fn event(sid: &Sid, event: &FormattedOutputEvent) -> Option<StreamResponse> {
    match &event.body {
        FormattedOutputBody::Status { status, .. } => Some(status_update(sid, &event.tid, state(*status))),
        FormattedOutputBody::Terminal { status, .. } => Some(status_update(sid, &event.tid, terminal(*status))),
        FormattedOutputBody::Artifact { content } => {
            let Some(update) = content::artifact_update(content) else {
                tracing::warn!(id = ?event.id, "skipping artifact event without readable content");
                return None;
            };
            Some(StreamResponse::ArtifactUpdate(TaskArtifactUpdateEvent {
                task_id: event.tid.0.clone(),
                context_id: sid.0.clone(),
                artifact: update.artifact,
                append: update.append,
                last_chunk: update.last_chunk,
                metadata: update.metadata,
            }))
        }
    }
}
