use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_bot::BotCore;
use bcs_group::{GroupCore, MemoryGroupRepo};
use bcs_service_api::port::repo::{GroupRepoPort, SessionRepoPort};
use bcs_service_api::{
    ActorKind, BotCapabilities, BotRegistryCoreService, CancelStateMachineRunCommand,
    CollaborationDefinition, CollaborationRuntimeError, CollaborationRuntimeService,
    ConfigureGroupRuntimeCommand, ConfigureGroupRuntimeOutcome, CreateSessionLaunch, DeliveryType,
    Group, GroupCoreService, GroupStrategy, HandleBotTerminalEventCommand,
    HandleBotTerminalEventOutcome, Participant, ParticipantMode, ParticipantRole,
    ReactivateSessionLaunch, ServiceResult, SessionCaller, SessionHistoryResult, SessionKind,
    SessionLaunchError, SessionLaunchRequest, SessionLaunchService, SessionManagementService,
    StartStateMachineRunCommand, StartStateMachineRunOutcome, StateMachineDeliveryCorrelation,
    StateMachineRun, StateMachineRunStatus, StateMachineRunView, SystemMessageEvent,
    SystemMessageService,
};
use bcs_session::{SessionLaunchApplication, SessionManagementServiceImpl};
use bcs_session_store::MemorySessionRepo;

#[derive(Default)]
struct RecordingRuntime {
    commands: Mutex<Vec<StartStateMachineRunCommand>>,
}

#[async_trait]
impl CollaborationRuntimeService for RecordingRuntime {
    async fn start_state_machine_run(
        &self,
        command: StartStateMachineRunCommand,
    ) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> {
        self.commands
            .lock()
            .expect("runtime commands lock")
            .push(command.clone());
        let session_id = command.session_id.clone().expect("Session launch pins id");
        Ok(StartStateMachineRunOutcome {
            view: StateMachineRunView {
                run: StateMachineRun {
                    run_id: format!("run-{session_id}"),
                    definition_id: "definition-1".into(),
                    definition_version: 1,
                    group_id: command.group_id,
                    group_version: 1,
                    session_id,
                    created_by: command.caller_id,
                    status: StateMachineRunStatus::Running,
                    input: command.input,
                    output: None,
                    error: None,
                    created_at: 1,
                    updated_at: 1,
                    completed_at: None,
                },
                nodes: Vec::new(),
                judge_outputs: Vec::new(),
            },
        })
    }

    async fn get_state_machine_run(
        &self,
        _run_id: &str,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        Ok(None)
    }

    async fn get_state_machine_session_history(
        &self,
        _session_id: &str,
        _limit: u64,
        _before: Option<u64>,
    ) -> Result<Option<SessionHistoryResult>, CollaborationRuntimeError> {
        Ok(None)
    }

    async fn cancel_state_machine_run(
        &self,
        command: CancelStateMachineRunCommand,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        Err(CollaborationRuntimeError::RunNotFound(command.run_id))
    }

    async fn lookup_delivery_correlation(
        &self,
        _run_id: &str,
    ) -> Result<Option<StateMachineDeliveryCorrelation>, CollaborationRuntimeError> {
        Ok(None)
    }

    async fn register_delivery_alias(
        &self,
        _delivery_request_id: &str,
        _bot_delivery_run_id: String,
    ) -> Result<(), CollaborationRuntimeError> {
        Ok(())
    }

    async fn handle_bot_terminal_event(
        &self,
        _command: HandleBotTerminalEventCommand,
    ) -> Result<HandleBotTerminalEventOutcome, CollaborationRuntimeError> {
        Ok(HandleBotTerminalEventOutcome {
            consumed: false,
            view: None,
        })
    }

    async fn upsert_definition(
        &self,
        _definition: CollaborationDefinition,
    ) -> Result<(), CollaborationRuntimeError> {
        Ok(())
    }

    async fn configure_group_runtime(
        &self,
        _command: ConfigureGroupRuntimeCommand,
    ) -> Result<ConfigureGroupRuntimeOutcome, CollaborationRuntimeError> {
        Err(CollaborationRuntimeError::InvalidRequest(
            "not used by Session launch tests".into(),
        ))
    }
}

#[derive(Default)]
struct RecordingSystemMessage {
    events: Mutex<Vec<(String, SystemMessageEvent, String, Vec<Participant>)>>,
}

#[async_trait]
impl SystemMessageService for RecordingSystemMessage {
    async fn notify(
        &self,
        group_id: &str,
        event: SystemMessageEvent,
        session_id: &str,
        participants: &[Participant],
    ) -> ServiceResult<usize> {
        self.events.lock().expect("system events lock").push((
            group_id.to_string(),
            event,
            session_id.to_string(),
            participants.to_vec(),
        ));
        Ok(participants.len())
    }
}

struct Fixture {
    service: SessionLaunchApplication,
    bots: Arc<BotCore>,
    groups: Arc<GroupCore>,
    sessions: Arc<SessionManagementServiceImpl>,
    session_repo: Arc<MemorySessionRepo>,
    runtime: Arc<RecordingRuntime>,
    system_message: Arc<RecordingSystemMessage>,
}

impl Fixture {
    fn new() -> Self {
        let bots = Arc::new(BotCore::memory());
        let group_repo: Arc<dyn GroupRepoPort> = Arc::new(MemoryGroupRepo::new());
        let groups = Arc::new(GroupCore::with_repo(group_repo.clone()));
        let session_repo = Arc::new(MemorySessionRepo::new());
        let sessions = Arc::new(SessionManagementServiceImpl::new(
            session_repo.clone(),
            group_repo,
        ));
        let runtime = Arc::new(RecordingRuntime::default());
        let system_message = Arc::new(RecordingSystemMessage::default());
        let service = SessionLaunchApplication::new(
            bots.clone(),
            groups.clone(),
            sessions.clone(),
            runtime.clone(),
            system_message.clone(),
        );
        Self {
            service,
            bots,
            groups,
            sessions,
            session_repo,
            runtime,
            system_message,
        }
    }

    async fn add_bot(&self, bot_id: &str, owner: &str) {
        self.bots
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    visibility: "public".into(),
                    ..Default::default()
                },
            )
            .await
            .expect("register bot");
        self.bots
            .save_created_by(bot_id, owner, true)
            .await
            .expect("store owner");
    }

    async fn add_group(&self, group: Group) {
        self.groups.upsert(group).await.expect("store group");
    }
}

fn human(owner_id: &str) -> SessionCaller {
    SessionCaller::Human {
        actor_id: format!("human_{owner_id}"),
        owner_id: owner_id.to_string(),
        display_name: Some(owner_id.to_string()),
    }
}

fn request(
    caller: SessionCaller,
    group_id: &str,
    requested_creator: Option<&str>,
) -> SessionLaunchRequest {
    SessionLaunchRequest {
        caller,
        group_id: group_id.to_string(),
        requested_creator: requested_creator.map(str::to_string),
        title: None,
        kind: Some(SessionKind::Chat),
        input: None,
        meta: None,
        public_creator_role: None,
        context_delivery: None,
    }
}

#[tokio::test]
async fn human_creates_as_owned_bot() {
    let fixture = Fixture::new();
    fixture.add_bot("driver", "alice").await;
    fixture.add_bot("worker", "alice").await;
    fixture
        .add_group(Group::new(
            "group-1",
            "driver",
            vec![
                Participant::bot("driver", ParticipantRole::Driver),
                Participant::bot("worker", ParticipantRole::Consultant),
            ],
        ))
        .await;

    let outcome = fixture
        .service
        .create(CreateSessionLaunch {
            request: request(human("alice"), "group-1", Some("worker")),
        })
        .await
        .expect("owned Bot may create");

    assert_eq!(outcome.session.created_by.as_deref(), Some("worker"));
    assert_eq!(
        outcome.session.caller_principal.as_deref(),
        Some("human_alice")
    );
}

#[tokio::test]
async fn human_cannot_create_as_unowned_bot() {
    let fixture = Fixture::new();
    fixture.add_bot("driver", "alice").await;
    fixture.add_bot("worker", "bob").await;
    fixture
        .add_group(Group::new(
            "group-1",
            "driver",
            vec![
                Participant::bot("driver", ParticipantRole::Driver),
                Participant::bot("worker", ParticipantRole::Consultant),
            ],
        ))
        .await;

    let result = fixture
        .service
        .create(CreateSessionLaunch {
            request: request(human("alice"), "group-1", Some("worker")),
        })
        .await;

    assert!(matches!(result, Err(SessionLaunchError::Forbidden(_))));
}

#[tokio::test]
async fn bot_creates_only_as_itself() {
    let fixture = Fixture::new();
    fixture.add_bot("driver", "alice").await;
    fixture.add_bot("worker", "alice").await;
    fixture
        .add_group(Group::new(
            "group-1",
            "driver",
            vec![
                Participant::bot("driver", ParticipantRole::Driver),
                Participant::bot("worker", ParticipantRole::Consultant),
            ],
        ))
        .await;

    let result = fixture
        .service
        .create(CreateSessionLaunch {
            request: request(
                SessionCaller::Bot {
                    bot_uuid: "driver".into(),
                },
                "group-1",
                Some("worker"),
            ),
        })
        .await;

    assert!(matches!(result, Err(SessionLaunchError::Forbidden(_))));
}

#[tokio::test]
async fn public_non_member_is_added_with_requested_role() {
    let fixture = Fixture::new();
    fixture.add_bot("driver", "alice").await;
    let mut group = Group::new(
        "group-public",
        "driver",
        vec![Participant::bot("driver", ParticipantRole::Driver)],
    );
    group.visibility = "public".into();
    fixture.add_group(group).await;

    let mut launch = request(human("bob"), "group-public", None);
    launch.public_creator_role = Some(ParticipantRole::Observer);
    let outcome = fixture
        .service
        .create(CreateSessionLaunch { request: launch })
        .await
        .expect("public non-member may create");

    let participant = outcome
        .session
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == "human_bob")
        .expect("Human inserted");
    assert_eq!(participant.actor_kind, ActorKind::Human);
    assert_eq!(participant.role, ParticipantRole::Observer);
    assert_eq!(participant.mode, Some(ParticipantMode::Present));
}

#[tokio::test]
async fn inferred_private_human_creator_is_not_auto_added() {
    let fixture = Fixture::new();
    fixture.add_bot("driver", "alice").await;
    fixture
        .add_group(Group::new(
            "group-1",
            "driver",
            vec![Participant::bot("driver", ParticipantRole::Driver)],
        ))
        .await;

    let outcome = fixture
        .service
        .create(CreateSessionLaunch {
            request: request(human("alice"), "group-1", None),
        })
        .await
        .expect("Human owner may create");

    assert_eq!(outcome.session.created_by.as_deref(), Some("human_alice"));
    assert!(
        outcome
            .session
            .participants
            .iter()
            .all(|participant| participant.bot_uuid != "human_alice")
    );
}

#[tokio::test]
async fn state_machine_human_is_added_as_present_observer() {
    let fixture = Fixture::new();
    fixture.add_bot("driver", "alice").await;
    let mut group = Group::new(
        "group-sm",
        "driver",
        vec![Participant::bot("driver", ParticipantRole::Driver)],
    );
    group.group_strategy = GroupStrategy::StateMachine;
    fixture.add_group(group).await;

    let outcome = fixture
        .service
        .create(CreateSessionLaunch {
            request: SessionLaunchRequest {
                kind: Some(SessionKind::ServiceInvocation),
                ..request(human("alice"), "group-sm", Some("driver"))
            },
        })
        .await
        .expect("StateMachine session created");

    let participant = outcome
        .session
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == "human_alice")
        .expect("authenticated Human inserted");
    assert_eq!(participant.role, ParticipantRole::Observer);
    assert_eq!(participant.mode, Some(ParticipantMode::Present));
}

#[tokio::test]
async fn launch_matrix_routes_only_state_machine_service_to_runtime() {
    let cases = [
        (GroupStrategy::Chat, SessionKind::Chat, false),
        (GroupStrategy::Chat, SessionKind::ServiceInvocation, false),
        (GroupStrategy::ManagerWorker, SessionKind::Chat, false),
        (
            GroupStrategy::ManagerWorker,
            SessionKind::ServiceInvocation,
            false,
        ),
        (GroupStrategy::StateMachine, SessionKind::Chat, false),
        (
            GroupStrategy::StateMachine,
            SessionKind::ServiceInvocation,
            true,
        ),
    ];

    for (index, (strategy, kind, expects_runtime)) in cases.into_iter().enumerate() {
        let fixture = Fixture::new();
        let lead_role = if strategy == GroupStrategy::ManagerWorker {
            ParticipantRole::Manager
        } else {
            ParticipantRole::Driver
        };
        fixture.add_bot("lead", "alice").await;
        let mut group = Group::new(
            format!("group-{index}"),
            "lead",
            vec![Participant::bot("lead", lead_role)],
        );
        group.group_strategy = strategy;
        let group_id = group.id.clone();
        fixture.add_group(group).await;

        let outcome = fixture
            .service
            .create(CreateSessionLaunch {
                request: SessionLaunchRequest {
                    kind: Some(kind),
                    input: Some(serde_json::json!({"case": index})),
                    ..request(human("alice"), &group_id, Some("lead"))
                },
            })
            .await
            .expect("matrix launch succeeds");
        tokio::task::yield_now().await;

        assert_eq!(outcome.state_machine_run.is_some(), expects_runtime);
        assert_eq!(
            fixture
                .runtime
                .commands
                .lock()
                .expect("runtime commands lock")
                .len(),
            usize::from(expects_runtime)
        );
        assert_eq!(
            fixture
                .system_message
                .events
                .lock()
                .expect("system events lock")
                .len(),
            usize::from(!expects_runtime)
        );
    }
}

#[tokio::test]
async fn raw_input_metadata_and_context_delivery_reach_session_context() {
    let fixture = Fixture::new();
    fixture.add_bot("driver", "alice").await;
    fixture
        .add_group(Group::new(
            "group-1",
            "driver",
            vec![Participant::bot("driver", ParticipantRole::Driver)],
        ))
        .await;
    let input = serde_json::json!({"query": "hello", "custom": {"n": 1}});
    let meta = serde_json::json!({
        "callback_target": {"baas_session_id": "external-1"},
        "channel": {"source": "caller-value", "binding_id": "binding-1"},
        "unknown": true
    });

    let outcome = fixture
        .service
        .create(CreateSessionLaunch {
            request: SessionLaunchRequest {
                input: Some(input.clone()),
                meta: Some(meta.clone()),
                context_delivery: Some(DeliveryType::Inject),
                ..request(human("alice"), "group-1", Some("driver"))
            },
        })
        .await
        .expect("launch succeeds");
    tokio::task::yield_now().await;

    assert_eq!(outcome.session.input, Some(input.clone()));
    assert_eq!(outcome.session.meta, Some(meta));
    let events = fixture
        .system_message
        .events
        .lock()
        .expect("system events lock");
    let SystemMessageEvent::SessionContext {
        session_input,
        driver_delivery,
        ..
    } = &events[0].1
    else {
        panic!("expected SessionContext")
    };
    assert_eq!(session_input, &Some(input));
    assert_eq!(*driver_delivery, Some(DeliveryType::Inject));
}

#[tokio::test]
async fn reactivate_replaces_input_and_preserves_other_session_fields() {
    let fixture = Fixture::new();
    fixture.add_bot("driver", "alice").await;
    fixture
        .add_group(Group::new(
            "group-1",
            "driver",
            vec![Participant::bot("driver", ParticipantRole::Driver)],
        ))
        .await;
    let old_meta = serde_json::json!({"channel": {"source": "original"}});
    let created = fixture
        .service
        .create(CreateSessionLaunch {
            request: SessionLaunchRequest {
                title: Some("original title".into()),
                kind: Some(SessionKind::ServiceInvocation),
                input: Some(serde_json::json!({"old": true})),
                meta: Some(old_meta.clone()),
                ..request(human("alice"), "group-1", Some("driver"))
            },
        })
        .await
        .expect("create");
    tokio::task::yield_now().await;
    fixture
        .system_message
        .events
        .lock()
        .expect("system events lock")
        .clear();
    fixture
        .sessions
        .complete_if_running(&created.session.id, None, None)
        .await
        .expect("complete");
    fixture
        .session_repo
        .update_callback_status(&created.session.id, "success")
        .await
        .expect("complete callback");

    let new_input = serde_json::json!({"new": true});
    let outcome = fixture
        .service
        .reactivate(ReactivateSessionLaunch {
            session_id: created.session.id.clone(),
            request: SessionLaunchRequest {
                title: Some("ignored title".into()),
                kind: Some(SessionKind::ServiceInvocation),
                input: Some(new_input.clone()),
                meta: Some(serde_json::json!({"ignored": true})),
                ..request(human("alice"), "group-1", Some("driver"))
            },
        })
        .await
        .expect("reactivate");
    tokio::task::yield_now().await;

    assert_eq!(outcome.session.input, Some(new_input));
    assert_eq!(outcome.session.meta, Some(old_meta));
    assert_eq!(
        outcome.session.session_title.as_deref(),
        Some("original title")
    );
    assert_eq!(outcome.session.session_kind, SessionKind::ServiceInvocation);
    assert!(
        fixture
            .system_message
            .events
            .lock()
            .expect("system events lock")
            .is_empty()
    );
}

#[tokio::test]
async fn reactivate_rejects_session_from_another_group() {
    let fixture = Fixture::new();
    fixture.add_bot("driver", "alice").await;
    fixture
        .add_group(Group::new(
            "group-1",
            "driver",
            vec![Participant::bot("driver", ParticipantRole::Driver)],
        ))
        .await;
    fixture
        .add_group(Group::new(
            "group-2",
            "driver",
            vec![Participant::bot("driver", ParticipantRole::Driver)],
        ))
        .await;
    let created = fixture
        .service
        .create(CreateSessionLaunch {
            request: request(human("alice"), "group-1", Some("driver")),
        })
        .await
        .expect("create");
    fixture
        .sessions
        .complete_if_running(&created.session.id, None, None)
        .await
        .expect("complete");

    let result = fixture
        .service
        .reactivate(ReactivateSessionLaunch {
            session_id: created.session.id,
            request: request(human("alice"), "group-2", Some("driver")),
        })
        .await;

    assert!(matches!(
        result,
        Err(SessionLaunchError::SessionNotFound(_))
    ));
}
