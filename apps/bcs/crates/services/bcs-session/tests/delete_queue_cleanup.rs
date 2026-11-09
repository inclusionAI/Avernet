//! Session deletion settles the Session's delivery queue before the delete;
//! a cleanup failure fails the delete and leaves the Session retryable.
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_group_store::MemoryGroupRepo;
use bcs_service_api::port::repo::SessionRepoPort;
use bcs_service_api::*;
use bcs_session::{SessionManagementServiceImpl, SessionManagementWithRuntimeCleanup};
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::{NoopCollaborationRuntimeService, NoopMessageFlowService};

#[derive(Default)]
struct RecordingFlow {
    calls: Mutex<Vec<(String, String, bool)>>,
    sessions: Option<Arc<MemorySessionRepo>>,
    fail: bool,
}

#[async_trait]
impl MessageFlowService for RecordingFlow {
    async fn handle_web_send(&self, cmd: WebSendCommand) -> ServiceResult<WebSendOutcome> { NoopMessageFlowService.handle_web_send(cmd).await }
    async fn handle_bot_event(&self, cmd: BotEventCommand) -> ServiceResult<BotEventOutcome> { NoopMessageFlowService.handle_bot_event(cmd).await }
    async fn handle_group_callback(&self, cmd: GroupCallbackCommand) -> ServiceResult<GroupCallbackOutcome> { NoopMessageFlowService.handle_group_callback(cmd).await }
    async fn handle_chat_abort(&self, cmd: ChatAbortCommand) -> ServiceResult<ChatAbortOutcome> { NoopMessageFlowService.handle_chat_abort(cmd).await }
    async fn register_task_run_alias(&self, task_id: &str, run_id: &str, bot_id: &str) -> ServiceResult<TaskRunAliasRegistration> { NoopMessageFlowService.register_task_run_alias(task_id, run_id, bot_id).await }
    async fn handle_task_dispatch(&self, cmd: TaskDispatchCommand) -> ServiceResult<TaskDispatchOutcome> { NoopMessageFlowService.handle_task_dispatch(cmd).await }
    async fn handle_task_complete(&self, cmd: TaskCompleteCommand) -> ServiceResult<TaskCompleteOutcome> { NoopMessageFlowService.handle_task_complete(cmd).await }
    async fn cancel_session_deliveries(&self, session_id: &str, reason: &str) -> ServiceResult<()> {
        let still_present = match &self.sessions { Some(repo) => repo.get(session_id).await.is_some(), None => false };
        self.calls.lock().unwrap().push((session_id.into(), reason.into(), still_present));
        if self.fail { return Err(ServiceError::InternalError("injected cleanup failure".into())); }
        Ok(())
    }
}

/// Accepts Session run cancellation (the Noop runtime refuses it); every
/// other call keeps the Noop behaviour.
struct CancellingRuntime;

#[async_trait]
impl CollaborationRuntimeService for CancellingRuntime {
    async fn start_state_machine_run(&self, cmd: StartStateMachineRunCommand) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> { NoopCollaborationRuntimeService.start_state_machine_run(cmd).await }
    async fn get_state_machine_run(&self, run_id: &str) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> { NoopCollaborationRuntimeService.get_state_machine_run(run_id).await }
    async fn get_state_machine_session_history(&self, session_id: &str, limit: u64, before: Option<u64>) -> Result<Option<SessionHistoryResult>, CollaborationRuntimeError> { NoopCollaborationRuntimeService.get_state_machine_session_history(session_id, limit, before).await }
    async fn cancel_state_machine_run(&self, cmd: CancelStateMachineRunCommand) -> Result<StateMachineRunView, CollaborationRuntimeError> { NoopCollaborationRuntimeService.cancel_state_machine_run(cmd).await }
    async fn lookup_delivery_correlation(&self, run_id: &str) -> Result<Option<StateMachineDeliveryCorrelation>, CollaborationRuntimeError> { NoopCollaborationRuntimeService.lookup_delivery_correlation(run_id).await }
    async fn register_delivery_alias(&self, request_id: &str, run_id: String) -> Result<(), CollaborationRuntimeError> { NoopCollaborationRuntimeService.register_delivery_alias(request_id, run_id).await }
    async fn handle_bot_terminal_event(&self, cmd: HandleBotTerminalEventCommand) -> Result<HandleBotTerminalEventOutcome, CollaborationRuntimeError> { NoopCollaborationRuntimeService.handle_bot_terminal_event(cmd).await }
    async fn upsert_definition(&self, definition: CollaborationDefinition) -> Result<(), CollaborationRuntimeError> { NoopCollaborationRuntimeService.upsert_definition(definition).await }
    async fn configure_group_runtime(&self, cmd: ConfigureGroupRuntimeCommand) -> Result<ConfigureGroupRuntimeOutcome, CollaborationRuntimeError> { NoopCollaborationRuntimeService.configure_group_runtime(cmd).await }
    async fn cancel_session_runs(&self, _session_id: &str, _reason: &str) -> Result<(), CollaborationRuntimeError> { Ok(()) }
}

async fn session_service(flow: Arc<RecordingFlow>, repo: Arc<MemorySessionRepo>) -> (SessionManagementWithRuntimeCleanup, String) {
    let session = repo.create("group-1", NewSessionParams {
        participants: vec![Participant::bot("bot-a", ParticipantRole::Worker), Participant::bot("bot-b", ParticipantRole::Worker)],
        ..Default::default()
    }).await.unwrap();
    let inner = Arc::new(SessionManagementServiceImpl::new(repo, Arc::new(MemoryGroupRepo::new())));
    let service = SessionManagementWithRuntimeCleanup::new(inner, Arc::new(CancellingRuntime))
        .with_message_flow(flow);
    (service, session.id)
}

#[tokio::test]
async fn delete_cancels_the_session_queue_before_removing_the_session() {
    let repo = Arc::new(MemorySessionRepo::new());
    let flow = Arc::new(RecordingFlow { sessions: Some(repo.clone()), ..Default::default() });
    let (service, session_id) = session_service(flow.clone(), repo).await;
    assert!(service.delete(&session_id).await.unwrap());
    let calls = flow.calls.lock().unwrap().clone();
    assert_eq!(calls.len(), 1);
    let (id, reason, still_present) = calls[0].clone();
    assert_eq!(id, session_id);
    assert_eq!(reason, "session_deleted");
    assert!(still_present, "cleanup runs while the Session can still be retried");
}

#[tokio::test]
async fn cleanup_failure_fails_the_delete_and_keeps_the_session() {
    let repo = Arc::new(MemorySessionRepo::new());
    let flow = Arc::new(RecordingFlow { fail: true, ..Default::default() });
    let (service, session_id) = session_service(flow.clone(), repo.clone()).await;
    let error = service.delete(&session_id).await.unwrap_err();
    assert!(error.to_string().contains("Failed to cancel queued deliveries"), "{error}");
    assert!(repo.get(&session_id).await.is_some(), "the delete can be retried");
    assert_eq!(flow.calls.lock().unwrap().len(), 1);
}

#[tokio::test]
async fn deleting_a_missing_session_does_not_touch_the_queue() {
    let repo = Arc::new(MemorySessionRepo::new());
    let flow = Arc::new(RecordingFlow::default());
    let (service, _) = session_service(flow.clone(), repo).await;
    assert!(!service.delete("no-such-session").await.unwrap());
    assert!(flow.calls.lock().unwrap().is_empty());
}
