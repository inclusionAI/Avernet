//! Manager RunResult for failed and aborted Runs (issue #2610).
use super::{
    ActorKind, Arc, BTreeMap, ChatEventState, CollaborationRuntime, CollaborationRuntimeService,
    CreateOrReactivateCommand, GroupCoreService, GroupStore, MemoryCollaborationStore, MemoryMessageRepo, MessageViewScope,
    Mutex, NewSessionParams, Participant, ParticipantMode, ParticipantRole, RecordingDelivery,
    RuntimeParticipantBinding, SessionKind, SessionManagementService, StartSessionStateMachineRunCommand,
    StateMachineRunRepoPort, StateMachineRunStatus, Value, async_trait, json, noop_judge, one_shot_authoring_yaml,
    one_shot_human_input_authoring_yaml, session_collaboration_group, test_group, test_sessions,
};
use bcs_domain::GroupStrategy;
use std::time::Duration;
use bcs_domain::{StateMachineNodeStatus, StateMachineRun};
use bcs_service_api::{
    CancelStateMachineRunCommand, CollaborationRuntimeError, HandleBotTerminalEventCommand,
    HandleBotTerminalEventOutcome, ServiceError, ServiceResult, StateMachineResultPublishCommand,
    StateMachineResultPublisherPort, StateMachineRunResultOutcome, StateMachineRunResultPayload,
    StateMachineRunResultStatus, StateMachineRunResultTerminal, StateMachineRunTerminalReason,
};
use std::collections::VecDeque;
use std::sync::atomic::{AtomicUsize, Ordering};

/// Records RunResults and answers with scripted outcomes (Delivered by default).
#[derive(Default)]
struct RunResultPublisher {
    results: Mutex<Vec<StateMachineRunResultPayload>>,
    outcomes: std::sync::Mutex<VecDeque<ServiceResult<StateMachineRunResultOutcome>>>,
    chat_results: AtomicUsize,
}

impl RunResultPublisher {
    fn scripted(outcomes: Vec<ServiceResult<StateMachineRunResultOutcome>>) -> Self {
        Self { outcomes: std::sync::Mutex::new(outcomes.into()), ..Default::default() }
    }
}

#[async_trait]
impl StateMachineResultPublisherPort for RunResultPublisher {
    async fn publish_state_machine_result(&self, _cmd: StateMachineResultPublishCommand) -> ServiceResult<()> {
        self.chat_results.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }

    async fn publish_state_machine_run_result(
        &self,
        payload: StateMachineRunResultPayload,
    ) -> ServiceResult<StateMachineRunResultOutcome> {
        self.results.lock().await.push(payload);
        self.outcomes.lock().unwrap().pop_front().unwrap_or(Ok(StateMachineRunResultOutcome::Delivered))
    }
}

struct Fixture {
    runtime: CollaborationRuntime,
    store: Arc<MemoryCollaborationStore>,
    delivery: Arc<RecordingDelivery>,
    publisher: Arc<RunResultPublisher>,
    session_id: String,
}

async fn fixture(publisher: RunResultPublisher) -> Fixture {
    let group_store = Arc::new(GroupStore::new());
    let group = test_group();
    group_store.upsert(group.clone()).await.expect("seed group");
    let mut participants = group.participants.clone();
    participants.push(Participant {
        bot_uuid: "worker-bot".to_string(),
        bot_name: Some("Worker".to_string()),
        kind: None,
        role: ParticipantRole::Consultant,
        actor_kind: ActorKind::Bot,
        mode: Some(ParticipantMode::Auto),
        tags: Vec::new(),
        message_view_scope: MessageViewScope::Full,
    });
    let sessions = test_sessions();
    let session = sessions
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: group.id.clone(),
            session_id: None,
            params: NewSessionParams { session_kind: SessionKind::Chat, participants, ..Default::default() },
        })
        .await
        .expect("seed chat session")
        .session;
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let publisher = Arc::new(publisher);
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group_store,
        sessions,
        delivery.clone(),
        noop_judge(),
    )
    .with_result_publisher(publisher.clone());
    Fixture { runtime, store, delivery, publisher, session_id: session.id }
}

async fn start(f: &Fixture) -> StateMachineRun {
    f.runtime
        .start_session_state_machine_run(StartSessionStateMachineRunCommand {
            session_id: f.session_id.clone(),
            caller_bot_id: "driver-bot".to_string(),
            definition_yaml: one_shot_authoring_yaml(),
            participant_bindings: BTreeMap::from([(
                "writer".to_string(),
                RuntimeParticipantBinding {
                    source: "manual".to_string(),
                    bot_ids: vec!["worker-bot".to_string()],
                    extensions: Default::default(),
                },
            )]),
            opening_message: None,
            input: json!({"question": "describe your word"}),
            judge_available: false,
        })
        .await
        .expect("start one-shot run")
        .view
        .run
}

async fn terminal(
    f: &Fixture,
    state: ChatEventState,
    text: &str,
) -> Result<HandleBotTerminalEventOutcome, CollaborationRuntimeError> {
    let id = f.delivery.commands.lock().await[0].run_id.clone();
    let state_name = if state == ChatEventState::Final { "final" } else { "error" };
    f.runtime
        .handle_bot_terminal_event(HandleBotTerminalEventCommand {
            bot_id: "worker-bot".to_string(),
            run_id: id.clone(),
            event_type: "chat.event".to_string(),
            event_payload: json!({
                "run_id": id,
                "state": state_name,
                "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}
            }),
            state,
            bcs_session_id: Some(f.session_id.clone()),
        })
        .await
}

async fn status(f: &Fixture, run: &StateMachineRun) -> StateMachineRunStatus {
    StateMachineRunRepoPort::get_run(&*f.store, &run.run_id).await.unwrap().unwrap().status
}

async fn result_status(f: &Fixture, run: &StateMachineRun) -> Option<StateMachineRunResultStatus> {
    f.store.get_run_result(&run.run_id).await.unwrap().map(|saved| saved.status)
}

#[tokio::test]
async fn node_failure_wakes_manager_once_without_artifacts_or_raw_errors() {
    let f = fixture(RunResultPublisher::default()).await;
    let run = start(&f).await;
    terminal(&f, ChatEventState::Error, "secret word 西装 leaked").await.expect("failure is handled");
    assert_eq!(status(&f, &run).await, StateMachineRunStatus::Failed);

    let results = f.publisher.results.lock().await.clone();
    assert_eq!(results.len(), 1);
    let result = &results[0];
    assert_eq!(result.target_bot_id, "driver-bot");
    assert_eq!(result.session_id, f.session_id);
    assert_eq!(result.terminal, StateMachineRunResultTerminal::Failed);
    assert_eq!(result.reason, StateMachineRunTerminalReason::NodeFailed);
    let failed = result.failed_node.as_ref().expect("failed node");
    assert_eq!((failed.node_id.as_str(), failed.attempt, failed.max_attempts), ("answer", 1, 1));
    assert!(result.completed_nodes.is_empty());
    let text = result.render_text();
    assert!(text.starts_with("[State Machine Run Result]"));
    assert!(!text.contains("西装") && !text.contains("secret"));
    assert_eq!(result_status(&f, &run).await, Some(StateMachineRunResultStatus::Delivered));

    // A replayed terminal event and a recovery scan find nothing to deliver.
    let _ = terminal(&f, ChatEventState::Error, "secret word 西装 leaked").await;
    let page = f.runtime.recover_state_machine_run_results(None, 10).await.expect("scan");
    assert_eq!(page.scanned, 0);
    assert_eq!(f.publisher.results.lock().await.len(), 1);
}

#[tokio::test]
async fn human_timeout_is_reported_with_its_own_reason() {
    let group_store = Arc::new(GroupStore::new());
    let group = session_collaboration_group(GroupStrategy::ManagerWorker);
    group_store.upsert(group.clone()).await.expect("seed group");
    let mut participants = group.participants.clone();
    let mut human = Participant::human("human_1001", ParticipantRole::Observer);
    human.mode = Some(ParticipantMode::Present);
    human.message_view_scope = MessageViewScope::Participant;
    participants.push(human);
    let sessions = test_sessions();
    let session = sessions
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: group.id.clone(),
            session_id: None,
            params: NewSessionParams {
                session_kind: SessionKind::Chat,
                participants,
                message_visibility_version: 1,
                ..Default::default()
            },
        })
        .await
        .expect("seed session")
        .session;
    let store = Arc::new(MemoryCollaborationStore::new());
    let publisher = Arc::new(RunResultPublisher::default());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group_store,
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    )
    .with_result_publisher(publisher.clone());
    runtime
        .start_session_state_machine_run(StartSessionStateMachineRunCommand {
            session_id: session.id.clone(),
            caller_bot_id: "driver-bot".to_string(),
            definition_yaml: one_shot_human_input_authoring_yaml().replace("60000", "1"),
            participant_bindings: BTreeMap::new(),
            opening_message: None,
            input: Value::Null,
            judge_available: false,
        })
        .await
        .expect("start one-shot HumanInput run");
    tokio::time::sleep(Duration::from_millis(5)).await;
    assert_eq!(runtime.process_expired_node_timeouts(10, 0).await.expect("scan timeouts"), 1);
    let results = publisher.results.lock().await.clone();
    assert_eq!(results.len(), 1);
    assert_eq!(results[0].reason, StateMachineRunTerminalReason::HumanTimeout);
    assert_eq!(results[0].failed_node.as_ref().map(|node| node.node_id.as_str()), Some("review"));
}

#[tokio::test]
async fn late_artifact_after_failure_does_not_advance_the_run() {
    let f = fixture(RunResultPublisher::default()).await;
    let run = start(&f).await;
    terminal(&f, ChatEventState::Error, "bot crashed").await.expect("failure is handled");
    let _ = terminal(&f, ChatEventState::Final, "late answer").await;
    assert_eq!(status(&f, &run).await, StateMachineRunStatus::Failed);
    let node = f.store.get_node_run(&run.run_id, "answer").await.unwrap().unwrap();
    assert_eq!(node.status, StateMachineNodeStatus::Failed);
    assert_eq!(f.publisher.chat_results.load(Ordering::SeqCst), 0);
    assert_eq!(f.publisher.results.lock().await.len(), 1);
}

#[tokio::test]
async fn user_abort_wakes_manager_without_failed_node_or_caller_text() {
    let f = fixture(RunResultPublisher::default()).await;
    let run = start(&f).await;
    let view = f
        .runtime
        .cancel_state_machine_run(CancelStateMachineRunCommand {
            run_id: run.run_id.clone(),
            reason: Some("player 西装 left".to_string()),
        })
        .await
        .expect("abort");
    assert_eq!(view.run.status, StateMachineRunStatus::Aborted);
    let results = f.publisher.results.lock().await.clone();
    assert_eq!(results.len(), 1);
    assert_eq!(results[0].terminal, StateMachineRunResultTerminal::Aborted);
    assert_eq!(results[0].reason, StateMachineRunTerminalReason::UserAborted);
    assert!(results[0].failed_node.is_none());
    assert!(!results[0].render_text().contains("西装"));
}

#[tokio::test]
async fn session_close_aborts_without_waking_the_manager() {
    let f = fixture(RunResultPublisher::default()).await;
    let run = start(&f).await;
    f.runtime.cancel_session_runs(&f.session_id, "session completed").await.expect("cancel session runs");
    assert_eq!(status(&f, &run).await, StateMachineRunStatus::Aborted);
    assert!(f.publisher.results.lock().await.is_empty());
    assert_eq!(result_status(&f, &run).await, None);
}

#[tokio::test]
async fn failure_and_abort_race_wake_the_manager_exactly_once() {
    let f = fixture(RunResultPublisher::default()).await;
    let run = start(&f).await;
    let cancel = CancelStateMachineRunCommand { run_id: run.run_id.clone(), reason: None };
    let (failed, aborted) = tokio::join!(
        terminal(&f, ChatEventState::Error, "bot crashed"),
        f.runtime.cancel_state_machine_run(cancel),
    );
    assert!(failed.is_ok() || aborted.is_ok());
    assert!(matches!(status(&f, &run).await, StateMachineRunStatus::Failed | StateMachineRunStatus::Aborted));
    assert_eq!(f.publisher.results.lock().await.len(), 1);
    assert_eq!(result_status(&f, &run).await, Some(StateMachineRunResultStatus::Delivered));
}

#[tokio::test]
async fn retryable_rejection_is_delivered_later_by_recovery() {
    let f = fixture(RunResultPublisher::scripted(vec![Ok(StateMachineRunResultOutcome::Retry {
        error: "queue_draining".to_string(),
    })]))
    .await;
    let run = start(&f).await;
    terminal(&f, ChatEventState::Error, "bot crashed").await.expect("failure is handled");
    assert_eq!(result_status(&f, &run).await, Some(StateMachineRunResultStatus::Pending));

    let page = f.runtime.recover_state_machine_run_results(None, 10).await.expect("scan");
    assert_eq!((page.scanned, page.reconciled), (1, 1));
    assert_eq!(result_status(&f, &run).await, Some(StateMachineRunResultStatus::Delivered));
    f.runtime.recover_state_machine_run_results(None, 10).await.expect("rescan");
    assert_eq!(f.publisher.results.lock().await.len(), 2);
}

#[tokio::test]
async fn uncertain_publication_is_never_resent() {
    let f = fixture(RunResultPublisher::scripted(vec![Err(ServiceError::InternalError(
        "connection reset after write".to_string(),
    ))]))
    .await;
    let run = start(&f).await;
    terminal(&f, ChatEventState::Error, "bot crashed").await.expect("failure is handled");
    assert_eq!(result_status(&f, &run).await, Some(StateMachineRunResultStatus::Failed));
    f.runtime.recover_state_machine_run_results(None, 10).await.expect("scan");
    assert_eq!(f.publisher.results.lock().await.len(), 1);
}
