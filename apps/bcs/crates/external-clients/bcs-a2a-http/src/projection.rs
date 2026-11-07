//! Stateful SDK event projection. Stage on a clone; commit after BCS ingestion.
use std::collections::HashMap;
use a2a::{Part, PartContent, Role, StreamResponse, TaskState};
use crate::text::{ArtifactText, TextProjectionError};

#[derive(Clone, Debug, PartialEq)]
pub enum Output {
    Identity { task_id: String, context_id: String },
    Delta(String),
    Status(TaskState),
    /// Preserved extension/status message for the negotiated extension decoder.
    StatusMessage(a2a::Message),
    Tool(serde_json::Value),
    MessageFinal { text: String, context_id: Option<String> },
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ProjectionError {
    IdentityMismatch,
    InitialTaskRequired,
    UnsupportedContent,
    UnexpectedMessage,
    EventAfterTerminal,
    Text(TextProjectionError),
    Tool(&'static str),
}

#[derive(Clone, Debug, Default)]
pub struct Projection {
    identity: Option<(String, String)>,
    artifacts: HashMap<String, ArtifactText>,
    terminal: Option<TaskState>,
    tools: crate::tools::Tools,
    state_recoveries: u8,
}

fn text(parts: &[Part]) -> Result<String, ProjectionError> {
    let mut result = String::new();
    for part in parts {
        match &part.content {
            PartContent::Text(value) => result.push_str(value),
            _ => return Err(ProjectionError::UnsupportedContent),
        }
    }
    Ok(result)
}

impl Projection {
    pub(crate) fn begin_state_recovery(&mut self)->bool {
        if self.state_recoveries>=3 {return false;}
        self.state_recoveries+=1;true
    }

    pub fn with_tools(mut self, enabled: bool) -> Self {
        self.tools = crate::tools::Tools::new(enabled); self
    }

    fn message(&mut self, message: a2a::Message, out: &mut Vec<Output>) -> Result<(), ProjectionError> {
        for record in self.tools.decode(&message).map_err(ProjectionError::Tool)? { out.push(Output::Tool(record)); }
        out.push(Output::StatusMessage(message));
        Ok(())
    }
    pub fn identity(&self) -> Option<(&str, &str)> {
        self.identity.as_ref().map(|(task, context)| (task.as_str(), context.as_str()))
    }

    /// Errors and failed ingestion never partially advance the caller's cursor.
    pub fn stage(&self, event: StreamResponse) -> Result<(Self, Vec<Output>), ProjectionError> {
        let mut next = self.clone();
        let mut out = Vec::new();
        next.apply(event, &mut out)?;
        Ok((next, out))
    }

    fn check(&self, task: &str, context: &str) -> Result<(), ProjectionError> {
        match &self.identity {
            Some((expected_task, expected_context)) if expected_task == task && expected_context == context => Ok(()),
            Some(_) => Err(ProjectionError::IdentityMismatch),
            None => Err(ProjectionError::InitialTaskRequired),
        }
    }

    fn status(&mut self, state: TaskState, out: &mut Vec<Output>) -> Result<(), ProjectionError> {
        if let Some(previous) = &self.terminal {
            return if previous == &state { Ok(()) } else { Err(ProjectionError::EventAfterTerminal) };
        }
        if state.is_terminal() { self.terminal = Some(state.clone()); }
        out.push(Output::Status(state));
        Ok(())
    }

    fn apply(&mut self, event: StreamResponse, out: &mut Vec<Output>) -> Result<(), ProjectionError> {
        match event {
            StreamResponse::Task(task) => {
                if self.identity.is_none() {
                    if task.id.is_empty() || task.context_id.is_empty() { return Err(ProjectionError::IdentityMismatch); }
                    self.identity = Some((task.id.clone(), task.context_id.clone()));
                    out.push(Output::Identity { task_id: task.id.clone(), context_id: task.context_id.clone() });
                } else { self.check(&task.id, &task.context_id)?; }
                for artifact in task.artifacts.iter().flatten() {
                    let suffix = self.artifacts.entry(artifact.artifact_id.clone()).or_default()
                        .snapshot(&text(&artifact.parts)?).map_err(ProjectionError::Text)?;
                    if !suffix.is_empty() { out.push(Output::Delta(suffix)); }
                }
                if let Some(message) = task.status.message { self.message(message, out)?; }
                self.status(task.status.state, out)?;
            }
            StreamResponse::ArtifactUpdate(event) => {
                self.check(&event.task_id, &event.context_id)?;
                if self.terminal.is_some() { return Err(ProjectionError::EventAfterTerminal); }
                let suffix = self.artifacts.entry(event.artifact.artifact_id).or_default()
                    .update(&text(&event.artifact.parts)?, event.append.unwrap_or(false), event.last_chunk.unwrap_or(false))
                    .map_err(ProjectionError::Text)?;
                if !suffix.is_empty() { out.push(Output::Delta(suffix)); }
            }
            StreamResponse::StatusUpdate(event) => {
                self.check(&event.task_id, &event.context_id)?;
                if let Some(message) = event.status.message { self.message(message, out)?; }
                self.status(event.status.state, out)?;
            }
            StreamResponse::Message(message) => {
                if self.identity.is_some() || message.role != Role::Agent {
                    return Err(ProjectionError::UnexpectedMessage);
                }
                if self.terminal.is_some() { return Err(ProjectionError::EventAfterTerminal); }
                if message.context_id.as_ref().is_some_and(String::is_empty) {
                    return Err(ProjectionError::IdentityMismatch);
                }
                out.push(Output::MessageFinal { text: text(&message.parts)?, context_id: message.context_id });
                self.terminal = Some(TaskState::Completed);
            }
        }
        Ok(())
    }
}
