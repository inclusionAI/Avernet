//! A2A operations translate into the existing Bridge runtime, not a second engine driver.
use std::{collections::HashMap, sync::Arc, time::Duration};
use a2a::{A2AError, SendMessageRequest, TaskState};
use bcs_protocol::stream::{ChatState, InteractionPhase, StreamEvent};
use futures::StreamExt;
use serde_json::json;
use tokio::sync::Mutex;
use crate::{runtime::{self, AppState, DownstreamRequest, RuntimeReply, RunDelivery, ToBot}, run::RunHandle};
use super::task::TaskEntry;

const MAX_TASKS: usize = 128;

#[derive(Default)]
struct Tasks {
    entries: HashMap<String, Arc<TaskEntry>>,
    messages: HashMap<(String, String), (String, String)>,
}
impl Tasks {
    fn sweep(&mut self) {
        self.entries.retain(|_, entry| !entry.expired());
        self.messages.retain(|_, (_, id)| self.entries.contains_key(id));
    }
}

pub(super) struct Service {
    pub state: Arc<AppState>,
    tasks: Mutex<Tasks>,
}
impl Service {
    pub fn new(state: Arc<AppState>) -> Self { Self { state, tasks: Mutex::new(Tasks::default()) } }

    pub async fn send(self: &Arc<Self>, bot: &str, request: SendMessageRequest) -> Result<Arc<TaskEntry>, A2AError> {
        validate_send(&request)?;
        // Admission and collector ownership outlive an HTTP disconnect, including
        // one occurring while runtime is acquiring the session slot.
        let service = self.clone();
        let bot = bot.to_owned();
        tokio::spawn(async move { service.admit(bot, request).await }).await
            .map_err(|_| A2AError::internal("Task admission failed"))?
    }

    async fn admit(self: Arc<Self>, bot: String, request: SendMessageRequest) -> Result<Arc<TaskEntry>, A2AError> {
        let mut tasks = self.tasks.lock().await;
        tasks.sweep();
        let key = (bot.clone(), request.message.message_id.clone());
        let fingerprint = crate::idempotency::fingerprint(&[&serde_json::to_string(&(&request.message, &request.metadata))
            .map_err(|_| A2AError::invalid_params("Invalid message"))?]);
        if let Some((previous, id)) = tasks.messages.get(&key) {
            if previous != &fingerprint { return Err(A2AError::invalid_params("messageId already used with different content")); }
            return Ok(tasks.entries[id].clone());
        }
        if let Some(id) = &request.message.task_id {
            let entry = tasks.entries.get(id).filter(|task| task.bot == bot)
                .ok_or_else(|| A2AError::task_not_found("task"))?;
            if request.message.context_id.as_ref().is_some_and(|context| context != &entry.context) {
                return Err(A2AError::invalid_params("Task and context do not match"));
            }
            return Err(A2AError::unsupported_operation("Send a new Task using contextId; in-place Task continuation is not supported"));
        }
        if tasks.entries.len() >= MAX_TASKS {
            return Err(A2AError::internal("Task capacity reached; retry after completed Tasks expire"));
        }
        let context = request.message.context_id.clone().unwrap_or_else(a2a::new_context_id);
        // A2A contexts never alias the webhook/plugin session namespace.
        let session = format!("a2a:{context}");
        let run_id = a2a::new_task_id();
        let content: Vec<_> = request.message.parts.iter().filter_map(|part| part.as_text())
            .map(|text| json!({"type":"text", "text":text})).collect();
        let downstream = DownstreamRequest {
            id: run_id.clone(), method: "chat.send".into(),
            to_bot: ToBot { provider_id: self.state.config.provider_id.clone(), provider_bot_ref: bot.clone() },
            session_id: Some(session), message: Some(json!({"role":"user", "content":content})),
            from: None, timeout_ms: None, params: None,
        };
        let reply = runtime::dispatch(self.state.clone(), downstream).await.map_err(|error| match error.code {
            "rate_limited" => A2AError::invalid_params("A Task is already active in this context"),
            _ => A2AError::internal("Bridge could not start the Task"),
        })?;
        let RuntimeReply::Run { handle, delivery: RunDelivery::Gateway { stream } } = reply else {
            return Err(A2AError::internal("A2A requires gateway delivery"));
        };
        let entry = Arc::new(TaskEntry::new(bot, context, request.message, handle.clone()));
        tasks.messages.insert(key, (fingerprint, run_id.clone()));
        tasks.entries.insert(run_id, entry.clone());
        tokio::spawn(collect(self.state.clone(), entry.clone(), handle, stream));
        Ok(entry)
    }

    pub async fn get(&self, bot: &str, id: &str) -> Result<Arc<TaskEntry>, A2AError> {
        let mut tasks = self.tasks.lock().await;
        tasks.sweep();
        tasks.entries.get(id).filter(|entry| entry.bot == bot).cloned()
            .ok_or_else(|| A2AError::task_not_found("task"))
    }

    pub async fn cancel(&self, bot: &str, id: &str) -> Result<a2a::Task, A2AError> {
        let task = self.get(bot, id).await?;
        task.cancel()?;
        self.state.interactions.invalidate_run(id, json!({"decision":"deny"}));
        let result = tokio::time::timeout(Duration::from_secs(10), task.wait(None)).await
            .map_err(|_| A2AError::internal("Task cancellation is not yet confirmed; query the Task"))?;
        if result.status.state != TaskState::Canceled { return Err(A2AError::task_not_cancelable("task")); }
        Ok(result)
    }

    pub async fn list(&self, bot: &str, request: a2a::ListTasksRequest) -> Result<a2a::ListTasksResponse, A2AError> {
        validate_common(request.tenant.as_deref(), request.history_length)?;
        let size = request.page_size.unwrap_or(50);
        if !(1..=100).contains(&size) { return Err(A2AError::invalid_params("pageSize must be between 1 and 100")); }
        let mut tasks = self.tasks.lock().await;
        tasks.sweep();
        let mut selected: Vec<_> = tasks.entries.values().filter(|entry| entry.bot == bot && entry.matches_filter(&request)).collect();
        selected.sort_by(|a, b| b.id.cmp(&a.id));
        let total_size = selected.len() as i32;
        let mut scope = request.clone(); scope.page_token = None; scope.page_size = None;
        let fingerprint = crate::idempotency::fingerprint(&[bot, &serde_json::to_string(&scope)
            .map_err(|_| A2AError::invalid_params("Invalid filters"))?]);
        let start = match request.page_token.as_deref().filter(|token| !token.is_empty()) {
            Some(token) => {
                let id = token.strip_prefix(&format!("{fingerprint}:"))
                    .ok_or_else(|| A2AError::invalid_params("Invalid pageToken"))?;
                selected.iter().position(|entry| entry.id == id)
                    .ok_or_else(|| A2AError::invalid_params("Expired or invalid pageToken"))? + 1
            }
            None => 0,
        };
        let end = (start + size as usize).min(selected.len());
        let next_page_token = if end < selected.len() { format!("{}:{}", fingerprint, selected[end - 1].id) } else { String::new() };
        let result = selected[start..end].iter().map(|entry| {
            let mut task = entry.snapshot(request.history_length);
            if request.include_artifacts != Some(true) { task.artifacts = None; }
            task
        }).collect();
        Ok(a2a::ListTasksResponse { tasks: result, next_page_token, page_size: size, total_size })
    }
}

pub(super) fn validate_common(tenant: Option<&str>, history: Option<i32>) -> Result<(), A2AError> {
    if tenant.is_some_and(|tenant| !tenant.is_empty()) { return Err(A2AError::invalid_params("Tenant selection is not supported")); }
    if history.is_some_and(|length| length < 0) { return Err(A2AError::invalid_params("historyLength must be nonnegative")); }
    Ok(())
}

fn validate_send(request: &SendMessageRequest) -> Result<(), A2AError> {
    let config = request.configuration.as_ref();
    validate_common(request.tenant.as_deref(), config.and_then(|config| config.history_length))?;
    let message = &request.message;
    for id in [Some(message.message_id.as_str()), message.context_id.as_deref(), message.task_id.as_deref()].into_iter().flatten() {
        if id.trim().is_empty() || id.len() > 256 || id.chars().any(char::is_control) {
            return Err(A2AError::invalid_params("Message, context and Task IDs must be nonempty and at most 256 bytes"));
        }
    }
    if message.role != a2a::Role::User || message.parts.is_empty() {
        return Err(A2AError::invalid_params("A user message with text parts is required"));
    }
    if message.parts.iter().any(|part| part.as_text().is_none()
        || part.media_type.as_deref().is_some_and(|mode| mode != "text/plain")) {
        return Err(A2AError::content_type_not_supported());
    }
    if message.extensions.as_ref().is_some_and(|extensions| !extensions.is_empty())
        || message.reference_task_ids.as_ref().is_some_and(|ids| !ids.is_empty()) {
        return Err(A2AError::unsupported_operation("Message extensions and referenceTaskIds are not supported"));
    }
    if let Some(config) = config {
        if config.task_push_notification_config.is_some() { return Err(A2AError::push_notification_not_supported()); }
        if config.accepted_output_modes.as_ref().is_some_and(|modes| !modes.is_empty()
            && !modes.iter().any(|mode| matches!(mode.as_str(), "text/plain" | "text/*" | "*/*"))) {
            return Err(A2AError::content_type_not_supported());
        }
    }
    Ok(())
}

async fn collect(state: Arc<AppState>, task: Arc<TaskEntry>, handle: RunHandle, mut stream: runtime::RunStream) {
    let mut terminal = (TaskState::Failed, Some("Engine ended without a confirmed result"));
    let mut failed = false;
    while let Some(record) = stream.next().await {
        if failed { continue; }
        let result = match record.event {
            StreamEvent::Chat(chat) => match chat.state {
                ChatState::Delta => task.text(chat.delta_text.as_deref().unwrap_or(""), true, false),
                ChatState::Final => {
                    terminal = (TaskState::Completed, None);
                    task.text(&crate::run::extract_message_text(chat.message.as_ref()), false, true)
                }
                ChatState::Error => {
                    terminal = (TaskState::Failed, Some("Bridge engine execution failed"));
                    failed = true; handle.request_abort("engine_error"); Ok(())
                }
                ChatState::Aborted => { terminal = (TaskState::Canceled, None); Ok(()) }
            },
            StreamEvent::Interaction(interaction) if interaction.phase == InteractionPhase::Requested => {
                terminal = (TaskState::Failed, Some("Interactive input is not supported by this A2A endpoint"));
                failed = true;
                state.interactions.invalidate_run(&task.id, json!({"decision":"deny"}));
                handle.request_abort("unsupported_interaction");
                Ok(())
            }
            _ => Ok(()), // No tools/thinking extension is advertised.
        };
        if result.is_err() {
            terminal = (TaskState::Failed, Some("Task output exceeded the size limit"));
            failed = true;
            handle.request_abort("output_limit");
        }
    }
    // The owner, unlike an HTTP subscriber, drains until runtime cleanup is done.
    // A2A retains its bounded Task projection, not the full gateway replay log.
    state.runs.remove(&task.id);
    task.finish(terminal.0, terminal.1);
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn full_task_registry_rejects_new_work_without_evicting_retained_tasks() {
        let dir = tempfile::tempdir().unwrap();
        let config = crate::config::ProviderConfig::from_toml(
            "provider_id='capacity'\nlisten='127.0.0.1:0'\nbcs_to_provider_token='test'\nstate_path='state.db'\nbot=[]",
            &dir.path().join("bridge.toml")).unwrap();
        let state = Arc::new(AppState::new(config, &crate::engine::EngineRegistry::new()).unwrap());
        let service = Arc::new(Service::new(state));
        {
            let mut tasks = service.tasks.lock().await;
            for n in 0..MAX_TASKS {
                let id = format!("task-{n}");
                let (handle, _) = crate::run::RunRegistry::new().begin(&id, "fp".into());
                let input = a2a::Message::new(a2a::Role::User, vec![a2a::Part::text("input")]);
                let task = Arc::new(TaskEntry::new("bot".into(), "context".into(), input, handle));
                task.finish(TaskState::Completed, None);
                tasks.entries.insert(id, task);
            }
        }
        let request = serde_json::from_value(json!({"message":{"messageId":"new","role":"ROLE_USER","parts":[{"text":"input"}]}})).unwrap();
        let error = service.send("bot", request).await.err().unwrap();
        assert!(error.message.contains("capacity"));
        assert_eq!(service.tasks.lock().await.entries.len(), MAX_TASKS);
        assert!(service.get("bot", "task-0").await.is_ok());
    }
}
