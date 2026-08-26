use std::collections::{BTreeMap, HashMap};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_bot::BotCore;
use bcs_bot_store::PersistentBotRepo;
use bcs_cache_local::InMemoryCachePlugin;
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement, DbTransactionStep,
    DbTransactionStepResult, DbValue,
};
use bcs_friend::FriendCore;
use bcs_group::{GroupConfig, GroupCore, GroupManagement, MemoryGroupRepo};
use bcs_group_store::MySqlGroupStore;
use bcs_relation::RelationCore;
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedCaller, AuthenticatedUserIdentity, BotFinalDelivery,
    ChatConfiguration, CollaborationConfiguration, CreateCollaborationGroup,
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

use bcs_app_group::{GroupProvisioningReconciler, GroupServiceConfig, GroupServiceImpl};

struct Fixture {
    service: GroupServiceImpl,
    groups: Arc<GroupCore>,
    bots: Arc<BotCore>,
    friends: Arc<FriendCore>,
    relation: Arc<RelationCore>,
    sessions: Arc<SessionManagementServiceImpl>,
}

impl Fixture {
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
            .for_v1_openapi(),
        );
        let service = GroupServiceImpl::new(
            groups.clone(),
            bots.clone(),
            friends.clone(),
            relation.clone(),
            sessions.clone(),
            management,
            GroupServiceConfig {
                relation_env: "dev".to_string(),
            },
        );
        Self {
            service,
            groups,
            bots,
            friends,
            relation: relation.clone(),
            sessions,
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
            .for_v1_openapi()
            .with_channel_binding_cleanup(Arc::new(FailingChannelBindingCleanup)),
        );
        let service = GroupServiceImpl::new(
            groups.clone(),
            bots.clone(),
            friends.clone(),
            relation.clone(),
            sessions.clone(),
            management,
            GroupServiceConfig {
                relation_env: "dev".to_string(),
            },
        )
        .with_collaboration_runtime(runtime);
        Self {
            service,
            groups,
            bots,
            friends,
            relation,
            sessions,
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
            .for_v1_openapi(),
        );
        let mut service = GroupServiceImpl::new(
            groups.clone(),
            bots.clone(),
            friends.clone(),
            relation.clone(),
            sessions.clone(),
            management,
            GroupServiceConfig {
                relation_env: "dev".to_string(),
            },
        );
        if let Some(runtime) = runtime {
            service = service.with_collaboration_runtime(runtime);
        }
        Self {
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
                    definition_id: "definition-1".into(),
                    definition_version: 1,
                    group_id: cmd.group_id,
                    group_version: 1,
                    session_id: cmd.session_id.unwrap_or_else(|| "session-1".into()),
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

#[tokio::test]
async fn list_filters_deduplicates_before_pagination_and_direct_wins() {
    let fixture = Fixture::new().await;
    for bot in ["target", "driver-a", "driver-b", "driver-c"] {
        fixture.add_public_bot(bot).await;
    }

    fixture
        .groups
        .upsert(normal_group(
            "direct-chat",
            "driver-a",
            vec![
                Participant::bot("driver-a", ParticipantRole::Driver),
                Participant::bot("target", ParticipantRole::Consultant),
            ],
            GroupStrategy::Chat,
            30,
        ))
        .await
        .expect("store direct");
    fixture
        .groups
        .upsert(normal_group(
            "both-state-machine",
            "driver-b",
            vec![
                Participant::bot("driver-b", ParticipantRole::Driver),
                Participant::bot("target", ParticipantRole::Consultant),
            ],
            GroupStrategy::StateMachine,
            20,
        ))
        .await
        .expect("store both");
    fixture
        .groups
        .upsert(normal_group(
            "session-only-state-machine",
            "driver-c",
            vec![Participant::bot("driver-c", ParticipantRole::Driver)],
            GroupStrategy::StateMachine,
            10,
        ))
        .await
        .expect("store session-only");

    for group_id in ["both-state-machine", "session-only-state-machine"] {
        fixture
            .sessions
            .create_or_reactivate(CreateOrReactivateCommand {
                group_id: group_id.into(),
                session_id: None,
                params: NewSessionParams {
                    participants: vec![Participant::bot("target", ParticipantRole::Consultant)],
                    ..Default::default()
                },
            })
            .await
            .expect("create session");
    }

    let page = fixture
        .service
        .list_groups(ListGroups {
            caller: bot_principal("target"),
            view_bot_id: Some("target".into()),
            offset: 0,
            limit: 10,
            q: None,
            visibility: None,
            membership: MembershipFilter::All,
            kind: GroupKindFilter::All,
            strategy: Some(V1GroupStrategy::StateMachine),
        })
        .await
        .expect("list groups");

    assert_eq!(page.total, 2);
    assert_eq!(page.items.len(), 2);
    match &page.items[0] {
        GroupSummary::Normal(summary) => {
            assert_eq!(summary.group_id, "both-state-machine");
            assert_eq!(summary.membership, Membership::Direct);
        }
        other => panic!("expected normal summary, got {other:?}"),
    }
    match &page.items[1] {
        GroupSummary::Normal(summary) => {
            assert_eq!(summary.group_id, "session-only-state-machine");
            assert_eq!(summary.membership, Membership::SessionOnly);
        }
        other => panic!("expected normal summary, got {other:?}"),
    }

    let session_only = fixture
        .service
        .list_groups(ListGroups {
            caller: bot_principal("target"),
            view_bot_id: Some("target".into()),
            offset: 0,
            limit: 1,
            q: None,
            visibility: None,
            membership: MembershipFilter::SessionOnly,
            kind: GroupKindFilter::All,
            strategy: None,
        })
        .await
        .expect("list session-only");
    assert_eq!(session_only.total, 1);
    assert_eq!(session_only.items.len(), 1);
}

#[tokio::test]
async fn list_groups_filters_by_visibility() {
    let fixture = Fixture::new().await;
    for bot in ["target", "driver"] {
        fixture.add_public_bot(bot).await;
    }

    for (group_id, visibility, created_at) in [
        ("public-newest", "public", 30),
        ("private-middle", "private", 20),
        ("public-oldest", "public", 10),
    ] {
        let mut group = normal_group(
            group_id,
            "driver",
            vec![
                Participant::bot("driver", ParticipantRole::Driver),
                Participant::bot("target", ParticipantRole::Consultant),
            ],
            GroupStrategy::Chat,
            created_at,
        );
        group.visibility = visibility.into();
        fixture.groups.upsert(group).await.expect("store Group");
    }

    let page = fixture
        .service
        .list_groups(ListGroups {
            caller: bot_principal("target"),
            view_bot_id: Some("target".into()),
            offset: 1,
            limit: 1,
            q: None,
            visibility: Some(GroupVisibility::Public),
            membership: MembershipFilter::Direct,
            kind: GroupKindFilter::All,
            strategy: None,
        })
        .await
        .expect("list public Groups");

    assert_eq!(page.total, 2);
    assert_eq!(page.items.len(), 1);
    match &page.items[0] {
        GroupSummary::Normal(summary) => assert_eq!(summary.group_id, "public-oldest"),
        other => panic!("expected normal summary, got {other:?}"),
    }
}

#[tokio::test]
async fn list_session_only_groups_filters_by_visibility() {
    let fixture = Fixture::new().await;
    for bot in ["target", "public-driver", "private-driver"] {
        fixture.add_public_bot(bot).await;
    }

    for (group_id, driver, visibility, created_at) in [
        ("public-session", "public-driver", "public", 20),
        ("private-session", "private-driver", "private", 10),
    ] {
        let mut group = normal_group(
            group_id,
            driver,
            vec![Participant::bot(driver, ParticipantRole::Driver)],
            GroupStrategy::Chat,
            created_at,
        );
        group.visibility = visibility.into();
        fixture.groups.upsert(group).await.expect("store Group");
        fixture
            .sessions
            .create_or_reactivate(CreateOrReactivateCommand {
                group_id: group_id.into(),
                session_id: None,
                params: NewSessionParams {
                    participants: vec![Participant::bot("target", ParticipantRole::Consultant)],
                    ..Default::default()
                },
            })
            .await
            .expect("create Session");
    }

    let page = fixture
        .service
        .list_groups(ListGroups {
            caller: bot_principal("target"),
            view_bot_id: Some("target".into()),
            offset: 0,
            limit: 20,
            q: None,
            visibility: Some(GroupVisibility::Private),
            membership: MembershipFilter::SessionOnly,
            kind: GroupKindFilter::All,
            strategy: None,
        })
        .await
        .expect("list private session-only Groups");

    assert_eq!(page.total, 1);
    assert_eq!(page.items.len(), 1);
    match &page.items[0] {
        GroupSummary::Normal(summary) => assert_eq!(summary.group_id, "private-session"),
        other => panic!("expected normal summary, got {other:?}"),
    }
}

#[tokio::test]
async fn list_defaults_to_the_authenticated_human_and_accepts_only_authorized_views() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("owned-by-someone-else").await;
    fixture
        .bots
        .register(
            "owned-by-alice".into(),
            BotCapabilities {
                visibility: "public".into(),
                ..Default::default()
            },
        )
        .await
        .expect("register Alice's Bot");
    fixture
        .bots
        .save_created_by("owned-by-alice", "alice", true)
        .await
        .expect("assign Alice's Bot ownership");
    fixture
        .groups
        .upsert(normal_group(
            "human-group",
            "owned-by-someone-else",
            vec![Participant::human("human_alice", ParticipantRole::Observer)],
            GroupStrategy::Chat,
            1,
        ))
        .await
        .expect("store Human Group");
    fixture
        .groups
        .upsert(normal_group(
            "owned-bot-group",
            "owned-by-someone-else",
            vec![Participant::bot(
                "owned-by-alice",
                ParticipantRole::Consultant,
            )],
            GroupStrategy::Chat,
            1,
        ))
        .await
        .expect("store owned Bot Group");

    let list = |view_bot_id: Option<&str>| ListGroups {
        caller: bot_principal("alice"),
        view_bot_id: view_bot_id.map(str::to_string),
        offset: 0,
        limit: 20,
        q: None,
        visibility: None,
        membership: MembershipFilter::All,
        kind: GroupKindFilter::All,
        strategy: None,
    };

    let default_view = fixture
        .service
        .list_groups(list(None))
        .await
        .expect("omission selects the Human Actor");
    assert_eq!(default_view.total, 1);
    let explicit_human = fixture
        .service
        .list_groups(list(Some("human_alice")))
        .await
        .expect("the caller's Human Actor is an explicit valid view");
    assert_eq!(explicit_human.total, 1);
    let owned_bot = fixture
        .service
        .list_groups(list(Some("owned-by-alice")))
        .await
        .expect("an exact-created_by Bot is an explicit valid view");
    assert_eq!(owned_bot.total, 1);

    for invalid_view in ["human_bob", "owned-by-someone-else", "unknown-bot"] {
        let error = fixture
            .service
            .list_groups(list(Some(invalid_view)))
            .await
            .expect_err("unauthorized View Actor must not fall back");
        assert!(matches!(error, ApplicationError::Forbidden(_)));
    }
}

#[tokio::test]
async fn group_detail_accepts_human_or_exact_owned_bot_participation_only() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "relation-only"] {
        fixture.add_public_bot(bot).await;
    }
    fixture
        .bots
        .register(
            "owned".into(),
            BotCapabilities {
                visibility: "public".into(),
                ..Default::default()
            },
        )
        .await
        .expect("register Alice's Bot");
    fixture
        .bots
        .save_created_by("owned", "alice", true)
        .await
        .expect("assign Alice's Bot ownership");
    fixture
        .relation
        .upsert_edge(bcs_service_api::RelationEdge {
            from_id: "human_alice".into(),
            to_id: "relation-only".into(),
            env: "dev".into(),
            kinds: 0,
            allow: 0,
            deny: 0,
            is_creator: true,
        })
        .await
        .expect("seed legacy creator relation");

    for (group_id, participant) in [
        (
            "human-detail",
            Participant::human("human_alice", ParticipantRole::Observer),
        ),
        (
            "owned-detail",
            Participant::bot("owned", ParticipantRole::Consultant),
        ),
        (
            "relation-detail",
            Participant::bot("relation-only", ParticipantRole::Consultant),
        ),
    ] {
        fixture
            .groups
            .upsert(normal_group(
                group_id,
                "driver",
                vec![participant],
                GroupStrategy::Chat,
                1,
            ))
            .await
            .expect("store Group");
    }

    for group_id in ["human-detail", "owned-detail"] {
        fixture
            .service
            .get(GetGroup {
                caller: bot_principal("alice"),
                group_id: group_id.into(),
            })
            .await
            .expect("direct Human or exact owned Bot participation grants detail read");
    }
    let error = fixture
        .service
        .get(GetGroup {
            caller: bot_principal("alice"),
            group_id: "relation-detail".into(),
        })
        .await
        .expect_err("creator relation alone is not Bot ownership");
    assert!(matches!(error, ApplicationError::Forbidden(_)));
}

#[tokio::test]
async fn get_public_group_readable_without_participation() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;
    let mut group = normal_group(
        "public-plaza-detail",
        "driver",
        vec![Participant::bot("driver", ParticipantRole::Driver)],
        GroupStrategy::Chat,
        1,
    );
    group.visibility = "public".to_string();
    fixture
        .groups
        .upsert(group)
        .await
        .expect("store public Group");

    let detail = fixture
        .service
        .get(GetGroup {
            caller: bot_principal("alice"),
            group_id: "public-plaza-detail".into(),
        })
        .await
        .expect("public Group is readable without participation or owned Bot");
    let GroupDetail::Collaboration(detail) = detail else {
        panic!("expected collaboration detail");
    };
    assert_eq!(detail.group_id, "public-plaza-detail");
    assert!(matches!(detail.visibility, GroupVisibility::Public));
}

#[tokio::test]
async fn group_detail_propagates_owned_bot_lookup_database_failure() {
    let bots = Arc::new(BotCore::with_repo(Arc::new(
        PersistentBotRepo::with_plugins(Arc::new(InMemoryCachePlugin::new()), Arc::new(FailingDb)),
    )));
    let fixture = Fixture::new_with_bots(bots).await;
    fixture
        .groups
        .upsert(normal_group(
            "owned-bot-lookup-failure",
            "driver",
            vec![Participant::bot("owned", ParticipantRole::Consultant)],
            GroupStrategy::Chat,
            1,
        ))
        .await
        .expect("store Group");

    let error = fixture
        .service
        .get(GetGroup {
            caller: bot_principal("alice"),
            group_id: "owned-bot-lookup-failure".into(),
        })
        .await
        .expect_err("owned-Bot lookup failure must not be reported as forbidden");

    assert!(matches!(
        error,
        ApplicationError::Internal(message) if message.contains("bot database unavailable")
    ));
}

#[tokio::test]
async fn list_groups_sorts_by_created_at_desc_not_updated_at() {
    // V1 contract declares `created_at DESC, group_id ASC`. The legacy
    // `updated_at DESC` ordering would put a recently-edited but older group
    // first, violating the contract. Seed three groups where the sort keys
    // diverge to prove the V1 facade uses `created_at` (with `group_id` as
    // the deterministic tie-breaker).
    let fixture = Fixture::new().await;
    for bot in ["target", "driver-a", "driver-b", "driver-c"] {
        fixture.add_public_bot(bot).await;
    }

    // Group A: oldest by created_at, but most recently edited.
    let mut group_a = normal_group(
        "group-a",
        "driver-a",
        vec![
            Participant::bot("driver-a", ParticipantRole::Driver),
            Participant::bot("target", ParticipantRole::Consultant),
        ],
        GroupStrategy::Chat,
        500,
    );
    group_a.created_at = 100;
    fixture.groups.upsert(group_a).await.expect("store group-a");

    // Group B: newer by created_at, older by updated_at.
    let mut group_b = normal_group(
        "group-b",
        "driver-b",
        vec![
            Participant::bot("driver-b", ParticipantRole::Driver),
            Participant::bot("target", ParticipantRole::Consultant),
        ],
        GroupStrategy::Chat,
        200,
    );
    group_b.created_at = 300;
    fixture.groups.upsert(group_b).await.expect("store group-b");

    // Group C: ties Group B on created_at, lower group_id; proves the
    // `group_id ASC` tie-breaker keeps B before C.
    let mut group_c = normal_group(
        "group-c",
        "driver-c",
        vec![
            Participant::bot("driver-c", ParticipantRole::Driver),
            Participant::bot("target", ParticipantRole::Consultant),
        ],
        GroupStrategy::Chat,
        50,
    );
    group_c.created_at = 300;
    fixture.groups.upsert(group_c).await.expect("store group-c");

    let page = fixture
        .service
        .list_groups(ListGroups {
            caller: bot_principal("target"),
            view_bot_id: Some("target".into()),
            offset: 0,
            limit: 10,
            q: None,
            visibility: None,
            membership: MembershipFilter::All,
            kind: GroupKindFilter::All,
            strategy: None,
        })
        .await
        .expect("list groups");

    assert_eq!(page.total, 3);
    assert_eq!(page.items.len(), 3);
    // created_at DESC (group-b & group-c at 300 before group-a at 100), then
    // group_id ASC tie-breaker (group-b before group-c).
    let ids = page
        .items
        .iter()
        .map(|summary| match summary {
            GroupSummary::Normal(it) => it.group_id.as_str(),
            other => panic!("expected normal summary, got {other:?}"),
        })
        .collect::<Vec<_>>();
    assert_eq!(ids, vec!["group-b", "group-c", "group-a"]);
}

#[tokio::test]
async fn create_uses_the_authenticated_human_as_originator() {
    let fixture = Fixture::new().await;
    for bot in ["requester", "driver", "helper"] {
        fixture.add_public_bot(bot).await;
    }

    let detail = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("requester"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                originator: None,
                name: Some("Planning".into()),
                context: Some("Plan the release".into()),
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "helper".into(),
                    role: ParticipantRole::Consultant,
                    tags: vec![
                        " tenant-a ".to_string(),
                        "".to_string(),
                        "scene-review".to_string(),
                    ],
                }],
                collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
                    delivery_policy: GroupDeliveryPolicy {
                        bot_final_delivery: BotFinalDelivery::SendToDriver,
                    },
                }),
            }),
        })
        .await
        .expect("requester may select a collaboration-eligible driver");

    let GroupDetail::Collaboration(detail) = detail else {
        panic!("expected collaboration detail");
    };
    assert_eq!(detail.driver_bot_uuid, "driver");
    assert_eq!(detail.originator_actor_id, "human_requester");
    assert_eq!(
        detail
            .participants
            .iter()
            .find(|participant| participant.actor_id == "helper")
            .expect("helper participant")
            .tags,
        vec!["tenant-a".to_string(), "scene-review".to_string()]
    );
    assert!(
        detail
            .participants
            .iter()
            .all(|p| p.actor_id != "requester")
    );
}

#[tokio::test]
async fn inline_subscription_create_uses_provisioning_and_finalizes_initial_session() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;
    let provisioner = Arc::new(RecordingGroupProvisioner::new(fixture.groups.clone()));
    let fixture = fixture.with_event_subscription_provisioner(provisioner.clone());

    let outcome = fixture
        .service
        .create_with_event_subscriptions(
            collaboration_create_command(),
            vec![inline_group_subscription()],
        )
        .await
        .expect("create Group with inline Subscription");

    assert!(outcome.created);
    let group_id = provisioner
        .prepared_group_ids
        .lock()
        .expect("provisioner lock")[0]
        .clone();
    assert_eq!(
        provisioner
            .finalized_group_ids
            .lock()
            .expect("provisioner lock")
            .as_slice(),
        [group_id.as_str()]
    );
    let group = fixture
        .groups
        .try_get(&group_id)
        .await
        .expect("load Group")
        .expect("created Group");
    assert_eq!(group.record_status, "active");
    let sessions = fixture
        .sessions
        .list_by_group(&group_id, None, 0, 10, None, None)
        .await
        .expect("list initial Sessions");
    assert_eq!(sessions.len(), 1);
}

#[tokio::test]
async fn eventing_enabled_create_without_inline_subscription_still_finalizes_events() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;
    let provisioner = Arc::new(RecordingGroupProvisioner::new(fixture.groups.clone()));
    let fixture = fixture.with_event_subscription_provisioner(provisioner.clone());

    fixture
        .service
        .create(collaboration_create_command())
        .await
        .expect("create Group without an inline Subscription");

    let prepared = provisioner
        .prepared_group_ids
        .lock()
        .expect("provisioner lock");
    let finalized = provisioner
        .finalized_group_ids
        .lock()
        .expect("provisioner lock");
    assert_eq!(prepared.len(), 1);
    assert_eq!(finalized.as_slice(), prepared.as_slice());
}

#[tokio::test]
async fn inline_subscription_dm_create_finalizes_without_an_initial_session() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("assistant").await;
    let provisioner = Arc::new(RecordingGroupProvisioner::new(fixture.groups.clone()));
    let fixture = fixture.with_event_subscription_provisioner(provisioner.clone());

    let outcome = fixture
        .service
        .create_with_event_subscriptions(dm_create_command(), vec![inline_group_subscription()])
        .await
        .expect("create DM with inline Subscription");

    assert!(outcome.created);
    let group_id = provisioner
        .prepared_group_ids
        .lock()
        .expect("provisioner lock")[0]
        .clone();
    assert_eq!(
        fixture
            .groups
            .try_get(&group_id)
            .await
            .expect("load DM")
            .expect("created DM")
            .record_status,
        "active"
    );
    assert!(
        fixture
            .sessions
            .list_by_group(&group_id, None, 0, 10, None, None)
            .await
            .expect("list DM Sessions")
            .is_empty()
    );
}

#[tokio::test]
async fn inline_subscription_dm_reuse_cancels_the_new_pending_set() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("assistant").await;
    let existing = fixture
        .service
        .create_with_outcome(dm_create_command())
        .await
        .expect("create canonical DM");
    assert!(existing.created);
    let provisioner = Arc::new(RecordingGroupProvisioner::new(fixture.groups.clone()));
    let fixture = fixture.with_event_subscription_provisioner(provisioner.clone());

    let reused = fixture
        .service
        .create_with_event_subscriptions(dm_create_command(), vec![inline_group_subscription()])
        .await
        .expect("reuse canonical DM");

    assert!(!reused.created);
    assert!(
        provisioner
            .finalized_group_ids
            .lock()
            .expect("provisioner lock")
            .is_empty()
    );
    assert_eq!(
        provisioner
            .cancelled_group_ids
            .lock()
            .expect("provisioner lock")
            .len(),
        1
    );
}

#[tokio::test]
async fn inline_subscription_validation_failure_creates_no_group_or_session() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;
    let provisioner = Arc::new(RecordingGroupProvisioner::new(fixture.groups.clone()));
    provisioner.fail_prepare.store(true, Ordering::SeqCst);
    let fixture = fixture.with_event_subscription_provisioner(provisioner.clone());

    let error = fixture
        .service
        .create_with_event_subscriptions(
            collaboration_create_command(),
            vec![inline_group_subscription()],
        )
        .await
        .expect_err("Subscription validation must fail before Group creation");
    assert_eq!(error.code(), "invalid_event_filter");
    let group_id = provisioner
        .prepared_group_ids
        .lock()
        .expect("provisioner lock")[0]
        .clone();
    assert!(fixture.groups.try_get(&group_id).await.unwrap().is_none());
    assert!(
        fixture
            .sessions
            .list_by_group(&group_id, None, 0, 10, None, None)
            .await
            .expect("list Sessions")
            .is_empty()
    );
}

#[tokio::test]
async fn inline_subscription_finalization_failure_compensates_group_session_and_pending_set() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;
    let provisioner = Arc::new(RecordingGroupProvisioner::new(fixture.groups.clone()));
    provisioner.fail_finalize.store(true, Ordering::SeqCst);
    let fixture = fixture.with_event_subscription_provisioner(provisioner.clone());

    let error = fixture
        .service
        .create_with_event_subscriptions(
            collaboration_create_command(),
            vec![inline_group_subscription()],
        )
        .await
        .expect_err("finalization failure must fail Group creation");
    assert!(error.to_string().contains("test finalization failure"));
    let group_id = provisioner
        .prepared_group_ids
        .lock()
        .expect("provisioner lock")[0]
        .clone();
    assert!(fixture.groups.try_get(&group_id).await.unwrap().is_none());
    assert!(
        fixture
            .sessions
            .list_by_group(&group_id, None, 0, 10, None, None)
            .await
            .expect("list Sessions")
            .is_empty()
    );
    assert_eq!(
        provisioner
            .cancelled_group_ids
            .lock()
            .expect("provisioner lock")
            .as_slice(),
        [group_id.as_str()]
    );
}

#[tokio::test]
async fn provisioning_groups_are_hidden_from_v1_reads_and_lists() {
    let fixture = Fixture::new().await;
    let mut group = normal_group(
        "hidden-provisioning",
        "driver",
        vec![Participant::human("human_alice", ParticipantRole::Observer)],
        GroupStrategy::Chat,
        1,
    );
    group.record_status = "provisioning".to_string();
    fixture.groups.upsert(group).await.expect("seed Group");

    let error = fixture
        .service
        .get(GetGroup {
            caller: bot_principal("alice"),
            group_id: "hidden-provisioning".to_string(),
        })
        .await
        .expect_err("provisioning Group must be hidden");
    assert_eq!(error.code(), "group_not_found");
    let page = fixture
        .service
        .list_groups(ListGroups {
            caller: bot_principal("alice"),
            view_bot_id: None,
            offset: 0,
            limit: 10,
            q: None,
            visibility: None,
            membership: MembershipFilter::All,
            kind: GroupKindFilter::All,
            strategy: None,
        })
        .await
        .expect("list Groups");
    assert_eq!(page.total, 0);
}

#[tokio::test]
async fn provisioning_reconciler_completes_a_crash_interrupted_group() {
    let fixture = Fixture::new().await;
    let provisioner = Arc::new(RecordingGroupProvisioner::new(fixture.groups.clone()));
    let mut group = Group::new(
        "crash-interrupted",
        "driver",
        vec![Participant::bot("driver", ParticipantRole::Driver)],
    );
    group.label = Some("Recovered".to_string());
    group.record_status = "provisioning".to_string();
    fixture
        .groups
        .upsert(group.clone())
        .await
        .expect("seed provisioning Group");
    fixture
        .sessions
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: group.id.clone(),
            session_id: None,
            params: NewSessionParams {
                participants: group.participants.clone(),
                created_by: Some("human_alice".to_string()),
                ..NewSessionParams::default()
            },
        })
        .await
        .expect("seed initial Session");
    let reconciler = GroupProvisioningReconciler::new(
        fixture.groups.clone(),
        fixture.sessions.clone(),
        None,
        provisioner,
    );

    let outcome = reconciler.reconcile_once(u64::MAX, 0).await;

    assert_eq!(outcome.finalized, 1);
    assert_eq!(outcome.compensated, 0);
    assert_eq!(outcome.deferred, 0);
    assert_eq!(
        fixture
            .groups
            .try_get(&group.id)
            .await
            .expect("load Group")
            .expect("recovered Group")
            .record_status,
        "active"
    );
}

#[tokio::test]
async fn provisioning_reconciler_compensates_an_incomplete_group() {
    let fixture = Fixture::new().await;
    let provisioner = Arc::new(RecordingGroupProvisioner::new(fixture.groups.clone()));
    let mut group = Group::new(
        "crash-incomplete",
        "driver",
        vec![Participant::bot("driver", ParticipantRole::Driver)],
    );
    group.record_status = "provisioning".to_string();
    fixture
        .groups
        .upsert(group.clone())
        .await
        .expect("seed incomplete Group");
    let reconciler = GroupProvisioningReconciler::new(
        fixture.groups.clone(),
        fixture.sessions.clone(),
        None,
        provisioner.clone(),
    );

    let outcome = reconciler.reconcile_once(u64::MAX, 0).await;

    assert_eq!(outcome.finalized, 0);
    assert_eq!(outcome.compensated, 1);
    assert!(fixture.groups.try_get(&group.id).await.unwrap().is_none());
    assert_eq!(
        provisioner
            .cancelled_group_ids
            .lock()
            .expect("provisioner lock")
            .as_slice(),
        [group.id.as_str()]
    );
}

#[tokio::test]
async fn provisioning_reconciler_compensates_state_machine_without_runtime_state() {
    let fixture = Fixture::new().await;
    let provisioner = Arc::new(RecordingGroupProvisioner::new(fixture.groups.clone()));
    let mut group = Group::new(
        "crash-state-machine",
        "driver",
        vec![Participant::bot("driver", ParticipantRole::Driver)],
    );
    group.group_strategy = GroupStrategy::StateMachine;
    group.record_status = "provisioning".to_string();
    fixture
        .groups
        .upsert(group.clone())
        .await
        .expect("seed provisioning Group");
    fixture
        .sessions
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: group.id.clone(),
            session_id: None,
            params: NewSessionParams {
                participants: group.participants.clone(),
                created_by: Some("human_alice".to_string()),
                ..NewSessionParams::default()
            },
        })
        .await
        .expect("seed initial Session");
    let reconciler = GroupProvisioningReconciler::new(
        fixture.groups.clone(),
        fixture.sessions.clone(),
        None,
        provisioner.clone(),
    );

    let outcome = reconciler.reconcile_once(u64::MAX, 0).await;

    assert_eq!(outcome.finalized, 0);
    assert_eq!(outcome.compensated, 1);
    assert_eq!(outcome.deferred, 0);
    assert!(fixture.groups.try_get(&group.id).await.unwrap().is_none());
}

#[tokio::test]
async fn provisioning_reconciler_cancels_only_stale_orphaned_pending_sets() {
    let fixture = Fixture::new().await;
    let provisioner = Arc::new(RecordingGroupProvisioner::new(fixture.groups.clone()));
    let recovery_actor = EventActor {
        actor_type: EventActorType::System,
        id: "test-reconciler".to_string(),
        display_name: None,
    };
    provisioner
        .pending_groups
        .lock()
        .expect("provisioner lock")
        .extend([
            PendingGroupEventSubscriptions {
                prepared: PreparedGroupEventSubscriptions {
                    group_id: "orphan-stale".to_string(),
                    subscription_ids: vec!["sub-stale".to_string()],
                    actor: recovery_actor.clone(),
                },
                created_at_ms: 100,
            },
            PendingGroupEventSubscriptions {
                prepared: PreparedGroupEventSubscriptions {
                    group_id: "orphan-in-flight".to_string(),
                    subscription_ids: vec!["sub-in-flight".to_string()],
                    actor: recovery_actor,
                },
                created_at_ms: 250,
            },
        ]);
    let reconciler = GroupProvisioningReconciler::new(
        fixture.groups.clone(),
        fixture.sessions.clone(),
        None,
        provisioner.clone(),
    );

    let outcome = reconciler.reconcile_once(300, 100).await;

    assert_eq!(outcome.finalized, 0);
    assert_eq!(outcome.compensated, 1);
    assert_eq!(outcome.deferred, 0);
    assert_eq!(
        provisioner
            .cancelled_group_ids
            .lock()
            .expect("provisioner lock")
            .as_slice(),
        ["orphan-stale"]
    );
}

#[tokio::test]
async fn human_participant_can_create_with_driver_reachable_protected_participants() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;
    fixture.add_protected_bot("helper").await;
    fixture
        .friends
        .add_friendship("driver", "helper")
        .await
        .expect("driver/helper friendship");

    let detail = fixture
        .service
        .create(CreateGroup {
            caller: human_principal_with_profile("staff-1", "alice-login", Some("Alice"), None),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                originator: None,
                name: Some("Protected collaboration".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![
                    CreateParticipant {
                        actor_id: "human_staff-1".into(),
                        role: ParticipantRole::Observer,
                        tags: Vec::new(),
                    },
                    CreateParticipant {
                        actor_id: "helper".into(),
                        role: ParticipantRole::Consultant,
                        tags: Vec::new(),
                    },
                ],
                collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
                    delivery_policy: GroupDeliveryPolicy {
                        bot_final_delivery: BotFinalDelivery::SendToDriver,
                    },
                }),
            }),
        })
        .await
        .expect("V1 uses driver collaboration reachability");

    let GroupDetail::Collaboration(detail) = detail else {
        panic!("expected collaboration detail");
    };
    assert_eq!(detail.originator_actor_id, "human_staff-1");
    let human = fixture
        .bots
        .get("human_staff-1")
        .await
        .expect("V1 normal Group creation must materialize the Human participant");
    assert_eq!(human.capabilities.name.as_deref(), Some("Alice"));
}

#[tokio::test]
async fn dm_create_uses_target_actor_id_and_projects_two_symmetric_participants() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("bot-a").await;
    fixture.add_public_bot("bot-b").await;

    let detail = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("bot-a"),
            group: CreateGroupSpec::DirectMessage(CreateDirectMessageGroup {
                name: Some("A and B".into()),
                context: None,
                target_actor_id: "bot-b".into(),
            }),
        })
        .await
        .expect("create DM");

    let GroupDetail::DirectMessage(detail) = detail else {
        panic!("expected DM detail");
    };
    assert_eq!(detail.participants.len(), 2);
    assert!(
        detail
            .participants
            .iter()
            .any(|p| p.actor_id == "human_bot-a")
    );
    assert!(detail.participants.iter().any(|p| p.actor_id == "bot-b"));
    let json = serde_json::to_value(&detail).expect("serialize DM detail");
    assert!(json.get("driver_bot_uuid").is_none());
    assert!(json.get("strategy").is_none());
    assert!(json.get("dm_pair_key").is_none());
}

#[tokio::test]
async fn dm_kind_rejects_a_strategy_filter() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("target").await;

    let result = fixture
        .service
        .list_groups(ListGroups {
            caller: bot_principal("target"),
            view_bot_id: Some("target".into()),
            offset: 0,
            limit: 20,
            q: None,
            visibility: None,
            membership: MembershipFilter::All,
            kind: GroupKindFilter::Dm,
            strategy: Some(V1GroupStrategy::Chat),
        })
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::InvalidInput { code, .. }) if code == "invalid_request"
    ));
}

#[tokio::test]
async fn explicit_view_requires_the_target_bot_to_exist_and_be_owned() {
    let fixture = Fixture::new().await;

    let result = fixture
        .service
        .list_groups(ListGroups {
            caller: bot_principal("missing"),
            view_bot_id: Some("missing".into()),
            offset: 0,
            limit: 20,
            q: None,
            visibility: None,
            membership: MembershipFilter::All,
            kind: GroupKindFilter::Normal,
            strategy: None,
        })
        .await;

    assert!(matches!(result, Err(ApplicationError::Forbidden(_))));
}

#[tokio::test]
async fn explicit_view_propagates_registry_database_failure() {
    let bots = Arc::new(BotCore::with_repo(Arc::new(
        PersistentBotRepo::with_plugins(Arc::new(InMemoryCachePlugin::new()), Arc::new(FailingDb)),
    )));
    let fixture = Fixture::new_with_bots(bots).await;

    let result = fixture
        .service
        .list_groups(ListGroups {
            caller: bot_principal("stored-bot"),
            view_bot_id: Some("stored-bot".into()),
            offset: 0,
            limit: 20,
            q: None,
            visibility: None,
            membership: MembershipFilter::All,
            kind: GroupKindFilter::Normal,
            strategy: None,
        })
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::Internal(message))
            if message.contains("bot database unavailable")
    ));
}

#[tokio::test]
async fn create_group_propagates_quota_lookup_database_failure() {
    let fixture = Fixture::new_with_failing_group_store().await;
    fixture.add_public_bot("driver").await;

    let result = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                originator: None,
                driver_bot_uuid: "driver".into(),
                name: Some("quota lookup failure".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                participants: Vec::new(),
                collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
                    delivery_policy: GroupDeliveryPolicy {
                        bot_final_delivery: BotFinalDelivery::SendToDriver,
                    },
                }),
            }),
        })
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::Internal(message))
            if message.contains("find Groups for participant")
    ));
}

#[tokio::test]
async fn create_group_propagates_non_driver_registry_database_failure() {
    let bots = Arc::new(BotCore::with_repo(Arc::new(
        PersistentBotRepo::with_plugins(
            Arc::new(InMemoryCachePlugin::new()),
            Arc::new(DriverThenFailingDb::default()),
        ),
    )));
    let fixture = Fixture::new_with_bots(bots).await;

    let result = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                originator: None,
                driver_bot_uuid: "driver".into(),
                name: Some("registry failure".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                participants: vec![CreateParticipant {
                    actor_id: "helper".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                }],
                collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
                    delivery_policy: GroupDeliveryPolicy {
                        bot_final_delivery: BotFinalDelivery::SendToDriver,
                    },
                }),
            }),
        })
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::Internal(message))
            if message.contains("participant registry unavailable")
    ));
}

#[tokio::test]
async fn bot_dm_propagates_caller_registry_failure_after_target_validation() {
    let bots = Arc::new(BotCore::with_repo(Arc::new(
        PersistentBotRepo::with_plugins(
            Arc::new(InMemoryCachePlugin::new()),
            Arc::new(DriverThenFailingDb::default()),
        ),
    )));
    let fixture = Fixture::new_with_bots(bots).await;

    let result = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("caller"),
            group: CreateGroupSpec::DirectMessage(CreateDirectMessageGroup {
                name: None,
                context: None,
                target_actor_id: "target".into(),
            }),
        })
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::Internal(message))
            if message.contains("participant registry unavailable")
    ));
}

#[tokio::test]
async fn bot_dm_propagates_friendship_failure_after_initial_validation() {
    let friends = Arc::new(FriendCore::with_repo(Arc::new(
        FirstFriendCheckThenFailingRepo::default(),
    )));
    let fixture = Fixture::new_with_friends(friends).await;
    fixture.add_public_bot("caller").await;
    fixture.add_protected_bot("target").await;

    let result = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("caller"),
            group: CreateGroupSpec::DirectMessage(CreateDirectMessageGroup {
                name: None,
                context: None,
                target_actor_id: "target".into(),
            }),
        })
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::Internal(message))
            if message.contains("friend store unavailable after validation")
    ));
}

#[tokio::test]
async fn create_rejects_non_bot_driver_and_dm_target_with_declared_code() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("requester").await;
    fixture
        .bots
        .ensure_human_actor("staff-1", "Alice")
        .await
        .expect("register Human actor");

    let driver = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("requester"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                originator: None,
                name: None,
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "human_staff-1".into(),
                participants: Vec::new(),
                collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
                    delivery_policy: GroupDeliveryPolicy {
                        bot_final_delivery: BotFinalDelivery::SendToDriver,
                    },
                }),
            }),
        })
        .await;
    assert!(matches!(
        driver,
        Err(ApplicationError::InvalidInput { code, message })
            if code == "invalid_participant"
                && message.contains("driver_bot_uuid")
    ));

    let target = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("requester"),
            group: CreateGroupSpec::DirectMessage(CreateDirectMessageGroup {
                name: None,
                context: None,
                target_actor_id: "human_staff-1".into(),
            }),
        })
        .await;
    assert!(matches!(
        target,
        Err(ApplicationError::InvalidInput { code, message })
            if code == "invalid_participant"
                && message.contains("target_actor_id")
    ));
}

#[tokio::test]
async fn state_machine_create_without_runtime_fails_before_persisting_group() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;
    fixture.add_public_bot("worker").await;

    let result = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                originator: None,
                name: Some("State machine".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinition::Reference(
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "definition-1".into(),
                                    version: 1,
                                },
                            ),
                        participant_bindings: vec![
                            bcs_service_api::application::v1::StateMachineParticipantBinding {
                                binding: "worker".into(),
                                actor_ids: vec!["worker".into()],
                            },
                        ],
                    },
                ),
            }),
        })
        .await;

    assert!(matches!(result, Err(ApplicationError::Internal(_))));
    assert_eq!(fixture.groups.count().await, 0);
}

#[tokio::test]
async fn state_machine_create_rejects_duplicate_participant_binding_names() {
    let runtime = Arc::new(RecordingRuntime::default());
    let fixture = Fixture::new_with_runtime(runtime.clone()).await;
    fixture.add_public_bot("driver").await;
    fixture.add_public_bot("worker").await;

    let result = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                originator: None,
                name: Some("State machine".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinition::Reference(
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "definition-1".into(),
                                    version: 1,
                                },
                            ),
                        participant_bindings: vec![
                            bcs_service_api::application::v1::StateMachineParticipantBinding {
                                binding: "worker".into(),
                                actor_ids: vec!["worker".into()],
                            },
                            bcs_service_api::application::v1::StateMachineParticipantBinding {
                                binding: "worker".into(),
                                actor_ids: vec!["driver".into()],
                            },
                        ],
                    },
                ),
            }),
        })
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::InvalidInput { code, .. })
            if code == "invalid_participant_binding"
    ));
    assert_eq!(fixture.groups.count().await, 0);
    assert!(runtime.configured.lock().expect("runtime lock").is_none());
}

#[tokio::test]
async fn state_machine_runtime_failure_rolls_back_created_group() {
    let runtime = Arc::new(RecordingRuntime::default());
    *runtime.configure_error.lock().expect("runtime lock") =
        Some("invalid runtime configuration".to_string());
    let fixture = Fixture::new_with_runtime(runtime).await;
    fixture.add_public_bot("driver").await;
    fixture.add_public_bot("worker").await;

    let result = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                originator: None,
                name: Some("State machine".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinition::Reference(
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "definition-1".into(),
                                    version: 1,
                                },
                            ),
                        participant_bindings: vec![],
                    },
                ),
            }),
        })
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::InvalidInput { code, .. }) if code == "invalid_request"
    ));
    assert_eq!(fixture.groups.count().await, 0);
}

#[tokio::test]
async fn state_machine_create_configures_runtime_and_returns_typed_detail() {
    let runtime = Arc::new(RecordingRuntime::default());
    let fixture = Fixture::new_with_runtime(runtime.clone()).await;
    fixture.add_public_bot("driver").await;
    fixture.add_public_bot("worker").await;

    let detail = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                originator: None,
                name: Some("State machine".into()),
                context: Some("Execute the workflow".into()),
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinition::Reference(
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "definition-1".into(),
                                    version: 3,
                                },
                            ),
                        participant_bindings: vec![
                            bcs_service_api::application::v1::StateMachineParticipantBinding {
                                binding: "worker".into(),
                                actor_ids: vec!["worker".into()],
                            },
                        ],
                    },
                ),
            }),
        })
        .await
        .expect("create state-machine Group");

    let GroupDetail::Collaboration(detail) = detail else {
        panic!("expected collaboration detail");
    };
    let CollaborationConfiguration::StateMachine(collaboration) = detail.collaboration else {
        panic!("expected state-machine collaboration");
    };
    match &collaboration.definition {
        bcs_service_api::application::v1::StateMachineDefinition::Reference(reference) => {
            assert_eq!(reference.definition_id, "definition-1");
            assert_eq!(reference.version, 3);
        }
        other => panic!("expected definition reference, got {other:?}"),
    }
    assert_eq!(collaboration.participant_bindings[0].binding, "worker");
    assert_eq!(
        collaboration.participant_bindings[0].actor_ids,
        vec!["worker"]
    );

    {
        let configured = runtime.configured.lock().expect("runtime lock");
        let configured = configured.as_ref().expect("configured runtime");
        assert!(configured.auto_start_on_service_invocation);
        assert_eq!(
            configured
                .definition_ref
                .as_ref()
                .expect("definition ref")
                .id,
            "definition-1"
        );
        assert_eq!(
            configured
                .participant_bindings
                .get("worker")
                .expect("worker binding")
                .bot_ids,
            vec!["worker"]
        );
        assert_eq!(
            configured
                .participant_bindings
                .get("worker")
                .expect("worker binding")
                .source,
            "manual"
        );
    }

    let started = runtime.started.lock().expect("runtime lock");
    assert_eq!(started.len(), 1);
    assert_eq!(started[0].group_id, detail.group_id);
    assert!(started[0].session_id.is_some());
    assert_eq!(started[0].caller_id.as_deref(), Some("human_driver"));
}

#[tokio::test]
async fn state_machine_create_with_inline_yaml_returns_persisted_definition_ref() {
    let runtime = Arc::new(RecordingRuntime::default());
    let fixture = Fixture::new_with_runtime(runtime.clone()).await;
    fixture.add_public_bot("driver").await;
    fixture.add_public_bot("worker").await;

    let detail = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                originator: None,
                name: Some("State machine".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinition::Content(
                                bcs_service_api::application::v1::StateMachineDefinitionContent {
                                    content_yaml: "version: 1\n".into(),
                                },
                            ),
                        participant_bindings: Vec::new(),
                    },
                ),
            }),
        })
        .await
        .expect("create state-machine Group from inline YAML");

    let GroupDetail::Collaboration(detail) = detail else {
        panic!("expected collaboration detail");
    };
    let CollaborationConfiguration::StateMachine(collaboration) = detail.collaboration else {
        panic!("expected state-machine collaboration");
    };
    match collaboration.definition {
        bcs_service_api::application::v1::StateMachineDefinition::Reference(reference) => {
            assert_eq!(reference.definition_id, "generated-definition");
            assert_eq!(reference.version, 1);
        }
        other => panic!("expected persisted definition reference, got {other:?}"),
    }

    let configured = runtime.configured.lock().expect("runtime lock");
    let configured = configured.as_ref().expect("configured runtime");
    assert_eq!(configured.definition_yaml.as_deref(), Some("version: 1\n"));
    assert!(configured.definition_ref.is_none());
}

#[tokio::test]
async fn state_machine_create_defers_initial_run_until_required_channel_is_bound() {
    let runtime = Arc::new(RecordingRuntime {
        requires_human_input_channel: true,
        ..Default::default()
    });
    let fixture = Fixture::new_with_runtime(runtime.clone()).await;
    for bot in ["driver", "worker"] {
        fixture.add_public_bot(bot).await;
    }

    let detail = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                originator: None,
                name: Some("Human review".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinition::Reference(
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "bot-human-bot-review".into(),
                                    version: 1,
                                },
                            ),
                        participant_bindings: Vec::new(),
                    },
                ),
            }),
        })
        .await
        .expect("create channel-backed StateMachine Group");

    assert!(matches!(detail, GroupDetail::Collaboration(_)));
    assert!(runtime.started.lock().expect("runtime lock").is_empty());
    assert_eq!(fixture.groups.count().await, 1);
}

#[tokio::test]
async fn state_machine_create_rejects_human_actors_in_bot_bindings() {
    let runtime = Arc::new(RecordingRuntime::default());
    let fixture = Fixture::new_with_runtime(runtime.clone()).await;
    fixture.add_public_bot("driver").await;
    fixture
        .bots
        .ensure_human_actor("staff-1", "Alice")
        .await
        .expect("register human actor");

    let result = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                originator: None,
                name: Some("State machine".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "human_staff-1".into(),
                    role: ParticipantRole::Observer,
                    tags: Vec::new(),
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinition::Reference(
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "definition-1".into(),
                                    version: 1,
                                },
                            ),
                        participant_bindings: vec![
                            bcs_service_api::application::v1::StateMachineParticipantBinding {
                                binding: "worker".into(),
                                actor_ids: vec!["human_staff-1".into()],
                            },
                        ],
                    },
                ),
            }),
        })
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::InvalidInput { code, .. })
            if code == "invalid_participant_binding"
    ));
    assert_eq!(fixture.groups.count().await, 0);
    assert!(runtime.configured.lock().expect("runtime lock").is_none());
}

#[tokio::test]
async fn state_machine_create_preserves_authenticated_human_in_audit_and_start() {
    let runtime = Arc::new(RecordingRuntime::default());
    let fixture = Fixture::new_with_runtime(runtime.clone()).await;
    fixture.add_public_bot("driver").await;
    fixture.add_public_bot("worker").await;

    let detail = fixture
        .service
        .create(CreateGroup {
            caller: human_principal_with_profile("staff-1", "alice", Some("Alice"), None),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                originator: None,
                name: Some("State machine".into()),
                context: Some("Review the release".into()),
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinition::Reference(
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "definition-1".into(),
                                    version: 1,
                                },
                            ),
                        participant_bindings: Vec::new(),
                    },
                ),
            }),
        })
        .await
        .expect("create state-machine group");
    let GroupDetail::Collaboration(detail) = detail else {
        panic!("expected collaboration detail");
    };
    assert_eq!(detail.originator_actor_id, "human_staff-1");

    let sessions = fixture
        .sessions
        .list_by_group(&detail.group_id, None, 0, 10, None, None)
        .await
        .expect("list initial sessions");
    assert_eq!(sessions.len(), 1);
    assert_eq!(
        sessions[0].caller_principal.as_deref(),
        Some("human_staff-1")
    );

    let started = runtime.started.lock().expect("runtime lock");
    assert_eq!(started.len(), 1);
    assert_eq!(started[0].caller_id.as_deref(), Some("human_staff-1"));
    assert_eq!(
        started[0]
            .authenticated_human
            .as_ref()
            .map(|human| (human.actor_id.as_str(), human.display_name.as_deref())),
        Some(("human_staff-1", Some("Alice")))
    );
}

#[tokio::test]
async fn state_machine_create_does_not_reread_runtime_for_its_response() {
    let runtime = Arc::new(RecordingRuntime::default());
    *runtime
        .projection_error
        .lock()
        .expect("projection error lock") = Some("transient projection failure".into());
    let fixture = Fixture::new_with_runtime(runtime).await;
    fixture.add_public_bot("driver").await;
    fixture.add_public_bot("worker").await;

    let result = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                originator: None,
                name: Some("State machine".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinition::Reference(
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "definition-1".into(),
                                    version: 1,
                                },
                            ),
                        participant_bindings: Vec::new(),
                    },
                ),
            }),
        })
        .await;

    assert!(matches!(result, Ok(GroupDetail::Collaboration(_))));
}

#[tokio::test]
async fn state_machine_start_failure_removes_runtime_session_and_group() {
    let runtime = Arc::new(RecordingRuntime::default());
    *runtime.start_error.lock().expect("runtime lock") = Some("dispatch failed".into());
    let fixture = Fixture::new_with_runtime(runtime.clone()).await;
    fixture.add_public_bot("driver").await;
    fixture.add_public_bot("worker").await;

    let result = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                originator: None,
                name: Some("State machine".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinition::Reference(
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "definition-1".into(),
                                    version: 1,
                                },
                            ),
                        participant_bindings: Vec::new(),
                    },
                ),
            }),
        })
        .await;

    assert!(matches!(result, Err(ApplicationError::InvalidInput { .. })));
    assert_eq!(fixture.groups.count().await, 0);
    let group_id = runtime
        .configured
        .lock()
        .expect("runtime lock")
        .as_ref()
        .expect("configured runtime")
        .group_id
        .clone();
    assert!(
        fixture
            .sessions
            .list_by_group(&group_id, None, 0, 10, None, None)
            .await
            .expect("list sessions")
            .is_empty()
    );
    assert_eq!(
        runtime.cancelled_groups.lock().expect("runtime lock").len(),
        1
    );
    assert_eq!(
        runtime
            .deleted_group_state
            .lock()
            .expect("runtime lock")
            .len(),
        1
    );
}

#[tokio::test]
async fn deleting_state_machine_group_cancels_runs_and_removes_runtime_state() {
    let runtime = Arc::new(RecordingRuntime::default());
    let fixture = Fixture::new_with_runtime(runtime.clone()).await;
    fixture.add_public_bot("driver").await;
    fixture.add_public_bot("worker").await;
    let detail = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                originator: None,
                name: Some("State machine".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinition::Reference(
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "definition-1".into(),
                                    version: 1,
                                },
                            ),
                        participant_bindings: Vec::new(),
                    },
                ),
            }),
        })
        .await
        .expect("create state-machine group");
    let GroupDetail::Collaboration(detail) = detail else {
        panic!("expected collaboration detail");
    };

    let deleted = fixture
        .service
        .delete(DeleteGroup {
            caller: bot_principal("driver"),
            group_id: detail.group_id.clone(),
            acting_bot_id: None,
        })
        .await
        .expect("delete state-machine group");

    assert!(deleted.deleted);
    assert_eq!(
        runtime
            .cancelled_groups
            .lock()
            .expect("runtime lock")
            .as_slice(),
        &[detail.group_id.clone()]
    );
    assert_eq!(
        runtime
            .deleted_group_state
            .lock()
            .expect("runtime lock")
            .as_slice(),
        &[detail.group_id]
    );
}

#[tokio::test]
async fn failed_binding_cleanup_keeps_deletion_and_cleans_state_machine_runtime() {
    let runtime = Arc::new(RecordingRuntime::default());
    let fixture = Fixture::new_with_runtime_and_failing_channel_cleanup(runtime.clone()).await;
    fixture.add_public_bot("driver").await;
    fixture
        .groups
        .upsert(normal_group(
            "group-1",
            "driver",
            vec![Participant::bot("driver", ParticipantRole::Driver)],
            GroupStrategy::StateMachine,
            1,
        ))
        .await
        .expect("store StateMachine Group");

    let error = fixture
        .service
        .delete(DeleteGroup {
            caller: bot_principal("driver"),
            group_id: "group-1".into(),
            acting_bot_id: None,
        })
        .await
        .expect_err("binding cleanup failure must fail deletion");

    assert!(matches!(error, ApplicationError::Internal(_)));
    assert!(fixture.groups.get("group-1").await.is_none());
    assert_eq!(
        runtime
            .cancelled_groups
            .lock()
            .expect("runtime lock")
            .as_slice(),
        &["group-1".to_string()]
    );
    assert_eq!(
        runtime
            .deleted_group_state
            .lock()
            .expect("runtime lock")
            .as_slice(),
        &["group-1".to_string()]
    );
}

#[tokio::test]
async fn update_preserves_hidden_legacy_routing_fields() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "helper"] {
        fixture.add_public_bot(bot).await;
    }
    let mut sender_routes = HashMap::new();
    sender_routes.insert("helper".into(), vec!["driver".into()]);
    let mut group = normal_group(
        "group-1",
        "driver",
        vec![
            Participant::bot("driver", ParticipantRole::Driver),
            Participant::bot("helper", ParticipantRole::Consultant),
        ],
        GroupStrategy::Chat,
        1,
    );
    group.routing_policy = Some(RoutingPolicy {
        mode: RoutingMode::Structured,
        default_bot_final_delivery: DefaultDelivery::SendToDriver,
        sender_routes: sender_routes.clone(),
    });
    let original_version = group.version;
    fixture.groups.upsert(group).await.expect("store group");

    let detail = fixture
        .service
        .update(UpdateGroup {
            caller: bot_principal("driver"),
            group_id: "group-1".into(),
            patch: GroupPatch {
                name: Some("Renamed".into()),
                context: Some("测试1".into()),
                delivery_policy: Some(GroupDeliveryPolicy {
                    bot_final_delivery: BotFinalDelivery::InjectObservers,
                }),
                ..Default::default()
            },
        })
        .await
        .expect("update");

    let stored = fixture.groups.get("group-1").await.expect("stored group");
    let GroupDetail::Collaboration(detail) = detail else {
        panic!("expected collaboration detail");
    };
    assert_eq!(detail.updated_at, stored.updated_at);
    assert_eq!(stored.version, original_version + 1);
    assert_eq!(detail.version, original_version + 1);
    let policy = stored.routing_policy.expect("routing policy");
    assert_eq!(policy.mode, RoutingMode::Structured);
    assert_eq!(policy.sender_routes, sender_routes);
    assert_eq!(
        policy.default_bot_final_delivery,
        DefaultDelivery::InjectObservers
    );
    assert_eq!(stored.label.as_deref(), Some("Renamed"));
    assert_eq!(stored.context.as_deref(), Some("测试1"));
    assert_eq!(detail.context.as_deref(), Some("测试1"));
}

#[tokio::test]
async fn get_requires_a_group_relation_and_delete_is_idempotent() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "outsider"] {
        fixture.add_public_bot(bot).await;
    }
    fixture
        .groups
        .upsert(normal_group(
            "group-1",
            "driver",
            vec![Participant::bot("driver", ParticipantRole::Driver)],
            GroupStrategy::Chat,
            1,
        ))
        .await
        .expect("store group");

    let denied = fixture
        .service
        .get(GetGroup {
            caller: bot_principal("outsider"),
            group_id: "group-1".into(),
        })
        .await;
    assert!(matches!(denied, Err(ApplicationError::Forbidden(_))));

    let first = fixture
        .service
        .delete(DeleteGroup {
            caller: bot_principal("driver"),
            group_id: "group-1".into(),
            acting_bot_id: None,
        })
        .await
        .expect("first delete");
    assert!(first.deleted);

    let second = fixture
        .service
        .delete(DeleteGroup {
            caller: bot_principal("driver"),
            group_id: "group-1".into(),
            acting_bot_id: None,
        })
        .await
        .expect("second delete");
    assert!(!second.deleted);
}

#[tokio::test]
async fn tenant_metadata_does_not_restrict_bot_collaboration() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker"] {
        fixture.add_public_bot(bot).await;
    }

    let detail = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal_in_tenant("driver", "tenant-b"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                originator: None,
                name: Some("Cross-tenant collaboration".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                }],
                collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
                    delivery_policy: GroupDeliveryPolicy {
                        bot_final_delivery: BotFinalDelivery::SendToDriver,
                    },
                }),
            }),
        })
        .await
        .expect("tenant metadata must not block collaboration");

    assert!(matches!(detail, GroupDetail::Collaboration(_)));
}

#[tokio::test]
async fn human_originator_can_update_and_delete_group() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "manager", "worker"] {
        fixture.add_public_bot(bot).await;
    }
    let mut group = normal_group(
        "group-1",
        "driver",
        vec![
            Participant::bot("driver", ParticipantRole::Worker),
            Participant::bot("manager", ParticipantRole::Manager),
            Participant::bot("worker", ParticipantRole::Worker),
        ],
        GroupStrategy::ManagerWorker,
        1,
    );
    group.originator = Some("human_manager".into());
    fixture.groups.upsert(group).await.expect("store group");

    fixture
        .service
        .update(UpdateGroup {
            caller: bot_principal("manager"),
            group_id: "group-1".into(),
            patch: GroupPatch {
                name: Some("Managed".into()),
                ..Default::default()
            },
        })
        .await
        .expect("manager may update");

    let deleted = fixture
        .service
        .delete(DeleteGroup {
            caller: bot_principal("manager"),
            group_id: "group-1".into(),
            acting_bot_id: None,
        })
        .await
        .expect("manager may delete");
    assert!(deleted.deleted);
}

#[tokio::test]
async fn delete_group_unauthorized_principal_forbidden() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "outsider"] {
        fixture.add_public_bot(bot).await;
    }
    fixture
        .groups
        .upsert(normal_group(
            "group-1",
            "driver",
            vec![Participant::bot("driver", ParticipantRole::Driver)],
            GroupStrategy::Chat,
            1,
        ))
        .await
        .expect("store group");

    // An unauthorized principal must not receive the idempotent
    // `deleted:false` mask when the group exists; the facade rejects the
    // call with 403 forbidden instead. Idempotent `deleted:false` is reserved
    // for an authorized caller whose target is already absent.
    let err = fixture
        .service
        .delete(DeleteGroup {
            caller: bot_principal("outsider"),
            group_id: "group-1".into(),
            acting_bot_id: None,
        })
        .await
        .expect_err("non-manager delete must be forbidden, not idempotent");
    assert!(
        matches!(err, ApplicationError::Forbidden(_)),
        "expected forbidden, got {err:?}"
    );
}

#[tokio::test]
async fn state_machine_patch_failure_does_not_commit_requested_changes() {
    let runtime = Arc::new(RecordingRuntime::default());
    let fixture = Fixture::new_with_runtime(runtime.clone()).await;
    for bot in ["driver", "helper"] {
        fixture.add_public_bot(bot).await;
    }

    fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                originator: None,
                name: Some("Before".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "helper".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinition::Reference(
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "definition-1".into(),
                                    version: 1,
                                },
                            ),
                        participant_bindings: Vec::new(),
                    },
                ),
            }),
        })
        .await
        .expect("create state-machine group");
    let group_id = runtime
        .configured
        .lock()
        .expect("runtime lock")
        .as_ref()
        .expect("configured runtime")
        .group_id
        .clone();
    *runtime
        .projection_error
        .lock()
        .expect("projection error lock") = Some("runtime unavailable".into());

    let result = fixture
        .service
        .update(UpdateGroup {
            caller: bot_principal("driver"),
            group_id: group_id.clone(),
            patch: GroupPatch {
                name: Some("After".into()),
                ..Default::default()
            },
        })
        .await;
    assert!(matches!(result, Err(ApplicationError::InvalidInput { .. })));
    assert_eq!(
        fixture
            .groups
            .get(&group_id)
            .await
            .expect("stored group")
            .label
            .as_deref(),
        Some("Before")
    );
}

#[tokio::test]
async fn create_does_not_friendship_check_driver_against_caller() {
    // caller↔driver was dropped: a protected driver the caller neither owns nor
    // is friends with is no longer friendship-checked (there are no protected
    // participants that would consult the friend store either), so even a
    // failing friend store must not block creation of the group.
    let friends = Arc::new(FriendCore::with_repo(Arc::new(FailingFriendRepo)));
    let fixture = Fixture::new_with_friends(friends).await;
    fixture.add_public_bot("requester").await;
    fixture.add_protected_bot("driver").await;

    let result = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("requester"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                originator: None,
                name: None,
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: Vec::new(),
                collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
                    delivery_policy: GroupDeliveryPolicy {
                        bot_final_delivery: BotFinalDelivery::SendToDriver,
                    },
                }),
            }),
        })
        .await;

    assert!(
        result.is_ok(),
        "driver must be ungated vs caller; got {result:?}"
    );
}

#[tokio::test]
async fn create_propagates_protected_participant_friendship_lookup_failure() {
    let friends = Arc::new(FriendCore::with_repo(Arc::new(FailingFriendRepo)));
    let fixture = Fixture::new_with_friends(friends).await;
    fixture.add_public_bot("driver").await;
    fixture.add_protected_bot("helper").await;

    let result = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                originator: None,
                name: None,
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "helper".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                }],
                collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
                    delivery_policy: GroupDeliveryPolicy {
                        bot_final_delivery: BotFinalDelivery::SendToDriver,
                    },
                }),
            }),
        })
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::Internal(message))
            if message.contains("friend store unavailable")
    ));
}

#[tokio::test]
async fn update_rejects_delivery_policy_for_non_chat_strategy() {
    let fixture = Fixture::new().await;
    for bot in ["manager", "worker"] {
        fixture.add_public_bot(bot).await;
    }
    fixture
        .groups
        .upsert(normal_group(
            "manager-worker",
            "manager",
            vec![
                Participant::bot("manager", ParticipantRole::Manager),
                Participant::bot("worker", ParticipantRole::Worker),
            ],
            GroupStrategy::ManagerWorker,
            1,
        ))
        .await
        .expect("store ManagerWorker Group");

    let result = fixture
        .service
        .update(UpdateGroup {
            caller: bot_principal("manager"),
            group_id: "manager-worker".into(),
            patch: GroupPatch {
                delivery_policy: Some(GroupDeliveryPolicy {
                    bot_final_delivery: BotFinalDelivery::InjectObservers,
                }),
                ..Default::default()
            },
        })
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::InvalidInput { code, .. }) if code == "invalid_request"
    ));
}

#[tokio::test]
async fn update_opening_message_preserves_patch_states_and_strategy_guard() {
    let runtime = Arc::new(RecordingRuntime::default());
    let fixture = Fixture::new_with_runtime(runtime.clone()).await;
    for bot in ["driver", "helper"] {
        fixture.add_public_bot(bot).await;
    }
    fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                originator: None,
                name: Some("StateMachine".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "helper".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinition::Reference(
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "definition-1".into(),
                                    version: 1,
                                },
                            ),
                        participant_bindings: Vec::new(),
                    },
                ),
            }),
        })
        .await
        .expect("create StateMachine Group");
    let group_id = runtime
        .configured
        .lock()
        .expect("runtime lock")
        .as_ref()
        .expect("configured runtime")
        .group_id
        .clone();

    let configured = OpeningMessage::Text("Run {{bcs.run_id}}".to_string());
    fixture
        .service
        .update(UpdateGroup {
            caller: bot_principal("driver"),
            group_id: group_id.clone(),
            patch: GroupPatch {
                opening_message: Some(Some(configured.clone())),
                ..Default::default()
            },
        })
        .await
        .expect("configure opening message");
    assert_eq!(
        fixture
            .groups
            .get(&group_id)
            .await
            .expect("stored StateMachine Group")
            .opening_message,
        Some(configured.clone())
    );

    fixture
        .service
        .update(UpdateGroup {
            caller: bot_principal("driver"),
            group_id: group_id.clone(),
            patch: GroupPatch {
                name: Some("Renamed".into()),
                ..Default::default()
            },
        })
        .await
        .expect("omit opening-message patch");
    assert_eq!(
        fixture
            .groups
            .get(&group_id)
            .await
            .expect("stored StateMachine Group")
            .opening_message,
        Some(configured)
    );

    fixture
        .service
        .update(UpdateGroup {
            caller: bot_principal("driver"),
            group_id: group_id.clone(),
            patch: GroupPatch {
                opening_message: Some(None),
                ..Default::default()
            },
        })
        .await
        .expect("clear opening message");
    assert_eq!(
        fixture
            .groups
            .get(&group_id)
            .await
            .expect("stored StateMachine Group")
            .opening_message,
        None
    );

    let mut chat = normal_group(
        "chat",
        "driver",
        vec![Participant::bot("driver", ParticipantRole::Driver)],
        GroupStrategy::Chat,
        1,
    );
    chat.opening_message = None;
    fixture.groups.upsert(chat).await.expect("store Chat Group");
    let error = fixture
        .service
        .update(UpdateGroup {
            caller: bot_principal("driver"),
            group_id: "chat".into(),
            patch: GroupPatch {
                opening_message: Some(Some(OpeningMessage::Text("hello".into()))),
                ..Default::default()
            },
        })
        .await
        .expect_err("Chat Group must reject opening message");
    assert!(matches!(
        error,
        ApplicationError::InvalidInput { code, .. } if code == "invalid_opening_message"
    ));
}

#[tokio::test]
async fn create_rejects_duplicate_participant_actor_ids() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "helper"] {
        fixture.add_public_bot(bot).await;
    }

    let result = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                originator: None,
                name: None,
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![
                    CreateParticipant {
                        actor_id: "helper".into(),
                        role: ParticipantRole::Consultant,
                        tags: Vec::new(),
                    },
                    CreateParticipant {
                        actor_id: "helper".into(),
                        role: ParticipantRole::Observer,
                        tags: Vec::new(),
                    },
                ],
                collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
                    delivery_policy: GroupDeliveryPolicy {
                        bot_final_delivery: BotFinalDelivery::SendToDriver,
                    },
                }),
            }),
        })
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::InvalidInput { code, .. }) if code == "invalid_participant"
    ));
}

#[tokio::test]
async fn create_rejects_roles_that_do_not_match_the_strategy_lead() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "manager", "worker"] {
        fixture.add_public_bot(bot).await;
    }

    let invalid_chat = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                originator: None,
                name: None,
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "manager".into(),
                    role: ParticipantRole::Manager,
                    tags: Vec::new(),
                }],
                collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
                    delivery_policy: GroupDeliveryPolicy {
                        bot_final_delivery: BotFinalDelivery::SendToDriver,
                    },
                }),
            }),
        })
        .await;
    assert!(matches!(
        invalid_chat,
        Err(ApplicationError::InvalidInput { code, .. }) if code == "invalid_participant"
    ));

    let invalid_manager_worker = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                originator: None,
                name: None,
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![
                    CreateParticipant {
                        actor_id: "manager".into(),
                        role: ParticipantRole::Manager,
                        tags: Vec::new(),
                    },
                    CreateParticipant {
                        actor_id: "worker".into(),
                        role: ParticipantRole::Worker,
                        tags: Vec::new(),
                    },
                ],
                collaboration: CollaborationConfiguration::ManagerWorker(Default::default()),
            }),
        })
        .await;
    assert!(matches!(
        invalid_manager_worker,
        Err(ApplicationError::InvalidInput { code, .. }) if code == "invalid_participant"
    ));
}

#[tokio::test]
async fn human_principal_creates_legacy_actor_with_current_display_name_priority() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("bot-b").await;

    for (staff_no, username, display_name, full_name, expected_name) in [
        (
            "staff-display",
            "alice-login",
            Some("Alice Display"),
            Some("Alice Full"),
            "Alice Display",
        ),
        (
            "staff-full",
            "bob-login",
            None,
            Some("Bob Full"),
            "Bob Full",
        ),
        ("staff-login", "carol-login", None, None, "carol-login"),
    ] {
        let detail = fixture
            .service
            .create(CreateGroup {
                caller: human_principal_with_profile(staff_no, username, display_name, full_name),
                group: CreateGroupSpec::DirectMessage(CreateDirectMessageGroup {
                    name: Some("Human and B".into()),
                    context: None,
                    target_actor_id: "bot-b".into(),
                }),
            })
            .await
            .expect("canonical Human actor creates DM");

        let GroupDetail::DirectMessage(detail) = detail else {
            panic!("expected DM detail");
        };
        let actor_id = format!("human_{staff_no}");
        assert!(
            detail
                .participants
                .iter()
                .any(|participant| participant.actor_id == actor_id)
        );
        let human = fixture
            .bots
            .get(&actor_id)
            .await
            .expect("V1 must materialize the legacy Human Actor");
        assert_eq!(human.capabilities.name.as_deref(), Some(expected_name));
        assert_eq!(human.created_by.as_deref(), Some(staff_no));
    }
}

#[tokio::test]
async fn human_principal_preserves_existing_legacy_actor_display_name() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("bot-b").await;
    fixture
        .bots
        .ensure_human_actor("staff-1", "Original Name")
        .await
        .expect("register existing Human actor");

    fixture
        .service
        .create(CreateGroup {
            caller: human_principal_with_profile(
                "staff-1",
                "alice-login",
                Some("Changed Name"),
                None,
            ),
            group: CreateGroupSpec::DirectMessage(CreateDirectMessageGroup {
                name: Some("Human and B".into()),
                context: None,
                target_actor_id: "bot-b".into(),
            }),
        })
        .await
        .expect("existing Human actor creates DM");

    let human = fixture
        .bots
        .get("human_staff-1")
        .await
        .expect("existing Human actor remains registered");
    assert_eq!(human.capabilities.name.as_deref(), Some("Original Name"));
}

#[tokio::test]
async fn dm_create_outcome_reports_when_the_existing_pair_is_reused() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("bot-a").await;
    fixture.add_public_bot("bot-b").await;
    let command = || CreateGroup {
        caller: bot_principal("bot-a"),
        group: CreateGroupSpec::DirectMessage(CreateDirectMessageGroup {
            name: Some("A and B".into()),
            context: Some("original context".into()),
            target_actor_id: "bot-b".into(),
        }),
    };

    let first = fixture
        .service
        .create_with_outcome(command())
        .await
        .expect("create first DM");
    let reused = fixture
        .service
        .create_with_outcome(command())
        .await
        .expect("reuse existing DM");

    assert!(first.created);
    assert!(!reused.created);
    let GroupDetail::DirectMessage(first) = first.group else {
        panic!("expected first DM detail");
    };
    let GroupDetail::DirectMessage(reused) = reused.group else {
        panic!("expected reused DM detail");
    };
    assert_eq!(reused.group_id, first.group_id);
}

#[tokio::test]
async fn client_caused_group_errors_map_to_documented_4xx_classes() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;
    fixture.add_protected_bot("protected").await;
    fixture
        .friends
        .add_friendship("driver", "protected")
        .await
        .expect("driver/protected friendship");

    let public_with_protected = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                originator: None,
                name: None,
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Public,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "protected".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                }],
                collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
                    delivery_policy: GroupDeliveryPolicy {
                        bot_final_delivery: BotFinalDelivery::SendToDriver,
                    },
                }),
            }),
        })
        .await;
    assert!(matches!(
        public_with_protected,
        Err(ApplicationError::Conflict { code, .. }) if code == "non_public_participant"
    ));
}

#[tokio::test]
async fn human_caller_does_not_require_a_same_named_bot() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("target").await;

    let result = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("missing-caller"),
            group: CreateGroupSpec::DirectMessage(CreateDirectMessageGroup {
                name: None,
                context: None,
                target_actor_id: "target".into(),
            }),
        })
        .await;

    assert!(result.is_ok());
}

#[tokio::test]
async fn deleting_dm_maps_the_legacy_rejection_to_contract_conflict() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;
    fixture.add_public_bot("peer").await;
    let detail = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::DirectMessage(CreateDirectMessageGroup {
                name: None,
                context: None,
                target_actor_id: "peer".into(),
            }),
        })
        .await
        .expect("create DM");
    let GroupDetail::DirectMessage(detail) = detail else {
        panic!("expected DM detail");
    };

    let result = fixture
        .service
        .delete(DeleteGroup {
            caller: bot_principal("driver"),
            group_id: detail.group_id,
            acting_bot_id: None,
        })
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::Conflict { code, .. }) if code == "conflict"
    ));
}

#[tokio::test]
async fn session_only_nonmember_dm_summary_omits_peer_actor() {
    let fixture = Fixture::new().await;
    for bot in ["bot-a", "bot-b", "target"] {
        fixture.add_public_bot(bot).await;
    }
    let (group, _) = fixture
        .groups
        .create_or_reuse_actor_dm_group(
            "dm-1",
            bcs_service_api::DmActorSpec {
                actor_id: "bot-a".into(),
                actor_kind: bcs_service_api::ActorKind::Bot,
                display_name: Some("bot-a".into()),
            },
            bcs_service_api::DmActorSpec {
                actor_id: "bot-b".into(),
                actor_kind: bcs_service_api::ActorKind::Bot,
                display_name: Some("bot-b".into()),
            },
            "bot-a",
            "bot-a",
            None,
            None,
        )
        .await
        .expect("create DM");
    fixture
        .sessions
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: group.id,
            session_id: None,
            params: NewSessionParams {
                participants: vec![Participant::bot("target", ParticipantRole::Consultant)],
                ..Default::default()
            },
        })
        .await
        .expect("create session");

    let page = fixture
        .service
        .list_groups(ListGroups {
            caller: bot_principal("target"),
            view_bot_id: Some("target".into()),
            offset: 0,
            limit: 20,
            q: None,
            visibility: None,
            membership: MembershipFilter::SessionOnly,
            kind: GroupKindFilter::Dm,
            strategy: None,
        })
        .await
        .expect("list related DMs");
    let GroupSummary::DirectMessage(summary) = &page.items[0] else {
        panic!("expected DM summary");
    };
    assert!(summary.peer_actor.is_none());
}

mod originator_v1_policy {
    //! V1 create-group originator authorization (caller↔originator, not
    //! caller↔driver). Tests run the `for_v1_openapi` core branch.
    use super::*;

    fn chat_group(
        originator: Option<String>,
        participants: Vec<CreateParticipant>,
    ) -> CreateGroup {
        chat_group_with_driver("driver", originator, participants)
    }

    fn chat_group_with_driver(
        driver: &str,
        originator: Option<String>,
        participants: Vec<CreateParticipant>,
    ) -> CreateGroup {
        CreateGroup {
            caller: human_principal_with_profile("staff-1", "alice", Some("Alice"), None),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                name: Some("Planning".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: driver.into(),
                participants,
                collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
                    delivery_policy: GroupDeliveryPolicy {
                        bot_final_delivery: BotFinalDelivery::SendToDriver,
                    },
                }),
                originator,
            }),
        }
    }

    fn collaboration_originator(detail: GroupDetail) -> String {
        let GroupDetail::Collaboration(it) = detail else {
            panic!("expected collaboration detail, got {detail:?}");
        };
        it.originator_actor_id.to_string()
    }

    #[tokio::test]
    async fn originator_defaults_to_caller_when_omitted() {
        let fixture = Fixture::new().await;
        fixture.add_public_bot("driver").await;
        let detail = fixture
            .service
            .create(chat_group(None, vec![]))
            .await
            .expect("create group");
        assert_eq!(collaboration_originator(detail), "human_staff-1");
    }

    #[tokio::test]
    async fn originator_accepts_explicit_caller_self() {
        let fixture = Fixture::new().await;
        fixture.add_public_bot("driver").await;
        let detail = fixture
            .service
            .create(chat_group(Some("human_staff-1".into()), vec![]))
            .await
            .expect("create group");
        assert_eq!(collaboration_originator(detail), "human_staff-1");
    }

    #[tokio::test]
    async fn originator_rejects_bot_not_owned_by_caller() {
        let fixture = Fixture::new().await;
        fixture.add_public_bot("driver").await;
        fixture.add_bot_owned_by("bot-other", "someone-else", "public").await;
        let err = fixture
            .service
            .create(chat_group(Some("bot-other".into()), vec![]))
            .await
            .expect_err("unowned originator must be rejected");
        assert!(matches!(err, ApplicationError::Forbidden { .. }), "got {err:?}");
    }

    #[tokio::test]
    async fn originator_rejects_unregistered_originator() {
        let fixture = Fixture::new().await;
        fixture.add_public_bot("driver").await;
        let err = fixture
            .service
            .create(chat_group(Some("does-not-exist".into()), vec![]))
            .await
            .expect_err("unregistered originator must be rejected");
        assert!(matches!(err, ApplicationError::Forbidden { .. }), "got {err:?}");
    }

    #[tokio::test]
    async fn originator_rejects_other_human_originator() {
        let fixture = Fixture::new().await;
        fixture.add_public_bot("driver").await;
        let err = fixture
            .service
            .create(chat_group(Some("human_staff-2".into()), vec![]))
            .await
            .expect_err("another human as originator must be rejected");
        assert!(matches!(err, ApplicationError::Forbidden { .. }), "got {err:?}");
    }

    #[tokio::test]
    async fn owned_bot_originator_accepted_when_driver_reachable() {
        let fixture = Fixture::new().await;
        fixture.add_public_bot("driver").await;
        fixture.add_bot_owned_by("bot-o", "staff-1", "public").await;
        let detail = fixture
            .service
            .create(chat_group(Some("bot-o".into()), vec![]))
            .await
            .expect("owned-bot originator with public driver");
        assert_eq!(collaboration_originator(detail), "bot-o");
    }

    #[tokio::test]
    async fn owned_bot_originator_rejects_unreachable_driver() {
        let fixture = Fixture::new().await;
        fixture.add_bot_owned_by("driver", "someone-else", "protected").await;
        fixture.add_bot_owned_by("bot-o", "staff-1", "public").await;
        let err = fixture
            .service
            .create(chat_group_with_driver(
                "driver",
                Some("bot-o".into()),
                vec![],
            ))
            .await
            .expect_err("driver not reachable from originator bot");
        assert!(matches!(err, ApplicationError::Forbidden { .. }), "got {err:?}");
    }

    #[tokio::test]
    async fn driver_not_gated_against_caller_when_originator_is_human() {
        // caller staff-1 does NOT own the driver and is not its friend; with
        // caller↔driver dropped and originator=caller (default), the group
        // must still be created (driver ungated vs caller).
        let fixture = Fixture::new().await;
        fixture.add_bot_owned_by("driver", "someone-else", "protected").await;
        let detail = fixture
            .service
            .create(chat_group_with_driver("driver", None, vec![]))
            .await
            .expect("driver ungated vs caller");
        assert_eq!(collaboration_originator(detail), "human_staff-1");
    }

    #[tokio::test]
    async fn owned_bot_originator_equal_to_driver_succeeds() {
        // A human designates an owned bot as BOTH originator and driver. The
        // driver is self-reachable — must not require self-friendship.
        let fixture = Fixture::new().await;
        fixture.add_bot_owned_by("bot-o", "staff-1", "protected").await;
        let detail = fixture
            .service
            .create(chat_group_with_driver("bot-o", Some("bot-o".into()), vec![]))
            .await
            .expect("originator==driver must succeed without self-friendship");
        assert_eq!(collaboration_originator(detail), "bot-o");
    }

    #[tokio::test]
    async fn owned_bot_originator_accepts_friend_driver() {
        // When the originator is a caller-owned Bot distinct from the driver,
        // the driver must be reachable from that originator bot — here, a
        // friend (covers the try_are_friends==true reachable branch).
        let fixture = Fixture::new().await;
        fixture.add_bot_owned_by("bot-o", "staff-1", "public").await;
        fixture.add_bot_owned_by("driver", "someone-else", "protected").await;
        fixture
            .friends
            .add_friendship("bot-o", "driver")
            .await
            .expect("originator/driver friendship");
        let detail = fixture
            .service
            .create(chat_group_with_driver("driver", Some("bot-o".into()), vec![]))
            .await
            .expect("friend driver reachable from originator bot");
        assert_eq!(collaboration_originator(detail), "bot-o");
    }

    #[tokio::test]
    async fn originator_rejects_registered_human_as_originator() {
        // A registered non-Bot actor (a human) passed as originator is not
        // the caller-self and is not a Bot, so it must be rejected with
        // invalid_originator (covers authorize_originator's actor_kind arm).
        let fixture = Fixture::new().await;
        fixture.add_public_bot("driver").await;
        fixture
            .bots
            .ensure_human_actor("staff-2", "Bob")
            .await
            .expect("register Human actor");
        let err = fixture
            .service
            .create(chat_group(Some("human_staff-2".into()), vec![]))
            .await
            .expect_err("registered non-bot originator must be rejected");
        assert!(
            matches!(err, ApplicationError::InvalidInput { ref code, .. } if code == "invalid_originator"),
            "got {err:?}"
        );
    }
}

#[tokio::test]
async fn list_public_groups_returns_all_public_and_excludes_private() {
    let fixture = Fixture::new().await;
    for bot in ["driver-a", "driver-b"] {
        fixture.add_public_bot(bot).await;
    }

    // driver-b 公开群；driver-a 私有群（caller 不是任何群的参与者）
    for (group_id, visibility, updated_at) in [
        ("pub-recent", "public", 30),
        ("priv-hidden", "private", 20),
        ("pub-older", "public", 10),
    ] {
        let mut group = normal_group(
            group_id,
            if group_id.starts_with("pub") { "driver-b" } else { "driver-a" },
            vec![Participant::bot(
                if group_id.starts_with("pub") { "driver-b" } else { "driver-a" },
                ParticipantRole::Driver,
            )],
            GroupStrategy::Chat,
            updated_at,
        );
        group.visibility = visibility.into();
        fixture.groups.upsert(group).await.expect("store Group");
    }

    let page = fixture
        .service
        .list_public_groups(ListPublicGroups {
            offset: 0,
            limit: 20,
            q: None,
            strategy: None,
        })
        .await
        .expect("list public groups");

    assert_eq!(page.total, 2);
    assert_eq!(page.items.len(), 2);
    for item in &page.items {
        match item {
            GroupSummary::Normal(summary) => {
                assert_eq!(summary.membership, Membership::None);
                assert!(summary.group_id.starts_with("pub"));
            }
            other => panic!("expected normal summary, got {other:?}"),
        }
    }
}

#[tokio::test]
async fn list_summaries_include_group_context() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "target"] {
        fixture.add_public_bot(bot).await;
    }
    let mut group = normal_group(
        "ctx-group",
        "driver",
        vec![
            Participant::bot("driver", ParticipantRole::Driver),
            Participant::bot("target", ParticipantRole::Consultant),
        ],
        GroupStrategy::Chat,
        10,
    );
    group.visibility = "public".into();
    group.context = Some("plaza context".to_string());
    fixture.groups.upsert(group).await.expect("store Group");

    let public_page = fixture
        .service
        .list_public_groups(ListPublicGroups {
            offset: 0,
            limit: 10,
            q: None,
            strategy: None,
        })
        .await
        .expect("list public groups");
    assert_eq!(public_page.items.len(), 1);
    match &public_page.items[0] {
        GroupSummary::Normal(summary) => {
            assert_eq!(summary.context.as_deref(), Some("plaza context"));
        }
        other => panic!("expected normal summary, got {other:?}"),
    }

    let page = fixture
        .service
        .list_groups(ListGroups {
            caller: bot_principal("target"),
            view_bot_id: Some("target".into()),
            offset: 0,
            limit: 10,
            q: None,
            visibility: None,
            membership: MembershipFilter::Direct,
            kind: GroupKindFilter::All,
            strategy: None,
        })
        .await
        .expect("list groups");
    assert_eq!(page.items.len(), 1);
    match &page.items[0] {
        GroupSummary::Normal(summary) => {
            assert_eq!(summary.context.as_deref(), Some("plaza context"));
        }
        other => panic!("expected normal summary, got {other:?}"),
    }
}

#[tokio::test]
async fn list_public_groups_filters_by_strategy() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;

    for (group_id, strategy) in [
        ("chat-group", GroupStrategy::Chat),
        ("sm-group", GroupStrategy::StateMachine),
    ] {
        let mut group = normal_group(
            group_id,
            "driver",
            vec![Participant::bot("driver", ParticipantRole::Driver)],
            strategy,
            10,
        );
        group.visibility = "public".into();
        fixture.groups.upsert(group).await.expect("store Group");
    }

    let page = fixture
        .service
        .list_public_groups(ListPublicGroups {
            offset: 0,
            limit: 20,
            q: None,
            strategy: Some(V1GroupStrategy::StateMachine),
        })
        .await
        .expect("list state_machine public groups");

    assert_eq!(page.total, 1);
    match &page.items[0] {
        GroupSummary::Normal(summary) => assert_eq!(summary.group_id, "sm-group"),
        other => panic!("expected normal summary, got {other:?}"),
    }
}

#[tokio::test]
async fn list_public_groups_paginates_and_reports_filtered_total() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;

    for idx in 0..5 {
        let mut group = normal_group(
            &format!("pub-{idx}"),
            "driver",
            vec![Participant::bot("driver", ParticipantRole::Driver)],
            GroupStrategy::Chat,
            10 + idx,
        );
        group.visibility = "public".into();
        fixture.groups.upsert(group).await.expect("store Group");
    }

    let page = fixture
        .service
        .list_public_groups(ListPublicGroups {
            offset: 1,
            limit: 2,
            q: None,
            strategy: None,
        })
        .await
        .expect("paginated public groups");

    assert_eq!(page.total, 5);
    assert_eq!(page.items.len(), 2);
    assert_eq!(page.offset, 1);
    assert_eq!(page.limit, 2);
}

#[tokio::test]
async fn list_public_groups_filters_by_label_query() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;

    for group_id in ["plaza-alpha", "plaza-beta", "other-gamma"] {
        let mut group = normal_group(
            group_id,
            "driver",
            vec![Participant::bot("driver", ParticipantRole::Driver)],
            GroupStrategy::Chat,
            10,
        );
        group.visibility = "public".into();
        fixture.groups.upsert(group).await.expect("store Group");
    }

    let page = fixture
        .service
        .list_public_groups(ListPublicGroups {
            offset: 0,
            limit: 20,
            q: Some("plaza".into()),
            strategy: None,
        })
        .await
        .expect("label-filtered public groups");

    assert_eq!(page.total, 2);
    let mut ids: Vec<_> = page
        .items
        .iter()
        .map(|item| match item {
            GroupSummary::Normal(s) => s.group_id.clone(),
            other => panic!("expected normal, got {other:?}"),
        })
        .collect();
    ids.sort();
    assert_eq!(ids, vec!["plaza-alpha", "plaza-beta"]);
}

#[tokio::test]
async fn list_public_groups_excludes_inactive_records() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;

    for (group_id, status) in [("active-one", "active"), ("deleted-one", "deleted")] {
        let mut group = normal_group(
            group_id,
            "driver",
            vec![Participant::bot("driver", ParticipantRole::Driver)],
            GroupStrategy::Chat,
            10,
        );
        group.visibility = "public".into();
        group.record_status = status.into();
        fixture.groups.upsert(group).await.expect("store Group");
    }

    let page = fixture
        .service
        .list_public_groups(ListPublicGroups {
            offset: 0,
            limit: 20,
            q: None,
            strategy: None,
        })
        .await
        .expect("active-only public groups");

    assert_eq!(page.total, 1);
    match &page.items[0] {
        GroupSummary::Normal(summary) => assert_eq!(summary.group_id, "active-one"),
        other => panic!("expected normal summary, got {other:?}"),
    }
}

#[tokio::test]
async fn list_public_groups_rejects_out_of_range_limit() {
    let fixture = Fixture::new().await;

    for invalid_limit in [0u64, 101] {
        let result = fixture
            .service
            .list_public_groups(ListPublicGroups {
                offset: 0,
                limit: invalid_limit,
                q: None,
                strategy: None,
            })
            .await;
        assert!(result.is_err(), "limit {invalid_limit} should be rejected");
        let err = result.unwrap_err();
        assert!(
            matches!(err, ApplicationError::InvalidInput { .. }),
            "expected InvalidInput for limit {invalid_limit}, got {err:?}"
        );
    }
}
