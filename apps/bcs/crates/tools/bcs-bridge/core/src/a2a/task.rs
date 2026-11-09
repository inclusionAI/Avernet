//! Bounded, process-local A2A projections. Engine/session ownership stays in runtime.
use std::{sync::{Arc, Mutex}, time::{Duration, Instant}};
use a2a::{A2AError, Artifact, Message, Part, Role, StreamResponse, Task, TaskState, TaskStatus,
    TaskArtifactUpdateEvent, TaskStatusUpdateEvent};
use futures::stream::BoxStream;
use tokio::sync::broadcast;
use crate::run::RunHandle;

pub(super) const MAX_TEXT_BYTES: usize = 1024 * 1024;
pub(super) const TASK_TTL: Duration = Duration::from_secs(300);

pub(super) struct TaskEntry {
    pub bot: String,
    pub id: String,
    pub context: String,
    inner: Mutex<Projection>,
    events: broadcast::Sender<Arc<StreamResponse>>,
}

struct Projection {
    task: Task,
    handle: Option<RunHandle>,
    finished: Option<Instant>,
}

fn status(state: TaskState, message: Option<&str>, id: &str, context: &str) -> TaskStatus {
    TaskStatus {
        state,
        timestamp: Some(chrono::Utc::now()),
        message: message.map(|text| {
            let mut message = Message::new(Role::Agent, vec![Part::text(text)]);
            message.task_id = Some(id.into());
            message.context_id = Some(context.into());
            message
        }),
    }
}

impl TaskEntry {
    pub fn new(bot: String, context: String, mut input: Message, handle: RunHandle) -> Self {
        let id = handle.run_id.clone();
        input.task_id = Some(id.clone());
        input.context_id = Some(context.clone());
        let task = Task {
            id: id.clone(), context_id: context.clone(),
            status: status(TaskState::Working, None, &id, &context),
            artifacts: None, history: Some(vec![input]), metadata: None,
        };
        Self { bot, id, context, inner: Mutex::new(Projection { task, handle: Some(handle), finished: None }),
            events: broadcast::channel(64).0 }
    }

    pub fn expired(&self) -> bool {
        self.inner.lock().unwrap_or_else(|p| p.into_inner()).finished
            .is_some_and(|finished| finished.elapsed() >= TASK_TTL)
    }

    pub fn snapshot(&self, history: Option<i32>) -> Task {
        Self::snapshot_locked(&self.inner.lock().unwrap_or_else(|p| p.into_inner()), history)
    }

    fn snapshot_locked(projection: &Projection, history: Option<i32>) -> Task {
        let mut task = projection.task.clone();
        let length = history.unwrap_or(0).max(0) as usize;
        if length == 0 { task.history = None; }
        else if let Some(messages) = &mut task.history {
            if messages.len() > length { messages.drain(..messages.len() - length); }
        }
        task
    }

    pub fn matches_filter(&self, request: &a2a::ListTasksRequest) -> bool {
        let projection = self.inner.lock().unwrap_or_else(|p| p.into_inner());
        request.context_id.as_ref().is_none_or(|id| id == &self.context)
            && request.status.as_ref().is_none_or(|state| state == &projection.task.status.state)
            && request.status_timestamp_after.is_none_or(|after| projection.task.status.timestamp.is_some_and(|at| at > after))
    }

    /// Append token text without cloning the accumulated artifact on each token.
    /// A final snapshot replaces that same artifact, never creates a second answer.
    pub fn text(&self, text: &str, append: bool, last: bool) -> Result<(), ()> {
        let mut projection = self.inner.lock().unwrap_or_else(|p| p.into_inner());
        if projection.finished.is_some() { return Ok(()); }
        let artifacts = projection.task.artifacts.get_or_insert_with(Vec::new);
        let old_len = artifacts.first().and_then(|a| a.parts.first()).and_then(Part::as_text).map_or(0, str::len);
        if text.len().saturating_add(if append { old_len } else { 0 }) > MAX_TEXT_BYTES { return Err(()); }
        let artifact = Artifact { artifact_id: "answer".into(), name: None, description: None,
            parts: vec![Part::text(text)], metadata: None, extensions: None };
        let append = append && !artifacts.is_empty();
        if append {
            if let a2a::PartContent::Text(value) = &mut artifacts[0].parts[0].content { value.push_str(text); }
        } else { *artifacts = vec![artifact.clone()]; }
        let _ = self.events.send(Arc::new(StreamResponse::ArtifactUpdate(TaskArtifactUpdateEvent {
            task_id: self.id.clone(), context_id: self.context.clone(), artifact,
            append: Some(append), last_chunk: Some(last), metadata: None,
        })));
        Ok(())
    }

    /// Publish terminal only after runtime has released the engine/session slot.
    pub fn finish(&self, state: TaskState, message: Option<&str>) {
        let mut projection = self.inner.lock().unwrap_or_else(|p| p.into_inner());
        if projection.finished.is_some() { return; }
        projection.task.status = status(state, message, &self.id, &self.context);
        projection.finished = Some(Instant::now());
        projection.handle = None;
        let _ = self.events.send(Arc::new(StreamResponse::StatusUpdate(TaskStatusUpdateEvent {
            task_id: self.id.clone(), context_id: self.context.clone(),
            status: projection.task.status.clone(), metadata: None,
        })));
    }

    pub fn cancel(&self) -> Result<(), A2AError> {
        let projection = self.inner.lock().unwrap_or_else(|p| p.into_inner());
        if projection.task.status.state == TaskState::Canceled { return Ok(()); }
        if projection.task.status.state.is_terminal() { return Err(A2AError::task_not_cancelable("task")); }
        if let Some(handle) = &projection.handle { handle.request_abort("user_cancelled"); }
        Ok(())
    }

    pub async fn wait(&self, history: Option<i32>) -> Task {
        let mut events = self.events.subscribe();
        loop {
            {
                let projection = self.inner.lock().unwrap_or_else(|p| p.into_inner());
                if projection.finished.is_some() { return Self::snapshot_locked(&projection, history); }
            }
            let _ = events.recv().await;
        }
    }

    pub fn stream(&self, history: Option<i32>) -> BoxStream<'static, Result<Arc<StreamResponse>, A2AError>> {
        // Subscription and initial snapshot share the publisher lock: no lost or
        // duplicated prefix when an engine event races this HTTP request.
        let projection = self.inner.lock().unwrap_or_else(|p| p.into_inner());
        let receiver = self.events.subscribe();
        let initial = Self::snapshot_locked(&projection, history);
        let done = initial.status.state.is_terminal();
        Box::pin(futures::stream::unfold((Some(initial), receiver, done), |(initial, mut receiver, done)| async move {
            if let Some(task) = initial {
                return Some((Ok(Arc::new(StreamResponse::Task(task))), (None, receiver, done)));
            }
            if done { return None; }
            match receiver.recv().await {
                Ok(event) => {
                    let done = matches!(event.as_ref(), StreamResponse::StatusUpdate(update) if update.status.state.is_terminal());
                    Some((Ok(event), (None, receiver, done)))
                }
                Err(broadcast::error::RecvError::Lagged(_)) => Some((Err(A2AError::internal(
                    "Stream consumer fell behind; query or subscribe to the Task again")), (None, receiver, true))),
                Err(broadcast::error::RecvError::Closed) => None,
            }
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use futures::StreamExt;
    fn entry() -> TaskEntry {
        let (handle, _) = crate::run::RunRegistry::new().begin("task", "fp".into());
        TaskEntry::new("bot".into(), "context".into(), Message::new(Role::User, vec![Part::text("hello")]), handle)
    }
    #[tokio::test]
    async fn snapshots_and_live_events_have_no_duplicate_prefix_and_lag_fails_only_subscription() {
        let entry = entry();
        entry.text("prefix", false, false).unwrap();
        let mut stream = entry.stream(None);
        let first = stream.next().await.unwrap().unwrap();
        let StreamResponse::Task(task) = first.as_ref() else { panic!("snapshot expected") };
        assert_eq!(task.artifacts.as_ref().unwrap()[0].parts[0].as_text(), Some("prefix"));
        entry.text("suffix", true, false).unwrap();
        let event = stream.next().await.unwrap().unwrap();
        let StreamResponse::ArtifactUpdate(event) = event.as_ref() else { panic!("artifact expected") };
        assert_eq!(event.append, Some(true)); assert_eq!(event.artifact.parts[0].as_text(), Some("suffix"));
        for _ in 0..100 { entry.text("x", true, false).unwrap(); }
        assert!(stream.next().await.unwrap().is_err());
        assert!(stream.next().await.is_none());
        assert!(!entry.inner.lock().unwrap().handle.as_ref().unwrap().abort.is_cancelled());
        entry.finish(TaskState::Completed, None);
        assert_eq!(entry.wait(None).await.status.state, TaskState::Completed);
    }
    #[test]
    fn terminal_immutability_size_limits_history_and_expiry() {
        let entry = entry();
        assert!(entry.text(&"x".repeat(MAX_TEXT_BYTES + 1), false, false).is_err());
        entry.text("a", false, false).unwrap();
        assert!(entry.text(&"x".repeat(MAX_TEXT_BYTES), true, false).is_err());
        assert_eq!(entry.snapshot(None).artifacts.unwrap()[0].parts[0].as_text(), Some("a"));
        assert!(entry.snapshot(None).history.is_none());
        assert_eq!(entry.snapshot(Some(100)).history.unwrap().len(), 1);
        entry.finish(TaskState::Completed, None);
        entry.finish(TaskState::Canceled, None);
        assert_eq!(entry.snapshot(None).status.state, TaskState::Completed);
        assert!(entry.cancel().is_err());
        assert!(!entry.expired());
        entry.inner.lock().unwrap().finished = Some(Instant::now() - TASK_TTL);
        assert!(entry.expired());
    }
}
