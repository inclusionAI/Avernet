use std::{
    collections::{BTreeMap, VecDeque},
    future::Future,
    io::{self, Write},
    sync::{Arc, OnceLock},
    time::Duration,
};

use async_trait::async_trait;
use bcs_collaboration_runtime::CollaborationRuntime;
use bcs_collaboration_store::MemoryCollaborationStore;
use bcs_domain::{
    ActorKind, CollaborationDefinition, CollaborationDefinitionRef,
    CollaborationRuntimeDefinition, Group, GroupMessageType, GroupStrategy, MessageRole,
    Participant, ParticipantMode, ParticipantRole, ResolvedParticipantBinding,
    RuntimeParticipantBinding, SessionStatus, StateMachineNodeStatus, StateMachineRun,
    StateMachineRunStatus, StateMachineTransition,
};
use bcs_group::{GroupManagement, GroupManagementWithRuntimeCleanup, GroupStore};
use bcs_group_store::MemoryGroupRepo;
use bcs_message_store::MemoryMessageRepo;
use bcs_protocol::{BcsFrame, ChatSendParams};
use bcs_service_api::port::repo::MessageRepoPort;
use bcs_service_api::{
    AuthenticatedHumanCaller, BotDeliveryCommand, BotDeliveryPort, BotDeliveryResult,
    BotDeliveryTarget, CallbackChannelConfig, CallbackConfig, ChatEventState,
    CollaborationEventRepoPort, CollaborationRuntimeError, CollaborationRuntimeService,
    ConfigureGroupRuntimeCommand, DefinitionYamlSource, FrontendDeliveryCommand,
    FrontendDeliveryPort, FrontendDeliveryResult, FrontendDeliveryTarget, GroupCoreService,
    GroupDeleteCommand, GroupManagementService, GroupRuntimeBindingRepoPort,
    HandleSessionHumanInputCommand, HandleSessionHumanInputOutcome, HumanInputReadyEvent,
    HumanResponseSource, HumanRunAccessCommand, JudgeDecision, JudgeEvaluatorPort, JudgeRequest,
    ListPendingHumanNodesCommand,
    PatchGroupCollaborationDefinitionCommand, RespondHumanNodeCommand, RespondHumanNodeOutcome,
    ServiceError, ServiceResult, ServiceSpec, SessionChannelDeliveryOutcome,
    SessionChannelOutboundPort, SessionManagementService,
    SessionStateMachinePermissionCommand, StartSessionStateMachineRunCommand,
    StartStateMachineRunCommand, StateMachineDefinitionRepoPort, StateMachineNodeSubStatus,
    StateMachineResultPublishCommand, StateMachineResultPublisherPort,
    StateMachineRunAccessCommand, StateMachineRunRepoPort,
};
use bcs_domain::{MessageOwnerFilter, MessageQuery, STATE_MACHINE_PANEL_MESSAGE_TYPE};
use bcs_service_api::{CreateOrReactivateCommand, NewSessionParams, SessionKind};
use bcs_session::{SessionManagementServiceImpl, SessionManagementWithRuntimeCleanup};
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::{NoopBotRegistryCoreService, NoopFriendCoreService};
use serde_json::{Value, json};
use tokio::sync::{Mutex, Notify};

#[derive(Clone, Default)]
struct SharedLogBuffer(Arc<std::sync::Mutex<Vec<u8>>>);

struct SharedLogWriter {
    buffer: Arc<std::sync::Mutex<Vec<u8>>>,
}

impl Write for SharedLogWriter {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        self.buffer.lock().unwrap().extend_from_slice(buf);
        Ok(buf.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

impl<'a> tracing_subscriber::fmt::MakeWriter<'a> for SharedLogBuffer {
    type Writer = SharedLogWriter;

    fn make_writer(&'a self) -> Self::Writer {
        SharedLogWriter {
            buffer: self.0.clone(),
        }
    }
}

async fn capture_tracing_logs<Fut, T>(future: Fut) -> (T, String)
where
    Fut: Future<Output = T>,
{
    static BUFFER: OnceLock<SharedLogBuffer> = OnceLock::new();
    let buffer = BUFFER
        .get_or_init(|| {
            let buffer = SharedLogBuffer::default();
            let subscriber = tracing_subscriber::fmt()
                .with_ansi(false)
                .with_level(false)
                .with_target(true)
                .with_writer(buffer.clone())
                .finish();
            tracing::subscriber::set_global_default(subscriber)
                .expect("install tracing subscriber");
            buffer
        })
        .clone();
    buffer.0.lock().unwrap().clear();
    let output = future.await;
    let logs = String::from_utf8(buffer.0.lock().unwrap().clone()).unwrap();
    (output, logs)
}

fn test_sessions() -> Arc<SessionManagementServiceImpl> {
    Arc::new(SessionManagementServiceImpl::new(
        Arc::new(MemorySessionRepo::new()),
        Arc::new(MemoryGroupRepo::new()),
    ))
}

#[derive(Default)]
struct RecordingSessionChannelOutbound {
    events: Mutex<Vec<HumanInputReadyEvent>>,
    validation_calls: Mutex<Vec<(String, String)>>,
    validation_error: Mutex<Option<String>>,
}

#[async_trait]
impl SessionChannelOutboundPort for RecordingSessionChannelOutbound {
    async fn validate_human_input_channel(
        &self,
        group_id: &str,
        channel_type: &str,
    ) -> ServiceResult<SessionChannelDeliveryOutcome> {
        self.validation_calls
            .lock()
            .await
            .push((group_id.to_string(), channel_type.to_string()));
        if let Some(message) = self.validation_error.lock().await.clone() {
            return Err(ServiceError::InvalidOperation {
                message,
                request_id: None,
            });
        }
        Ok(SessionChannelDeliveryOutcome::NotApplicable)
    }

    async fn publish_human_input_ready(
        &self,
        event: HumanInputReadyEvent,
    ) -> ServiceResult<SessionChannelDeliveryOutcome> {
        self.events.lock().await.push(event);
        Ok(SessionChannelDeliveryOutcome::Delivered)
    }
}

fn assert_inferred_default_requires(definition: &CollaborationDefinition) {
    let requires = definition
        .requires
        .as_ref()
        .expect("requires should be inferred");
    assert!(
        requires
            .server_features
            .contains(&"state_machine.graph_mode.acyclic".to_string())
    );
    assert!(
        requires
            .server_features
            .contains(&"state_machine.node.kind.bot_task".to_string())
    );
    assert!(
        requires
            .server_features
            .contains(&"state_machine.transitions.complete".to_string())
    );
    assert!(
        requires
            .bot_runtime_features
            .contains(&"delivery.chat_send_task_compat".to_string())
    );
}

#[tokio::test]
async fn current_session_permission_is_owned_by_chat_and_manager_group_driver() {
    let group_store = Arc::new(GroupStore::new());
    let chat_group = session_collaboration_group(GroupStrategy::Chat);
    group_store
        .upsert(chat_group.clone())
        .await
        .expect("seed chat group");
    let sessions = test_sessions();
    let session = sessions
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: chat_group.id.clone(),
            session_id: None,
            params: NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: chat_group.participants.clone(),
                ..Default::default()
            },
        })
        .await
        .expect("seed chat session")
        .session;
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group_store.clone(),
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    );

    let chat_owner = runtime
        .get_session_state_machine_permission(SessionStateMachinePermissionCommand {
            session_id: session.id.clone(),
            caller_bot_id: "driver-bot".to_string(),
        })
        .await
        .expect("query chat owner permission");
    assert!(chat_owner.allowed);
    assert_eq!(chat_owner.reason_code, "allowed");
    assert_eq!(chat_owner.group_strategy, "chat");
    assert_eq!(chat_owner.group_owner_bot_id, "driver-bot");

    let chat_member = runtime
        .get_session_state_machine_permission(SessionStateMachinePermissionCommand {
            session_id: session.id.clone(),
            caller_bot_id: "worker-bot".to_string(),
        })
        .await
        .expect("query chat member permission");
    assert!(!chat_member.allowed);
    assert_eq!(chat_member.reason_code, "caller_not_group_owner");
    let bypass = runtime
        .start_session_state_machine_run(StartSessionStateMachineRunCommand {
            session_id: session.id.clone(),
            caller_bot_id: "worker-bot".to_string(),
            definition_yaml: one_shot_authoring_yaml(),
            participant_bindings: BTreeMap::from([(
                "writer".to_string(),
                RuntimeParticipantBinding {
                    source: "manual".to_string(),
                    bot_ids: vec!["worker-bot".to_string()],
                    extensions: Default::default(),
                },
            )]),
            input: Value::Null,
            judge_available: false,
        })
        .await
        .expect_err("run start must re-check permission server-side");
    assert!(matches!(
        bypass,
        CollaborationRuntimeError::Forbidden(message)
            if message.contains("caller_not_group_owner")
    ));

    let manager_group = session_collaboration_group(GroupStrategy::ManagerWorker);
    group_store
        .upsert(manager_group)
        .await
        .expect("switch to manager-worker group");
    let manager = runtime
        .get_session_state_machine_permission(SessionStateMachinePermissionCommand {
            session_id: session.id.clone(),
            caller_bot_id: "driver-bot".to_string(),
        })
        .await
        .expect("query manager permission");
    assert!(manager.allowed);
    assert_eq!(manager.group_strategy, "manager_worker");

    let state_machine_group = session_collaboration_group(GroupStrategy::StateMachine);
    group_store
        .upsert(state_machine_group)
        .await
        .expect("switch to state-machine group");
    let unsupported = runtime
        .get_session_state_machine_permission(SessionStateMachinePermissionCommand {
            session_id: session.id,
            caller_bot_id: "driver-bot".to_string(),
        })
        .await
        .expect("query unsupported strategy");
    assert!(!unsupported.allowed);
    assert_eq!(unsupported.reason_code, "unsupported_group_strategy");
}

#[tokio::test]
async fn one_shot_session_run_uses_transient_bindings_keeps_chat_open_and_publishes_as_initiator() {
    let group_store = Arc::new(GroupStore::new());
    let group = test_group();
    group_store
        .upsert(group.clone())
        .await
        .expect("seed group");
    let mut session_participants = group.participants.clone();
    session_participants.push(Participant {
        bot_uuid: "worker-bot".to_string(),
        bot_name: Some("Worker".to_string()),
        kind: None,
        role: ParticipantRole::Consultant,
        actor_kind: ActorKind::Bot,
        mode: Some(ParticipantMode::Auto),
    });
    let sessions = test_sessions();
    let session = sessions
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: group.id.clone(),
            session_id: None,
            params: NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: session_participants,
                ..Default::default()
            },
        })
        .await
        .expect("seed chat session")
        .session;
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let frontend_delivery = Arc::new(RecordingFrontendDelivery::default());
    let result_publisher = Arc::new(RecordingResultPublisher::default());
    let message_repo = Arc::new(MemoryMessageRepo::new());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group_store,
        sessions.clone(),
        delivery.clone(),
        noop_judge(),
    )
    .with_frontend_delivery(frontend_delivery.clone())
    .with_message_repo(message_repo.clone())
    .with_result_publisher(result_publisher.clone());

    let started = runtime
        .start_session_state_machine_run(StartSessionStateMachineRunCommand {
            session_id: session.id.clone(),
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
            input: json!({"question": "resolve this"}),
            judge_available: false,
        })
        .await
        .expect("start one-shot run");

    assert_eq!(started.view.run.session_id, session.id);
    assert_eq!(started.view.run.created_by.as_deref(), Some("driver-bot"));
    assert_eq!(
        started.view.nodes[0].assignee_bot_id.as_deref(),
        Some("worker-bot")
    );
    assert_eq!(delivery.commands.lock().await[0].target_bot_id(), "worker-bot");
    assert!(
        GroupRuntimeBindingRepoPort::get(&*store, "group-1")
            .await
            .expect("read persisted group binding")
            .is_none(),
        "one-shot role bindings must not mutate group runtime configuration"
    );
    assert!(
        StateMachineDefinitionRepoPort::get(
            &*store,
            &started.view.run.definition_id,
            started.view.run.definition_version,
        )
        .await
        .expect("read global definition store")
        .is_none(),
        "one-shot inline YAML must not create a reusable global definition"
    );
    let run_snapshot = StateMachineDefinitionRepoPort::get_run_snapshot(
        &*store,
        &started.view.run.run_id,
    )
    .await
    .expect("read one-shot run snapshot")
    .expect("one-shot run snapshot");
    assert_eq!(run_snapshot.id, started.view.run.definition_id);

    let frontend_commands = frontend_delivery.commands.lock().await;
    assert_eq!(frontend_commands.len(), 1);
    let panel_event: Value =
        serde_json::from_str(&frontend_commands[0].event_json).expect("panel event json");
    let panel_text = panel_event["payload"]["message"]["content"][0]["text"]
        .as_str()
        .expect("panel AixUI text");
    assert!(panel_text.contains("<AixUI"));
    assert!(panel_text.contains("bcsPanel.StateMachineRunView"));
    drop(frontend_commands);

    let panel_history = message_repo
        .query_messages(MessageQuery {
            group_id: group.id.clone(),
            session_id: session.id.clone(),
            cursor: None,
            limit: 10,
            keyword: None,
            sender_id: None,
            message_type: Some(STATE_MACHINE_PANEL_MESSAGE_TYPE.to_string()),
            owner_filter: MessageOwnerFilter::Any,
            time_range: None,
            visible_from_seq: None,
        })
        .await
        .expect("query persisted panel history");
    assert_eq!(panel_history.messages.len(), 1);
    let persisted_panel = &panel_history.messages[0];
    assert_eq!(
        persisted_panel.client_msg_id.as_deref(),
        Some(format!("{}:000-panel", started.view.run.run_id).as_str())
    );
    assert_eq!(
        persisted_panel.content["metadata"]["state_machine"]["event"].as_str(),
        Some("panel")
    );
    assert_eq!(
        persisted_panel.content["metadata"]["state_machine"]["run_id"].as_str(),
        Some(started.view.run.run_id.as_str())
    );
    assert_eq!(persisted_panel.content["text"].as_str(), Some(panel_text));

    let while_running = runtime
        .get_session_state_machine_permission(SessionStateMachinePermissionCommand {
            session_id: session.id.clone(),
            caller_bot_id: "driver-bot".to_string(),
        })
        .await
        .expect("query permission while run is active");
    assert!(!while_running.allowed);
    assert_eq!(while_running.reason_code, "state_machine_run_active");
    assert_eq!(
        while_running.active_run_id.as_deref(),
        Some(started.view.run.run_id.as_str())
    );

    let delivery_run_id = delivery.commands.lock().await[0].run_id.clone();
    let completed = runtime
        .handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
            bot_id: "worker-bot".to_string(),
            run_id: delivery_run_id.clone(),
            event_type: "chat.event".to_string(),
            event_payload: json!({
                "run_id": delivery_run_id,
                "bcs_group_id": "group-1",
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "resolved result"}]
                }
            }),
            state: ChatEventState::Final,
            bcs_session_id: Some(session.id.clone()),
        })
        .await
        .expect("complete one-shot run");

    let view = completed.view.expect("completed run view");
    assert_eq!(view.run.status, StateMachineRunStatus::Completed);
    assert_eq!(view.run.output.as_deref(), Some("resolved result"));
    let publish_commands = result_publisher.commands.lock().await;
    assert_eq!(publish_commands.len(), 1);
    assert_eq!(publish_commands[0].run_id, view.run.run_id);
    assert_eq!(publish_commands[0].group_id, "group-1");
    assert_eq!(publish_commands[0].session_id, session.id);
    assert_eq!(publish_commands[0].sender_bot_id, "driver-bot");
    assert_eq!(publish_commands[0].content, "resolved result");
    drop(publish_commands);

    let preserved_session = sessions
        .get(&session.id)
        .await
        .expect("read chat session")
        .expect("chat session exists");
    assert_eq!(preserved_session.status, SessionStatus::Running);
}

#[tokio::test]
async fn one_shot_result_publication_failure_marks_run_failed_instead_of_completed() {
    let group_store = Arc::new(GroupStore::new());
    let group = test_group();
    group_store
        .upsert(group.clone())
        .await
        .expect("seed group");
    let mut session_participants = group.participants.clone();
    session_participants.push(Participant {
        bot_uuid: "worker-bot".to_string(),
        bot_name: Some("Worker".to_string()),
        kind: None,
        role: ParticipantRole::Consultant,
        actor_kind: ActorKind::Bot,
        mode: Some(ParticipantMode::Auto),
    });
    let sessions = test_sessions();
    let session = sessions
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: group.id.clone(),
            session_id: None,
            params: NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: session_participants,
                ..Default::default()
            },
        })
        .await
        .expect("seed chat session")
        .session;
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group_store,
        sessions.clone(),
        delivery.clone(),
        noop_judge(),
    )
    .with_message_repo(Arc::new(MemoryMessageRepo::new()))
    .with_result_publisher(Arc::new(FailingResultPublisher));

    let started = runtime
        .start_session_state_machine_run(StartSessionStateMachineRunCommand {
            session_id: session.id.clone(),
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
            input: json!({"question": "resolve this"}),
            judge_available: false,
        })
        .await
        .expect("start one-shot run");
    let delivery_run_id = delivery.commands.lock().await[0].run_id.clone();
    let completed = runtime
        .handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
            bot_id: "worker-bot".to_string(),
            run_id: delivery_run_id.clone(),
            event_type: "chat.event".to_string(),
            event_payload: json!({
                "run_id": delivery_run_id,
                "bcs_group_id": "group-1",
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "resolved result"}]
                }
            }),
            state: ChatEventState::Final,
            bcs_session_id: Some(session.id.clone()),
        })
        .await
        .expect("terminal event returns failed run");

    let view = completed.view.expect("failed run view");
    assert_eq!(view.run.run_id, started.view.run.run_id);
    assert_eq!(view.run.status, StateMachineRunStatus::Failed);
    assert!(
        view.run
            .error
            .as_deref()
            .is_some_and(|error| error.contains("result publication failed"))
    );
    assert_eq!(
        sessions
            .get(&session.id)
            .await
            .expect("read chat session")
            .expect("chat session")
            .status,
        SessionStatus::Running
    );
}

#[tokio::test]
async fn human_input_without_authenticated_or_present_human_is_invalid_request() {
    let group = Arc::new(GroupStore::new());
    group
        .upsert(state_machine_test_group())
        .await
        .expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group,
        sessions,
        delivery.clone(),
        noop_judge(),
    );

    let error = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(human_input_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"proposal": "ship it"}),
            caller_id: Some("human_untrusted".to_string()),
            authenticated_human: None,
        })
        .await
        .expect_err("HumanInput must require a Present Human session participant");

    assert!(matches!(
        error,
        CollaborationRuntimeError::InvalidRequest(message)
            if message.contains("Present Human session participant")
    ));
    assert!(delivery.commands.lock().await.is_empty());
}

#[tokio::test]
async fn human_input_can_start_without_authenticated_human_when_session_has_present_human() {
    let group = Arc::new(GroupStore::new());
    let seeded_group = state_machine_test_group();
    group
        .upsert(seeded_group.clone())
        .await
        .expect("seed group");
    let sessions = test_sessions();
    let mut reviewer = Participant::human("human_1001", ParticipantRole::Observer);
    reviewer.mode = Some(ParticipantMode::Present);
    let session = sessions
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: seeded_group.id.clone(),
            session_id: None,
            params: NewSessionParams {
                session_kind: SessionKind::ServiceInvocation,
                participants: vec![reviewer],
                ..Default::default()
            },
        })
        .await
        .expect("seed session")
        .session;
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: seeded_group.id,
            session_id: Some(session.id),
            definition_yaml: Some(human_input_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"proposal": "ship it"}),
            caller_id: Some("bot_driver".to_string()),
            authenticated_human: None,
        })
        .await
        .expect("existing Present Human should allow service invocation");

    assert_eq!(
        started.view.nodes[0].status,
        StateMachineNodeStatus::Running
    );
}

#[tokio::test]
async fn authenticated_human_is_added_or_restored_before_human_input_starts() {
    let group = Arc::new(GroupStore::new());
    let seeded_group = state_machine_test_group();
    group
        .upsert(seeded_group.clone())
        .await
        .expect("seed group");
    let sessions = test_sessions();
    let session = sessions
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: seeded_group.id.clone(),
            session_id: None,
            params: NewSessionParams {
                session_kind: SessionKind::ServiceInvocation,
                participants: seeded_group.participants.clone(),
                ..Default::default()
            },
        })
        .await
        .expect("seed session")
        .session;
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group,
        sessions.clone(),
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    );

    runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: seeded_group.id.clone(),
            session_id: Some(session.id.clone()),
            definition_yaml: Some(human_input_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"proposal": "ship it"}),
            caller_id: Some("bot_driver".to_string()),
            authenticated_human: Some(AuthenticatedHumanCaller {
                actor_id: "human_1001".to_string(),
                display_name: Some("Reviewer".to_string()),
            }),
        })
        .await
        .expect("authenticated Human should be materialized into session");

    let updated = sessions
        .get(&session.id)
        .await
        .expect("read session")
        .expect("session exists");
    let reviewer = updated
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == "human_1001")
        .expect("Human added");
    assert!(reviewer.is_human());
    assert_eq!(reviewer.role, ParticipantRole::Observer);
    assert_eq!(reviewer.effective_mode(), ParticipantMode::Present);
    assert_eq!(reviewer.bot_name.as_deref(), Some("Reviewer"));

    let mut returning_reviewer = Participant::human("human_2002", ParticipantRole::Observer);
    returning_reviewer.mode = Some(ParticipantMode::Absent);
    let returning_session = sessions
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: seeded_group.id.clone(),
            session_id: None,
            params: NewSessionParams {
                session_kind: SessionKind::ServiceInvocation,
                participants: vec![returning_reviewer],
                ..Default::default()
            },
        })
        .await
        .expect("seed session with absent Human")
        .session;

    runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: seeded_group.id,
            session_id: Some(returning_session.id.clone()),
            definition_yaml: Some(
                human_input_yaml()
                    .replace("human_input_single", "returning_human_input")
                    .replace("human_1001", "human_2002"),
            ),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"proposal": "ship it"}),
            caller_id: Some("bot_driver".to_string()),
            authenticated_human: Some(AuthenticatedHumanCaller {
                actor_id: "human_2002".to_string(),
                display_name: Some("Returning Reviewer".to_string()),
            }),
        })
        .await
        .expect("authenticated Human should be restored to Present");

    let updated = sessions
        .get(&returning_session.id)
        .await
        .expect("read returning session")
        .expect("returning session exists");
    let reviewer = updated
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == "human_2002")
        .expect("returning Human retained");
    assert_eq!(reviewer.effective_mode(), ParticipantMode::Present);
}

#[tokio::test]
async fn human_input_waits_without_bot_delivery_and_completes_from_natural_language() {
    let group = Arc::new(GroupStore::new());
    group
        .upsert(state_machine_test_group())
        .await
        .expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let channel_outbound = Arc::new(RecordingSessionChannelOutbound::default());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group,
        sessions,
        delivery.clone(),
        noop_judge(),
    )
    .with_session_channel_outbound(channel_outbound.clone());

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(human_input_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"proposal": "ship it"}),
            caller_id: Some("human_1001".to_string()),
            authenticated_human: Some(AuthenticatedHumanCaller {
                actor_id: "human_1001".to_string(),
                display_name: Some("Reviewer".to_string()),
            }),
        })
        .await
        .expect("start human run");

    let unauthenticated_read = runtime
        .get_state_machine_run_with_access(StateMachineRunAccessCommand {
            run_id: started.view.run.run_id.clone(),
            authenticated_human: None,
        })
        .await
        .expect_err("HumanInput run must reject unauthenticated reads");
    assert!(matches!(
        unauthenticated_read,
        CollaborationRuntimeError::Unauthenticated
    ));

    assert!(delivery.commands.lock().await.is_empty());
    assert_eq!(
        started.view.nodes[0].status,
        StateMachineNodeStatus::Running
    );
    assert!(started.view.nodes[0].assignee_bot_id.is_none());
    assert!(started.view.nodes[0].delivery_request_id.is_none());
    let channel_events = channel_outbound.events.lock().await;
    assert_eq!(channel_events.len(), 1);
    assert_eq!(channel_events[0].run_id, started.view.run.run_id);
    assert_eq!(channel_events[0].node_id, "review");
    assert_eq!(
        channel_events[0].response_ref,
        format!("{}/review", started.view.run.run_id)
    );
    assert_eq!(channel_events[0].display_name, "Review");
    assert_eq!(channel_events[0].instruction, "请用自然语言给出你的意见。");
    assert!(channel_events[0].upstream_artifacts.is_empty());
    drop(channel_events);

    let human_access = HumanRunAccessCommand {
        run_id: started.view.run.run_id.clone(),
        caller_actor_id: "human_1001".to_string(),
    };
    assert!(
        runtime
            .get_state_machine_run_for_human(human_access.clone())
            .await
            .expect("Human reads run")
            .is_some()
    );
    assert!(
        runtime
            .get_state_machine_node_run_for_human(human_access.clone(), "review")
            .await
            .expect("Human reads node")
            .is_some()
    );
    assert!(
        runtime
            .get_state_machine_run_graph_for_human(human_access.clone())
            .await
            .expect("Human reads graph")
            .is_some()
    );
    let authenticated_access = StateMachineRunAccessCommand {
        run_id: started.view.run.run_id.clone(),
        authenticated_human: Some(AuthenticatedHumanCaller {
            actor_id: "human_1001".to_string(),
            display_name: Some("Reviewer".to_string()),
        }),
    };
    assert!(
        runtime
            .get_state_machine_node_run_with_access(authenticated_access.clone(), "review")
            .await
            .expect("authenticated Human reads node")
            .is_some()
    );
    assert!(
        runtime
            .get_state_machine_run_graph_with_access(authenticated_access)
            .await
            .expect("authenticated Human reads graph")
            .is_some()
    );
    let pending = runtime
        .list_pending_human_nodes(ListPendingHumanNodesCommand {
            run_id: started.view.run.run_id.clone(),
            caller_actor_id: "human_1001".to_string(),
        })
        .await
        .expect("list pending human nodes");
    assert_eq!(pending.len(), 1);
    assert_eq!(
        pending[0].response_ref,
        format!("{}/review", started.view.run.run_id)
    );
    let forbidden = runtime
        .list_pending_human_nodes(ListPendingHumanNodesCommand {
            run_id: started.view.run.run_id.clone(),
            caller_actor_id: "human_2002".to_string(),
        })
        .await
        .expect_err("non-participant must not inspect pending Human nodes");
    assert!(matches!(forbidden, CollaborationRuntimeError::Forbidden(_)));

    for content in ["   ".to_string(), "x".repeat(64 * 1024 + 1)] {
        let invalid = runtime
            .respond_human_node(RespondHumanNodeCommand {
                run_id: started.view.run.run_id.clone(),
                node_id: "review".to_string(),
                caller_actor_id: "human_1001".to_string(),
                content,
                source: HumanResponseSource::Http,
            })
            .await
            .expect_err("invalid Human response must be rejected");
        assert!(matches!(
            invalid,
            CollaborationRuntimeError::InvalidRequest(_)
        ));
    }

    let completed = runtime
        .handle_session_human_input(HandleSessionHumanInputCommand {
            group_id: "group-1".to_string(),
            session_id: Some(started.view.run.session_id.clone()),
            caller_actor_id: "human_1001".to_string(),
            content: "这个方案可以发布".to_string(),
            source: HumanResponseSource::Http,
        })
        .await
        .expect("route session message to HumanInput");
    let HandleSessionHumanInputOutcome::Consumed {
        response: completed,
    } = completed
    else {
        panic!("state-machine session message should be consumed")
    };
    let RespondHumanNodeOutcome { node, run } = completed;
    assert_eq!(node.status, StateMachineNodeStatus::Completed);
    assert_eq!(node.outcome.as_deref(), Some("complete"));
    assert_eq!(node.responded_by.as_deref(), Some("human_1001"));
    assert_eq!(node.artifact_text.as_deref(), Some("这个方案可以发布"));
    assert_eq!(run.status, StateMachineRunStatus::Completed);

    let history = runtime
        .get_state_machine_session_history(&run.session_id, 20, None)
        .await
        .expect("human history")
        .expect("history result");
    let human_message = history
        .messages
        .iter()
        .find(|message| message.sender == "human_1001")
        .expect("human history message");
    assert_eq!(human_message.message_type, GroupMessageType::Bot);
    assert_eq!(human_message.role, MessageRole::User);
    assert_eq!(human_message.bot_name.as_deref(), Some("Reviewer"));
    let no_longer_pending = runtime
        .list_pending_human_nodes(ListPendingHumanNodesCommand {
            run_id: run.run_id,
            caller_actor_id: "human_1001".to_string(),
        })
        .await
        .expect("completed Human run has no pending nodes");
    assert!(no_longer_pending.is_empty());
}

#[tokio::test]
async fn frontend_human_input_skips_im_delivery_and_accepts_present_human() {
    let group = Arc::new(GroupStore::new());
    group
        .upsert(state_machine_test_group())
        .await
        .expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let channel_outbound = Arc::new(RecordingSessionChannelOutbound::default());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group,
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    )
    .with_session_channel_outbound(channel_outbound.clone());

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(frontend_human_input_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"proposal": "review in frontend"}),
            caller_id: Some("human_1001".to_string()),
            authenticated_human: Some(AuthenticatedHumanCaller {
                actor_id: "human_1001".to_string(),
                display_name: Some("Reviewer".to_string()),
            }),
        })
        .await
        .expect("start frontend HumanInput run");

    assert_eq!(
        started.view.nodes[0].status,
        StateMachineNodeStatus::Running
    );
    assert!(channel_outbound.events.lock().await.is_empty());

    let pending = runtime
        .list_pending_human_nodes(ListPendingHumanNodesCommand {
            run_id: started.view.run.run_id.clone(),
            caller_actor_id: "human_1001".to_string(),
        })
        .await
        .expect("frontend Human lists pending node");
    assert_eq!(pending.len(), 1);

    let completed = runtime
        .respond_human_node(RespondHumanNodeCommand {
            run_id: started.view.run.run_id,
            node_id: "review".to_string(),
            caller_actor_id: "human_1001".to_string(),
            content: "approved in frontend".to_string(),
            source: HumanResponseSource::Http,
        })
        .await
        .expect("Present Human responds from frontend");

    assert_eq!(completed.node.status, StateMachineNodeStatus::Completed);
    assert_eq!(completed.run.status, StateMachineRunStatus::Completed);
}

#[tokio::test]
async fn im_human_input_rejects_a_different_present_human() {
    let group = Arc::new(GroupStore::new());
    let seeded_group = state_machine_test_group();
    group
        .upsert(seeded_group.clone())
        .await
        .expect("seed group");
    let sessions = test_sessions();
    let mut assigned = Participant::human("human_1001", ParticipantRole::Observer);
    assigned.mode = Some(ParticipantMode::Present);
    let mut other = Participant::human("human_2002", ParticipantRole::Observer);
    other.mode = Some(ParticipantMode::Present);
    let session = sessions
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: seeded_group.id.clone(),
            session_id: None,
            params: NewSessionParams {
                session_kind: SessionKind::ServiceInvocation,
                participants: vec![assigned, other],
                ..Default::default()
            },
        })
        .await
        .expect("seed session")
        .session;
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group,
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: seeded_group.id,
            session_id: Some(session.id),
            definition_yaml: Some(human_input_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"proposal": "assigned review"}),
            caller_id: Some("bot_driver".to_string()),
            authenticated_human: None,
        })
        .await
        .expect("start assigned IM HumanInput run");

    let pending = runtime
        .list_pending_human_nodes(ListPendingHumanNodesCommand {
            run_id: started.view.run.run_id.clone(),
            caller_actor_id: "human_2002".to_string(),
        })
        .await
        .expect("other Present Human may inspect the run");
    assert!(pending.is_empty());

    let error = runtime
        .respond_human_node(RespondHumanNodeCommand {
            run_id: started.view.run.run_id,
            node_id: "review".to_string(),
            caller_actor_id: "human_2002".to_string(),
            content: "attempted response".to_string(),
            source: HumanResponseSource::Http,
        })
        .await
        .expect_err("IM HumanInput must reject a non-assignee");
    assert!(matches!(error, CollaborationRuntimeError::Forbidden(_)));
}

#[tokio::test]
async fn state_machine_session_rejects_message_without_pending_human_input() {
    let group = Arc::new(GroupStore::new());
    group
        .upsert(state_machine_test_group())
        .await
        .expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group,
        sessions,
        delivery.clone(),
        noop_judge(),
    );
    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"question": "wait for the bot"}),
            caller_id: Some("human_1001".to_string()),
            authenticated_human: Some(AuthenticatedHumanCaller {
                actor_id: "human_1001".to_string(),
                display_name: Some("Reviewer".to_string()),
            }),
        })
        .await
        .expect("start bot run");

    let error = runtime
        .handle_session_human_input(HandleSessionHumanInputCommand {
            group_id: "group-1".to_string(),
            session_id: Some(started.view.run.session_id),
            caller_actor_id: "human_1001".to_string(),
            content: "这条消息不能发送给 bot".to_string(),
            source: HumanResponseSource::Http,
        })
        .await
        .expect_err("state-machine chat must reject input while no HumanInput is pending");
    assert!(matches!(error, CollaborationRuntimeError::Conflict(_)));
    let non_human_node = runtime
        .respond_human_node(RespondHumanNodeCommand {
            run_id: started.view.run.run_id,
            node_id: "answer".to_string(),
            caller_actor_id: "human_1001".to_string(),
            content: "不能直接响应 bot 节点".to_string(),
            source: HumanResponseSource::Http,
        })
        .await
        .expect_err("Human response endpoint must reject bot_task nodes");
    assert!(matches!(
        non_human_node,
        CollaborationRuntimeError::InvalidRequest(_)
    ));
    assert_eq!(delivery.commands.lock().await.len(), 1);
}

#[tokio::test]
async fn human_run_owner_can_cancel_through_human_access_api() {
    let group = Arc::new(GroupStore::new());
    group
        .upsert(state_machine_test_group())
        .await
        .expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group,
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(human_input_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"proposal": "cancel it"}),
            caller_id: Some("human_1001".to_string()),
            authenticated_human: Some(AuthenticatedHumanCaller {
                actor_id: "human_1001".to_string(),
                display_name: Some("Reviewer".to_string()),
            }),
        })
        .await
        .expect("start Human run");

    let cancelled = runtime
        .cancel_state_machine_run_for_human(
            HumanRunAccessCommand {
                run_id: started.view.run.run_id,
                caller_actor_id: "human_1001".to_string(),
            },
            Some("user cancelled".to_string()),
        )
        .await
        .expect("run owner cancels Human run");

    assert_eq!(cancelled.run.status, StateMachineRunStatus::Aborted);
    assert_eq!(cancelled.nodes[0].status, StateMachineNodeStatus::Running);
}

#[tokio::test]
async fn human_runtime_rejects_missing_context_and_invalid_response_targets() {
    let group = Arc::new(GroupStore::new());
    group
        .upsert(state_machine_test_group())
        .await
        .expect("seed state-machine group");
    let mut second_state_machine_group = state_machine_test_group();
    second_state_machine_group.id = "group-2".to_string();
    group
        .upsert(second_state_machine_group)
        .await
        .expect("seed second state-machine group");
    let mut regular_group = test_group();
    regular_group.id = "group-regular".to_string();
    group
        .upsert(regular_group)
        .await
        .expect("seed regular group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group,
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(human_input_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"proposal": "validate errors"}),
            caller_id: Some("human_1001".to_string()),
            authenticated_human: Some(AuthenticatedHumanCaller {
                actor_id: "human_1001".to_string(),
                display_name: Some("Reviewer".to_string()),
            }),
        })
        .await
        .expect("start Human run");

    for (group_id, session_id, expected_fragment) in [
        ("missing-group", None, "group not found"),
        (
            "group-1",
            Some("missing-session".to_string()),
            "session not found",
        ),
    ] {
        let error = runtime
            .start_state_machine_run(StartStateMachineRunCommand {
                group_id: group_id.to_string(),
                session_id,
                definition_yaml: Some(human_input_yaml()),
                definition: None,
                definition_ref: None,
                participant_bindings: None,
                input: json!({"proposal": "invalid start"}),
                caller_id: Some("human_1001".to_string()),
                authenticated_human: Some(AuthenticatedHumanCaller {
                    actor_id: "human_1001".to_string(),
                    display_name: Some("Reviewer".to_string()),
                }),
            })
            .await
            .expect_err("invalid run context must reject start");
        assert!(error.to_string().contains(expected_fragment));
    }

    let not_state_machine = runtime
        .handle_session_human_input(HandleSessionHumanInputCommand {
            group_id: "group-regular".to_string(),
            session_id: None,
            caller_actor_id: "human_1001".to_string(),
            content: "regular chat".to_string(),
            source: HumanResponseSource::Http,
        })
        .await
        .expect("regular group remains outside state-machine input routing");
    assert!(matches!(
        not_state_machine,
        HandleSessionHumanInputOutcome::NotStateMachine
    ));

    for (session_id, expected_fragment) in [
        (None, "require a session id"),
        (Some("missing-session".to_string()), "has no active run"),
        (
            Some(started.view.run.session_id.clone()),
            "does not belong to the target group",
        ),
    ] {
        let group_id = if expected_fragment.contains("target group") {
            "group-2"
        } else {
            "group-1"
        };
        let error = runtime
            .handle_session_human_input(HandleSessionHumanInputCommand {
                group_id: group_id.to_string(),
                session_id,
                caller_actor_id: "human_1001".to_string(),
                content: "invalid context".to_string(),
                source: HumanResponseSource::Http,
            })
            .await
            .expect_err("invalid state-machine input context must be rejected");
        assert!(error.to_string().contains(expected_fragment));
    }

    let missing_run = runtime
        .respond_human_node(RespondHumanNodeCommand {
            run_id: "missing-run".to_string(),
            node_id: "review".to_string(),
            caller_actor_id: "human_1001".to_string(),
            content: "approve".to_string(),
            source: HumanResponseSource::Http,
        })
        .await
        .expect_err("missing run must be rejected");
    assert!(matches!(
        missing_run,
        CollaborationRuntimeError::RunNotFound(_)
    ));

    let missing_node = runtime
        .respond_human_node(RespondHumanNodeCommand {
            run_id: started.view.run.run_id.clone(),
            node_id: "missing-node".to_string(),
            caller_actor_id: "human_1001".to_string(),
            content: "approve".to_string(),
            source: HumanResponseSource::Http,
        })
        .await
        .expect_err("missing Human node must be rejected");
    assert!(matches!(
        missing_node,
        CollaborationRuntimeError::NodeNotFound { .. }
    ));

    let missing_access = HumanRunAccessCommand {
        run_id: "missing-run".to_string(),
        caller_actor_id: "human_1001".to_string(),
    };
    assert!(
        runtime
            .get_state_machine_run_for_human(missing_access.clone())
            .await
            .expect("missing run lookup")
            .is_none()
    );
    assert!(
        runtime
            .get_state_machine_node_run_for_human(missing_access.clone(), "review")
            .await
            .expect("missing node run lookup")
            .is_none()
    );
    assert!(
        runtime
            .get_state_machine_run_graph_for_human(missing_access)
            .await
            .expect("missing graph lookup")
            .is_none()
    );

    runtime
        .cancel_state_machine_run_for_human(
            HumanRunAccessCommand {
                run_id: started.view.run.run_id.clone(),
                caller_actor_id: "human_1001".to_string(),
            },
            None,
        )
        .await
        .expect("cancel Human run");
    let late_response = runtime
        .respond_human_node(RespondHumanNodeCommand {
            run_id: started.view.run.run_id,
            node_id: "review".to_string(),
            caller_actor_id: "human_1001".to_string(),
            content: "too late".to_string(),
            source: HumanResponseSource::Http,
        })
        .await
        .expect_err("aborted run must reject Human responses");
    assert!(matches!(
        late_response,
        CollaborationRuntimeError::Conflict(_)
    ));
}

#[tokio::test]
async fn human_input_uses_the_same_judge_contract_as_bot_output() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let judge = Arc::new(SequencedJudge::new(vec![JudgeDecision {
        outcome: "approved".to_string(),
        reason: "approval".to_string(),
        confidence: 0.4,
        checked_criteria: Vec::new(),
        retry_instruction: "unused by the regular judge contract".to_string(),
        raw_response: None,
    }]));
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group,
        sessions,
        Arc::new(RecordingDelivery::default()),
        judge.clone(),
    );
    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(judged_human_input_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"proposal": "ship it"}),
            caller_id: Some("human_1001".to_string()),
            authenticated_human: Some(AuthenticatedHumanCaller {
                actor_id: "human_1001".to_string(),
                display_name: Some("Reviewer".to_string()),
            }),
        })
        .await
        .expect("start judged Human run");

    let completed = runtime
        .respond_human_node(RespondHumanNodeCommand {
            run_id: started.view.run.run_id.clone(),
            node_id: "review".to_string(),
            caller_actor_id: "human_1001".to_string(),
            content: "看起来还行".to_string(),
            source: HumanResponseSource::Http,
        })
        .await
        .expect("Judge outcome should complete the Human node");
    let RespondHumanNodeOutcome { node, run } = completed;
    assert_eq!(node.outcome.as_deref(), Some("approved"));
    assert_eq!(node.artifact_text.as_deref(), Some("看起来还行"));
    assert_eq!(run.status, StateMachineRunStatus::Completed);
    assert_eq!(judge.requests.lock().await.len(), 1);
}

#[tokio::test]
async fn human_input_is_persisted_and_no_longer_pending_while_judging() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let judge = Arc::new(BlockingJudge::new("approved"));
    let runtime = Arc::new(CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group,
        sessions,
        Arc::new(RecordingDelivery::default()),
        judge.clone(),
    ));
    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(judged_human_input_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"proposal": "ship it"}),
            caller_id: Some("human_1001".to_string()),
            authenticated_human: Some(AuthenticatedHumanCaller {
                actor_id: "human_1001".to_string(),
                display_name: Some("Reviewer".to_string()),
            }),
        })
        .await
        .expect("start judged Human run");
    let run_id = started.view.run.run_id.clone();

    let response_task = tokio::spawn({
        let runtime = runtime.clone();
        let run_id = run_id.clone();
        async move {
            runtime
                .respond_human_node(RespondHumanNodeCommand {
                    run_id,
                    node_id: "review".to_string(),
                    caller_actor_id: "human_1001".to_string(),
                    content: "看起来还行".to_string(),
                    source: HumanResponseSource::Http,
                })
                .await
        }
    });
    judge.started.notified().await;

    let judging = runtime
        .get_state_machine_node_run(&run_id, "review")
        .await
        .expect("load judging Human node")
        .expect("Human node exists");
    assert_eq!(judging.node.status, StateMachineNodeStatus::Running);
    assert_eq!(judging.node.artifact_text.as_deref(), Some("看起来还行"));
    assert_eq!(judging.node.responded_by.as_deref(), Some("human_1001"));
    assert_eq!(judging.sub_status, Some(StateMachineNodeSubStatus::Judging));
    let pending = runtime
        .list_pending_human_nodes(ListPendingHumanNodesCommand {
            run_id: run_id.clone(),
            caller_actor_id: "human_1001".to_string(),
        })
        .await
        .expect("list pending Human nodes while judging");
    assert!(pending.is_empty());

    let duplicate = runtime
        .respond_human_node(RespondHumanNodeCommand {
            run_id: run_id.clone(),
            node_id: "review".to_string(),
            caller_actor_id: "human_1001".to_string(),
            content: "第二次提交".to_string(),
            source: HumanResponseSource::Http,
        })
        .await
        .expect_err("Judging Human node must reject duplicate responses");
    assert!(matches!(duplicate, CollaborationRuntimeError::Conflict(_)));

    judge.release.notify_one();
    let completed = response_task
        .await
        .expect("Human response task should join")
        .expect("Judge should complete the Human node");
    assert_eq!(completed.node.status, StateMachineNodeStatus::Completed);
    assert_eq!(completed.node.artifact_text.as_deref(), Some("看起来还行"));
}

#[tokio::test]
async fn human_input_remains_persisted_when_judge_fails() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let judge = Arc::new(RecordingJudge::with_error("judge provider unavailable"));
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        Arc::new(RecordingDelivery::default()),
        judge,
    );
    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(judged_human_input_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"proposal": "ship it"}),
            caller_id: Some("human_1001".to_string()),
            authenticated_human: Some(AuthenticatedHumanCaller {
                actor_id: "human_1001".to_string(),
                display_name: Some("Reviewer".to_string()),
            }),
        })
        .await
        .expect("start judged Human run");

    let error = runtime
        .respond_human_node(RespondHumanNodeCommand {
            run_id: started.view.run.run_id.clone(),
            node_id: "review".to_string(),
            caller_actor_id: "human_1001".to_string(),
            content: "请补充风险说明".to_string(),
            source: HumanResponseSource::Http,
        })
        .await
        .expect_err("Judge failure should fail the Human response request");
    assert!(matches!(
        error,
        CollaborationRuntimeError::JudgeUnavailable(_)
    ));

    let failed = store
        .get_node_run(&started.view.run.run_id, "review")
        .await
        .expect("load failed Human node")
        .expect("Human node exists");
    assert_eq!(failed.status, StateMachineNodeStatus::Failed);
    assert_eq!(failed.artifact_text.as_deref(), Some("请补充风险说明"));
    assert_eq!(failed.responded_by.as_deref(), Some("human_1001"));
}

#[tokio::test]
async fn human_input_timeout_fails_run_without_bot_retry_and_rejects_late_response() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group,
        sessions,
        delivery.clone(),
        noop_judge(),
    );
    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(human_input_yaml().replace("60000", "1")),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"proposal": "ship it"}),
            caller_id: Some("human_1001".to_string()),
            authenticated_human: Some(AuthenticatedHumanCaller {
                actor_id: "human_1001".to_string(),
                display_name: Some("Reviewer".to_string()),
            }),
        })
        .await
        .expect("start Human timeout run");
    tokio::time::sleep(Duration::from_millis(5)).await;

    let processed = runtime
        .process_expired_node_timeouts(10, 0)
        .await
        .expect("scan Human timeout");
    assert_eq!(processed, 1);
    let view = runtime
        .get_state_machine_run(&started.view.run.run_id)
        .await
        .expect("get timed out run")
        .expect("timed out run");
    assert_eq!(view.run.status, StateMachineRunStatus::Failed);
    assert_eq!(view.nodes[0].status, StateMachineNodeStatus::Failed);
    assert_eq!(view.nodes[0].attempt, 0);
    assert!(delivery.commands.lock().await.is_empty());

    let late = runtime
        .respond_human_node(RespondHumanNodeCommand {
            run_id: started.view.run.run_id,
            node_id: "review".to_string(),
            caller_actor_id: "human_1001".to_string(),
            content: "批准".to_string(),
            source: HumanResponseSource::Http,
        })
        .await
        .expect_err("late Human response must be rejected");
    assert!(matches!(late, CollaborationRuntimeError::Conflict(_)));
}

#[tokio::test]
async fn timeout_scanner_aborts_run_when_group_is_missing() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group.clone(),
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    );
    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(
                single_node_yaml()
                    .replace("node_timeout_ms: 60000", "node_timeout_ms: 1")
                    .replace("max_attempts: 3", "max_attempts: 1"),
            ),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"question": "group will be deleted"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start run");
    group.delete("group-1").await.expect("delete group");
    tokio::time::sleep(Duration::from_millis(5)).await;

    let processed = runtime
        .process_expired_node_timeouts(10, 0)
        .await
        .expect("scan missing-group timeout");

    assert_eq!(processed, 1);
    let view = runtime
        .get_state_machine_run(&started.view.run.run_id)
        .await
        .expect("get aborted run")
        .expect("aborted run");
    assert_eq!(view.run.status, StateMachineRunStatus::Aborted);
    assert_eq!(view.run.error.as_deref(), Some("group_not_found"));
}

#[tokio::test]
async fn timeout_scanner_aborts_run_when_session_is_missing() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group,
        sessions.clone(),
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    );
    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(
                single_node_yaml()
                    .replace("node_timeout_ms: 60000", "node_timeout_ms: 1")
                    .replace("max_attempts: 3", "max_attempts: 1"),
            ),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"question": "session will be deleted"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start run");
    sessions
        .delete(&started.view.run.session_id)
        .await
        .expect("delete session");
    tokio::time::sleep(Duration::from_millis(5)).await;

    let processed = runtime
        .process_expired_node_timeouts(10, 0)
        .await
        .expect("scan missing-session timeout");

    assert_eq!(processed, 1);
    let view = runtime
        .get_state_machine_run(&started.view.run.run_id)
        .await
        .expect("get aborted run")
        .expect("aborted run");
    assert_eq!(view.run.status, StateMachineRunStatus::Aborted);
    assert_eq!(view.run.error.as_deref(), Some("session_not_found"));
}

#[tokio::test]
async fn timeout_scanner_skips_invalid_candidate_and_processes_later_run() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    );
    let valid_yaml = single_node_yaml()
        .replace("id: single_node", "id: valid_timeout")
        .replace("node_timeout_ms: 60000", "node_timeout_ms: 1")
        .replace("max_attempts: 3", "max_attempts: 1");
    let valid = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(valid_yaml),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"question": "valid timeout"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start valid run");

    let mut invalid_definition: CollaborationDefinition = serde_yaml::from_str(
        &single_node_yaml().replace("id: single_node", "id: invalid_snapshot"),
    )
    .expect("parse invalid-definition fixture base");
    let CollaborationRuntimeDefinition::StateMachine(state_machine) =
        &mut invalid_definition.runtime
    else {
        panic!("fixture must be a state machine");
    };
    state_machine
        .nodes
        .get_mut("answer")
        .expect("answer node")
        .transitions
        .insert(
            "complete".to_string(),
            StateMachineTransition {
                targets: vec!["answer".to_string()],
                guard: None,
            },
        );
    StateMachineDefinitionRepoPort::upsert(&*store, invalid_definition)
        .await
        .expect("seed invalid historical definition");

    let mut poison_run = valid.view.run.clone();
    poison_run.run_id = "invalid-timeout-candidate".to_string();
    poison_run.definition_id = "invalid_snapshot".to_string();
    poison_run.created_at = 1;
    poison_run.updated_at = 1;
    let mut poison_node = StateMachineRunRepoPort::get_node_run(
        &*store,
        &valid.view.run.run_id,
        "answer",
    )
    .await
    .expect("read valid node")
    .expect("valid node");
    poison_node.run_id = poison_run.run_id.clone();
    poison_node.timeout_deadline_ms = Some(1);
    StateMachineRunRepoPort::create_run(&*store, poison_run.clone(), vec![poison_node])
        .await
        .expect("seed invalid timeout candidate");
    tokio::time::sleep(Duration::from_millis(5)).await;

    let processed = runtime
        .process_expired_node_timeouts(10, 0)
        .await
        .expect("scan timeout candidates");

    assert_eq!(processed, 1);
    let poison = StateMachineRunRepoPort::get_run(&*store, &poison_run.run_id)
        .await
        .expect("read invalid run")
        .expect("invalid run");
    assert_eq!(poison.status, StateMachineRunStatus::Running);
    let valid = StateMachineRunRepoPort::get_run(&*store, &valid.view.run.run_id)
        .await
        .expect("read valid run")
        .expect("valid run");
    assert_eq!(valid.status, StateMachineRunStatus::Failed);
}

#[tokio::test]
async fn single_node_run_completes_session_with_bot_final_text() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let frontend_delivery = Arc::new(RecordingFrontendDelivery::default());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions.clone(),
        delivery.clone(),
        noop_judge(),
    )
    .with_frontend_delivery(frontend_delivery.clone());

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"question": "review this"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start run");

    assert_eq!(started.view.nodes.len(), 1);
    assert_eq!(started.view.nodes[0].node_timeout_ms, Some(60_000));
    assert_eq!(started.view.nodes[0].max_attempts, 3);
    let persisted_definition = StateMachineDefinitionRepoPort::get(&*store, "single_node", 1)
        .await
        .expect("get persisted definition")
        .expect("persisted definition");
    assert_inferred_default_requires(&persisted_definition);
    let snapshot =
        StateMachineDefinitionRepoPort::get_run_snapshot(&*store, &started.view.run.run_id)
            .await
            .expect("get run snapshot")
            .expect("run snapshot");
    assert_inferred_default_requires(&snapshot);
    let command = delivery.commands.lock().await[0].clone();
    let params = chat_send_params(&command);
    let frontend_commands = frontend_delivery.commands.lock().await;
    assert_eq!(frontend_commands.len(), 1);
    assert!(matches!(
        frontend_commands[0].target,
        FrontendDeliveryTarget::Session { ref session_id }
            if session_id == &started.view.run.session_id
    ));
    let panel_event: Value =
        serde_json::from_str(&frontend_commands[0].event_json).expect("panel event json");
    assert_eq!(panel_event["event"].as_str(), Some("chat"));
    assert_eq!(panel_event["bot_uuid"].as_str(), Some("bcs_state_machine"));
    assert_eq!(
        panel_event["payload"]["run_id"].as_str(),
        Some(started.view.run.run_id.as_str())
    );
    assert_eq!(panel_event["payload"]["role"].as_str(), Some("assistant"));
    assert_eq!(panel_event["payload"]["message_type"].as_str(), Some("bot"));
    assert_eq!(
        panel_event["payload"]["bot_name"].as_str(),
        Some("BCS State Machine")
    );
    assert_eq!(
        panel_event["payload"]["message"]["role"].as_str(),
        Some("assistant")
    );
    assert_eq!(
        panel_event["payload"]["metadata"]["state_machine"]["event"].as_str(),
        Some("panel")
    );
    let panel_text = panel_event["payload"]["message"]["content"][0]["text"]
        .as_str()
        .expect("panel text");
    assert!(panel_text.contains("<AixUI"));
    assert!(panel_text.contains("type=\"panel\""));
    assert!(panel_text.contains("params='"));
    assert!(panel_text.contains("bcsPanel.StateMachineRunView"));
    assert!(panel_text.contains(&format!("state-machine-run-{}", started.view.run.run_id)));
    assert!(panel_text.contains("State Machine - Single Node"));
    drop(frontend_commands);
    assert_eq!(params.bcs_group_id, started.view.run.session_id);
    assert_eq!(params.bcs_session_id, None);
    assert_eq!(
        params.session_context.session_id,
        started.view.run.session_id
    );
    let correlation = runtime
        .lookup_delivery_correlation(&command.run_id)
        .await
        .expect("lookup")
        .expect("correlation");
    assert_eq!(correlation.node_id, "answer");
    let delivery_run_id = command.run_id.clone();

    let delta = runtime
        .handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
            bot_id: "driver-bot".to_string(),
            run_id: delivery_run_id.clone(),
            event_type: "chat.event".to_string(),
            event_payload: json!({
                "run_id": delivery_run_id.clone(),
                "bcs_group_id": "group-1",
                "state": "delta",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "draft"}]
                }
            }),
            state: ChatEventState::Delta,
            bcs_session_id: Some(started.view.run.session_id.clone()),
        })
        .await
        .expect("handle delta");
    assert!(delta.consumed);
    let frontend_commands = frontend_delivery.commands.lock().await;
    assert_eq!(frontend_commands.len(), 2);
    let delta_event: Value =
        serde_json::from_str(&frontend_commands[1].event_json).expect("delta event json");
    assert_eq!(delta_event["event"].as_str(), Some("chat"));
    assert_eq!(delta_event["bot_uuid"].as_str(), Some("driver-bot"));
    assert_eq!(delta_event["payload"]["state"].as_str(), Some("delta"));
    assert_eq!(
        delta_event["payload"]["message"]["content"][0]["text"].as_str(),
        Some("draft")
    );
    drop(frontend_commands);
    let delta_events = CollaborationEventRepoPort::list_events_by_run_node_and_type(
        &*store,
        &started.view.run.run_id,
        "answer",
        "chat.event",
    )
    .await
    .expect("list raw delta events");
    assert!(delta_events.is_empty());
    let bot_events = CollaborationEventRepoPort::list_events_by_run_node_and_type(
        &*store,
        &started.view.run.run_id,
        "answer",
        "state_machine.node.bot_event",
    )
    .await
    .expect("list compact bot events");
    assert!(bot_events.is_empty());

    let handled = runtime
        .handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
            bot_id: "driver-bot".to_string(),
            run_id: delivery_run_id.clone(),
            event_type: "chat.event".to_string(),
            event_payload: json!({
                "run_id": "ignored-by-runtime-test",
                "bcs_group_id": "group-1",
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "final answer"}]
                }
            }),
            state: ChatEventState::Final,
            bcs_session_id: Some(started.view.run.session_id.clone()),
        })
        .await
        .expect("handle final");

    assert!(handled.consumed);
    let frontend_commands = frontend_delivery.commands.lock().await;
    assert_eq!(frontend_commands.len(), 3);
    assert!(matches!(
        frontend_commands[2].target,
        FrontendDeliveryTarget::Session { ref session_id }
            if session_id == &started.view.run.session_id
    ));
    let bot_event: Value =
        serde_json::from_str(&frontend_commands[2].event_json).expect("bot event json");
    assert_eq!(bot_event["event"].as_str(), Some("chat"));
    assert_eq!(bot_event["bot_uuid"].as_str(), Some("driver-bot"));
    assert_eq!(
        bot_event["payload"]["bcs_session_id"].as_str(),
        Some(started.view.run.session_id.as_str())
    );
    assert_eq!(
        bot_event["payload"]["run_id"].as_str(),
        Some("ignored-by-runtime-test")
    );
    assert_eq!(
        bot_event["payload"]["message"]["content"][0]["text"].as_str(),
        Some("final answer")
    );
    let view = handled.view.expect("run view");
    assert_eq!(view.run.output.as_deref(), Some("final answer"));
    let raw_final_events = CollaborationEventRepoPort::list_events_by_run_node_and_type(
        &*store,
        &started.view.run.run_id,
        "answer",
        "chat.event",
    )
    .await
    .expect("list raw final events");
    assert!(raw_final_events.is_empty());
    let bot_events = CollaborationEventRepoPort::list_events_by_run_node_and_type(
        &*store,
        &started.view.run.run_id,
        "answer",
        "state_machine.node.bot_event",
    )
    .await
    .expect("list compact bot events");
    assert_eq!(bot_events.len(), 1);
    assert_eq!(bot_events[0].attempt, Some(0));
    assert_eq!(bot_events[0].payload["state"].as_str(), Some("final"));
    assert_eq!(
        bot_events[0].payload["source_event_type"].as_str(),
        Some("chat.event")
    );
    assert_eq!(bot_events[0].payload["text_len"].as_u64(), Some(12));
    assert!(bot_events[0].payload.get("message").is_none());
    let session = sessions
        .get(&started.view.run.session_id)
        .await
        .expect("get session")
        .expect("session");
    assert_eq!(session.output, Some(json!("final answer")));
    let history = runtime
        .get_state_machine_session_history(&started.view.run.session_id, 50, None)
        .await
        .expect("history")
        .expect("state-machine history");
    assert_eq!(history.messages.len(), 2);
    assert_eq!(history.messages[0].sender, "bcs_state_machine");
    assert_eq!(history.messages[0].message_type, GroupMessageType::Bot);
    assert_eq!(history.messages[0].role, MessageRole::Assistant);
    assert_eq!(
        history.messages[0].bot_name.as_deref(),
        Some("BCS State Machine")
    );
    assert!(history.messages[0].content.contains("<AixUI"));
    assert!(history.messages[0].content.contains("type=\"panel\""));
    assert!(history.messages[0].content.contains("params='"));
    assert!(
        history.messages[0]
            .content
            .contains(&format!("\"runId\":\"{}\"", started.view.run.run_id))
    );
    assert_eq!(history.messages[1].sender, "driver-bot");
    assert_eq!(history.messages[1].bot_name.as_deref(), Some("Driver"));
    assert_eq!(history.messages[1].role, MessageRole::Assistant);
    assert_eq!(history.messages[1].message_type, GroupMessageType::Bot);
    assert_eq!(history.messages[1].content, "final answer");
    assert_eq!(
        history.messages[1]
            .metadata
            .as_ref()
            .and_then(|metadata| metadata["state_machine"]["event"].as_str()),
        Some("output")
    );
}

#[tokio::test]
async fn start_run_fails_and_marks_node_failed_when_delivery_returns_not_delivered() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RejectingDelivery::default());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery.clone(),
        noop_judge(),
    );

    let result = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"question": "review this"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await;

    let error = result.err().expect("delivery rejection should fail start");
    assert!(
        error
            .to_string()
            .contains("state-machine node delivery failed")
    );
    let commands = delivery.commands.lock().await;
    let delivery_id = commands[0].run_id.clone();
    drop(commands);
    let run_id = delivery_id
        .strip_prefix("smnode-")
        .and_then(|value| value.strip_suffix("-answer-0"))
        .expect("state-machine delivery id should include run id");
    let view = runtime
        .get_state_machine_run(run_id)
        .await
        .expect("get failed run")
        .expect("failed run should be persisted");
    assert_eq!(view.run.status, StateMachineRunStatus::Failed);
    assert_eq!(view.nodes[0].status, StateMachineNodeStatus::Failed);
    assert!(
        view.nodes[0]
            .error
            .as_ref()
            .expect("node error")
            .contains("not connected")
    );
}

#[tokio::test]
async fn message_less_final_fails_attempt_and_schedules_retry() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let frontend_delivery = Arc::new(RecordingFrontendDelivery::default());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery.clone(),
        noop_judge(),
    )
    .with_frontend_delivery(frontend_delivery.clone());

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"question": "empty final should retry"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start run");
    let first_delivery_run_id = delivery.commands.lock().await[0].run_id.clone();

    let tool_event = runtime
        .handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
            bot_id: "driver-bot".to_string(),
            run_id: first_delivery_run_id.clone(),
            event_type: "agent".to_string(),
            event_payload: json!({
                "run_id": first_delivery_run_id.clone(),
                "stream": "tool",
                "data": {
                    "name": "lookup",
                    "phase": "result",
                    "toolCallId": "tool-1",
                },
            }),
            state: ChatEventState::ToolCallEnd,
            bcs_session_id: Some(started.view.run.session_id.clone()),
        })
        .await
        .expect("handle tool event");
    assert!(tool_event.consumed);

    let handled = runtime
        .handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
            bot_id: "driver-bot".to_string(),
            run_id: first_delivery_run_id,
            event_type: "chat.event".to_string(),
            event_payload: json!({"state": "final"}),
            state: ChatEventState::Final,
            bcs_session_id: Some(started.view.run.session_id.clone()),
        })
        .await
        .expect("message-less final should enter retry flow");

    assert!(handled.consumed);
    let view = handled.view.expect("run view after retry scheduling");
    assert_eq!(view.run.status, StateMachineRunStatus::Running);
    assert_eq!(view.nodes[0].status, StateMachineNodeStatus::Running);
    assert_eq!(view.nodes[0].attempt, 1);
    assert_eq!(delivery.commands.lock().await.len(), 2);

    let frontend_commands = frontend_delivery.commands.lock().await;
    assert_eq!(frontend_commands.len(), 3);
    let tool_event: Value =
        serde_json::from_str(&frontend_commands[1].event_json).expect("tool event json");
    assert_eq!(tool_event["event"], "agent");
    assert_eq!(tool_event["payload"]["stream"], "tool");
    assert_eq!(tool_event["payload"]["data"]["phase"], "result");
    let final_event: Value =
        serde_json::from_str(&frontend_commands[2].event_json).expect("empty final event json");
    assert_eq!(final_event["event"], "chat");
    assert_eq!(final_event["payload"]["state"], "final");
    assert!(final_event["payload"].get("message").is_none());
    assert!(final_event["payload"].get("stop_reason").is_none());

    let retry_events = CollaborationEventRepoPort::list_events_by_run_node_and_type(
        &*store,
        &started.view.run.run_id,
        "answer",
        "state_machine.node.retry_scheduled",
    )
    .await
    .expect("list retry events");
    assert_eq!(retry_events.len(), 1);
    assert_eq!(
        retry_events[0].payload["reason"],
        "bot completed without visible output"
    );
}

#[tokio::test]
async fn state_machine_completion_dispatches_service_callback() {
    let group = Arc::new(GroupStore::new());
    let mut seeded_group = test_group();
    seeded_group.service_spec = Some(ServiceSpec {
        callback_config: Some(CallbackConfig {
            channels: vec![CallbackChannelConfig::Baas {
                base_url: "http://127.0.0.1:0".to_string(),
                api_key: "sk-test".to_string(),
                bot_id: "default:callback-test".to_string(),
                metadata: None,
            }],
        }),
        timeout_seconds: None,
        max_concurrency: None,
    });
    group.upsert(seeded_group).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions.clone(),
        delivery.clone(),
        noop_judge(),
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"question": "callback after completion"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start run");
    let initial_session = sessions
        .get(&started.view.run.session_id)
        .await
        .expect("get session")
        .expect("session");
    assert_eq!(initial_session.callback_status.as_deref(), Some("pending"));

    let delivery_run_id = delivery.commands.lock().await[0].run_id.clone();
    complete_with_text(
        &runtime,
        &delivery_run_id,
        &started.view.run.session_id,
        "callback payload",
    )
    .await;

    wait_for_callback_status(&sessions, &started.view.run.session_id, "failed").await;
}

#[tokio::test(flavor = "current_thread")]
async fn state_machine_runtime_logs_run_node_and_terminal_lifecycle() {
    let ((run_id, session_id, delivery_run_id), logs) = capture_tracing_logs(async {
        let group = Arc::new(GroupStore::new());
        group.upsert(test_group()).await.expect("seed group");
        let sessions = test_sessions();
        let store = Arc::new(MemoryCollaborationStore::new());
        let delivery = Arc::new(RecordingDelivery::default());
        let runtime = CollaborationRuntime::new(
            store.clone(),
            store.clone(),
            store.clone(),
            store.clone(),
            group,
            sessions,
            delivery.clone(),
            noop_judge(),
        );

        let started = runtime
            .start_state_machine_run(StartStateMachineRunCommand {
                group_id: "group-1".to_string(),
                session_id: None,
                definition_yaml: Some(single_node_yaml()),
                definition: None,
                definition_ref: None,
                participant_bindings: None,
                input: json!({"question": "logging"}),
                caller_id: Some("caller-1".to_string()),
                authenticated_human: None,
            })
            .await
            .expect("start run");
        let delivery_run_id = delivery.commands.lock().await[0].run_id.clone();
        complete_with_text(
            &runtime,
            &delivery_run_id,
            &started.view.run.session_id,
            "final answer",
        )
        .await;

        (
            started.view.run.run_id,
            started.view.run.session_id,
            delivery_run_id,
        )
    })
    .await;

    for expected in [
        "state_machine: run started",
        "state_machine: node dispatch started",
        "state_machine: node dispatch completed",
        "state_machine: bot terminal event received",
        "state_machine: node completed",
        "state_machine: run completed",
        "group-1",
        "single_node",
        "answer",
        "driver-bot",
        "caller-1",
        "complete",
        "completed",
    ] {
        assert!(
            logs.contains(expected),
            "expected logs to contain {expected:?}; logs:\n{logs}"
        );
    }
    assert!(
        logs.contains(&run_id),
        "expected logs to contain run id {run_id}; logs:\n{logs}"
    );
    assert!(
        logs.contains(&session_id),
        "expected logs to contain session id {session_id}; logs:\n{logs}"
    );
    assert!(
        logs.contains(&delivery_run_id),
        "expected logs to contain delivery run id {delivery_run_id}; logs:\n{logs}"
    );
}

#[tokio::test]
async fn start_run_uses_group_default_definition_binding() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery.clone(),
        noop_judge(),
    );

    let configured = runtime
        .configure_group_runtime(ConfigureGroupRuntimeCommand {
            group_id: "group-1".to_string(),
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: Default::default(),
            auto_start_on_service_invocation: true,
        })
        .await
        .expect("configure group runtime");

    assert_eq!(
        configured.default_definition.expect("definition").id,
        "single_node"
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: None,
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"question": "use binding"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start run from binding");

    assert_eq!(started.view.run.definition_id, "single_node");
    assert_eq!(delivery.commands.lock().await.len(), 1);
}

#[tokio::test]
async fn deleting_session_aborts_all_active_state_machine_runs() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = Arc::new(CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions.clone(),
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    ));
    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"question": "delete the session"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start run");
    let mut second_run = started.view.run.clone();
    second_run.run_id = "session-cleanup-second-run".to_string();
    second_run.created_at = second_run.created_at.saturating_add(1);
    second_run.updated_at = second_run.created_at;
    StateMachineRunRepoPort::create_run(&*store, second_run.clone(), Vec::new())
        .await
        .expect("seed second active run for the session");
    let service = SessionManagementWithRuntimeCleanup::new(sessions.clone(), runtime);

    let deleted = service
        .delete(&started.view.run.session_id)
        .await
        .expect("delete session and cancel runs");

    assert!(deleted);
    assert!(
        sessions
            .get(&started.view.run.session_id)
            .await
            .expect("read deleted session")
            .is_none()
    );
    for run_id in [&started.view.run.run_id, &second_run.run_id] {
        let run = StateMachineRunRepoPort::get_run(&*store, run_id)
            .await
            .expect("read aborted run")
            .expect("aborted run remains for audit");
        assert_eq!(run.status, StateMachineRunStatus::Aborted);
        assert_eq!(run.error.as_deref(), Some("session_deleted"));
    }
}

#[tokio::test]
async fn deleting_group_aborts_active_state_machine_runs() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = Arc::new(CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group.clone(),
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    ));
    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"question": "delete the group"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start run");
    let group_management = Arc::new(GroupManagement::with_defaults(
        group,
        Arc::new(NoopBotRegistryCoreService),
        Arc::new(NoopFriendCoreService),
    ));
    let service = GroupManagementWithRuntimeCleanup::new(group_management, runtime);

    let deleted = service
        .delete_group(GroupDeleteCommand {
            caller_actor_id: "driver-bot".to_string(),
            group_id: "group-1".to_string(),
        })
        .await
        .expect("delete group and runtime state");

    assert!(deleted.deleted);
    let run = StateMachineRunRepoPort::get_run(&*store, &started.view.run.run_id)
        .await
        .expect("read aborted run")
        .expect("aborted run remains for audit");
    assert_eq!(run.status, StateMachineRunStatus::Aborted);
    assert_eq!(run.error.as_deref(), Some("group_deleted"));
}

#[tokio::test]
async fn retrying_deleted_group_cleanup_aborts_orphaned_active_runs() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = Arc::new(CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group.clone(),
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    ));
    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"question": "recover orphaned run"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start run");
    group.delete("group-1").await.expect("simulate partial delete");
    let group_management = Arc::new(GroupManagement::with_defaults(
        group,
        Arc::new(NoopBotRegistryCoreService),
        Arc::new(NoopFriendCoreService),
    ));
    let service = GroupManagementWithRuntimeCleanup::new(group_management, runtime);

    let deleted = service
        .delete_group(GroupDeleteCommand {
            caller_actor_id: "driver-bot".to_string(),
            group_id: "group-1".to_string(),
        })
        .await
        .expect("retry group runtime cleanup");

    assert!(!deleted.deleted);
    let run = StateMachineRunRepoPort::get_run(&*store, &started.view.run.run_id)
        .await
        .expect("read recovered run")
        .expect("run remains for audit");
    assert_eq!(run.status, StateMachineRunStatus::Aborted);
    assert_eq!(run.error.as_deref(), Some("group_deleted"));
}

#[tokio::test]
async fn group_runtime_cleanup_aborts_runs_and_removes_sessions_and_binding() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions.clone(),
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    );
    runtime
        .configure_group_runtime(ConfigureGroupRuntimeCommand {
            group_id: "group-1".to_string(),
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: Default::default(),
            auto_start_on_service_invocation: true,
        })
        .await
        .expect("configure group runtime");
    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: None,
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"question": "review this"}),
            caller_id: Some("driver-bot".to_string()),
            authenticated_human: None,
        })
        .await
        .expect("start run");
    let mut second_run = started.view.run.clone();
    second_run.run_id = "group-cleanup-second-run".to_string();
    second_run.created_at = second_run.created_at.saturating_add(1);
    second_run.updated_at = second_run.created_at;
    StateMachineRunRepoPort::create_run(&*store, second_run.clone(), Vec::new())
        .await
        .expect("seed second active run for the same session");

    runtime
        .cancel_group_runs("group-1", "group_deleted")
        .await
        .expect("cancel active group runs");
    let aborted = StateMachineRunRepoPort::get_run(&*store, &started.view.run.run_id)
        .await
        .expect("read cancelled run")
        .expect("cancelled run remains for audit");
    assert_eq!(aborted.status, StateMachineRunStatus::Aborted);
    let second_aborted = StateMachineRunRepoPort::get_run(&*store, &second_run.run_id)
        .await
        .expect("read second cancelled run")
        .expect("second cancelled run remains for audit");
    assert_eq!(second_aborted.status, StateMachineRunStatus::Aborted);

    runtime
        .delete_group_runtime_state("group-1")
        .await
        .expect("delete runtime state");
    assert!(
        sessions
            .get(&started.view.run.session_id)
            .await
            .expect("read deleted session")
            .is_none()
    );
    assert!(
        GroupRuntimeBindingRepoPort::get(&*store, "group-1")
            .await
            .expect("read deleted binding")
            .is_none()
    );
    runtime
        .delete_group_runtime_state("group-1")
        .await
        .expect("runtime state deletion is idempotent");
}

#[tokio::test]
async fn configure_im_definition_defers_channel_validation_until_run_start() {
    let group = Arc::new(GroupStore::new());
    group
        .upsert(state_machine_test_group())
        .await
        .expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let channel_outbound = Arc::new(RecordingSessionChannelOutbound::default());
    *channel_outbound.validation_error.lock().await =
        Some("no active dingtalk ChannelBinding exists".to_string());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group,
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    )
    .with_session_channel_outbound(channel_outbound.clone());

    let configured = runtime
        .configure_group_runtime(ConfigureGroupRuntimeCommand {
            group_id: "group-1".to_string(),
            definition_yaml: Some(human_input_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: Default::default(),
            auto_start_on_service_invocation: true,
        })
        .await
        .expect("configuration must not require a binding that needs the group id");
    assert!(configured.requires_human_input_channel);
    assert!(channel_outbound.validation_calls.lock().await.is_empty());

    let error = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: None,
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: Value::Null,
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect_err("run start must still enforce the active binding");
    assert!(error.to_string().contains("no active dingtalk ChannelBinding"));
    assert_eq!(
        channel_outbound.validation_calls.lock().await.as_slice(),
        &[("group-1".to_string(), "dingtalk".to_string())]
    );
}

#[tokio::test]
async fn group_collaboration_definition_get_and_patch_preserve_source_yaml() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    );

    let no_definition = runtime
        .get_group_collaboration_definition("group-1")
        .await
        .expect("get no definition");
    assert_eq!(
        no_definition.yaml_source,
        DefinitionYamlSource::NoDefinition
    );
    assert!(no_definition.default_definition.is_none());

    let source_yaml = single_node_authoring_yaml("Source One");
    runtime
        .configure_group_runtime(ConfigureGroupRuntimeCommand {
            group_id: "group-1".to_string(),
            definition_yaml: Some(source_yaml.clone()),
            definition: None,
            definition_ref: None,
            participant_bindings: BTreeMap::new(),
            auto_start_on_service_invocation: false,
        })
        .await
        .expect("configure group runtime");
    let current = runtime
        .get_group_collaboration_definition("group-1")
        .await
        .expect("get source definition");
    assert_eq!(current.yaml_source, DefinitionYamlSource::Original);
    assert_eq!(
        current.definition_yaml.as_deref(),
        Some(source_yaml.as_str())
    );
    let base = current
        .default_definition
        .clone()
        .expect("default definition");

    let patched_yaml = single_node_authoring_yaml("Source Two");
    let patched = runtime
        .patch_group_collaboration_definition(PatchGroupCollaborationDefinitionCommand {
            group_id: "group-1".to_string(),
            base_definition: base.clone(),
            definition_yaml: patched_yaml.clone(),
            participant_bindings: None,
        })
        .await
        .expect("patch definition");
    let next = patched.default_definition.expect("patched definition ref");
    assert_eq!(next.id, base.id);
    assert_eq!(next.version, base.version + 1);
    assert_eq!(patched.yaml_source, DefinitionYamlSource::Original);
    assert_eq!(
        patched.definition_yaml.as_deref(),
        Some(patched_yaml.as_str())
    );
}

#[tokio::test]
async fn group_collaboration_definition_get_generates_legacy_authoring_yaml_without_identity() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    );

    let definition: CollaborationDefinition =
        serde_yaml::from_str(&single_node_yaml()).expect("legacy definition yaml");
    runtime
        .upsert_definition(definition)
        .await
        .expect("upsert legacy normalized definition");
    runtime
        .configure_group_runtime(ConfigureGroupRuntimeCommand {
            group_id: "group-1".to_string(),
            definition_yaml: None,
            definition: None,
            definition_ref: Some(CollaborationDefinitionRef {
                id: "single_node".to_string(),
                version: 1,
            }),
            participant_bindings: BTreeMap::new(),
            auto_start_on_service_invocation: false,
        })
        .await
        .expect("bind legacy definition");

    let current = runtime
        .get_group_collaboration_definition("group-1")
        .await
        .expect("get legacy definition");
    assert_eq!(
        current.yaml_source,
        DefinitionYamlSource::GeneratedNormalized
    );
    assert_eq!(
        current.definition.as_ref().expect("definition").id,
        "single_node"
    );
    let base = current.default_definition.clone().expect("definition ref");
    assert_eq!(
        base,
        CollaborationDefinitionRef {
            id: "single_node".to_string(),
            version: 1,
        }
    );
    let generated_yaml = current.definition_yaml.expect("generated yaml");
    let generated_value: serde_yaml::Value =
        serde_yaml::from_str(&generated_yaml).expect("generated yaml should parse");
    let keys: Vec<&str> = generated_value
        .as_mapping()
        .expect("generated yaml root mapping")
        .keys()
        .filter_map(|key| key.as_str())
        .collect();
    assert!(!keys.contains(&"id"));
    assert!(!keys.contains(&"version"));
    assert!(!keys.contains(&"api_version"));
    assert!(!keys.contains(&"requires"));
    assert!(!keys.contains(&"metadata"));
    assert!(!keys.contains(&"extensions"));
    let root = generated_value
        .as_mapping()
        .expect("generated yaml root mapping");
    let state_machine = root
        .get("runtime")
        .and_then(serde_yaml::Value::as_mapping)
        .and_then(|runtime| runtime.get("state_machine"))
        .and_then(serde_yaml::Value::as_mapping)
        .expect("state machine mapping");
    assert!(!state_machine.contains_key("version"));
    assert!(!state_machine.contains_key("graph_mode"));
    assert!(!state_machine.contains_key("projection"));
    assert!(!state_machine.contains_key("variables"));
    assert!(!state_machine.contains_key("events"));
    assert!(!state_machine.contains_key("extensions"));
    let defaults = state_machine
        .get("defaults")
        .and_then(serde_yaml::Value::as_mapping)
        .expect("non-default defaults should remain");
    assert_eq!(
        defaults
            .get("max_attempts")
            .and_then(serde_yaml::Value::as_i64),
        Some(2)
    );
    assert_eq!(
        defaults
            .get("node_timeout_ms")
            .and_then(serde_yaml::Value::as_i64),
        Some(120000)
    );
    let driver = root
        .get("participants")
        .and_then(serde_yaml::Value::as_mapping)
        .and_then(|participants| participants.get("driver"))
        .and_then(serde_yaml::Value::as_mapping)
        .expect("driver participant mapping");
    assert!(!driver.contains_key("extensions"));
    assert_eq!(
        driver.get("bot_id").and_then(serde_yaml::Value::as_str),
        Some("driver-bot")
    );
    assert_eq!(
        driver.get("required").and_then(serde_yaml::Value::as_bool),
        Some(true)
    );

    let patched = runtime
        .patch_group_collaboration_definition(PatchGroupCollaborationDefinitionCommand {
            group_id: "group-1".to_string(),
            base_definition: base,
            definition_yaml: generated_yaml,
            participant_bindings: None,
        })
        .await
        .expect("patch generated legacy authoring yaml");
    assert_eq!(
        patched
            .default_definition
            .expect("patched definition")
            .version,
        2
    );
}

#[tokio::test]
async fn start_run_from_group_binding_does_not_upsert_persisted_definition() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let backing_store = Arc::new(MemoryCollaborationStore::new());
    let definitions = Arc::new(CountingDefinitionRepo::new(backing_store.clone()));
    let delivery = Arc::new(RecordingDelivery::default());
    let message_repo = Arc::new(MemoryMessageRepo::new());
    let runtime = CollaborationRuntime::new(
        definitions.clone(),
        backing_store.clone(),
        backing_store.clone(),
        backing_store.clone(),
        group,
        sessions,
        delivery.clone(),
        noop_judge(),
    )
    .with_message_repo(message_repo.clone());

    runtime
        .configure_group_runtime(ConfigureGroupRuntimeCommand {
            group_id: "group-1".to_string(),
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: Default::default(),
            auto_start_on_service_invocation: true,
        })
        .await
        .expect("configure group runtime");
    assert_eq!(definitions.upsert_calls().await, 1);

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: None,
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"question": "use binding without rewriting definition"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start run from binding");

    assert_eq!(started.view.run.definition_id, "single_node");
    assert_eq!(
        definitions.upsert_calls().await,
        1,
        "group binding runs must not rewrite persisted definition rows"
    );
    let snapshot =
        StateMachineDefinitionRepoPort::get_run_snapshot(&*definitions, &started.view.run.run_id)
            .await
            .expect("get run snapshot")
            .expect("run snapshot");
    assert_inferred_default_requires(&snapshot);
    assert_eq!(delivery.commands.lock().await.len(), 1);
    let panels = message_repo
        .query_messages(MessageQuery {
            group_id: "group-1".to_string(),
            session_id: started.view.run.session_id.clone(),
            cursor: None,
            limit: 10,
            keyword: None,
            sender_id: None,
            message_type: Some(STATE_MACHINE_PANEL_MESSAGE_TYPE.to_string()),
            owner_filter: MessageOwnerFilter::Any,
            time_range: None,
            visible_from_seq: None,
        })
        .await
        .expect("query generic run panel anchors");
    assert!(
        panels.messages.is_empty(),
        "configured service-invocation runs must not write chat panel anchors"
    );
}

#[tokio::test]
async fn start_run_uses_group_participant_bindings_for_template_definition() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery.clone(),
        noop_judge(),
    );

    runtime
        .configure_group_runtime(ConfigureGroupRuntimeCommand {
            group_id: "group-1".to_string(),
            definition_yaml: Some(single_node_template_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: BTreeMap::from([(
                "driver".to_string(),
                RuntimeParticipantBinding {
                    source: "manual".to_string(),
                    bot_ids: vec!["driver-bot".to_string()],
                    extensions: Default::default(),
                },
            )]),
            auto_start_on_service_invocation: true,
        })
        .await
        .expect("configure group runtime");

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: None,
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"question": "use participant binding"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start run from binding");

    assert_eq!(
        started.view.nodes[0].assignee_bot_id.as_deref(),
        Some("driver-bot")
    );
    assert_eq!(delivery.commands.lock().await.len(), 1);
}

#[tokio::test]
async fn start_run_rejects_multi_bot_slot_with_current_single_assignee_runtime() {
    let group = Arc::new(GroupStore::new());
    let mut seeded_group = test_group();
    seeded_group.participants.push(Participant {
        bot_uuid: "reviewer-bot".to_string(),
        bot_name: Some("Reviewer".to_string()),
        kind: None,
        role: ParticipantRole::Consultant,
        actor_kind: ActorKind::Bot,
        mode: Some(ParticipantMode::Auto),
    });
    group.upsert(seeded_group).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery,
        noop_judge(),
    );

    runtime
        .configure_group_runtime(ConfigureGroupRuntimeCommand {
            group_id: "group-1".to_string(),
            definition_yaml: Some(single_node_template_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: BTreeMap::from([(
                "driver".to_string(),
                RuntimeParticipantBinding {
                    source: "manual".to_string(),
                    bot_ids: vec!["driver-bot".to_string(), "reviewer-bot".to_string()],
                    extensions: Default::default(),
                },
            )]),
            auto_start_on_service_invocation: true,
        })
        .await
        .expect("configure group runtime");

    let error = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: None,
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"question": "multi"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect_err("multi bot slot is not supported by the current single-assignee runtime");

    assert!(error.to_string().contains("exactly one bot"));
}

#[tokio::test]
async fn graph_view_returns_snapshot_edges_and_node_status() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery,
        noop_judge(),
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(join_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"question": "graph"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start run");

    let graph = runtime
        .get_state_machine_run_graph(&started.view.run.run_id)
        .await
        .expect("get graph")
        .expect("graph");

    assert_eq!(graph.definition.id, "join_graph");
    assert_eq!(graph.definition.initial_nodes, vec!["start"]);
    let mut edges = graph
        .edges
        .iter()
        .map(|edge| {
            (
                edge.source.as_str(),
                edge.outcome.as_str(),
                edge.target.as_str(),
            )
        })
        .collect::<Vec<_>>();
    edges.sort();
    assert_eq!(
        edges,
        vec![
            ("branch_b", "complete", "join"),
            ("branch_c", "complete", "join"),
            ("start", "complete", "branch_b"),
            ("start", "complete", "branch_c"),
        ]
    );
    let by_id = graph
        .nodes
        .iter()
        .map(|node| (node.node_id.as_str(), node.status))
        .collect::<BTreeMap<_, _>>();
    assert_eq!(by_id["start"], Some(StateMachineNodeStatus::Running));
    assert_eq!(by_id["branch_b"], Some(StateMachineNodeStatus::Pending));
    assert_eq!(by_id["join"], Some(StateMachineNodeStatus::Pending));
}

#[tokio::test]
async fn complete_transitions_support_fan_out_and_implicit_all_join() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery.clone(),
        noop_judge(),
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(join_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"question": "join"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start run");

    assert_eq!(delivery.commands.lock().await.len(), 1);
    let first_run_id = delivery.commands.lock().await[0].run_id.clone();
    complete_with_text(&runtime, &first_run_id, &started.view.run.session_id, "a").await;
    assert_eq!(delivery.commands.lock().await.len(), 3);

    let branch_b_run_id = delivery.commands.lock().await[1].run_id.clone();
    let branch_c_run_id = delivery.commands.lock().await[2].run_id.clone();
    complete_with_text(
        &runtime,
        &branch_b_run_id,
        &started.view.run.session_id,
        "b",
    )
    .await;
    assert_eq!(
        delivery.commands.lock().await.len(),
        3,
        "join node must wait for the other upstream"
    );

    complete_with_text(
        &runtime,
        &branch_c_run_id,
        &started.view.run.session_id,
        "c",
    )
    .await;
    assert_eq!(delivery.commands.lock().await.len(), 4);

    let join_run_id = delivery.commands.lock().await[3].run_id.clone();
    let handled = complete_with_text(
        &runtime,
        &join_run_id,
        &started.view.run.session_id,
        "joined",
    )
    .await;
    let view = handled.view.expect("completed run");
    assert_eq!(view.run.output.as_deref(), Some("joined"));
}

#[tokio::test]
async fn judged_node_routes_selected_outcome_and_skips_unselected_branch() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let judge = Arc::new(RecordingJudge::with_outcome("approved"));
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery.clone(),
        judge.clone(),
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(judge_branch_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"question": "judge branch"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start run");

    assert_eq!(delivery.commands.lock().await.len(), 1);
    let review_run_id = delivery.commands.lock().await[0].run_id.clone();
    complete_with_text(
        &runtime,
        &review_run_id,
        &started.view.run.session_id,
        "candidate final answer",
    )
    .await;
    assert_eq!(delivery.commands.lock().await.len(), 2);
    assert_eq!(
        judge.requests.lock().await[0].allowed_outcomes,
        vec!["approved", "rejected"]
    );
    let judged_view = runtime
        .get_state_machine_run(&started.view.run.run_id)
        .await
        .expect("get judged run")
        .expect("run view");
    assert_eq!(judged_view.judge_outputs.len(), 1);
    assert_eq!(judged_view.judge_outputs[0].node_id, "review");
    assert_eq!(judged_view.judge_outputs[0].attempt, 0);
    assert_eq!(judged_view.judge_outputs[0].decision.outcome, "approved");
    let node_view = runtime
        .get_state_machine_node_run(&started.view.run.run_id, "review")
        .await
        .expect("get node run")
        .expect("node view");
    assert_eq!(node_view.node.node_id, "review");
    assert_eq!(node_view.judge_outputs.len(), 1);
    assert_eq!(node_view.judge_outputs[0].decision.outcome, "approved");

    let publish_run_id = delivery.commands.lock().await[1].run_id.clone();
    let handled = complete_with_text(
        &runtime,
        &publish_run_id,
        &started.view.run.session_id,
        "approved final",
    )
    .await;
    let view = handled.view.expect("completed run");

    assert_eq!(view.run.output.as_deref(), Some("approved final"));
    let by_id = view
        .nodes
        .iter()
        .map(|node| (node.node_id.as_str(), node.status))
        .collect::<BTreeMap<_, _>>();
    assert_eq!(by_id["review"], StateMachineNodeStatus::Completed);
    assert_eq!(by_id["publish"], StateMachineNodeStatus::Completed);
    assert_eq!(by_id["revise"], StateMachineNodeStatus::Skipped);
    assert_eq!(by_id["manual_review"], StateMachineNodeStatus::Skipped);
}

#[tokio::test]
async fn judged_node_publishes_bot_output_but_not_judge_message_to_workbench() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let frontend_delivery = Arc::new(RecordingFrontendDelivery::default());
    let judge = Arc::new(RecordingJudge::with_outcome("approved"));
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery.clone(),
        judge,
    )
    .with_frontend_delivery(frontend_delivery.clone());

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(judge_branch_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"question": "judge branch"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start run");

    let review_run_id = delivery.commands.lock().await[0].run_id.clone();
    complete_with_text(
        &runtime,
        &review_run_id,
        &started.view.run.session_id,
        "candidate final answer",
    )
    .await;

    let frontend_commands = frontend_delivery.commands.lock().await;
    assert_eq!(frontend_commands.len(), 2);
    let panel_event: Value =
        serde_json::from_str(&frontend_commands[0].event_json).expect("panel event json");
    assert_eq!(
        panel_event["payload"]["metadata"]["state_machine"]["event"].as_str(),
        Some("panel")
    );
    let bot_event: Value =
        serde_json::from_str(&frontend_commands[1].event_json).expect("bot event json");
    assert_eq!(bot_event["event"].as_str(), Some("chat"));
    assert_eq!(bot_event["bot_uuid"].as_str(), Some("driver-bot"));
    assert_eq!(
        bot_event["payload"]["message"]["content"][0]["text"].as_str(),
        Some("candidate final answer")
    );
    assert_ne!(
        bot_event["payload"]["metadata"]["state_machine"]["event"].as_str(),
        Some("judge")
    );
}

#[tokio::test]
async fn judged_node_failure_records_runtime_event_and_fails_run() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let judge = Arc::new(RecordingJudge::with_error("judge provider timed out"));
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery.clone(),
        judge,
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(judge_branch_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"question": "judge branch"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start run");

    let review_run_id = delivery.commands.lock().await[0].run_id.clone();
    let handled = complete_with_text(
        &runtime,
        &review_run_id,
        &started.view.run.session_id,
        "candidate final answer",
    )
    .await;

    let view = handled.view.expect("failed run view");
    assert_eq!(view.run.status, StateMachineRunStatus::Failed);
    assert_eq!(
        view.run.error.as_deref(),
        Some("judge failed for node review attempt 0: judge provider timed out")
    );
    let review = view
        .nodes
        .iter()
        .find(|node| node.node_id == "review")
        .expect("review node");
    assert_eq!(review.status, StateMachineNodeStatus::Failed);
    assert_eq!(
        review.error.as_deref(),
        Some("judge failed for node review attempt 0: judge provider timed out")
    );
    let failure_events = CollaborationEventRepoPort::list_events_by_run_node_and_type(
        &*store,
        &started.view.run.run_id,
        "review",
        "state_machine.judge.failed",
    )
    .await
    .expect("list judge failure events");
    assert_eq!(failure_events.len(), 1);
    assert_eq!(failure_events[0].attempt, Some(0));
    assert_eq!(
        failure_events[0].payload["error"].as_str(),
        Some("judge provider timed out")
    );
    assert_eq!(
        failure_events[0].payload["reason"].as_str(),
        Some("judge_failed")
    );
    assert_eq!(delivery.commands.lock().await.len(), 1);
}

#[tokio::test]
async fn judged_node_timeout_records_runtime_event_and_fails_run() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let judge = Arc::new(RecordingJudge::with_delayed_outcome("approved", 25));
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery.clone(),
        judge,
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(judge_timeout_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"question": "judge branch"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start run");

    let review_run_id = delivery.commands.lock().await[0].run_id.clone();
    let handled = complete_with_text(
        &runtime,
        &review_run_id,
        &started.view.run.session_id,
        "candidate final answer",
    )
    .await;

    let view = handled.view.expect("failed run view");
    assert_eq!(view.run.status, StateMachineRunStatus::Failed);
    assert_eq!(
        view.run.error.as_deref(),
        Some("judge timed out for node review attempt 0 after 1ms")
    );
    let review = view
        .nodes
        .iter()
        .find(|node| node.node_id == "review")
        .expect("review node");
    assert_eq!(review.status, StateMachineNodeStatus::Failed);
    assert_eq!(
        review.error.as_deref(),
        Some("judge timed out for node review attempt 0 after 1ms")
    );
    let failure_events = CollaborationEventRepoPort::list_events_by_run_node_and_type(
        &*store,
        &started.view.run.run_id,
        "review",
        "state_machine.judge.failed",
    )
    .await
    .expect("list judge failure events");
    assert_eq!(failure_events.len(), 1);
    assert_eq!(failure_events[0].attempt, Some(0));
    assert_eq!(
        failure_events[0].payload["reason"].as_str(),
        Some("judge_timeout")
    );
    assert_eq!(failure_events[0].payload["timeout_ms"].as_u64(), Some(1));
    assert_eq!(delivery.commands.lock().await.len(), 1);
}

#[tokio::test]
async fn judged_node_keeps_shared_merge_reachable_from_selected_branch() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery.clone(),
        Arc::new(RecordingJudge::with_outcome("approved")),
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(judge_shared_merge_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            input: json!({"question": "judge shared merge"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start run");

    let review_run_id = delivery.commands.lock().await[0].run_id.clone();
    complete_with_text(
        &runtime,
        &review_run_id,
        &started.view.run.session_id,
        "candidate",
    )
    .await;
    assert_eq!(delivery.commands.lock().await.len(), 2);

    let fast_run_id = delivery.commands.lock().await[1].run_id.clone();
    complete_with_text(&runtime, &fast_run_id, &started.view.run.session_id, "fast").await;
    assert_eq!(delivery.commands.lock().await.len(), 3);

    let merge_run_id = delivery.commands.lock().await[2].run_id.clone();
    let handled = complete_with_text(
        &runtime,
        &merge_run_id,
        &started.view.run.session_id,
        "merged",
    )
    .await;
    let view = handled.view.expect("completed run");
    assert_eq!(view.run.output.as_deref(), Some("merged"));
    let by_id = view
        .nodes
        .iter()
        .map(|node| (node.node_id.as_str(), node.status))
        .collect::<BTreeMap<_, _>>();
    assert_eq!(by_id["fast"], StateMachineNodeStatus::Completed);
    assert_eq!(by_id["slow"], StateMachineNodeStatus::Skipped);
    assert_eq!(by_id["merge"], StateMachineNodeStatus::Completed);
}

#[derive(Default)]
struct CountingDefinitionRepo {
    inner: Arc<MemoryCollaborationStore>,
    upsert_calls: Mutex<usize>,
}

impl CountingDefinitionRepo {
    fn new(inner: Arc<MemoryCollaborationStore>) -> Self {
        Self {
            inner,
            upsert_calls: Mutex::new(0),
        }
    }

    async fn upsert_calls(&self) -> usize {
        *self.upsert_calls.lock().await
    }
}

#[async_trait]
impl StateMachineDefinitionRepoPort for CountingDefinitionRepo {
    async fn upsert(&self, definition: CollaborationDefinition) -> ServiceResult<()> {
        *self.upsert_calls.lock().await += 1;
        StateMachineDefinitionRepoPort::upsert(&*self.inner, definition).await
    }

    async fn get(&self, id: &str, version: i32) -> ServiceResult<Option<CollaborationDefinition>> {
        StateMachineDefinitionRepoPort::get(&*self.inner, id, version).await
    }

    async fn save_run_snapshot(
        &self,
        run: &StateMachineRun,
        group_version: i32,
        definition: &CollaborationDefinition,
        resolved_participant_bindings: Option<&BTreeMap<String, ResolvedParticipantBinding>>,
    ) -> ServiceResult<()> {
        StateMachineDefinitionRepoPort::save_run_snapshot(
            &*self.inner,
            run,
            group_version,
            definition,
            resolved_participant_bindings,
        )
        .await
    }

    async fn get_run_snapshot(
        &self,
        run_id: &str,
    ) -> ServiceResult<Option<CollaborationDefinition>> {
        StateMachineDefinitionRepoPort::get_run_snapshot(&*self.inner, run_id).await
    }
}

#[derive(Default)]
struct RecordingDelivery {
    commands: Mutex<Vec<BotDeliveryCommand>>,
}

#[async_trait]
impl BotDeliveryPort for RecordingDelivery {
    async fn is_available(&self, _target: &BotDeliveryTarget) -> bool {
        true
    }

    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        self.commands.lock().await.push(cmd.clone());
        Ok(BotDeliveryResult {
            target_bot_id: cmd.target_bot_id().to_string(),
            delivered: true,
            error: None,
        })
    }
}

#[derive(Default)]
struct RejectingDelivery {
    commands: Mutex<Vec<BotDeliveryCommand>>,
}

#[async_trait]
impl BotDeliveryPort for RejectingDelivery {
    async fn is_available(&self, _target: &BotDeliveryTarget) -> bool {
        true
    }

    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        self.commands.lock().await.push(cmd.clone());
        Ok(BotDeliveryResult {
            target_bot_id: cmd.target_bot_id().to_string(),
            delivered: false,
            error: Some(ServiceError::BotNotConnected(
                cmd.target_bot_id().to_string(),
            )),
        })
    }
}

#[derive(Default)]
struct RecordingJudge {
    outcome: String,
    error: Option<String>,
    delay_ms: Option<u64>,
    requests: Mutex<Vec<JudgeRequest>>,
}

impl RecordingJudge {
    fn with_outcome(outcome: &str) -> Self {
        Self {
            outcome: outcome.to_string(),
            error: None,
            delay_ms: None,
            requests: Mutex::new(Vec::new()),
        }
    }

    fn with_error(error: &str) -> Self {
        Self {
            outcome: String::new(),
            error: Some(error.to_string()),
            delay_ms: None,
            requests: Mutex::new(Vec::new()),
        }
    }

    fn with_delayed_outcome(outcome: &str, delay_ms: u64) -> Self {
        Self {
            outcome: outcome.to_string(),
            error: None,
            delay_ms: Some(delay_ms),
            requests: Mutex::new(Vec::new()),
        }
    }
}

#[async_trait]
impl JudgeEvaluatorPort for RecordingJudge {
    async fn judge(&self, request: JudgeRequest) -> Result<JudgeDecision, ServiceError> {
        self.requests.lock().await.push(request);
        if let Some(delay_ms) = self.delay_ms {
            tokio::time::sleep(Duration::from_millis(delay_ms)).await;
        }
        if let Some(error) = &self.error {
            return Err(ServiceError::InternalError(error.clone()));
        }
        Ok(JudgeDecision {
            outcome: self.outcome.clone(),
            reason: "mock decision".to_string(),
            confidence: 1.0,
            checked_criteria: Vec::new(),
            retry_instruction: String::new(),
            raw_response: None,
        })
    }
}

struct BlockingJudge {
    outcome: String,
    requests: Mutex<Vec<JudgeRequest>>,
    started: Notify,
    release: Notify,
}

impl BlockingJudge {
    fn new(outcome: &str) -> Self {
        Self {
            outcome: outcome.to_string(),
            requests: Mutex::new(Vec::new()),
            started: Notify::new(),
            release: Notify::new(),
        }
    }
}

#[async_trait]
impl JudgeEvaluatorPort for BlockingJudge {
    async fn judge(&self, request: JudgeRequest) -> Result<JudgeDecision, ServiceError> {
        self.requests.lock().await.push(request);
        self.started.notify_one();
        self.release.notified().await;
        Ok(JudgeDecision {
            outcome: self.outcome.clone(),
            reason: "mock decision".to_string(),
            confidence: 1.0,
            checked_criteria: Vec::new(),
            retry_instruction: String::new(),
            raw_response: None,
        })
    }
}

struct SequencedJudge {
    decisions: Mutex<VecDeque<JudgeDecision>>,
    requests: Mutex<Vec<JudgeRequest>>,
}

impl SequencedJudge {
    fn new(decisions: Vec<JudgeDecision>) -> Self {
        Self {
            decisions: Mutex::new(decisions.into()),
            requests: Mutex::new(Vec::new()),
        }
    }
}

#[async_trait]
impl JudgeEvaluatorPort for SequencedJudge {
    async fn judge(&self, request: JudgeRequest) -> Result<JudgeDecision, ServiceError> {
        self.requests.lock().await.push(request);
        self.decisions
            .lock()
            .await
            .pop_front()
            .ok_or_else(|| ServiceError::InternalError("missing test decision".to_string()))
    }
}

fn noop_judge() -> Arc<RecordingJudge> {
    Arc::new(RecordingJudge::with_outcome("complete"))
}

#[derive(Default)]
struct RecordingFrontendDelivery {
    commands: Mutex<Vec<FrontendDeliveryCommand>>,
}

#[async_trait]
impl FrontendDeliveryPort for RecordingFrontendDelivery {
    async fn publish(&self, cmd: FrontendDeliveryCommand) -> ServiceResult<FrontendDeliveryResult> {
        let target = cmd.target.clone();
        self.commands.lock().await.push(cmd);
        Ok(FrontendDeliveryResult {
            target,
            delivered: 1,
        })
    }

    async fn unregister_run(&self, _run_id: &str) -> ServiceResult<()> {
        Ok(())
    }
}

#[derive(Default)]
struct RecordingResultPublisher {
    commands: Mutex<Vec<StateMachineResultPublishCommand>>,
}

#[async_trait]
impl StateMachineResultPublisherPort for RecordingResultPublisher {
    async fn publish_state_machine_result(
        &self,
        cmd: StateMachineResultPublishCommand,
    ) -> ServiceResult<()> {
        self.commands.lock().await.push(cmd);
        Ok(())
    }
}

struct FailingResultPublisher;

#[async_trait]
impl StateMachineResultPublisherPort for FailingResultPublisher {
    async fn publish_state_machine_result(
        &self,
        _cmd: StateMachineResultPublishCommand,
    ) -> ServiceResult<()> {
        Err(ServiceError::InternalError(
            "simulated result persistence failure".to_string(),
        ))
    }
}

fn chat_send_params(command: &BotDeliveryCommand) -> ChatSendParams {
    match &command.frame {
        BcsFrame::Request(request) => {
            assert_eq!(request.method, "chat.send");
            serde_json::from_value(request.params.clone().expect("chat.send params"))
                .expect("chat.send params decode")
        }
        _ => panic!("expected chat.send request frame"),
    }
}

fn test_group() -> Group {
    Group::new(
        "group-1",
        "driver-bot",
        vec![Participant {
            bot_uuid: "driver-bot".to_string(),
            bot_name: Some("Driver".to_string()),
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: ActorKind::Bot,
            mode: Some(ParticipantMode::Auto),
        }],
    )
}

fn session_collaboration_group(strategy: GroupStrategy) -> Group {
    let mut group = Group::new(
        "group-1",
        "driver-bot",
        vec![
            Participant {
                bot_uuid: "driver-bot".to_string(),
                bot_name: Some("Driver".to_string()),
                kind: None,
                role: match strategy {
                    GroupStrategy::ManagerWorker => ParticipantRole::Manager,
                    GroupStrategy::Chat | GroupStrategy::StateMachine => ParticipantRole::Driver,
                },
                actor_kind: ActorKind::Bot,
                mode: Some(ParticipantMode::Auto),
            },
            Participant {
                bot_uuid: "worker-bot".to_string(),
                bot_name: Some("Worker".to_string()),
                kind: None,
                role: match strategy {
                    GroupStrategy::ManagerWorker => ParticipantRole::Worker,
                    GroupStrategy::Chat | GroupStrategy::StateMachine => {
                        ParticipantRole::Consultant
                    }
                },
                actor_kind: ActorKind::Bot,
                mode: Some(ParticipantMode::Auto),
            },
        ],
    );
    group.group_strategy = strategy;
    group
}

fn state_machine_test_group() -> Group {
    let mut group = test_group();
    group.group_strategy = GroupStrategy::StateMachine;
    group
}

fn one_shot_authoring_yaml() -> String {
    r#"
name: One Shot
participants:
  writer:
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      answer:
        kind: bot_task
        display_name: Answer
        assignee:
          type: bot_binding
          binding: writer
        instruction: Answer the current question.
        final_output: true
"#
    .to_string()
}

fn single_node_yaml() -> String {
    r#"
api_version: bcs.collaboration/v1
id: single_node
version: 1
name: Single Node
participants:
  driver:
    bot_id: driver-bot
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    defaults:
      node_timeout_ms: 120000
      max_attempts: 2
    nodes:
      answer:
        kind: bot_task
        display_name: Answer
        node_timeout_ms: 60000
        max_attempts: 3
        assignee:
          type: bot_binding
          binding: driver
        instruction: Answer the question.
        final_output: true
"#
    .to_string()
}

fn human_input_yaml() -> String {
    r#"
api_version: bcs.collaboration/v1
id: human_input_single
version: 1
name: Human Review
participants: {}
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    human_input_channel:
      channel_type: dingtalk
      fixed_group:
        conversation_type: group
        conversation_id: cid-review
    nodes:
      review:
        kind: human_input
        display_name: Review
        assignee:
          type: runtime_actor
          actor: human_1001
        notification:
          mode: fixed_group
        instruction: 请用自然语言给出你的意见。
        node_timeout_ms: 60000
"#
    .to_string()
}

fn frontend_human_input_yaml() -> String {
    r#"
api_version: bcs.collaboration/v1
id: frontend_human_input
version: 1
name: Frontend Human Review
participants: {}
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      review:
        kind: human_input
        display_name: Review
        instruction: 请在前端给出你的意见。
        node_timeout_ms: 60000
"#
    .to_string()
}

fn judged_human_input_yaml() -> String {
    r#"
api_version: bcs.collaboration/v1
id: human_input_judged
version: 1
name: Human Review With Judge
participants: {}
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    human_input_channel:
      channel_type: dingtalk
      fixed_group:
        conversation_type: group
        conversation_id: cid-review
    nodes:
      review:
        kind: human_input
        display_name: Review
        assignee:
          type: runtime_actor
          actor: human_1001
        notification:
          mode: fixed_group
        instruction: 请用自然语言给出你的意见。
        node_timeout_ms: 60000
        transitions:
          approved:
            targets: []
          rejected:
            targets: []
        judge:
          type: llm
          criteria:
            - 是否明确批准或拒绝
          outcomes:
            - approved
            - rejected
"#
    .to_string()
}

fn single_node_authoring_yaml(name: &str) -> String {
    format!(
        r#"
api_version: bcs.collaboration/v1
name: {name}
participants:
  driver:
    bot_id: driver-bot
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      answer:
        kind: bot_task
        display_name: Answer
        assignee:
          type: bot_binding
          binding: driver
        instruction: Answer the question.
        final_output: true
"#
    )
}

fn single_node_template_yaml() -> String {
    r#"
api_version: bcs.collaboration/v1
id: single_node_template
version: 1
name: Single Node Template
participants:
  driver:
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      answer:
        kind: bot_task
        display_name: Answer
        assignee:
          type: bot_binding
          binding: driver
        instruction: Answer the question.
        final_output: true
"#
    .to_string()
}

async fn complete_with_text(
    runtime: &CollaborationRuntime,
    delivery_run_id: &str,
    session_id: &str,
    text: &str,
) -> bcs_service_api::HandleBotTerminalEventOutcome {
    runtime
        .handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
            bot_id: "driver-bot".to_string(),
            run_id: delivery_run_id.to_string(),
            event_type: "chat.event".to_string(),
            event_payload: json!({
                "run_id": delivery_run_id,
                "bcs_group_id": "group-1",
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": text}]
                }
            }),
            state: ChatEventState::Final,
            bcs_session_id: Some(session_id.to_string()),
        })
        .await
        .expect("handle final")
}

async fn wait_for_callback_status(
    sessions: &Arc<SessionManagementServiceImpl>,
    session_id: &str,
    expected: &str,
) {
    let mut last = None;
    for _ in 0..100 {
        let session = sessions
            .get(session_id)
            .await
            .expect("get session")
            .expect("session");
        last = session.callback_status.clone();
        if session.callback_status.as_deref() == Some(expected) {
            return;
        }
        tokio::time::sleep(Duration::from_millis(25)).await;
    }
    panic!(
        "callback_status did not become {expected}; last status was {:?}",
        last
    );
}

fn join_yaml() -> String {
    r#"
api_version: bcs.collaboration/v1
id: join_graph
version: 1
name: Join Graph
participants:
  driver:
    bot_id: driver-bot
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      start:
        kind: bot_task
        display_name: Start
        assignee:
          type: bot_binding
          binding: driver
        instruction: Start.
        transitions:
          complete:
            targets: [branch_b, branch_c]
      branch_b:
        kind: bot_task
        display_name: Branch B
        assignee:
          type: bot_binding
          binding: driver
        instruction: B.
        transitions:
          complete:
            targets: [join]
      branch_c:
        kind: bot_task
        display_name: Branch C
        assignee:
          type: bot_binding
          binding: driver
        instruction: C.
        transitions:
          complete:
            targets: [join]
      join:
        kind: bot_task
        display_name: Join
        assignee:
          type: bot_binding
          binding: driver
        instruction: Join.
        final_output: true
"#
    .to_string()
}

fn judge_branch_yaml() -> String {
    r#"
api_version: bcs.collaboration/v1
id: judge_branch
version: 1
name: Judge Branch
participants:
  driver:
    bot_id: driver-bot
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      review:
        kind: bot_task
        display_name: Review
        assignee:
          type: bot_binding
          binding: driver
        instruction: Produce a candidate artifact.
        judge:
          type: llm
          criteria:
            - Is the answer good enough?
          outcomes: [approved, rejected]
        transitions:
          approved:
            targets: [publish]
          rejected:
            targets: [revise]
      publish:
        kind: bot_task
        display_name: Publish
        assignee:
          type: bot_binding
          binding: driver
        instruction: Publish final answer.
        final_output: true
      revise:
        kind: bot_task
        display_name: Revise
        assignee:
          type: bot_binding
          binding: driver
        instruction: Revise answer.
        transitions:
          complete:
            targets: [manual_review]
      manual_review:
        kind: bot_task
        display_name: Manual Review
        assignee:
          type: bot_binding
          binding: driver
        instruction: Review revised answer.
        transitions:
          complete:
            targets: [publish]
"#
    .to_string()
}

fn judge_timeout_yaml() -> String {
    r#"
api_version: bcs.collaboration/v1
id: judge_timeout
version: 1
name: Judge Timeout
participants:
  driver:
    bot_id: driver-bot
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      review:
        kind: bot_task
        display_name: Review
        node_timeout_ms: 1
        assignee:
          type: bot_binding
          binding: driver
        instruction: Produce a candidate artifact.
        judge:
          type: llm
          criteria:
            - Is the answer good enough?
          outcomes: [approved, rejected]
        transitions:
          approved:
            targets: [publish]
          rejected:
            targets: []
      publish:
        kind: bot_task
        display_name: Publish
        assignee:
          type: bot_binding
          binding: driver
        instruction: Publish final answer.
        final_output: true
"#
    .to_string()
}

fn judge_shared_merge_yaml() -> String {
    r#"
api_version: bcs.collaboration/v1
id: judge_shared_merge
version: 1
name: Judge Shared Merge
participants:
  driver:
    bot_id: driver-bot
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      review:
        kind: bot_task
        display_name: Review
        assignee:
          type: bot_binding
          binding: driver
        instruction: Produce a candidate artifact.
        judge:
          type: llm
          criteria:
            - Is the answer good enough?
          outcomes: [approved, rejected]
        transitions:
          approved:
            targets: [fast]
          rejected:
            targets: [slow]
      fast:
        kind: bot_task
        display_name: Fast Path
        assignee:
          type: bot_binding
          binding: driver
        instruction: Continue approved answer.
        transitions:
          complete:
            targets: [merge]
      slow:
        kind: bot_task
        display_name: Slow Path
        assignee:
          type: bot_binding
          binding: driver
        instruction: Revise rejected answer.
        transitions:
          complete:
            targets: [merge]
      merge:
        kind: bot_task
        display_name: Merge
        assignee:
          type: bot_binding
          binding: driver
        instruction: Produce final answer.
        final_output: true
"#
    .to_string()
}
