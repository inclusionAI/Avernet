use std::collections::{BTreeMap, HashMap};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_bot::BotCore;
use bcs_bot_store::{MemoryBotRepo, PersistentBotRepo};
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement, DbTransactionStep,
    DbTransactionStepResult, DbValue,
};
use bcs_friend::FriendCore;
use bcs_group::{GroupConfig, GroupCore, GroupManagement, MemoryGroupRepo};
use bcs_group_store::MySqlGroupStore;
use bcs_relation::RelationCore;
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedBotIdentity, AuthenticatedCaller, AuthenticatedUserIdentity,
    BotFinalDelivery, ChatConfiguration, CollaborationConfiguration, CreateCollaborationGroup,
    CreateDirectMessageGroup, CreateGroup, CreateGroupSpec, CreateParticipant, DeleteGroup,
    EventSinkInput, GetGroup, GroupDeliveryPolicy, GroupDetail, GroupEventSubscriptionProvisioner,
    GroupKindFilter, GroupPatch, GroupService, GroupStrategy as V1GroupStrategy, GroupSummary,
    GroupVisibility, InlineGroupEventSubscriptionRequest, ListGroups, ListPublicGroups,
    Membership, MembershipFilter,
    PendingGroupEventSubscriptions, PreparedGroupEventSubscriptions, UpdateGroup,
};
use bcs_service_api::types::{EventActor, EventActorType, EventPayload, OpeningMessage};
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, CancelStateMachineRunCommand,
    ChannelBindingCleanupPort, CollaborationDefinition, CollaborationDefinitionRef,
    CollaborationRuntimeError, CollaborationRuntimeService, ConfigureGroupRuntimeCommand,
    ConfigureGroupRuntimeOutcome, CreateOrReactivateCommand, DefaultDelivery, DefinitionYamlSource,
    FriendCoreService, FriendRepoPort, Group, GroupCollaborationDefinitionView, GroupCoreService,
    GroupStrategy, HandleBotTerminalEventCommand, HandleBotTerminalEventOutcome, NewSessionParams,
    Participant, ParticipantRole, RelationCoreService, RoutingMode, RoutingPolicy, ServiceError,
    ServiceResult, SessionHistoryResult, SessionManagementService, StartStateMachineRunCommand,
    StartStateMachineRunOutcome, StateMachineDeliveryCorrelation, StateMachineRun,
    StateMachineRunStatus, StateMachineRunView, SystemMessageService,
};
use bcs_session::SessionManagementServiceImpl;
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::NoopSystemMessageService;

use bcs_app_group::{GroupProvisioningReconciler, GroupServiceImpl};

#[path = "v1_group_service/create_notify_mode.rs"]
mod create_notify_mode;

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
    service: GroupServiceImpl,
    groups: Arc<GroupCore>,
    bots: Arc<BotCore>,
    friends: Arc<FriendCore>,
    relation: Arc<RelationCore>,
    sessions: Arc<SessionManagementServiceImpl>,
    /// The authority store backing the Group facade's
    /// hook (the CURRENT control edges).
    authority_repo: Arc<MemoryBotRepo>,
}

impl Fixture {
    /// Grant the CURRENT owner edge this suite uses
    /// in place of the historical `created_by`.
    async fn grant_owner(&self, bot_id: &str, user: &str) {
        bcs_test_support::grant_owner_edge(&self.authority_repo, bot_id, user)
            .await;
    }

    /// The failing-store variant builds the
    /// authority repo inline below.
    async fn new() -> Self {
        Self::build(None, None, None).await
    }

    fn with_event_subscription_provisioner(
        mut self,
        provisioner: Arc<dyn GroupEventSubscriptionProvisioner>,
    ) -> Self {
        self.service = self
            .service
            .with_event_subscription_provisioner(provisioner);
        self
    }

    async fn new_with_runtime(runtime: Arc<dyn CollaborationRuntimeService>) -> Self {
        Self::build(Some(runtime), None, None).await
    }

    async fn new_with_friends(friends: Arc<FriendCore>) -> Self {
        Self::build(None, Some(friends), None).await
    }

    async fn new_with_bots(bots: Arc<BotCore>) -> Self {
        Self::build(None, None, Some(bots)).await
    }

    async fn new_with_failing_group_store() -> Self {
        let group_repo = Arc::new(MySqlGroupStore::new(Arc::new(FailingDb), "dev".to_string()));
        let groups = Arc::new(GroupCore::with_repo(group_repo.clone()));
        let bots = Arc::new(BotCore::memory());
        let relation = Arc::new(RelationCore::memory());
        let friends = Arc::new(FriendCore::memory().with_relation(relation.clone()));
        let sessions = Arc::new(SessionManagementServiceImpl::new(
            Arc::new(MemorySessionRepo::new()),
            group_repo,
        ));
        let system_message: Arc<dyn SystemMessageService> = Arc::new(NoopSystemMessageService);
        let authority_repo = Arc::new(MemoryBotRepo::new());
        let management = Arc::new(
            GroupManagement::new(
                groups.clone(),
                bots.clone(),
                friends.clone(),
                relation.clone(),
                GroupConfig::default(),
                sessions.clone(),
                system_message,
            )
            .with_bot_authority(bcs_test_support::authority_hook_for(
                authority_repo.clone(),
            ))
            .for_v1_openapi(),
        );
        let service = GroupServiceImpl::new(
            groups.clone(),
            bots.clone(),
            friends.clone(),
            sessions.clone(),
            management,
        )
        .with_bot_authority_hook(bcs_test_support::authority_hook_for(
            authority_repo.clone(),
        ));
        Self {
            service,
            groups,
            bots,
            friends,
            relation: relation.clone(),
            sessions,
            authority_repo,
        }
    }

    async fn new_with_runtime_and_failing_channel_cleanup(
        runtime: Arc<dyn CollaborationRuntimeService>,
    ) -> Self {
        let group_repo = Arc::new(MemoryGroupRepo::new());
        let groups = Arc::new(GroupCore::with_repo(group_repo.clone()));
        let bots = Arc::new(BotCore::memory());
        let relation = Arc::new(RelationCore::memory());
        let friends = Arc::new(FriendCore::memory().with_relation(relation.clone()));
        let sessions = Arc::new(SessionManagementServiceImpl::new(
            Arc::new(MemorySessionRepo::new()),
            group_repo,
        ));
        let system_message: Arc<dyn SystemMessageService> = Arc::new(NoopSystemMessageService);
        let authority_repo = Arc::new(MemoryBotRepo::new());
        let management = Arc::new(
            GroupManagement::new(
                groups.clone(),
                bots.clone(),
                friends.clone(),
                relation.clone(),
                GroupConfig::default(),
                sessions.clone(),
                system_message,
            )
            .with_bot_authority(bcs_test_support::authority_hook_for(
                authority_repo.clone(),
            ))
            .for_v1_openapi()
            .with_channel_binding_cleanup(Arc::new(FailingChannelBindingCleanup)),
        );
        let service = GroupServiceImpl::new(
            groups.clone(),
            bots.clone(),
            friends.clone(),
            sessions.clone(),
            management,
        )
        .with_bot_authority_hook(bcs_test_support::authority_hook_for(
            authority_repo.clone(),
        ))
        .with_collaboration_runtime(runtime);
        Self {
            service,
            groups,
            bots,
            friends,
            relation,
            sessions,
            authority_repo,
        }
    }

    async fn build(
        runtime: Option<Arc<dyn CollaborationRuntimeService>>,
        friends: Option<Arc<FriendCore>>,
        bots: Option<Arc<BotCore>>,
    ) -> Self {
        let group_repo = Arc::new(MemoryGroupRepo::new());
        let groups = Arc::new(GroupCore::with_repo(group_repo.clone()));
        let bots = bots.unwrap_or_else(|| Arc::new(BotCore::memory()));
        let relation = Arc::new(RelationCore::memory());
        let friends = friends
            .unwrap_or_else(|| Arc::new(FriendCore::memory().with_relation(relation.clone())));
        let sessions = Arc::new(SessionManagementServiceImpl::new(
            Arc::new(MemorySessionRepo::new()),
            group_repo,
        ));
        let system_message: Arc<dyn SystemMessageService> = Arc::new(NoopSystemMessageService);
        let authority_repo = Arc::new(MemoryBotRepo::new());
        let management = Arc::new(
            GroupManagement::new(
                groups.clone(),
                bots.clone(),
                friends.clone(),
                relation.clone(),
                GroupConfig::default(),
                sessions.clone(),
                system_message,
            )
            .with_bot_authority(bcs_test_support::authority_hook_for(
                authority_repo.clone(),
            ))
            .for_v1_openapi(),
        );
        let mut service = GroupServiceImpl::new(
            groups.clone(),
            bots.clone(),
            friends.clone(),
            sessions.clone(),
            management,
        )
        .with_bot_authority_hook(bcs_test_support::authority_hook_for(
            authority_repo.clone(),
        ));
        if let Some(runtime) = runtime {
            service = service.with_collaboration_runtime(runtime);
        }
        Self {
            authority_repo,
            service,
            groups,
            bots,
            friends,
            relation,
            sessions,
        }
    }

    async fn add_public_bot(&self, bot_uuid: &str) {
        let capabilities = BotCapabilities {
            name: Some(bot_uuid.to_string()),
            visibility: "public".into(),
            ..Default::default()
        };
        self.bots
            .register(bot_uuid.to_string(), capabilities)
            .await
            .expect("register bot");
        self.bots
            .save_created_by(bot_uuid, bot_uuid, true)
            .await
            .expect("assign test Bot owner");
        bcs_test_support::grant_owner_edge(
            &self.authority_repo,
            bot_uuid,
            bot_uuid,
        )
        .await;
    }

    async fn add_protected_bot(&self, bot_uuid: &str) {
        let capabilities = BotCapabilities {
            name: Some(bot_uuid.to_string()),
            visibility: "protected".into(),
            ..Default::default()
        };
        self.bots
            .register(bot_uuid.to_string(), capabilities)
            .await
            .expect("register bot");
        self.bots
            .save_created_by(bot_uuid, bot_uuid, true)
            .await
            .expect("assign test Bot owner");
        bcs_test_support::grant_owner_edge(
            &self.authority_repo,
            bot_uuid,
            bot_uuid,
        )
        .await;
    }

    async fn add_bot_owned_by(
        &self,
        bot_uuid: &str,
        owner_staff_no: &str,
        visibility: &str,
    ) {
        let capabilities = BotCapabilities {
            name: Some(bot_uuid.to_string()),
            visibility: visibility.into(),
            ..Default::default()
        };
        self.bots
            .register(bot_uuid.to_string(), capabilities)
            .await
            .expect("register bot");
        self.bots
            .save_created_by(bot_uuid, owner_staff_no, true)
            .await
            .expect("assign test Bot owner");
        // The CURRENT owner edge replaces the historical
        // `created_by` fact (bot-authority plan Task 6.2).
        bcs_test_support::grant_owner_edge(
            &self.authority_repo,
            bot_uuid,
            owner_staff_no,
        )
        .await;
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

#[derive(Default)]
struct DriverThenFailingDb {
    queries: AtomicUsize,
}

#[async_trait]
impl DbPlugin for DriverThenFailingDb {
    async fn query(&self, _statement: DbStatement) -> DbResult<Vec<DbRow>> {
        if self.queries.fetch_add(1, Ordering::SeqCst) == 0 {
            return Ok(vec![DbRow::new(BTreeMap::from([
                ("name".into(), DbValue::from("driver")),
                ("bot_info".into(), DbValue::from("{}")),
                ("visibility".into(), DbValue::from("public")),
                ("status".into(), DbValue::from("online")),
                ("actor_kind".into(), DbValue::from("bot")),
                ("env".into(), DbValue::from("dev")),
            ]))]);
        }
        Err(DbError::Backend("participant registry unavailable".into()))
    }

    async fn execute(&self, _statement: DbStatement) -> DbResult<DbExecuteResult> {
        Err(DbError::Backend("participant registry unavailable".into()))
    }

    async fn transaction(
        &self,
        _steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        Err(DbError::Backend("participant registry unavailable".into()))
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        Ok(DbHealth::unhealthy("participant registry unavailable"))
    }
}

struct FailingFriendRepo;

#[async_trait]
impl FriendRepoPort for FailingFriendRepo {
    async fn list_friends(&self, _bot_id: &str) -> ServiceResult<Vec<String>> {
        Err(ServiceError::InternalError(
            "friend store unavailable".into(),
        ))
    }

    async fn are_friends(&self, _bot_a: &str, _bot_b: &str) -> ServiceResult<bool> {
        Err(ServiceError::InternalError(
            "friend store unavailable".into(),
        ))
    }

    async fn add_friendship(&self, _bot_a: &str, _bot_b: &str) -> ServiceResult<()> {
        Err(ServiceError::InternalError(
            "friend store unavailable".into(),
        ))
    }

    async fn remove_all_friendships(&self, _bot_id: &str) -> ServiceResult<usize> {
        Err(ServiceError::InternalError(
            "friend store unavailable".into(),
        ))
    }
}

#[derive(Default)]
struct FirstFriendCheckThenFailingRepo {
    checks: AtomicUsize,
}

#[async_trait]
impl FriendRepoPort for FirstFriendCheckThenFailingRepo {
    async fn list_friends(&self, _bot_id: &str) -> ServiceResult<Vec<String>> {
        Ok(Vec::new())
    }

    async fn are_friends(&self, _bot_a: &str, _bot_b: &str) -> ServiceResult<bool> {
        if self.checks.fetch_add(1, Ordering::SeqCst) == 0 {
            return Ok(true);
        }
        Err(ServiceError::InternalError(
            "friend store unavailable after validation".into(),
        ))
    }

    async fn add_friendship(&self, _bot_a: &str, _bot_b: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn remove_all_friendships(&self, _bot_id: &str) -> ServiceResult<usize> {
        Ok(0)
    }
}

struct FailingChannelBindingCleanup;

#[async_trait]
impl ChannelBindingCleanupPort for FailingChannelBindingCleanup {
    async fn delete_bindings_for_group(&self, _group_id: &str) -> ServiceResult<u64> {
        Err(ServiceError::InternalError(
            "channel binding cleanup failed".to_string(),
        ))
    }

    async fn delete_bindings_for_bot(&self, _bot_id: &str) -> ServiceResult<u64> {
        Err(ServiceError::InternalError(
            "channel binding cleanup failed".to_string(),
        ))
    }
}

struct RecordingGroupProvisioner {
    groups: Arc<dyn GroupCoreService>,
    prepared_group_ids: Mutex<Vec<String>>,
    cancelled_group_ids: Mutex<Vec<String>>,
    finalized_group_ids: Mutex<Vec<String>>,
    pending_groups: Mutex<Vec<PendingGroupEventSubscriptions>>,
    fail_prepare: AtomicBool,
    fail_finalize: AtomicBool,
}

impl RecordingGroupProvisioner {
    fn new(groups: Arc<dyn GroupCoreService>) -> Self {
        Self {
            groups,
            prepared_group_ids: Mutex::new(Vec::new()),
            cancelled_group_ids: Mutex::new(Vec::new()),
            finalized_group_ids: Mutex::new(Vec::new()),
            pending_groups: Mutex::new(Vec::new()),
            fail_prepare: AtomicBool::new(false),
            fail_finalize: AtomicBool::new(false),
        }
    }
}

#[async_trait]
impl GroupEventSubscriptionProvisioner for RecordingGroupProvisioner {
    async fn prepare(
        &self,
        _caller: &AuthenticatedCaller,
        group_id: &str,
        _requests: Vec<InlineGroupEventSubscriptionRequest>,
    ) -> Result<PreparedGroupEventSubscriptions, ApplicationError> {
        self.prepared_group_ids
            .lock()
            .expect("provisioner lock")
            .push(group_id.to_string());
        if self.fail_prepare.load(Ordering::SeqCst) {
            return Err(ApplicationError::invalid(
                "invalid_event_filter",
                "test validation failure",
            ));
        }
        Ok(PreparedGroupEventSubscriptions {
            group_id: group_id.to_string(),
            subscription_ids: vec!["sub-inline".to_string()],
            actor: EventActor {
                actor_type: EventActorType::Human,
                id: "human_alice".to_string(),
                display_name: Some("Alice".to_string()),
            },
        })
    }

    async fn cancel(
        &self,
        prepared: &PreparedGroupEventSubscriptions,
        _reason: &str,
    ) -> Result<(), ApplicationError> {
        self.cancelled_group_ids
            .lock()
            .expect("provisioner lock")
            .push(prepared.group_id.clone());
        Ok(())
    }

    async fn finalize(
        &self,
        prepared: &PreparedGroupEventSubscriptions,
        group: &Group,
        _initial_session: Option<&bcs_service_api::Session>,
    ) -> Result<(), ApplicationError> {
        self.finalized_group_ids
            .lock()
            .expect("provisioner lock")
            .push(prepared.group_id.clone());
        if self.fail_finalize.load(Ordering::SeqCst) {
            return Err(ApplicationError::internal("test finalization failure"));
        }
        let mut active = group.clone();
        active.record_status = "active".to_string();
        self.groups
            .upsert(active)
            .await
            .map_err(|error| ApplicationError::internal(format!("test activation failed: {error}")))
    }

    async fn recover_pending(
        &self,
        group_id: &str,
    ) -> Result<PreparedGroupEventSubscriptions, ApplicationError> {
        Ok(PreparedGroupEventSubscriptions {
            group_id: group_id.to_string(),
            subscription_ids: vec!["sub-inline".to_string()],
            actor: EventActor {
                actor_type: EventActorType::System,
                id: "test-reconciler".to_string(),
                display_name: None,
            },
        })
    }

    async fn list_pending_groups(
        &self,
    ) -> Result<Vec<PendingGroupEventSubscriptions>, ApplicationError> {
        Ok(self
            .pending_groups
            .lock()
            .expect("provisioner lock")
            .clone())
    }

    async fn load_activated(
        &self,
        _prepared: &PreparedGroupEventSubscriptions,
    ) -> Result<Vec<bcs_service_api::application::v1::EventSubscription>, ApplicationError> {
        Ok(Vec::new())
    }
}

#[derive(Default)]
struct RecordingRuntime {
    configured: Mutex<Option<ConfigureGroupRuntimeCommand>>,
    started: Mutex<Vec<StartStateMachineRunCommand>>,
    cancelled_groups: Mutex<Vec<String>>,
    deleted_group_state: Mutex<Vec<String>>,
    configure_error: Mutex<Option<String>>,
    start_error: Mutex<Option<String>>,
    projection_error: Mutex<Option<String>>,
    requires_human_input_channel: bool,
}

#[async_trait]
impl CollaborationRuntimeService for RecordingRuntime {
    async fn start_state_machine_run(
        &self,
        cmd: StartStateMachineRunCommand,
    ) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> {
        self.started.lock().expect("runtime lock").push(cmd.clone());
        if let Some(message) = self.start_error.lock().expect("runtime lock").clone() {
            return Err(CollaborationRuntimeError::InvalidRequest(message));
        }
        Ok(StartStateMachineRunOutcome {
            view: StateMachineRunView {
                run: StateMachineRun {
                    run_id: "run-1".into(),
                    root_run_id: Some("run-1".into()),
                    rerun_of: None,
                    definition_id: "definition-1".into(),
                    definition_version: 1,
                    group_id: cmd.group_id,
                    group_version: 1,
                    session_id: cmd.session_id.unwrap_or_else(|| "session-1".into()),
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
        cmd: CancelStateMachineRunCommand,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        Err(CollaborationRuntimeError::RunNotFound(cmd.run_id))
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
        cmd: ConfigureGroupRuntimeCommand,
    ) -> Result<ConfigureGroupRuntimeOutcome, CollaborationRuntimeError> {
        if let Some(message) = self.configure_error.lock().expect("runtime lock").clone() {
            return Err(CollaborationRuntimeError::InvalidRequest(message));
        }
        let outcome = ConfigureGroupRuntimeOutcome {
            group_id: cmd.group_id.clone(),
            default_definition: cmd.definition_ref.clone().or_else(|| {
                cmd.definition_yaml
                    .as_ref()
                    .map(|_| CollaborationDefinitionRef {
                        id: "generated-definition".into(),
                        version: 1,
                    })
            }),
            auto_start_on_service_invocation: cmd.auto_start_on_service_invocation,
            requires_human_input_channel: self.requires_human_input_channel,
        };
        *self.configured.lock().expect("runtime lock") = Some(cmd);
        Ok(outcome)
    }

    async fn cancel_group_runs(
        &self,
        group_id: &str,
        _reason: &str,
    ) -> Result<(), CollaborationRuntimeError> {
        self.cancelled_groups
            .lock()
            .expect("runtime lock")
            .push(group_id.to_string());
        Ok(())
    }

    async fn delete_group_runtime_state(
        &self,
        group_id: &str,
    ) -> Result<(), CollaborationRuntimeError> {
        self.deleted_group_state
            .lock()
            .expect("runtime lock")
            .push(group_id.to_string());
        Ok(())
    }

    async fn get_group_collaboration_definition(
        &self,
        group_id: &str,
    ) -> Result<GroupCollaborationDefinitionView, CollaborationRuntimeError> {
        if let Some(message) = self
            .projection_error
            .lock()
            .expect("projection error lock")
            .clone()
        {
            return Err(CollaborationRuntimeError::InvalidRequest(message));
        }
        let configured = self.configured.lock().expect("runtime lock");
        let configured = configured.as_ref().ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest("Group runtime is not configured".into())
        })?;
        if configured.group_id != group_id {
            return Err(CollaborationRuntimeError::InvalidRequest(
                "Unexpected Group".into(),
            ));
        }
        Ok(GroupCollaborationDefinitionView {
            group_id: configured.group_id.clone(),
            default_definition: configured.definition_ref.clone(),
            definition: None,
            definition_yaml: None,
            yaml_source: DefinitionYamlSource::Unavailable,
            participant_bindings: configured.participant_bindings.clone(),
        })
    }
}

fn bot_principal(bot_uuid: &str) -> AuthenticatedCaller {
    human_principal_with_profile(bot_uuid, bot_uuid, None, None)
}

fn authenticated_bot_principal(bot_uuid: &str, owner_id: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".into()),
        user: None,
        bot: Some(AuthenticatedBotIdentity {
            bot_uuid: bot_uuid.into(),
            owner_id: owner_id.into(),
            app_id: 7,
            agent_code: format!("agent-{bot_uuid}"),
        }),
        app: None,
        access_key: None,
    }
}

fn inline_group_subscription() -> InlineGroupEventSubscriptionRequest {
    InlineGroupEventSubscriptionRequest {
        name: "group-create-events".to_string(),
        event_filters: vec!["group.created".to_string(), "session.created".to_string()],
        payload: EventPayload::default(),
        sink: EventSinkInput::Webhook {
            url: "https://events.example.com/groups".to_string(),
            request_timeout_ms: None,
        },
    }
}

fn collaboration_create_command() -> CreateGroup {
    CreateGroup {
        caller: bot_principal("alice"),
        group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
            human_mention_notify_mode: Default::default(),
            originator: None,
            name: Some("Provisioned Group".to_string()),
            context: None,
            opening_message: None,
            visibility: GroupVisibility::Private,
            driver_bot_uuid: "driver".to_string(),
            participants: Vec::new(),
            collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
                delivery_policy: GroupDeliveryPolicy {
                    bot_final_delivery: BotFinalDelivery::SendToDriver,
                },
            }),
        }),
    }
}

fn dm_create_command() -> CreateGroup {
    CreateGroup {
        caller: bot_principal("alice"),
        group: CreateGroupSpec::DirectMessage(CreateDirectMessageGroup {
            name: Some("Alice and Assistant".to_string()),
            context: None,
            target_actor_id: "assistant".to_string(),
        }),
    }
}

fn bot_principal_in_tenant(bot_uuid: &str, tenant: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some(tenant.to_string()),
        user: Some(AuthenticatedUserIdentity {
            id: bot_uuid.to_string(),
            username: bot_uuid.to_string(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

fn human_principal_with_profile(
    subject_id: &str,
    username: &str,
    display_name: Option<&str>,
    full_name: Option<&str>,
) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".into()),
        user: Some(AuthenticatedUserIdentity {
            id: subject_id.into(),
            username: username.into(),
            display_name: display_name.map(str::to_string),
            full_name: full_name.map(str::to_string),
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

fn normal_group(
    group_id: &str,
    driver: &str,
    participants: Vec<Participant>,
    strategy: GroupStrategy,
    updated_at: u64,
) -> Group {
    let mut group = Group::new(group_id, driver, participants);
    group.originator = Some(format!("human_{driver}"));
    group.label = Some(group_id.to_string());
    group.group_strategy = strategy;
    group.updated_at = updated_at;
    // V1 list_groups sorts by `created_at DESC, group_id ASC`; pin both
    // timestamps to the same value so existing ordering assertions remain
    // deterministic under the new comparator.
    group.created_at = updated_at;
    group
}

fn detail_group_id(detail: &GroupDetail) -> &str {
    match detail {
        GroupDetail::Collaboration(detail) => &detail.group_id,
        GroupDetail::DirectMessage(detail) => &detail.group_id,
    }
}

fn detail_notify_mode(detail: &GroupDetail) -> bcs_service_api::HumanMentionNotifyMode {
    match detail {
        GroupDetail::Collaboration(detail) => detail.human_mention_notify_mode,
        GroupDetail::DirectMessage(detail) => detail.human_mention_notify_mode,
    }
}

fn summary_notify_mode(summary: &GroupSummary) -> bcs_service_api::HumanMentionNotifyMode {
    match summary {
        GroupSummary::Normal(summary) => summary.human_mention_notify_mode,
        GroupSummary::DirectMessage(summary) => summary.human_mention_notify_mode,
    }
}

#[path = "v1_group_service/visibility.rs"]
mod visibility;

#[path = "v1_group_service/provisioning.rs"]
mod provisioning;

#[path = "v1_group_service/creation_authorization.rs"]
mod creation_authorization;

#[path = "v1_group_service/state_machine.rs"]
mod state_machine;

#[path = "v1_group_service/mutation_authorization.rs"]
mod mutation_authorization;

#[path = "v1_group_service/validation.rs"]
mod validation;

#[path = "v1_group_service/public_catalog.rs"]
mod public_catalog;

#[path = "v1_group_service/notify_mode.rs"]
mod notify_mode;

#[path = "v1_group_service/originator_v1_policy.rs"]
mod originator_v1_policy;
