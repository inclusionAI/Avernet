//! Integration tests for the V1 Session facade.
//!
//! Exercises the `SessionService` + `SessionMessageService` impls against the
//! real in-memory store stack (GroupCore / BotCore / SessionManagementService
//! / MemorySessionRepo / MemoryMessageRepo), mirroring the sibling
//! `bcs-app-group` test harness.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use async_trait::async_trait;
use bcs_app_session::SessionServiceImpl;
use bcs_bot::BotCore;
use bcs_bot_store::{MemoryBotRepo, PersistentBotRepo};
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
    AddSessionParticipant, ApplicationError as V1ApplicationError, AuthenticatedAppIdentity,
    AuthenticatedBotIdentity, AuthenticatedCaller, AuthenticatedUserIdentity, CollectSession,
    CompleteSession, CreateSession, DeleteSession, DeleteSessionParticipant, GetSession,
    ListSessionMessages, ListSessions, SessionMessageService, SessionParticipantInput,
    SessionService, SessionStatus as V1SessionStatus, UncollectSession, UpdateSession,
    UpdateSessionParticipant,
};
use bcs_service_api::port::{ParticipantViewBindingPort, ParticipantViewScopeChangeLease};
use bcs_service_api::port::repo::{NewSessionParams, SessionRepoPort};
use bcs_service_api::types::{HumanMessageView, MessageViewScope};
use bcs_service_api::{
    ActorKind, ActorStatus, BotCapabilities, BotRegistryCoreService, CallerContext,
    CancelStateMachineRunCommand, CollaborationDefinition, CollaborationRuntimeError,
    CollaborationRuntimeService, ConfigureGroupRuntimeCommand, ConfigureGroupRuntimeOutcome,
    FriendCoreService, Group, GroupCoreService, GroupHistoryCommand, GroupHistoryResult,
    GroupMessage, GroupMessageHistoryService, GroupMessageType, GroupStrategy, GroupUseCaseError,
    HandleBotTerminalEventCommand, HandleBotTerminalEventOutcome, HumanActor,
    MessageHistoryOptions, MessageRole, Participant, ParticipantMode, ParticipantRole,
    ServiceResult, SessionHistoryCommand, SessionHistoryResult, SessionKind,
    StartStateMachineRunCommand, StartStateMachineRunOutcome, StateMachineDeliveryCorrelation,
    StateMachineRun, StateMachineRunStatus, StateMachineRunView, SystemMessageEvent,
};
use bcs_session::{SessionLaunchApplication, SessionManagementServiceImpl};
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::NoopSystemMessageService;

#[derive(Default)]
struct RecordingHistoryService {
    session_calls: Mutex<Vec<SessionHistoryCommand>>,
    session_options: Mutex<Vec<MessageHistoryOptions>>,
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

#[derive(Default)]
struct RecordingParticipantViewBindings {
    begun: Mutex<Vec<(String, String)>>,
    finished: Mutex<Vec<ParticipantViewScopeChangeLease>>,
}

#[async_trait]
impl ParticipantViewBindingPort for RecordingParticipantViewBindings {
    async fn begin_scope_change(
        &self,
        scope_id: &str,
        human_actor_id: &str,
    ) -> ServiceResult<ParticipantViewScopeChangeLease> {
        self.begun
            .lock()
            .expect("scope binding begin lock")
            .push((scope_id.to_string(), human_actor_id.to_string()));
        Ok(ParticipantViewScopeChangeLease {
            scope_id: scope_id.to_string(),
            human_actor_id: human_actor_id.to_string(),
            lease_id: 1,
        })
    }

    async fn finish_scope_change(
        &self,
        lease: ParticipantViewScopeChangeLease,
    ) -> ServiceResult<()> {
        self.finished
            .lock()
            .expect("scope binding finish lock")
            .push(lease);
        Ok(())
    }
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

    async fn get_session_history_with_options(
        &self,
        cmd: SessionHistoryCommand,
        options: MessageHistoryOptions,
    ) -> Result<SessionHistoryResult, GroupUseCaseError> {
        self.session_options
            .lock()
            .expect("history options lock")
            .push(options);
        self.get_session_history(cmd).await
    }
}

#[derive(Default)]
struct RecordingRuntime {
    start_calls: Mutex<Vec<StartStateMachineRunCommand>>,
    history_calls: Mutex<Vec<(String, u64, Option<u64>)>>,
    participant_history_calls: Mutex<Vec<(String, u64, Option<u64>, HumanMessageView)>>,
    history_result: Mutex<Option<SessionHistoryResult>>,
    session_run: Mutex<Option<StateMachineRunView>>,
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
                terminal_result: None,
                run: StateMachineRun {
                    run_id: "run-1".into(),
                    root_run_id: Some("run-1".into()),
                    rerun_of: None,
                    definition_id: "definition-1".into(),
                    definition_version: 1,
                    group_id: cmd.group_id,
                    group_version: 1,
                    session_id,
                    session_activation_count: None,
                    created_by: cmd.caller_id,
                    status: StateMachineRunStatus::Running,
                    input: cmd.input,
                    opening_message_override: None,
                    output: None,
                    error: None,
                    created_at: 1,
                    updated_at: 1,
                    completed_at: None,
                },
                nodes: Vec::new(),
                node_execution_metadata: None,
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

    async fn get_state_machine_run_by_session_id(
        &self,
        _session_id: &str,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        Ok(self
            .session_run
            .lock()
            .expect("runtime session run lock")
            .clone())
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

    async fn get_state_machine_session_history_for_view(
        &self,
        session_id: &str,
        limit: u64,
        before: Option<u64>,
        human_view: HumanMessageView,
    ) -> Result<Option<SessionHistoryResult>, CollaborationRuntimeError> {
        if human_view.scope == MessageViewScope::Full {
            return self
                .get_state_machine_session_history(session_id, limit, before)
                .await;
        }
        self.participant_history_calls
            .lock()
            .expect("runtime participant history lock")
            .push((session_id.to_string(), limit, before, human_view));
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

/// A live Human ACTOR row on the shared bots registry (the
/// authority guards' Human existence source).
async fn ensure_human_actor(fixture: &Fixture, user_id: &str) {
    use bcs_service_api::port::repo::BotRepoPort;

    fixture
        .authority_repo
        .ensure_human_actor(user_id, user_id)
        .await
        .expect("ensure fixture Human actor row");
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
    /// The authority store backing the Session
    /// facade's hook (CURRENT control edges).
    authority_repo: Arc<MemoryBotRepo>,
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
        let _launch = Arc::new(SessionLaunchApplication::new(
            bots.clone(),
            groups.clone(),
            sessions.clone(),
            runtime.clone(),
            Arc::new(NoopSystemMessageService),
        ));
        let authority_repo = Arc::new(MemoryBotRepo::new());
        let authority_hook =
            bcs_test_support::authority_hook_for(authority_repo.clone());
        let launch = Arc::new(
            SessionLaunchApplication::new(
                bots.clone(),
                groups.clone(),
                sessions.clone(),
                runtime.clone(),
                Arc::new(NoopSystemMessageService),
            )
            .with_bot_authority_hook(authority_hook.clone()),
        );
        let service = SessionServiceImpl::new(
            launch,
            sessions,
            groups.clone(),
            bots.clone(),
            friends,
            session_repo.clone(),
            history.clone(),
            runtime.clone(),
            system_messages.clone(),
        )
        .with_bot_authority_hook(authority_hook)
        // §8.3: the detail resource-role legs read
        // the strict Session snapshot core.
        .with_session_authorization_core(
            Arc::new(bcs_session::SessionAuthorizationCoreImpl::new(
                session_repo.clone(),
            )),
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
            authority_repo,
        }
    }

    fn with_participant_view_bindings(
        mut self,
        participant_view_bindings: Arc<dyn ParticipantViewBindingPort>,
    ) -> Self {
        self.service = self
            .service
            .with_participant_view_bindings(participant_view_bindings);
        self
    }

    /// Grant the CURRENT owner edge in place of the
    /// historical `created_by` (bot-authority plan
    /// Task 6.2 fixture).
    async fn grant_owner(&self, bot_uuid: &str, user: &str) {
        bcs_test_support::grant_owner_edge(&self.authority_repo, bot_uuid, user)
            .await;
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
        self.grant_owner(bot_uuid, bot_uuid).await;
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
        if !created_by.is_empty() {
            self.grant_owner(bot_uuid, created_by).await;
        }
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
            caller,
            group_id: group_id.to_string(),
            title: title.map(str::to_string),
            kind: None,
            acting_bot_id: None,
            creator_role: None,
            message_view_scope: None,
            input,
            meta: None,
            context_delivery: None,
        })
        .await
        .expect("create session")
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
                actor_id, from, to, ..
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

#[path = "v1_session_service/lifecycle.rs"]
mod lifecycle;

#[path = "v1_session_service/collection.rs"]
mod collection;

#[path = "v1_session_service/history.rs"]
mod history;

#[path = "v1_session_service/participants.rs"]
mod participants;

#[path = "v1_session_service/human_scope.rs"]
mod human_scope;

#[path = "v1_session_service/participant_authorization.rs"]
mod participant_authorization;

#[path = "v1_session_service/session_visibility.rs"]
mod session_visibility;

#[path = "v1_session_service/message_views.rs"]
mod message_views;

#[path = "v1_session_service/participant_eligibility.rs"]
mod participant_eligibility;
