//! Integration tests for the V1 Session facade.
//!
//! Exercises the `SessionService` + `SessionMessageService` impls against the
//! real in-memory store stack (GroupCore / BotCore / SessionManagementService
//! / MemorySessionRepo / MemoryMessageRepo), mirroring the sibling
//! `bcs-app-group` test harness.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use async_trait::async_trait;
use bcs_app_session::{SessionServiceConfig, SessionServiceImpl};
use bcs_bot::BotCore;
use bcs_bot_store::PersistentBotRepo;
use bcs_cache_local::InMemoryCachePlugin;
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement, DbTransactionStep,
    DbTransactionStepResult,
};
use bcs_domain::{AttachmentType, MessageAttachment};
use bcs_friend::FriendCore;
use bcs_group::{GroupCore, MemoryGroupRepo};
use bcs_relation::RelationCore;
use bcs_service_api::application::system_message::SystemMessageService;
use bcs_service_api::application::v1::{
    AddSessionParticipant, AuthenticatedAppIdentity, AuthenticatedBotIdentity, AuthenticatedCaller,
    AuthenticatedUserIdentity, CollectSession, CompleteSession, CreateSession, DeleteSession,
    DeleteSessionParticipant, GetSession, ListSessionMessages, ListSessions, SessionMessageService,
    SessionParticipantInput, SessionService,
    SessionStatus as V1SessionStatus, UncollectSession, UpdateSession, UpdateSessionParticipant,
};
use bcs_service_api::port::repo::{NewSessionParams, SessionRepoPort};
use bcs_service_api::{
    ActorKind, ActorStatus, BotCapabilities, BotRegistryCoreService, CallerContext,
    CancelStateMachineRunCommand, CollaborationDefinition, CollaborationRuntimeError,
    CollaborationRuntimeService, ConfigureGroupRuntimeCommand, ConfigureGroupRuntimeOutcome,
    FriendCoreService, Group,
    GroupCoreService, GroupHistoryCommand, GroupHistoryResult, GroupMessage,
    GroupMessageHistoryService, GroupMessageType, GroupStrategy, GroupUseCaseError,
    HandleBotTerminalEventCommand, HandleBotTerminalEventOutcome, HumanActor, MessageRole,
    Participant, ParticipantMode, ParticipantRole, ServiceResult, SessionCaller,
    SessionHistoryCommand, SessionHistoryResult, SessionKind, StartStateMachineRunCommand,
    StartStateMachineRunOutcome, StateMachineDeliveryCorrelation, StateMachineRun,
    StateMachineRunStatus, StateMachineRunView, SystemMessageEvent,
};
use bcs_session::{SessionLaunchApplication, SessionManagementServiceImpl};
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::NoopSystemMessageService;

#[derive(Default)]
struct RecordingHistoryService {
    session_calls: Mutex<Vec<SessionHistoryCommand>>,
    messages: Mutex<Vec<GroupMessage>>,
}

/// Records every `SystemMessageService::notify` call so tests can assert that
/// `SessionServiceImpl::update_participant` emits `ParticipantModeChanged`.
#[derive(Default)]
struct RecordingSystemMessageService {
    events: Mutex<Vec<RecordedSystemMessage>>,
}

struct RecordedSystemMessage {
    #[allow(dead_code)]
    group_id: String,
    session_id: String,
    event: SystemMessageEvent,
}

#[async_trait]
impl SystemMessageService for RecordingSystemMessageService {
    async fn notify(
        &self,
        group_id: &str,
        event: SystemMessageEvent,
        session_id: &str,
        _participants: &[Participant],
    ) -> ServiceResult<usize> {
        self.events
            .lock()
            .expect("sysmsg lock")
            .push(RecordedSystemMessage {
                group_id: group_id.to_string(),
                session_id: session_id.to_string(),
                event,
            });
        Ok(0)
    }
}

#[async_trait]
impl GroupMessageHistoryService for RecordingHistoryService {
    async fn get_history(
        &self,
        _cmd: GroupHistoryCommand,
    ) -> Result<GroupHistoryResult, GroupUseCaseError> {
        panic!("group history is not used by SessionServiceImpl")
    }

    async fn get_session_history(
        &self,
        cmd: SessionHistoryCommand,
    ) -> Result<SessionHistoryResult, GroupUseCaseError> {
        let messages = self.messages.lock().expect("messages lock").clone();
        self.session_calls
            .lock()
            .expect("history lock")
            .push(cmd.clone());
        Ok(SessionHistoryResult {
            session_id: cmd.session_id,
            messages,
            limit: cmd.limit,
            before: cmd.before,
            next_before: None,
        })
    }
}

#[derive(Default)]
struct RecordingRuntime {
    start_calls: Mutex<Vec<StartStateMachineRunCommand>>,
    history_calls: Mutex<Vec<(String, u64, Option<u64>)>>,
    history_result: Mutex<Option<SessionHistoryResult>>,
}

#[async_trait]
impl CollaborationRuntimeService for RecordingRuntime {
    async fn start_state_machine_run(
        &self,
        cmd: StartStateMachineRunCommand,
    ) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> {
        self.start_calls
            .lock()
            .expect("runtime start lock")
            .push(cmd.clone());
        let session_id = cmd.session_id.expect("Session launch pins id");
        Ok(StartStateMachineRunOutcome {
            view: StateMachineRunView {
                run: StateMachineRun {
                    run_id: "run-1".into(),
                    definition_id: "definition-1".into(),
                    definition_version: 1,
                    group_id: cmd.group_id,
                    group_version: 1,
                    session_id,
                    created_by: cmd.caller_id,
                    status: StateMachineRunStatus::Running,
                    input: cmd.input,
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
        session_id: &str,
        limit: u64,
        before: Option<u64>,
    ) -> Result<Option<SessionHistoryResult>, CollaborationRuntimeError> {
        self.history_calls
            .lock()
            .expect("runtime history lock")
            .push((session_id.to_string(), limit, before));
        Ok(self
            .history_result
            .lock()
            .expect("runtime result lock")
            .clone())
    }

    async fn cancel_state_machine_run(
        &self,
        _cmd: CancelStateMachineRunCommand,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        panic!("cancel_state_machine_run is not used by SessionServiceImpl")
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
        _cmd: HandleBotTerminalEventCommand,
    ) -> Result<HandleBotTerminalEventOutcome, CollaborationRuntimeError> {
        panic!("handle_bot_terminal_event is not used by SessionServiceImpl")
    }

    async fn upsert_definition(
        &self,
        _definition: CollaborationDefinition,
    ) -> Result<(), CollaborationRuntimeError> {
        Ok(())
    }

    async fn configure_group_runtime(
        &self,
        _cmd: ConfigureGroupRuntimeCommand,
    ) -> Result<ConfigureGroupRuntimeOutcome, CollaborationRuntimeError> {
        panic!("configure_group_runtime is not used by SessionServiceImpl")
    }
}

fn rich_group_message() -> GroupMessage {
    GroupMessage {
        id: "message-1".into(),
        timestamp: 1_786_590_000_000,
        sender: "worker-a".into(),
        content: "done".into(),
        message_type: GroupMessageType::Bot,
        bot_name: Some("Worker A".into()),
        role: MessageRole::Assistant,
        run_id: "run-1".into(),
        history_meta: Some(serde_json::json!({"assistantAggregation": true})),
        metadata: Some(serde_json::json!({"tool": "search"})),
        attachments: Some(vec![MessageAttachment {
            attachment_id: "attachment-1".into(),
            attachment_type: AttachmentType::Image,
            file_name: "result.png".into(),
            mime_type: Some("image/png".into()),
            size: Some(42),
            sha256: Some("abcd".into()),
            url: Some("https://download.example/result.png".into()),
            expires_at: Some(1_786_590_060),
        }]),
    }
}

struct Fixture {
    service: SessionServiceImpl,
    groups: Arc<GroupCore>,
    bots: Arc<BotCore>,
    friends: Arc<FriendCore>,
    history: Arc<RecordingHistoryService>,
    runtime: Arc<RecordingRuntime>,
    system_messages: Arc<RecordingSystemMessageService>,
    session_repo: Arc<dyn SessionRepoPort>,
}

impl Fixture {
    async fn new() -> Self {
        Self::new_with_bots(Arc::new(BotCore::memory())).await
    }

    async fn new_with_bots(bots: Arc<BotCore>) -> Self {
        let group_repo: Arc<dyn bcs_service_api::port::repo::GroupRepoPort> =
            Arc::new(MemoryGroupRepo::new());
        let groups = Arc::new(GroupCore::with_repo(group_repo.clone()));
        let relation = Arc::new(RelationCore::memory());
        let friends = Arc::new(FriendCore::memory().with_relation(relation.clone()));
        let friends_handle = friends.clone();
        let session_repo: Arc<dyn SessionRepoPort> = Arc::new(MemorySessionRepo::new());
        let history = Arc::new(RecordingHistoryService::default());
        let runtime = Arc::new(RecordingRuntime::default());
        let system_messages = Arc::new(RecordingSystemMessageService::default());
        let sessions = Arc::new(SessionManagementServiceImpl::new(
            session_repo.clone(),
            group_repo,
        ));
        let launch = Arc::new(SessionLaunchApplication::new(
            bots.clone(),
            groups.clone(),
            sessions.clone(),
            runtime.clone(),
            Arc::new(NoopSystemMessageService),
        ));
        let service = SessionServiceImpl::new(
            launch,
            sessions,
            groups.clone(),
            bots.clone(),
            friends,
            relation,
            session_repo.clone(),
            history.clone(),
            runtime.clone(),
            system_messages.clone(),
            SessionServiceConfig {
                relation_env: "dev".to_string(),
            },
        );
        Self {
            service,
            groups,
            bots,
            friends: friends_handle,
            history,
            runtime,
            system_messages,
            session_repo,
        }
    }

    async fn add_bot(&self, bot_uuid: &str) {
        self.bots
            .register(
                bot_uuid.to_string(),
                BotCapabilities {
                    name: Some(bot_uuid.to_string()),
                    visibility: "public".into(),
                    ..Default::default()
                },
            )
            .await
            .expect("register bot");
        self.bots
            .save_created_by(bot_uuid, bot_uuid, true)
            .await
            .expect("assign test Bot owner");
    }

    /// Register a Bot with explicit `visibility` and `created_by` owner, for
    /// collaboration-eligibility tests that need a non-public or non-caller-owned
    /// actor (the default `add_bot` always registers a `public` Bot owned by
    /// itself).
    async fn add_bot_with(&self, bot_uuid: &str, visibility: &str, created_by: &str) {
        self.bots
            .register(
                bot_uuid.to_string(),
                BotCapabilities {
                    name: Some(bot_uuid.to_string()),
                    visibility: visibility.to_string(),
                    ..Default::default()
                },
            )
            .await
            .expect("register bot");
        self.bots
            .save_created_by(bot_uuid, created_by, true)
            .await
            .expect("assign test Bot owner");
    }

    /// Establish a bidirectional friendship between two Bots in the fixture's
    /// shared in-memory friend repo.
    async fn befriend(&self, bot_a: &str, bot_b: &str) {
        self.friends
            .add_friendship(bot_a, bot_b)
            .await
            .expect("establish friendship");
    }

    async fn store_group(&self, group_id: &str, driver: &str, context: Option<&str>) {
        self.store_group_with_originator(group_id, driver, &format!("human_{driver}"), context)
            .await;
    }

    /// Like `store_group` but lets the caller name a distinct group
    /// `originator` for direct Human management tests.
    async fn store_group_with_originator(
        &self,
        group_id: &str,
        driver: &str,
        originator: &str,
        context: Option<&str>,
    ) {
        let mut group = Group::new(
            group_id,
            driver,
            vec![Participant::bot(driver, ParticipantRole::Driver)],
        );
        group.originator = Some(originator.to_string());
        group.label = Some(group_id.to_string());
        group.group_strategy = GroupStrategy::Chat;
        group.context = context.map(str::to_string);
        self.groups.upsert(group).await.expect("store group");
    }

    /// Store a ManagerWorker group whose `originator` is the given actor id
    /// and whose roster seeds the driver (Driver) plus each worker (Worker).
    /// Used by the view_bot_id authz tests: the ManagerWorker owner-filter
    /// scoping (`IsNull` public vs `Eq(worker)`) is deterministic in the memory
    /// fixture, unlike the Chat `visible_from_seq` cutoff which needs a
    /// `current_msg_seq` bump only the MySQL store performs.
    async fn store_manager_worker_group_with_originator(
        &self,
        group_id: &str,
        driver: &str,
        workers: &[&str],
        originator: &str,
        context: Option<&str>,
    ) {
        let mut participants = vec![Participant::bot(driver, ParticipantRole::Driver)];
        for &worker in workers {
            participants.push(Participant::bot(worker, ParticipantRole::Worker));
        }
        let mut group = Group::new(group_id, driver, participants);
        group.originator = Some(originator.to_string());
        group.label = Some(group_id.to_string());
        group.group_strategy = GroupStrategy::ManagerWorker;
        group.context = context.map(str::to_string);
        self.groups.upsert(group).await.expect("store group");
    }

    async fn store_state_machine_group(&self, group_id: &str, driver: &str) {
        let mut group = Group::new(
            group_id,
            driver,
            vec![Participant::bot(driver, ParticipantRole::Driver)],
        );
        group.originator = Some("human_staff-1".into());
        group.label = Some(group_id.to_string());
        group.group_strategy = GroupStrategy::StateMachine;
        self.groups.upsert(group).await.expect("store group");
    }
}

struct FailingDb;

#[async_trait]
impl DbPlugin for FailingDb {
    async fn query(&self, _statement: DbStatement) -> DbResult<Vec<DbRow>> {
        Err(DbError::Backend("bot database unavailable".into()))
    }

    async fn execute(&self, _statement: DbStatement) -> DbResult<DbExecuteResult> {
        Err(DbError::Backend("bot database unavailable".into()))
    }

    async fn transaction(
        &self,
        _steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        Err(DbError::Backend("bot database unavailable".into()))
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        Ok(DbHealth::unhealthy("bot database unavailable"))
    }
}

fn bot_principal(bot_uuid: &str) -> AuthenticatedCaller {
    human_principal(bot_uuid)
}

fn human_principal(staff_no: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".into()),
        user: Some(AuthenticatedUserIdentity {
            id: staff_no.to_string(),
            username: staff_no.to_string(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

fn bot_only_caller(bot_uuid: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".into()),
        user: None,
        bot: Some(AuthenticatedBotIdentity {
            bot_uuid: bot_uuid.into(),
            owner_id: "alice".into(),
            app_id: 1,
            agent_code: "test".into(),
        }),
        app: None,
        access_key: None,
    }
}

fn app_only_caller() -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".into()),
        user: None,
        bot: None,
        app: Some(AuthenticatedAppIdentity {
            app_id: 1,
            app_name: "test-app".into(),
            owners: "owner-1".into(),
            app_type: "service".into(),
        }),
        access_key: None,
    }
}

fn launch_caller(caller: AuthenticatedCaller) -> SessionCaller {
    if let Some(bot) = caller.bot {
        return SessionCaller::Bot {
            bot_uuid: bot.bot_uuid,
        };
    }
    let user = caller.user.expect("launch test caller identity");
    SessionCaller::Human {
        actor_id: format!("human_{}", user.id),
        owner_id: user.id,
        display_name: user.display_name.or(user.full_name),
    }
}

fn participant_input(bot_uuid: &str, _mode: Option<ParticipantMode>) -> SessionParticipantInput {
    SessionParticipantInput {
        bot_uuid: bot_uuid.to_string(),
    }
}

async fn create_session(
    fixture: &Fixture,
    caller: AuthenticatedCaller,
    group_id: &str,
    _driver: &str,
    _participants: Vec<SessionParticipantInput>,
    input: Option<serde_json::Value>,
    title: Option<&str>,
) -> bcs_service_api::application::v1::CreateSessionOutcome {
    fixture
        .service
        .create(CreateSession {
            caller: launch_caller(caller),
            group_id: group_id.to_string(),
            title: title.map(str::to_string),
            kind: None,
            acting_bot_id: None,
            creator_role: None,
            input,
            meta: None,
            context_delivery: None,
        })
        .await
        .expect("create session")
}

#[tokio::test]
async fn create_as_manager_succeeds_and_projects_participants() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["expert"],
            "human_driver",
            Some("the task"),
        )
        .await;

    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", Some(ParticipantMode::Muted))],
        None,
        Some("session title"),
    )
    .await;

    assert!(outcome.created);
    let detail = outcome.session;
    assert_eq!(detail.group_id, "g1");
    assert_eq!(detail.status, V1SessionStatus::Running);
    assert_eq!(detail.title.as_deref(), Some("session title"));
    // Omitted input stays omitted; shared launch logic does not synthesize it.
    assert_eq!(detail.input, None);
    // Driver (Driver role) + inherited expert from the parent group.
    assert_eq!(detail.participants.len(), 2);
    let expert = detail
        .participants
        .iter()
        .find(|p| p.actor_id == "expert")
        .expect("expert participant");
    assert_eq!(expert.role, ParticipantRole::Worker);
    assert_eq!(expert.mode, ParticipantMode::Auto);
    assert_eq!(expert.name.as_deref(), Some("expert"));
    let driver = detail
        .participants
        .iter()
        .find(|p| p.actor_id == "driver")
        .expect("driver participant");
    assert_eq!(driver.role, ParticipantRole::Driver);
    assert_eq!(driver.mode, ParticipantMode::Auto);
}

#[tokio::test]
async fn create_with_explicit_input_does_not_fall_back() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .store_group("g1", "driver", Some("ignored context"))
        .await;

    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        Some(serde_json::json!({"query": "explicit query"})),
        None,
    )
    .await;

    assert_eq!(
        outcome.session.input,
        Some(serde_json::json!({"query": "explicit query"}))
    );
}

#[tokio::test]
async fn state_machine_service_create_projects_raw_fields_and_run() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture.store_state_machine_group("g1", "driver").await;
    let input = serde_json::json!("run this task");
    let meta = serde_json::json!({
        "callback_target": {"baas_session_id": "baas-1"},
        "channel": {"source": "caller-value"}
    });

    let outcome = fixture
        .service
        .create(CreateSession {
            caller: launch_caller(bot_principal("driver")),
            group_id: "g1".into(),
            title: Some("Invocation".into()),
            kind: Some(SessionKind::ServiceInvocation),
            acting_bot_id: Some("driver".into()),
            creator_role: None,
            input: Some(input.clone()),
            meta: Some(meta.clone()),
            context_delivery: None,
        })
        .await
        .expect("StateMachine service invocation");

    assert_eq!(outcome.session.kind, SessionKind::ServiceInvocation);
    assert_eq!(outcome.session.input, Some(input.clone()));
    assert_eq!(outcome.session.meta, Some(meta));
    assert_eq!(
        outcome.session.state_machine_run_id.as_deref(),
        Some("run-1")
    );
    assert_eq!(
        outcome
            .session
            .state_machine_run
            .as_ref()
            .map(|view| view.run.run_id.as_str()),
        Some("run-1")
    );
    let calls = fixture
        .runtime
        .start_calls
        .lock()
        .expect("runtime start lock");
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].input, input);
}

#[tokio::test]
async fn create_as_non_manager_is_forbidden() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture.store_group("g1", "driver", None).await;

    let error = fixture
        .service
        .create(CreateSession {
            caller: launch_caller(bot_principal("outsider")),
            group_id: "g1".into(),
            title: None,
            kind: None,
            acting_bot_id: None,
            creator_role: None,
            input: None,
            meta: None,
            context_delivery: None,
        })
        .await
        .expect_err("non-manager should be forbidden");
    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}

#[tokio::test]
async fn create_with_unknown_group_is_not_found() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;

    let error = fixture
        .service
        .create(CreateSession {
            caller: launch_caller(bot_principal("driver")),
            group_id: "missing-group".into(),
            title: None,
            kind: None,
            acting_bot_id: None,
            creator_role: None,
            input: None,
            meta: None,
            context_delivery: None,
        })
        .await
        .expect_err("unknown group should 404");
    assert_eq!(error.code(), "group_not_found");
}

#[tokio::test]
async fn list_sorts_desc_and_reports_total() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;

    // Create three sessions; sleep briefly so created_at strictly differs
    // and the DESC primary sort (not just the id tie-breaker) is exercised.
    for _ in 0..3 {
        create_session(
            &fixture,
            bot_principal("driver"),
            "g1",
            "driver",
            vec![participant_input("expert", None)],
            None,
            None,
        )
        .await;
        tokio::time::sleep(Duration::from_millis(6)).await;
    }

    let page = SessionService::list(
        &fixture.service,
        ListSessions {
            caller: bot_principal("driver"),
            group_id: "g1".into(),
            view_bot_id: Some("driver".into()),
            offset: 0,
            limit: 10,
            status: None,
        },
    )
    .await
    .expect("list sessions");

    assert_eq!(page.total, 3);
    assert_eq!(page.items.len(), 3);
    // created_at must be non-increasing (DESC).
    for window in page.items.windows(2) {
        assert!(
            window[0].created_at >= window[1].created_at,
            "sessions must be sorted by created_at DESC"
        );
    }
}

#[tokio::test]
async fn list_authorized_human_with_no_participating_sessions_is_empty() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture.store_group("g1", "driver", None).await;

    let error = SessionService::list(
        &fixture.service,
        ListSessions {
            caller: bot_principal("outsider"),
            group_id: "g1".into(),
            view_bot_id: None,
            offset: 0,
            limit: 10,
            status: None,
        },
    )
    .await
    .expect("an authorized Human view with no matching sessions is empty");
    assert_eq!(error.total, 0);
    assert!(error.items.is_empty());
}

#[tokio::test]
async fn session_list_uses_only_the_selected_authorized_view_actor() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "owned", "unowned"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .bots
        .save_created_by("owned", "alice", true)
        .await
        .expect("assign Alice's Bot ownership");
    fixture.store_group("g1", "driver", None).await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    for participant in ["human_alice", "owned", "unowned"] {
        fixture
            .session_repo
            .create(
                "g1",
                NewSessionParams {
                    participants: vec![if participant.starts_with("human_") {
                        Participant::human(participant, ParticipantRole::Observer)
                    } else {
                        Participant::bot(participant, ParticipantRole::Consultant)
                    }],
                    group_version: Some(group.version),
                    ..Default::default()
                },
            )
            .await
            .expect("seed actor-scoped Session");
    }

    let list = |view_bot_id| ListSessions {
        caller: human_principal("alice"),
        group_id: "g1".into(),
        view_bot_id,
        offset: 0,
        limit: 20,
        status: None,
    };
    let default_human = SessionService::list(&fixture.service, list(None))
        .await
        .expect("omission selects the authenticated Human");
    assert_eq!(default_human.total, 1);
    let explicit_human = SessionService::list(&fixture.service, list(Some("human_alice".into())))
        .await
        .expect("the authenticated Human is a valid explicit view");
    assert_eq!(explicit_human.total, 1);
    let owned_bot = SessionService::list(&fixture.service, list(Some("owned".into())))
        .await
        .expect("an exact-created_by Bot is a valid explicit view");
    assert_eq!(owned_bot.total, 1);

    for invalid_view in ["human_bob", "unowned", "missing"] {
        let error = SessionService::list(&fixture.service, list(Some(invalid_view.into())))
            .await
            .expect_err("an unauthorized explicit view never falls back to Human");
        assert!(matches!(
            error,
            bcs_service_api::application::v1::ApplicationError::Forbidden(_)
        ));
    }
}

#[tokio::test]
async fn session_detail_accepts_human_or_exact_owned_bot_participation_only() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "owned", "unowned"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .bots
        .save_created_by("owned", "alice", true)
        .await
        .expect("assign Alice's Bot ownership");
    fixture.store_group("g1", "driver", None).await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let mut ids = Vec::new();
    for participant in ["human_alice", "owned", "unowned"] {
        let session = fixture
            .session_repo
            .create(
                "g1",
                NewSessionParams {
                    participants: vec![if participant.starts_with("human_") {
                        Participant::human(participant, ParticipantRole::Observer)
                    } else {
                        Participant::bot(participant, ParticipantRole::Consultant)
                    }],
                    group_version: Some(group.version),
                    ..Default::default()
                },
            )
            .await
            .expect("seed Session");
        ids.push(session.id);
    }

    for session_id in &ids[..2] {
        fixture
            .service
            .get(GetSession {
                caller: human_principal("alice"),
                session_id: session_id.clone(),
            })
            .await
            .expect("Human or exact owned Bot participation grants detail read");
    }
    let error = fixture
        .service
        .get(GetSession {
            caller: human_principal("alice"),
            session_id: ids[2].clone(),
        })
        .await
        .expect_err("an unowned Bot participant grants no detail read");
    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}

#[tokio::test]
async fn session_detail_preserves_legacy_json_input_and_metadata() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture.store_group("g1", "driver", None).await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![Participant::bot("driver", ParticipantRole::Driver)],
                group_version: Some(group.version),
                input: Some(serde_json::json!(["legacy", 1])),
                meta: Some(serde_json::json!("legacy-metadata")),
                ..Default::default()
            },
        )
        .await
        .expect("seed legacy-shaped Session");

    let detail = fixture
        .service
        .get(GetSession {
            caller: bot_principal("driver"),
            session_id: session.id,
        })
        .await
        .expect("read legacy-shaped Session through V1");

    assert_eq!(detail.input, Some(serde_json::json!(["legacy", 1])));
    assert_eq!(detail.meta, Some(serde_json::json!("legacy-metadata")));
}

#[tokio::test]
async fn session_detail_propagates_owned_bot_lookup_database_failure() {
    let bots = Arc::new(BotCore::with_repo(Arc::new(
        PersistentBotRepo::with_plugins(Arc::new(InMemoryCachePlugin::new()), Arc::new(FailingDb)),
    )));
    let fixture = Fixture::new_with_bots(bots).await;
    fixture.store_group("g1", "driver", None).await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![Participant::bot("owned", ParticipantRole::Consultant)],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed Session");

    let error = fixture
        .service
        .get(GetSession {
            caller: human_principal("alice"),
            session_id: session.id,
        })
        .await
        .expect_err("owned-Bot lookup failure must not be reported as forbidden");

    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Internal(message)
            if message.contains("bot database unavailable")
    ));
}

#[tokio::test]
async fn session_v1_rejects_a_caller_without_user_identity() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture.store_group("g1", "driver", None).await;
    let error = SessionService::list(
        &fixture.service,
        ListSessions {
            caller: bot_only_caller("driver"),
            group_id: "g1".into(),
            view_bot_id: Some("driver".into()),
            offset: 0,
            limit: 20,
            status: None,
        },
    )
    .await
    .expect_err("current V1 operations require an authenticated User");
    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}

#[tokio::test]
async fn get_returns_detail_and_not_found_for_missing() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        Some("title"),
    )
    .await;

    let detail = fixture
        .service
        .get(GetSession {
            caller: bot_principal("driver"),
            session_id: outcome.session.session_id.clone(),
        })
        .await
        .expect("get session");
    assert_eq!(detail.session_id, outcome.session.session_id);
    assert_eq!(detail.title.as_deref(), Some("title"));

    let error = fixture
        .service
        .get(GetSession {
            caller: bot_principal("driver"),
            session_id: "g1:deadbeef".into(),
        })
        .await
        .expect_err("missing session should 404");
    assert_eq!(error.code(), "session_not_found");
}

#[tokio::test]
async fn update_changes_title() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        Some("old title"),
    )
    .await;

    let detail = fixture
        .service
        .update(UpdateSession {
            caller: bot_principal("driver"),
            session_id: outcome.session.session_id.clone(),
            title: Some("new title".into()),
        })
        .await
        .expect("update session");
    assert_eq!(detail.title.as_deref(), Some("new title"));
}

#[tokio::test]
async fn update_requires_a_field() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        Some("title"),
    )
    .await;

    let error = fixture
        .service
        .update(UpdateSession {
            caller: bot_principal("driver"),
            session_id: outcome.session.session_id,
            title: None,
        })
        .await
        .expect_err("empty patch should 400");
    assert_eq!(error.code(), "invalid_request");
}

#[tokio::test]
async fn delete_is_idempotent() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    let first = fixture
        .service
        .delete(DeleteSession {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            acting_bot_id: None,
        })
        .await
        .expect("first delete");
    assert!(first.deleted);

    let second = fixture
        .service
        .delete(DeleteSession {
            caller: bot_principal("driver"),
            session_id,
            acting_bot_id: None,
        })
        .await
        .expect("second delete");
    // Idempotent: a missing session yields deleted=false, not a 404.
    assert!(!second.deleted);
}

#[tokio::test]
async fn complete_is_idempotent() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    let first = fixture
        .service
        .complete(CompleteSession {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
        })
        .await
        .expect("first complete");
    assert_eq!(first.status, V1SessionStatus::Completed);
    assert!(first.completed_at > 0);

    let second = fixture
        .service
        .complete(CompleteSession {
            caller: bot_principal("driver"),
            session_id,
        })
        .await
        .expect("second complete (idempotent)");
    assert_eq!(second.status, V1SessionStatus::Completed);
    // Idempotent: same completed_at as the first completion.
    assert_eq!(second.completed_at, first.completed_at);
}

#[tokio::test]
async fn human_collects_and_uncollects_for_owned_participant_bot_idempotently() {
    let fixture = Fixture::new().await;
    fixture.add_bot("bot-1").await;
    fixture
        .bots
        .save_created_by("bot-1", "owner-1", true)
        .await
        .expect("assign Bot ownership");
    fixture
        .store_group_with_originator("g1", "bot-1", "human_owner-1", None)
        .await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![Participant::bot("bot-1", ParticipantRole::Driver)],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed Session");

    for _ in 0..2 {
        let result = fixture
            .service
            .collect(CollectSession {
                caller: human_principal("owner-1"),
                session_id: session.id.clone(),
                participant: "bot-1".into(),
            })
            .await
            .expect("owned participant Bot can collect");
        assert_eq!(result.session_id, session.id);
        assert_eq!(result.participant, "bot-1");
        assert!(result.collected);
    }
    let collected = fixture
        .session_repo
        .collected_at_map(&[session.id.as_str()], "bot-1")
        .await;
    assert_eq!(collected.len(), 1);
    assert_eq!(collected[0].0, session.id);

    for _ in 0..2 {
        let result = fixture
            .service
            .uncollect(UncollectSession {
                caller: human_principal("owner-1"),
                session_id: session.id.clone(),
                participant: "bot-1".into(),
            })
            .await
            .expect("owned participant Bot can uncollect idempotently");
        assert_eq!(result.participant, "bot-1");
        assert!(!result.collected);
    }
    assert!(
        fixture
            .session_repo
            .collected_at_map(&[session.id.as_str()], "bot-1")
            .await
            .is_empty()
    );
}

#[tokio::test]
async fn human_collects_as_own_human_actor_idempotently() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    // Register the human's own actor entry (`human_owner-1`) the way
    // `ensure_human_actor` does: actor_kind = Human, created_by = the staff_no.
    fixture
        .bots
        .ensure_human_actor("owner-1", "Owner")
        .await
        .expect("provision human actor");
    fixture
        .store_group_with_originator("g1", "driver", "human_owner-1", None)
        .await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::human("human_owner-1", ParticipantRole::Observer),
                ],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed Session");

    // Collect as the human's own actor id (participant = "human_owner-1").
    for _ in 0..2 {
        let result = fixture
            .service
            .collect(CollectSession {
                caller: human_principal("owner-1"),
                session_id: session.id.clone(),
                participant: "human_owner-1".into(),
            })
            .await
            .expect("human can collect as its own actor");
        assert_eq!(result.session_id, session.id);
        assert_eq!(result.participant, "human_owner-1");
        assert!(result.collected);
    }
    let collected = fixture
        .session_repo
        .collected_at_map(&[session.id.as_str()], "human_owner-1")
        .await;
    assert_eq!(collected.len(), 1);
    assert_eq!(collected[0].0, session.id);

    // Uncollecting as the human actor is idempotent.
    for _ in 0..2 {
        let result = fixture
            .service
            .uncollect(UncollectSession {
                caller: human_principal("owner-1"),
                session_id: session.id.clone(),
                participant: "human_owner-1".into(),
            })
            .await
            .expect("human can uncollect as its own actor");
        assert_eq!(result.participant, "human_owner-1");
        assert!(!result.collected);
    }
    assert!(
        fixture
            .session_repo
            .collected_at_map(&[session.id.as_str()], "human_owner-1")
            .await
            .is_empty()
    );
}

#[tokio::test]
async fn collect_rejects_participant_not_owned_by_authenticated_human() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture
        .bots
        .ensure_human_actor("owner-1", "Owner")
        .await
        .expect("provision authenticated human actor");
    fixture
        .bots
        .ensure_human_actor("other", "Other")
        .await
        .expect("provision a different human actor");
    fixture
        .store_group_with_originator("g1", "driver", "human_owner-1", None)
        .await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::human("human_owner-1", ParticipantRole::Observer),
                    Participant::human("human_other", ParticipantRole::Observer),
                ],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed Session");

    let error = fixture
        .service
        .collect(CollectSession {
            caller: human_principal("owner-1"),
            session_id: session.id.clone(),
            // `human_other` is registered, but not owned by the authenticated
            // human (`owner-1`); collecting on its behalf must be forbidden.
            participant: "human_other".into(),
        })
        .await
        .expect_err("must not collect as an actor the human does not own");
    assert!(
        matches!(error, bcs_service_api::application::v1::ApplicationError::Forbidden(_)),
        "expected forbidden, got {error:?}"
    );
}

#[tokio::test]
async fn list_surfaces_per_session_collected_for_explicit_view_actor() {
    let fixture = Fixture::new().await;
    fixture.add_bot("bot-1").await;
    fixture
        .bots
        .save_created_by("bot-1", "owner-1", true)
        .await
        .expect("assign Bot ownership");
    fixture
        .store_group_with_originator("g1", "bot-1", "human_owner-1", None)
        .await;
    let group = fixture.groups.get("g1").await.expect("group exists");

    // Seed two sessions both listing bot-1 as a participant.
    let mk = || Participant::bot("bot-1", ParticipantRole::Driver);
    let s_a = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![mk()],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed session A");
    let s_b = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![mk()],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed session B");

    // Collect only session A as the owned participant bot.
    fixture
        .service
        .collect(CollectSession {
            caller: human_principal("owner-1"),
            session_id: s_a.id.clone(),
            participant: "bot-1".into(),
        })
        .await
        .expect("collect session A");

    let page = SessionService::list(
        &fixture.service,
        ListSessions {
            caller: human_principal("owner-1"),
            group_id: "g1".into(),
            view_bot_id: Some("bot-1".into()),
            offset: 0,
            limit: 10,
            status: None,
        },
    )
    .await
    .expect("list sessions");

    let by_id = page
        .items
        .iter()
        .map(|s| (s.session_id.as_str(), s.collected))
        .collect::<std::collections::HashMap<&str, Option<bool>>>();
    // The explicitly named view actor sees its per-session collected state.
    assert_eq!(by_id.get(s_a.id.as_str()), Some(&Some(true)));
    assert_eq!(by_id.get(s_b.id.as_str()), Some(&Some(false)));

    // When no view actor is named the field is left absent (None). Here the
    // human views as themselves, so the bot-participant sessions are filtered
    // out and the page is empty — confirming collected is not synthesized.
    let none_page = SessionService::list(
        &fixture.service,
        ListSessions {
            caller: human_principal("owner-1"),
            group_id: "g1".into(),
            view_bot_id: None,
            offset: 0,
            limit: 10,
            status: None,
        },
    )
    .await
    .expect("list sessions (no view actor)");
    assert!(
        none_page.items.iter().all(|s| s.collected.is_none()),
        "collected must be absent when view_bot_id is not specified"
    );
}

#[tokio::test]
async fn session_collection_rejects_an_unowned_bot() {
    let fixture = Fixture::new().await;
    fixture.add_bot("bot-1").await;
    fixture
        .bots
        .save_created_by("bot-1", "owner-1", true)
        .await
        .expect("assign Bot ownership");

    let error = fixture
        .service
        .collect(CollectSession {
            caller: human_principal("owner-2"),
            session_id: "session-1".into(),
            participant: "bot-1".into(),
        })
        .await
        .expect_err("another Human cannot collect for this Bot");
    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}

#[tokio::test]
async fn session_collection_hides_an_owned_bot_membership_miss() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "bot-1"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .bots
        .save_created_by("bot-1", "owner-1", true)
        .await
        .expect("assign Bot ownership");
    fixture.store_group("g1", "driver", None).await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![Participant::bot("driver", ParticipantRole::Driver)],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed Session");

    let error = fixture
        .service
        .collect(CollectSession {
            caller: human_principal("owner-1"),
            session_id: session.id,
            participant: "bot-1".into(),
        })
        .await
        .expect_err("a non-participant is hidden as a missing Session");
    assert_eq!(error.code(), "session_not_found");
}

#[tokio::test]
async fn session_collection_returns_not_found_for_a_missing_session() {
    let fixture = Fixture::new().await;
    fixture.add_bot("bot-1").await;
    fixture
        .bots
        .save_created_by("bot-1", "owner-1", true)
        .await
        .expect("assign Bot ownership");

    let error = fixture
        .service
        .collect(CollectSession {
            caller: human_principal("owner-1"),
            session_id: "missing".into(),
            participant: "bot-1".into(),
        })
        .await
        .expect_err("missing Session is rejected");
    assert_eq!(error.code(), "session_not_found");
}

#[tokio::test]
async fn session_collection_accepts_only_a_human_identity() {
    let fixture = Fixture::new().await;
    fixture.add_bot("bot-1").await;
    fixture
        .bots
        .save_created_by("bot-1", "owner-1", true)
        .await
        .expect("assign Bot ownership");

    for caller in [bot_only_caller("bot-1"), app_only_caller()] {
        let error = fixture
            .service
            .collect(CollectSession {
                caller,
                session_id: "session-1".into(),
                participant: "bot-1".into(),
            })
            .await
            .expect_err("Bot-only and App-only callers are rejected");
        assert!(matches!(
            error,
            bcs_service_api::application::v1::ApplicationError::Forbidden(_)
        ));
    }
}

#[tokio::test]
async fn list_messages_delegates_and_returns_legacy_group_messages_unchanged() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture.store_group("g1", "driver", None).await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::human("human_staff-1", ParticipantRole::Observer),
                ],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");
    let expected = rich_group_message();
    *fixture.history.messages.lock().expect("messages lock") = vec![expected.clone()];

    let messages = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            caller: human_principal("staff-1"),
            session_id: session.id.clone(),
            before: Some(1_786_590_000_000),
            limit: 25,
            view_bot_id: None,
        },
    )
    .await
    .expect("list messages");

    assert_eq!(
        serde_json::to_value(&messages).expect("serialize messages"),
        serde_json::to_value([expected]).expect("serialize expected")
    );
    let calls = fixture.history.session_calls.lock().expect("history lock");
    let call = calls.last().expect("history call");
    assert_eq!(call.group_id, "g1");
    assert_eq!(call.session_id, session.id);
    assert_eq!(call.view_bot_id.as_deref(), Some("human_staff-1"));
    assert_eq!(call.limit, 25);
    assert_eq!(call.before, Some(1_786_590_000_000));
    assert_eq!(
        call.caller,
        CallerContext::Human(HumanActor {
            actor_id: "human_staff-1".into(),
            staff_no: "staff-1".into(),
        })
    );
}

#[tokio::test]
async fn list_messages_rejects_invalid_limit_before_calling_history() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        Vec::new(),
        None,
        None,
    )
    .await;
    let error = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            caller: bot_principal("driver"),
            session_id: outcome.session.session_id,
            before: None,
            limit: 0,
            view_bot_id: Some("driver".into()),
        },
    )
    .await
    .expect_err("zero limit should be invalid");
    assert_eq!(error.code(), "invalid_request");
    assert!(
        fixture
            .history
            .session_calls
            .lock()
            .expect("history lock")
            .is_empty()
    );
}

#[tokio::test]
async fn state_machine_session_history_uses_runtime_and_returns_messages_unchanged() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture.store_state_machine_group("g1", "driver").await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::human("human_staff-1", ParticipantRole::Observer),
                ],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed state-machine session");
    let expected = rich_group_message();
    *fixture
        .runtime
        .history_result
        .lock()
        .expect("runtime result lock") = Some(SessionHistoryResult {
        session_id: session.id.clone(),
        messages: vec![expected.clone()],
        limit: 20,
        before: Some(1_786_590_000_000),
        next_before: None,
    });

    let messages = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            caller: human_principal("staff-1"),
            session_id: session.id.clone(),
            before: Some(1_786_590_000_000),
            limit: 20,
            view_bot_id: None,
        },
    )
    .await
    .expect("state-machine history");

    assert_eq!(
        serde_json::to_value(messages).expect("serialize messages"),
        serde_json::to_value([expected]).expect("serialize expected")
    );
    assert_eq!(
        fixture
            .runtime
            .history_calls
            .lock()
            .expect("runtime history lock")
            .as_slice(),
        &[(session.id, 20, Some(1_786_590_000_000))]
    );
    assert!(
        fixture
            .history
            .session_calls
            .lock()
            .expect("history lock")
            .is_empty()
    );
}

#[tokio::test]
async fn human_owner_of_group_driver_can_add_session_participant_without_human_membership() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert", "newcomer"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .bots
        .save_created_by("driver", "staff-driver", true)
        .await
        .expect("assign driver owner");
    fixture
        .store_group_with_originator("g1", "driver", "human_other", None)
        .await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::bot("expert", ParticipantRole::Consultant),
                ],
                group_version: Some(group.version),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");

    let err = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: human_principal("staff-unrelated"),
            session_id: session.id.clone(),
            bot_uuid: "newcomer".into(),
        })
        .await
        .expect_err("unrelated Human cannot manage session through Bot ownership");
    assert!(matches!(
        err,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));

    let added = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: human_principal("staff-driver"),
            session_id: session.id,
            bot_uuid: "newcomer".into(),
        })
        .await
        .expect("Human owner of group driver can manage session");

    assert_eq!(added.actor_id, "newcomer");
}

#[tokio::test]
async fn human_owner_of_session_creator_can_update_session_without_group_management() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert", "creator-bot"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .bots
        .save_created_by("creator-bot", "staff-creator", true)
        .await
        .expect("assign creator owner");
    fixture
        .store_group_with_originator("g1", "driver", "human_other", None)
        .await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![Participant::bot("expert", ParticipantRole::Consultant)],
                group_version: Some(group.version),
                created_by: Some("creator-bot".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");

    let err = fixture
        .service
        .update(UpdateSession {
            caller: human_principal("staff-unrelated"),
            session_id: session.id.clone(),
            title: Some("unrelated update".into()),
        })
        .await
        .expect_err("unrelated Human cannot manage session through creator ownership");
    assert!(matches!(
        err,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));

    let detail = fixture
        .service
        .update(UpdateSession {
            caller: human_principal("staff-creator"),
            session_id: session.id,
            title: Some("owned creator update".into()),
        })
        .await
        .expect("Human owner of session creator can manage session");

    assert_eq!(detail.title.as_deref(), Some("owned creator update"));
}

#[tokio::test]
async fn chat_manager_role_does_not_grant_session_management_to_human_owner() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert", "manager", "newcomer"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .bots
        .save_created_by("manager", "staff-manager-owner", true)
        .await
        .expect("assign manager owner");
    fixture
        .store_group_with_originator("g1", "driver", "human_other", None)
        .await;
    let mut group = fixture.groups.get("g1").await.expect("group exists");
    group
        .participants
        .push(Participant::bot("manager", ParticipantRole::Manager));
    fixture
        .groups
        .upsert(group.clone())
        .await
        .expect("store group");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::bot("expert", ParticipantRole::Consultant),
                    Participant::bot("manager", ParticipantRole::Manager),
                ],
                group_version: Some(group.version),
                created_by: Some("expert".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");

    let err = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: human_principal("staff-manager-owner"),
            session_id: session.id,
            bot_uuid: "newcomer".into(),
        })
        .await
        .expect_err("Chat manager role must not grant session management authority");
    assert!(matches!(
        err,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}

#[tokio::test]
async fn participant_add_update_remove_lifecycle() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert", "newcomer"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    // Add.
    let added = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "newcomer".into(),
        })
        .await
        .expect("add participant");
    assert_eq!(added.actor_id, "newcomer");
    assert_eq!(added.role, ParticipantRole::Consultant);
    assert_eq!(added.mode, ParticipantMode::Auto);

    // Adding twice is a conflict.
    let error = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "newcomer".into(),
        })
        .await
        .expect_err("duplicate add should conflict");
    assert_eq!(error.code(), "participant_already_exists");

    // Update mode.
    let updated = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "newcomer".into(),
            mode: ParticipantMode::Muted,
        })
        .await
        .expect("update participant");
    assert_eq!(updated.mode, ParticipantMode::Muted);

    // Remove.
    let removed = fixture
        .service
        .delete_participant(DeleteSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "newcomer".into(),
        })
        .await
        .expect("delete participant");
    assert!(removed.deleted);

    // Remove again is idempotent.
    let again = fixture
        .service
        .delete_participant(DeleteSessionParticipant {
            caller: bot_principal("driver"),
            session_id,
            bot_uuid: "newcomer".into(),
        })
        .await
        .expect("idempotent delete participant");
    assert!(!again.deleted);
}

#[tokio::test]
async fn update_participant_emits_mode_changed_system_message_only_on_change() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    // Add expert (defaults to Auto) so its mode can be updated.
    let added = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "expert".into(),
        })
        .await
        .expect("add expert");
    assert_eq!(added.mode, ParticipantMode::Auto);

    // Changing expert Auto -> Muted must emit ParticipantModeChanged.
    fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "expert".into(),
            mode: ParticipantMode::Muted,
        })
        .await
        .expect("mute expert");

    let mode_changes = recorded_mode_changes(&fixture, &session_id);
    assert_eq!(mode_changes.len(), 1, "exactly one mode-change event");
    assert_eq!(mode_changes[0].0, "expert");
    assert_eq!(mode_changes[0].1, Some(ParticipantMode::Auto));
    assert_eq!(mode_changes[0].2, ParticipantMode::Muted);

    // Re-applying the same mode is a no-op and must NOT emit another event.
    fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "expert".into(),
            mode: ParticipantMode::Muted,
        })
        .await
        .expect("re-mute expert is idempotent");

    let mode_changes = recorded_mode_changes(&fixture, &session_id);
    assert_eq!(
        mode_changes.len(),
        1,
        "no new event when the mode is unchanged"
    );
}

#[tokio::test]
async fn add_participant_emits_bot_joined_system_message() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    // Adding a participant must emit a BotJoined notification (parity with the
    // legacy `add_session_participant` route).
    fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "expert".into(),
        })
        .await
        .expect("add expert");

    let joined = recorded_bot_joined(&fixture, &session_id);
    assert_eq!(joined.len(), 1, "exactly one BotJoined event");
    assert_eq!(joined[0], "expert");

    // A duplicate add is a conflict and must NOT emit another event.
    let error = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "expert".into(),
        })
        .await
        .expect_err("duplicate add should conflict");
    assert_eq!(error.code(), "participant_already_exists");
    assert_eq!(
        recorded_bot_joined(&fixture, &session_id).len(),
        1,
        "no new event on duplicate add"
    );
}

#[tokio::test]
async fn delete_participant_emits_bot_left_system_message() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();
    fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "expert".into(),
        })
        .await
        .expect("add expert");

    // Removing a participant must emit a BotLeft notification (parity with the
    // legacy `remove_session_participant` route).
    let removed = fixture
        .service
        .delete_participant(DeleteSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "expert".into(),
        })
        .await
        .expect("delete expert");
    assert!(removed.deleted);

    let left = recorded_bot_left(&fixture, &session_id);
    assert_eq!(left.len(), 1, "exactly one BotLeft event");
    assert_eq!(left[0], "expert");

    // Removing an already-absent participant is idempotent and must NOT emit
    // another event.
    let again = fixture
        .service
        .delete_participant(DeleteSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "expert".into(),
        })
        .await
        .expect("idempotent delete");
    assert!(!again.deleted);
    assert_eq!(
        recorded_bot_left(&fixture, &session_id).len(),
        1,
        "no new event on idempotent delete"
    );
}

#[tokio::test]
async fn delete_participant_allows_removing_group_originator() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    // Group whose originator is the human `human_owner-1` (distinct from the
    // driver bot). The originator is a session participant and must be allowed
    // to leave the session — only the driver/manager are structurally pinned.
    fixture
        .store_group_with_originator("g1", "driver", "human_owner-1", None)
        .await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::human("human_owner-1", ParticipantRole::Observer),
                ],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed Session");

    let removed = fixture
        .service
        .delete_participant(DeleteSessionParticipant {
            caller: human_principal("owner-1"),
            session_id: session.id.clone(),
            bot_uuid: "human_owner-1".into(),
        })
        .await
        .expect("originator can leave the session");
    assert!(removed.deleted);
    let left = recorded_bot_left(&fixture, &session.id);
    assert_eq!(left.len(), 1);
    assert_eq!(left[0], "human_owner-1");
}

#[tokio::test]
async fn delete_participant_still_rejects_group_driver() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture
        .store_group_with_originator("g1", "driver", "human_owner-1", None)
        .await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::human("human_owner-1", ParticipantRole::Observer),
                ],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed Session");

    let error = fixture
        .service
        .delete_participant(DeleteSessionParticipant {
            caller: human_principal("owner-1"),
            session_id: session.id.clone(),
            bot_uuid: "driver".into(),
        })
        .await
        .expect_err("driver remains pinned");
    assert!(
        matches!(error, bcs_service_api::application::v1::ApplicationError::InvalidInput { .. }),
        "expected invalid_request, got {error:?}"
    );
    // No leave event when removal is rejected.
    assert!(recorded_bot_left(&fixture, &session.id).is_empty());
}

/// Collect `ParticipantModeChanged` events recorded for `session_id`.
fn recorded_mode_changes(
    fixture: &Fixture,
    session_id: &str,
) -> Vec<(String, Option<ParticipantMode>, ParticipantMode)> {
    fixture
        .system_messages
        .events
        .lock()
        .expect("sysmsg lock")
        .iter()
        .filter(|recorded| recorded.session_id == session_id)
        .filter_map(|recorded| {
            if let SystemMessageEvent::ParticipantModeChanged {
                actor_id,
                from,
                to,
                ..
            } = &recorded.event
            {
                Some((actor_id.clone(), *from, *to))
            } else {
                None
            }
        })
        .collect()
}

/// Collect `BotJoined` actor ids recorded for `session_id`.
fn recorded_bot_joined(fixture: &Fixture, session_id: &str) -> Vec<String> {
    fixture
        .system_messages
        .events
        .lock()
        .expect("sysmsg lock")
        .iter()
        .filter(|recorded| recorded.session_id == session_id)
        .filter_map(|recorded| match &recorded.event {
            SystemMessageEvent::BotJoined { actor, .. } => Some(actor.bot_uuid.clone()),
            _ => None,
        })
        .collect()
}

/// Collect `BotLeft` actor ids recorded for `session_id`.
fn recorded_bot_left(fixture: &Fixture, session_id: &str) -> Vec<String> {
    fixture
        .system_messages
        .events
        .lock()
        .expect("sysmsg lock")
        .iter()
        .filter(|recorded| recorded.session_id == session_id)
        .filter_map(|recorded| match &recorded.event {
            SystemMessageEvent::BotLeft { actor, .. } => Some(actor.bot_uuid.clone()),
            _ => None,
        })
        .collect()
}

#[tokio::test]
async fn human_participant_can_update_own_mode_without_session_management() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture
        .store_group_with_originator("g1", "driver", "human_other", None)
        .await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::human("human_staff-1", ParticipantRole::Observer),
                ],
                group_version: Some(group.version),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");

    let updated = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("staff-1"),
            session_id: session.id,
            bot_uuid: "human_staff-1".into(),
            mode: ParticipantMode::Present,
        })
        .await
        .expect("Human participant updates own presence");

    assert_eq!(updated.actor_kind, ActorKind::Human);
    assert_eq!(updated.role, ParticipantRole::Observer);
    assert_eq!(updated.mode, ParticipantMode::Present);
}

#[tokio::test]
async fn readable_session_auto_adds_missing_human_as_present_observer() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "owned-bot"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .bots
        .save_created_by("owned-bot", "staff-1", true)
        .await
        .expect("assign owned Bot");
    fixture
        .store_group_with_originator("g1", "driver", "human_other", None)
        .await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::bot("owned-bot", ParticipantRole::Consultant),
                ],
                group_version: Some(group.version),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");
    let session_id = session.id.clone();

    let inserted = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("staff-1"),
            session_id: session.id,
            bot_uuid: "human_staff-1".into(),
            mode: ParticipantMode::Present,
        })
        .await
        .expect("missing Human self-inserts into readable Session");

    assert_eq!(inserted.actor_kind, ActorKind::Human);
    assert_eq!(inserted.role, ParticipantRole::Observer);
    assert_eq!(inserted.mode, ParticipantMode::Present);
    let stored = fixture
        .session_repo
        .get(&session_id)
        .await
        .expect("stored session");
    let matching = stored
        .participants
        .iter()
        .filter(|participant| participant.bot_uuid == "human_staff-1")
        .collect::<Vec<_>>();
    assert_eq!(matching.len(), 1);
    assert_eq!(matching[0].role, ParticipantRole::Observer);
    assert_eq!(matching[0].mode, Some(ParticipantMode::Present));
}

#[tokio::test]
async fn readable_session_auto_adds_missing_human_as_absent_observer() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "owned-bot"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .bots
        .save_created_by("owned-bot", "staff-1", true)
        .await
        .expect("assign owned Bot");
    fixture
        .store_group_with_originator("g1", "driver", "human_other", None)
        .await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::bot("owned-bot", ParticipantRole::Consultant),
                ],
                group_version: Some(group.version),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");
    let session_id = session.id.clone();

    let inserted = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("staff-1"),
            session_id: session.id,
            bot_uuid: "human_staff-1".into(),
            mode: ParticipantMode::Absent,
        })
        .await
        .expect("missing Human self-inserts as absent");

    assert_eq!(inserted.actor_kind, ActorKind::Human);
    assert_eq!(inserted.role, ParticipantRole::Observer);
    assert_eq!(inserted.mode, ParticipantMode::Absent);
    let stored = fixture
        .session_repo
        .get(&session_id)
        .await
        .expect("stored session");
    let matching = stored
        .participants
        .iter()
        .filter(|participant| participant.bot_uuid == "human_staff-1")
        .collect::<Vec<_>>();
    assert_eq!(matching.len(), 1);
    assert_eq!(matching[0].role, ParticipantRole::Observer);
    assert_eq!(matching[0].mode, Some(ParticipantMode::Absent));
}

#[tokio::test]
async fn session_manager_cannot_update_another_human_mode() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture
        .bots
        .save_created_by("driver", "staff-manager", true)
        .await
        .expect("assign driver owner");
    fixture
        .store_group_with_originator("g1", "driver", "human_other", None)
        .await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::human("human_target", ParticipantRole::Observer),
                ],
                group_version: Some(group.version),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");

    let error = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("staff-manager"),
            session_id: session.id,
            bot_uuid: "human_target".into(),
            mode: ParticipantMode::Present,
        })
        .await
        .expect_err("Session manager cannot control another Human's presence");

    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}

#[tokio::test]
async fn session_manager_cannot_auto_add_another_human() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture
        .bots
        .save_created_by("driver", "staff-manager", true)
        .await
        .expect("assign driver owner");
    fixture
        .store_group_with_originator("g1", "driver", "human_other", None)
        .await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![Participant::bot("driver", ParticipantRole::Driver)],
                group_version: Some(group.version),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");
    let session_id = session.id.clone();

    let error = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("staff-manager"),
            session_id: session.id,
            bot_uuid: "human_target".into(),
            mode: ParticipantMode::Present,
        })
        .await
        .expect_err("Session manager cannot auto-add another Human");
    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));

    let stored = fixture
        .session_repo
        .get(&session_id)
        .await
        .expect("stored session");
    assert!(
        stored
            .participants
            .iter()
            .all(|participant| participant.bot_uuid != "human_target")
    );
}

#[tokio::test]
async fn human_cannot_auto_join_an_unreadable_session() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture
        .store_group_with_originator("g1", "driver", "human_other", None)
        .await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![Participant::bot("driver", ParticipantRole::Driver)],
                group_version: Some(group.version),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");

    let error = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("staff-1"),
            session_id: session.id,
            bot_uuid: "human_staff-1".into(),
            mode: ParticipantMode::Present,
        })
        .await
        .expect_err("Human cannot join a Session it cannot read");

    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}

#[tokio::test]
async fn participant_mode_must_match_the_target_actor_kind() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture.store_group("g1", "driver", None).await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::human("human_staff-1", ParticipantRole::Observer),
                ],
                group_version: Some(group.version),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");

    let human_error = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("staff-1"),
            session_id: session.id.clone(),
            bot_uuid: "human_staff-1".into(),
            mode: ParticipantMode::Auto,
        })
        .await
        .expect_err("Human rejects Bot-only mode");
    assert_eq!(human_error.code(), "invalid_participant_mode");

    let bot_error = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("driver"),
            session_id: session.id,
            bot_uuid: "driver".into(),
            mode: ParticipantMode::Present,
        })
        .await
        .expect_err("Bot rejects Human-only mode");
    assert_eq!(bot_error.code(), "invalid_participant_mode");
}

#[tokio::test]
async fn bot_mode_does_not_auto_add_a_missing_human() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "owned-bot"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .bots
        .save_created_by("owned-bot", "staff-1", true)
        .await
        .expect("assign owned Bot");
    fixture
        .store_group_with_originator("g1", "driver", "human_other", None)
        .await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::bot("owned-bot", ParticipantRole::Consultant),
                ],
                group_version: Some(group.version),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");
    let session_id = session.id.clone();

    let error = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("staff-1"),
            session_id: session.id,
            bot_uuid: "human_staff-1".into(),
            mode: ParticipantMode::Auto,
        })
        .await
        .expect_err("Bot-only mode cannot auto-add a Human");
    assert_eq!(error.code(), "invalid_participant_mode");

    let stored = fixture
        .session_repo
        .get(&session_id)
        .await
        .expect("stored session");
    assert!(
        stored
            .participants
            .iter()
            .all(|participant| participant.bot_uuid != "human_staff-1")
    );
}

#[tokio::test]
async fn update_mode_does_not_auto_add_a_missing_bot() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture.store_group("g1", "driver", None).await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![Participant::bot("driver", ParticipantRole::Driver)],
                group_version: Some(group.version),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");
    let session_id = session.id.clone();

    let error = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("driver"),
            session_id: session.id,
            bot_uuid: "missing-bot".into(),
            mode: ParticipantMode::Auto,
        })
        .await
        .expect_err("missing Bot is not auto-added");
    assert_eq!(error.code(), "participant_not_found");

    let stored = fixture
        .session_repo
        .get(&session_id)
        .await
        .expect("stored session");
    assert!(
        stored
            .participants
            .iter()
            .all(|participant| participant.bot_uuid != "missing-bot")
    );
}

#[tokio::test]
async fn owned_bot_does_not_grant_session_participant_removal_permission() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "bot-a"] {
        fixture.add_bot(bot).await;
    }
    // bot-a is owned by Human staff-1 via created_by.
    fixture
        .bots
        .save_created_by("bot-a", "staff-1", true)
        .await
        .expect("save owner");
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("bot-a", None)],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    let owner_error = fixture
        .service
        .delete_participant(DeleteSessionParticipant {
            caller: human_principal("staff-1"),
            session_id: session_id.clone(),
            bot_uuid: "bot-a".into(),
        })
        .await
        .expect_err("Bot ownership grants detail read only, not management");
    assert!(matches!(
        owner_error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));

    // Non-owner Human (staff-2) is forbidden — neither self, owner, nor
    // manager/creator.
    let error = fixture
        .service
        .delete_participant(DeleteSessionParticipant {
            caller: human_principal("staff-2"),
            session_id,
            bot_uuid: "bot-a".into(),
        })
        .await
        .expect_err("non-owner human should be forbidden");
    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}

#[tokio::test]
async fn owned_bot_does_not_grant_session_participant_update_permission() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "bot-a"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .bots
        .save_created_by("bot-a", "staff-1", true)
        .await
        .expect("save owner");
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("bot-a", None)],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    let owner_error = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("staff-1"),
            session_id: session_id.clone(),
            bot_uuid: "bot-a".into(),
            mode: ParticipantMode::Muted,
        })
        .await
        .expect_err("Bot ownership grants detail read only, not management");
    assert!(matches!(
        owner_error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));

    // Non-owner Human is forbidden.
    let error = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("staff-2"),
            session_id,
            bot_uuid: "bot-a".into(),
            mode: ParticipantMode::Auto,
        })
        .await
        .expect_err("non-owner human should be forbidden");
    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}

#[tokio::test]
async fn complete_service_invocation_session_rejected() {
    // VaGQN: ServiceInvocation sessions have their own callback/output
    // lifecycle and must not be completed via this V1 endpoint (legacy
    // handler rejects "service sessions cannot be completed via this
    // endpoint"). Gate the CAS with a session_kind check.
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;

    // The V1 facade `create` hardcodes SessionKind::Chat, so seed a
    // ServiceInvocation session directly via the repo.
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                session_kind: SessionKind::ServiceInvocation,
                participants: vec![Participant::bot("driver", ParticipantRole::Driver)],
                group_version: Some(group.version),
                caller_id: Some("driver".to_string()),
                caller_principal: Some("driver".to_string()),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed service invocation session");
    assert_eq!(session.session_kind, SessionKind::ServiceInvocation);

    let error = fixture
        .service
        .complete(CompleteSession {
            caller: bot_principal("driver"),
            session_id: session.id,
        })
        .await
        .expect_err("service sessions cannot be completed via V1");

    assert!(
        matches!(
            error,
            bcs_service_api::application::v1::ApplicationError::Conflict { .. }
        ),
        "expected Conflict, got {error:?}",
    );
    assert_eq!(error.code(), "conflict");
}

#[tokio::test]
async fn session_only_participant_can_list_sessions() {
    // VaGQQ: a Bot that is only a session participant (added to a session
    // but NOT to group.participants) must still be able to list the group's
    // sessions. The sibling bcs-app-group facade already permits this via
    // `list_group_ids_by_session_participant`; the session-v1 facade's
    // `can_read_group` must mirror that check.
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert", "newcomer"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    // Add newcomer to the SESSION (not the group). newcomer is public so the
    // collaboration-eligibility check passes; the driver is the manager.
    fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "newcomer".into(),
        })
        .await
        .expect("add newcomer to session");

    // Guard: newcomer must be session-only (not in group.participants).
    let group = fixture.groups.get("g1").await.expect("group exists");
    assert!(
        !group.participants.iter().any(|p| p.bot_uuid == "newcomer"),
        "newcomer must be session-only (not in group.participants)"
    );

    // Session-only participant may list the group's sessions.
    let page = SessionService::list(
        &fixture.service,
        ListSessions {
            caller: bot_principal("newcomer"),
            group_id: "g1".into(),
            view_bot_id: Some("newcomer".into()),
            offset: 0,
            limit: 10,
            status: None,
        },
    )
    .await
    .expect("session-only participant may list sessions");
    assert_eq!(page.total, 1);
    assert_eq!(page.items.len(), 1);
    assert_eq!(page.items[0].group_id, "g1");
}

#[tokio::test]
async fn session_only_participant_list_sessions_scoped() {
    // Vcj5: a session-only Bot (in `session.participants` but NOT in
    // `group.participants`) must see ONLY the sessions it participates in
    // when calling `list_sessions` — not the entire group session pool. The
    // prior VaGQQ fix let a session-only Bot pass `can_read_group`, but the
    // list + count calls still passed `participant_id=None`, surfacing ALL
    // group sessions. The V1 facade now scopes both `list_by_group` and
    // `count_by_group` to `Some(principal.actor_id())` when access derives
    // solely from session membership.
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert", "expert2", "expert3", "newcomer"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;

    // S1: driver + expert. Add newcomer to S1 only (session-only for
    // newcomer — newcomer is NOT a group.participants member).
    let s1 = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        Some("S1"),
    )
    .await;
    let s1_id = s1.session.session_id.clone();
    fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: bot_principal("driver"),
            session_id: s1_id.clone(),
            bot_uuid: "newcomer".into(),
        })
        .await
        .expect("add newcomer to S1");

    // S2 + S3: driver + a different expert; newcomer is NOT a participant.
    let s2 = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert2", None)],
        None,
        Some("S2"),
    )
    .await;
    let s3 = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert3", None)],
        None,
        Some("S3"),
    )
    .await;

    // Guard: newcomer is session-only (in S1.participants, NOT in
    // group.participants).
    let group = fixture.groups.get("g1").await.expect("group exists");
    assert!(
        !group.participants.iter().any(|p| p.bot_uuid == "newcomer"),
        "newcomer must be session-only (not in group.participants)"
    );

    // Session-only Bot lists g1 sessions → must see ONLY S1, not S2 / S3.
    let page = SessionService::list(
        &fixture.service,
        ListSessions {
            caller: bot_principal("newcomer"),
            group_id: "g1".into(),
            view_bot_id: Some("newcomer".into()),
            offset: 0,
            limit: 10,
            status: None,
        },
    )
    .await
    .expect("session-only participant lists scoped sessions");
    assert_eq!(page.total, 1, "total must reflect scoping (only S1)");
    assert_eq!(page.items.len(), 1, "items must contain only S1");
    assert_eq!(page.items[0].session_id, s1_id);
    assert_ne!(page.items[0].session_id, s2.session.session_id);
    assert_ne!(page.items[0].session_id, s3.session.session_id);
}

#[tokio::test]
async fn create_session_inherits_parent_group_participants_without_request_roster() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["expert"],
            "human_driver",
            None,
        )
        .await;
    let mut group = fixture.groups.get("g1").await.expect("group exists");
    group.participants.push(Participant::human(
        "human_driver",
        ParticipantRole::Observer,
    ));
    fixture
        .groups
        .upsert(group)
        .await
        .expect("store Human participant");

    let outcome = fixture
        .service
        .create(CreateSession {
            caller: launch_caller(bot_principal("driver")),
            group_id: "g1".into(),
            title: None,
            kind: None,
            acting_bot_id: None,
            creator_role: None,
            input: None,
            meta: None,
            context_delivery: None,
        })
        .await
        .expect("session should inherit parent group roster");

    assert!(
        outcome
            .session
            .participants
            .iter()
            .any(|p| p.actor_id == "expert")
    );
    let inherited_human = outcome
        .session
        .participants
        .iter()
        .find(|p| p.actor_id == "human_driver")
        .expect("Human Group participant should be inherited into Session");
    assert_eq!(inherited_human.actor_kind, ActorKind::Human);
    assert!(
        outcome
            .session
            .participants
            .iter()
            .any(|p| p.actor_id == "driver" && p.role == ParticipantRole::Driver)
    );
}

/// Build the ManagerWorker session used by every view_bot_id authorization
/// test. Message visibility itself belongs to the injected history service;
/// these facade tests only verify the selected actor passed to that service.
async fn setup_manager_worker_session(fixture: &Fixture) -> String {
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::bot("worker-a", ParticipantRole::Worker),
                    Participant::bot("worker-b", ParticipantRole::Worker),
                    Participant::human("human_staff-1", ParticipantRole::Observer),
                ],
                group_version: Some(group.version),
                caller_id: Some("driver".to_string()),
                caller_principal: Some("driver".to_string()),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed manager-worker session");
    session.id
}

#[tokio::test]
async fn omitted_message_view_selects_human_and_requires_session_participation() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker-a", "worker-b"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker-a", "worker-b"],
            "driver",
            None,
        )
        .await;
    let session_id = setup_manager_worker_session(&fixture).await;

    let error = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            caller: bot_principal("worker-a"),
            session_id: session_id.clone(),
            before: None,
            limit: 100,
            view_bot_id: None,
        },
    )
    .await
    .expect_err("omission selects human_worker-a, which is not a participant");
    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}

#[tokio::test]
async fn human_caller_can_explicitly_select_an_owned_bot_message_view() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker-a", "worker-b"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker-a", "worker-b"],
            "driver",
            None,
        )
        .await;
    let session_id = setup_manager_worker_session(&fixture).await;

    let messages = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            caller: bot_principal("worker-a"),
            session_id: session_id.clone(),
            before: None,
            limit: 100,
            view_bot_id: Some("worker-a".to_string()),
        },
    )
    .await
    .expect("list messages");

    assert!(messages.is_empty());
    let calls = fixture.history.session_calls.lock().expect("history lock");
    assert_eq!(
        calls.last().expect("history call").view_bot_id.as_deref(),
        Some("worker-a")
    );
}

#[tokio::test]
async fn human_caller_cannot_select_an_unowned_bot_message_view() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker-a", "worker-b"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker-a", "worker-b"],
            "driver",
            None,
        )
        .await;
    let session_id = setup_manager_worker_session(&fixture).await;

    let error = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            caller: bot_principal("worker-a"),
            session_id,
            before: None,
            limit: 100,
            view_bot_id: Some("worker-b".to_string()),
        },
    )
    .await
    .expect_err("an unowned Bot view should be forbidden");
    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}

#[tokio::test]
async fn omitted_human_message_view_equals_the_human_participant_view() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker-a", "worker-b"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker-a", "worker-b"],
            "human_staff-1",
            None,
        )
        .await;
    let session_id = setup_manager_worker_session(&fixture).await;

    let messages = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            caller: human_principal("staff-1"),
            session_id: session_id.clone(),
            before: None,
            limit: 100,
            view_bot_id: None,
        },
    )
    .await
    .expect("list messages");

    assert!(messages.is_empty());
    let calls = fixture.history.session_calls.lock().expect("history lock");
    assert_eq!(
        calls.last().expect("history call").view_bot_id.as_deref(),
        Some("human_staff-1")
    );
}

#[tokio::test]
async fn human_view_session_messages_as_self_human() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker-a", "worker-b"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker-a", "worker-b"],
            "human_staff-1",
            None,
        )
        .await;
    let session_id = setup_manager_worker_session(&fixture).await;

    let messages = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            caller: human_principal("staff-1"),
            session_id: session_id.clone(),
            before: None,
            limit: 100,
            view_bot_id: Some("human_staff-1".to_string()),
        },
    )
    .await
    .expect("list messages");

    assert!(messages.is_empty());
    let calls = fixture.history.session_calls.lock().expect("history lock");
    assert_eq!(
        calls.last().expect("history call").view_bot_id.as_deref(),
        Some("human_staff-1")
    );
}

#[tokio::test]
async fn human_view_session_messages_as_owned_bot() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker-a", "worker-b"] {
        fixture.add_bot(bot).await;
    }
    // worker-a is owned by Human staff-1 via created_by.
    fixture
        .bots
        .save_created_by("worker-a", "staff-1", true)
        .await
        .expect("save owner");
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker-a", "worker-b"],
            "human_staff-1",
            None,
        )
        .await;
    let session_id = setup_manager_worker_session(&fixture).await;

    let messages = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            caller: human_principal("staff-1"),
            session_id: session_id.clone(),
            before: None,
            limit: 100,
            view_bot_id: Some("worker-a".to_string()),
        },
    )
    .await
    .expect("human views as owned bot");

    assert!(messages.is_empty());
    let calls = fixture.history.session_calls.lock().expect("history lock");
    assert_eq!(
        calls.last().expect("history call").view_bot_id.as_deref(),
        Some("worker-a")
    );
}

#[tokio::test]
async fn human_view_session_messages_as_unowned_bot_forbidden() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker-a", "worker-b"] {
        fixture.add_bot(bot).await;
    }
    // worker-b is registered but NOT owned by staff-1 (created_by stays None).
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker-a", "worker-b"],
            "human_staff-1",
            None,
        )
        .await;
    let session_id = setup_manager_worker_session(&fixture).await;

    let error = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            caller: human_principal("staff-1"),
            session_id,
            before: None,
            limit: 100,
            view_bot_id: Some("worker-b".to_string()),
        },
    )
    .await
    .expect_err("human viewing as unowned bot should be forbidden");
    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}

#[tokio::test]
async fn get_preserves_human_participant_from_legacy_invitation_join() {
    // Vey7i: the legacy invitation-accept path (`join_session_by_invite`)
    // inserts a Human participant directly into `session.participants` with
    // `actor_kind: Human, mode: Present` (see
    // `bcs-group/src/application/invite.rs`). The V1 facade `get` must surface
    // that participant verbatim — `actor_kind: Human` and `mode: Present` —
    // NOT boot-truncate it to the Bot-only `Auto`. This seeds the same roster
    // shape via the repo (the contract `join_session_by_invite` ultimately
    // writes via `SessionManagementService::add_participant`) and reads it
    // back through the V1 `SessionService::get` projection path.
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture.store_group("g1", "driver", None).await;

    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant {
                        bot_uuid: "human_staff-1".into(),
                        bot_name: Some("Alice".into()),
                        kind: None,
                        role: ParticipantRole::Consultant,
                        actor_kind: ActorKind::Human,
                        mode: Some(ParticipantMode::Present),
                    },
                ],
                group_version: Some(group.version),
                caller_id: Some("driver".to_string()),
                caller_principal: Some("driver".to_string()),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session with human participant");

    let detail = fixture
        .service
        .get(GetSession {
            caller: bot_principal("driver"),
            session_id: session.id.clone(),
        })
        .await
        .expect("get session");

    let human = detail
        .participants
        .iter()
        .find(|p| p.actor_id == "human_staff-1")
        .expect("human participant present in V1 projection");
    assert_eq!(human.actor_kind, ActorKind::Human);
    assert_eq!(human.mode, ParticipantMode::Present);
    assert_eq!(human.role, ParticipantRole::Consultant);

    // The Bot driver is still projected as a Bot with Auto (no regression).
    let driver = detail
        .participants
        .iter()
        .find(|p| p.actor_id == "driver")
        .expect("driver participant present");
    assert_eq!(driver.actor_kind, ActorKind::Bot);
    assert_eq!(driver.mode, ParticipantMode::Auto);
}

// ── VfhG3: derive session participant role from parent group ───────────────
//
// create_session / add_participant previously hardcoded
// ParticipantRole::Consultant for every participant, losing the Worker /
// Manager role carried by the parent group's roster. The V1 facade now derives
// the role from group.participants first, then falls back to the strategy
// default (ManagerWorker→Worker, else Consultant), mirroring the legacy
// bcs-http handler which cloned group.participants. add_participant also
// surfaces an explicit `participant_already_exists` 409 (the legacy memory repo
// silently skipped duplicates).

#[tokio::test]
async fn create_session_preserves_manager_worker_worker_role() {
    // VfhG3: when the parent group is ManagerWorker and a participant Bot is a
    // Worker in group.participants, create_session must derive the Worker role
    // for the session participant rather than hardcoding Consultant.
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker"],
            "human_driver",
            Some("the task"),
        )
        .await;

    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("worker", None)],
        None,
        None,
    )
    .await;

    let worker = outcome
        .session
        .participants
        .iter()
        .find(|p| p.actor_id == "worker")
        .expect("worker participant projected");
    assert_eq!(
        worker.role,
        ParticipantRole::Worker,
        "VfhG3: ManagerWorker Worker must keep Worker role in session, not Consultant"
    );
    assert_eq!(worker.mode, ParticipantMode::Auto);

    // The driver is still forced to the Driver role regardless of derivation.
    let driver = outcome
        .session
        .participants
        .iter()
        .find(|p| p.actor_id == "driver")
        .expect("driver participant projected");
    assert_eq!(driver.role, ParticipantRole::Driver);
}

#[tokio::test]
async fn create_session_defaults_consultant_for_chat_context() {
    // VfhG3: a Bot NOT present in the parent group.participants (Chat strategy)
    // falls back to the strategy default (Chat → Consultant), preserving the
    // pre-VfhG3 behaviour for the common Chat case.
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    let mut group = Group::new(
        "g1",
        "driver",
        vec![
            Participant::bot("driver", ParticipantRole::Driver),
            Participant::bot("expert", ParticipantRole::Consultant),
        ],
    );
    group.originator = Some("human_driver".into());
    group.group_strategy = GroupStrategy::Chat;
    fixture.groups.upsert(group).await.expect("store group");

    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        None,
    )
    .await;

    let expert = outcome
        .session
        .participants
        .iter()
        .find(|p| p.actor_id == "expert")
        .expect("expert participant projected");
    assert_eq!(
        expert.role,
        ParticipantRole::Consultant,
        "VfhG3: Chat group + non-roster participant defaults to Consultant"
    );
}

#[tokio::test]
async fn add_participant_duplication_rejects_409() {
    // VfhG3: adding the same Bot to a session twice must reject the second call
    // with a 409 Conflict carrying the explicit `participant_already_exists`
    // code (the legacy memory repo silently skipped duplicates).
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert", "newcomer"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    // First add of newcomer succeeds.
    fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "newcomer".into(),
        })
        .await
        .expect("first add of newcomer");

    // Second add of newcomer is rejected with participant_already_exists.
    let error = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "newcomer".into(),
        })
        .await
        .expect_err("duplicate add should reject with participant_already_exists");
    assert!(
        matches!(
            error,
            bcs_service_api::application::v1::ApplicationError::Conflict { ref code, .. }
                if code == "participant_already_exists"
        ),
        "expected Conflict(participant_already_exists), got {error:?}",
    );
    assert_eq!(error.code(), "participant_already_exists");
}

#[tokio::test]
async fn add_participant_derives_worker_role_for_manager_worker() {
    // VfhG3: when the parent group is ManagerWorker and the added Bot is a
    // Worker in group.participants, add_participant must derive the Worker role
    // rather than hardcoding Consultant.
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker"],
            "human_driver",
            None,
        )
        .await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        None,
    )
    .await;
    let worker = outcome
        .session
        .participants
        .iter()
        .find(|p| p.actor_id == "worker")
        .expect("worker participant inherited");

    assert_eq!(
        worker.role,
        ParticipantRole::Worker,
        "VfhG3: ManagerWorker Worker gains Worker role on add_participant, not Consultant"
    );
    assert_eq!(worker.mode, ParticipantMode::Auto);
}

// ── ensure_collaboration_eligible: session add-participant anchor set ──
//
// VSN7B (revised): an added Bot is admitted when collaboration-reachable from
// the caller OR from the parent Group's driver/originator. These tests pin the
// widened behavior so a manager is not blocked from pulling a Bot the group's
// driver/originator already collaborates with.

async fn eligibility_fixture(group_id: &str, driver: &str, originator: &str) -> Fixture {
    let fixture = Fixture::new().await;
    // The Human caller `manager` owns the public driver Bot, which grants group
    // access for session creation and group-management authority.
    fixture.add_bot_with(driver, "public", "manager").await;
    fixture
        .store_group_with_originator(group_id, driver, originator, None)
        .await;
    fixture
}

#[tokio::test]
async fn add_participant_admits_protected_bot_reachable_from_driver_friend() {
    // Protected Bot not owned by / friends with the caller, but friends with the
    // group driver → admitted via the driver anchor.
    let fixture = eligibility_fixture("g1", "driver-bot", "human_manager").await;
    fixture
        .add_bot_with("px", "protected", "other-owner")
        .await;
    fixture.befriend("driver-bot", "px").await;

    let outcome = create_session(
        &fixture,
        human_principal("manager"),
        "g1",
        "driver-bot",
        vec![],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    let added = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: human_principal("manager"),
            session_id: session_id.clone(),
            bot_uuid: "px".into(),
        })
        .await
        .expect("driver-friend Bot should be admitted");
    assert_eq!(added.actor_id, "px");
}

#[tokio::test]
async fn add_participant_rejects_protected_bot_unreachable_from_all_anchors() {
    // Protected Bot reachable from none of caller/driver/originator → 403.
    let fixture = eligibility_fixture("g1", "driver-bot", "human_manager").await;
    fixture
        .add_bot_with("px", "protected", "other-owner")
        .await;

    let outcome = create_session(
        &fixture,
        human_principal("manager"),
        "g1",
        "driver-bot",
        vec![],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    let error = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: human_principal("manager"),
            session_id: session_id.clone(),
            bot_uuid: "px".into(),
        })
        .await
        .expect_err("unreachable Bot should be rejected");
    assert!(
        matches!(
            error,
            bcs_service_api::application::v1::ApplicationError::Forbidden(_)
        ),
        "expected Forbidden, got {error:?}",
    );
    assert_eq!(error.code(), "forbidden");
}

#[tokio::test]
async fn add_participant_admits_protected_bot_owned_by_caller() {
    // Protected Bot whose `created_by` matches the Human caller → admitted via
    // the caller (Human) anchor's ownership rule.
    let fixture = eligibility_fixture("g1", "driver-bot", "human_manager").await;
    fixture.add_bot_with("px", "protected", "manager").await;

    let outcome = create_session(
        &fixture,
        human_principal("manager"),
        "g1",
        "driver-bot",
        vec![],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    let added = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: human_principal("manager"),
            session_id: session_id.clone(),
            bot_uuid: "px".into(),
        })
        .await
        .expect("caller-owned protected Bot should be admitted");
    assert_eq!(added.actor_id, "px");
}

#[tokio::test]
async fn add_participant_admits_protected_bot_reachable_from_originator_friend() {
    // Distinct Bot originator (not the driver) is the only anchor that reaches
    // the target → admitted via the originator anchor, proving the anchor set is
    // not collapsed to caller+driver only.
    let fixture = eligibility_fixture("g1", "driver-bot", "originator-bot").await;
    fixture
        .add_bot_with("px", "protected", "other-owner")
        .await;
    fixture.befriend("originator-bot", "px").await;

    let outcome = create_session(
        &fixture,
        human_principal("manager"),
        "g1",
        "driver-bot",
        vec![],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    let added = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: human_principal("manager"),
            session_id: session_id.clone(),
            bot_uuid: "px".into(),
        })
        .await
        .expect("originator-friend Bot should be admitted");
    assert_eq!(added.actor_id, "px");
}

#[tokio::test]
async fn add_participant_rejects_hidden_bot_regardless_of_anchors() {
    // A Hidden Bot is rejected outright before any anchor is consulted, even when
    // the driver is its friend.
    let fixture = eligibility_fixture("g1", "driver-bot", "human_manager").await;
    fixture
        .add_bot_with("px", "protected", "other-owner")
        .await;
    fixture.befriend("driver-bot", "px").await;
    fixture
        .bots
        .update_actor_status("px", ActorStatus::Hidden)
        .await
        .expect("hide bot");

    let outcome = create_session(
        &fixture,
        human_principal("manager"),
        "g1",
        "driver-bot",
        vec![],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    let error = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: human_principal("manager"),
            session_id: session_id.clone(),
            bot_uuid: "px".into(),
        })
        .await
        .expect_err("hidden Bot should be rejected");
    assert!(
        matches!(
            error,
            bcs_service_api::application::v1::ApplicationError::Forbidden(ref message)
                if message.contains("hidden")
        ),
        "expected hidden-Bot Forbidden, got {error:?}",
    );
    assert_eq!(error.code(), "forbidden");
}
