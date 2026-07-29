use std::collections::{BTreeSet, HashMap};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_bot::BotCore;
use bcs_bot_store::PersistentBotRepo;
use bcs_cache_local::InMemoryCachePlugin;
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement, DbTransactionStep,
    DbTransactionStepResult,
};
use bcs_friend::FriendCore;
use bcs_group::{GroupConfig, GroupCore, GroupManagement, MemoryGroupRepo};
use bcs_relation::RelationCore;
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedUser, BotFinalDelivery, ChatConfiguration,
    CollaborationConfiguration, CreateCollaborationGroup, CreateDirectMessageGroup, CreateGroup,
    CreateGroupSpec, CreateParticipant, DeleteGroup, GetGroup, GroupDeliveryPolicy, GroupDetail,
    GroupKindFilter, GroupPatch, GroupService, GroupStrategy as V1GroupStrategy, GroupSummary,
    GroupVisibility, ListBotGroups, Membership, MembershipFilter, Principal, UpdateGroup,
};
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, CancelStateMachineRunCommand, CollaborationDefinition,
    CollaborationRuntimeError, CollaborationRuntimeService, ConfigureGroupRuntimeCommand,
    ConfigureGroupRuntimeOutcome, CreateOrReactivateCommand, DefaultDelivery, DefinitionYamlSource,
    FriendCoreService, FriendRepoPort, Group, GroupCollaborationDefinitionView, GroupCoreService,
    GroupStrategy, HandleBotTerminalEventCommand, HandleBotTerminalEventOutcome, NewSessionParams,
    Participant, ParticipantRole, RoutingMode, RoutingPolicy, ServiceError, ServiceResult,
    SessionHistoryResult, SessionManagementService, StartStateMachineRunCommand,
    StartStateMachineRunOutcome, StateMachineDeliveryCorrelation, StateMachineRun,
    StateMachineRunStatus, StateMachineRunView, SystemMessageService,
};
use bcs_session::SessionManagementServiceImpl;
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::NoopSystemMessageService;

use bcs_group_v1::{GroupServiceConfig, GroupServiceImpl};

struct Fixture {
    service: GroupServiceImpl,
    groups: Arc<GroupCore>,
    bots: Arc<BotCore>,
    friends: Arc<FriendCore>,
    sessions: Arc<SessionManagementServiceImpl>,
}

impl Fixture {
    async fn new() -> Self {
        Self::build(None, None, None).await
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
            relation,
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
struct RecordingRuntime {
    configured: Mutex<Option<ConfigureGroupRuntimeCommand>>,
    started: Mutex<Vec<StartStateMachineRunCommand>>,
    cancelled_groups: Mutex<Vec<String>>,
    deleted_group_state: Mutex<Vec<String>>,
    configure_error: Mutex<Option<String>>,
    start_error: Mutex<Option<String>>,
    projection_error: Mutex<Option<String>>,
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
            default_definition: cmd.definition_ref.clone(),
            auto_start_on_service_invocation: cmd.auto_start_on_service_invocation,
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

fn bot_principal(bot_uuid: &str) -> Principal {
    Principal::bot(bot_uuid, "tenant-a", BTreeSet::new())
}

fn bot_principal_in_tenant(bot_uuid: &str, tenant: &str) -> Principal {
    Principal::bot(bot_uuid, tenant, BTreeSet::new())
}

fn human_principal_with_profile(
    subject_id: &str,
    username: &str,
    display_name: Option<&str>,
    full_name: Option<&str>,
) -> Principal {
    Principal::human(
        AuthenticatedUser {
            id: subject_id.into(),
            username: username.into(),
            display_name: display_name.map(str::to_string),
            full_name: full_name.map(str::to_string),
        },
        "tenant-a",
        BTreeSet::new(),
    )
}

fn normal_group(
    group_id: &str,
    driver: &str,
    participants: Vec<Participant>,
    strategy: GroupStrategy,
    updated_at: u64,
) -> Group {
    let mut group = Group::new(group_id, driver, participants);
    group.originator = Some(driver.to_string());
    group.label = Some(group_id.to_string());
    group.group_strategy = strategy;
    group.updated_at = updated_at;
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
        .list_bot_groups(ListBotGroups {
            principal: bot_principal("target"),
            bot_uuid: "target".into(),
            offset: 0,
            limit: 10,
            q: None,
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
        .list_bot_groups(ListBotGroups {
            principal: bot_principal("target"),
            bot_uuid: "target".into(),
            offset: 0,
            limit: 1,
            q: None,
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
async fn create_derives_originator_after_driver_is_added_to_canonical_participants() {
    let fixture = Fixture::new().await;
    for bot in ["requester", "driver", "helper"] {
        fixture.add_public_bot(bot).await;
    }

    let detail = fixture
        .service
        .create(CreateGroup {
            principal: bot_principal("requester"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                name: Some("Planning".into()),
                context: Some("Plan the release".into()),
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "helper".into(),
                    role: ParticipantRole::Consultant,
                }],
                collaboration: CollaborationConfiguration::Chat(
                    ChatConfiguration {
                        delivery_policy: GroupDeliveryPolicy {
                            bot_final_delivery: BotFinalDelivery::SendToDriver,
                        },
                    },
                ),
            }),
        })
        .await
        .expect("requester may select a collaboration-eligible driver");

    let GroupDetail::Collaboration(detail) = detail else {
        panic!("expected collaboration detail");
    };
    assert_eq!(detail.driver_bot_uuid, "driver");
    assert_eq!(detail.originator_actor_id, "driver");
    assert!(
        detail
            .participants
            .iter()
            .all(|p| p.actor_id != "requester")
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
            principal: human_principal_with_profile(
                "staff-1",
                "alice-login",
                Some("Alice"),
                None,
            ),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                name: Some("Protected collaboration".into()),
                context: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![
                    CreateParticipant {
                        actor_id: "human_staff-1".into(),
                        role: ParticipantRole::Observer,
                    },
                    CreateParticipant {
                        actor_id: "helper".into(),
                        role: ParticipantRole::Consultant,
                    },
                ],
                collaboration: CollaborationConfiguration::Chat(
                    ChatConfiguration {
                        delivery_policy: GroupDeliveryPolicy {
                            bot_final_delivery: BotFinalDelivery::SendToDriver,
                        },
                    },
                ),
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
            principal: bot_principal("bot-a"),
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
    assert!(detail.participants.iter().any(|p| p.actor_id == "bot-a"));
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
        .list_bot_groups(ListBotGroups {
            principal: bot_principal("target"),
            bot_uuid: "target".into(),
            offset: 0,
            limit: 20,
            q: None,
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
async fn bot_principal_list_requires_the_target_bot_to_exist() {
    let fixture = Fixture::new().await;

    let result = fixture
        .service
        .list_bot_groups(ListBotGroups {
            principal: bot_principal("missing"),
            bot_uuid: "missing".into(),
            offset: 0,
            limit: 20,
            q: None,
            membership: MembershipFilter::All,
            kind: GroupKindFilter::Normal,
            strategy: None,
        })
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::NotFound { code, .. }) if code == "bot_not_found"
    ));
}

#[tokio::test]
async fn bot_principal_list_propagates_registry_database_failure() {
    let bots = Arc::new(BotCore::with_repo(Arc::new(
        PersistentBotRepo::with_plugins(
            Arc::new(InMemoryCachePlugin::new()),
            Arc::new(FailingDb),
        ),
    )));
    let fixture = Fixture::new_with_bots(bots).await;

    let result = fixture
        .service
        .list_bot_groups(ListBotGroups {
            principal: bot_principal("stored-bot"),
            bot_uuid: "stored-bot".into(),
            offset: 0,
            limit: 20,
            q: None,
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
            principal: bot_principal("requester"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                name: None,
                context: None,
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
            principal: bot_principal("requester"),
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
            principal: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                name: Some("State machine".into()),
                context: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                }],
                collaboration:
                    CollaborationConfiguration::StateMachine(
                        bcs_service_api::application::v1::StateMachineConfiguration {
                            definition:
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "definition-1".into(),
                                    version: 1,
                                },
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
            principal: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                name: Some("State machine".into()),
                context: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                }],
                collaboration:
                    CollaborationConfiguration::StateMachine(
                        bcs_service_api::application::v1::StateMachineConfiguration {
                            definition:
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "definition-1".into(),
                                    version: 1,
                                },
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
            principal: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                name: Some("State machine".into()),
                context: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                }],
                collaboration:
                    CollaborationConfiguration::StateMachine(
                        bcs_service_api::application::v1::StateMachineConfiguration {
                            definition:
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "definition-1".into(),
                                    version: 1,
                                },
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
            principal: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                name: Some("State machine".into()),
                context: Some("Execute the workflow".into()),
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                }],
                collaboration:
                    CollaborationConfiguration::StateMachine(
                        bcs_service_api::application::v1::StateMachineConfiguration {
                            definition:
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "definition-1".into(),
                                    version: 3,
                                },
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
    let CollaborationConfiguration::StateMachine(collaboration) =
        detail.collaboration
    else {
        panic!("expected state-machine collaboration");
    };
    assert_eq!(collaboration.definition.definition_id, "definition-1");
    assert_eq!(collaboration.definition.version, 3);
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
    assert_eq!(started[0].caller_id.as_deref(), Some("driver"));
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
            principal: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                name: Some("State machine".into()),
                context: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "human_staff-1".into(),
                    role: ParticipantRole::Observer,
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinitionReference {
                                definition_id: "definition-1".into(),
                                version: 1,
                            },
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
            principal: human_principal_with_profile("staff-1", "alice", Some("Alice"), None),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                name: Some("State machine".into()),
                context: Some("Review the release".into()),
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinitionReference {
                                definition_id: "definition-1".into(),
                                version: 1,
                            },
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
    assert_eq!(detail.originator_actor_id, "driver");

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
            principal: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                name: Some("State machine".into()),
                context: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinitionReference {
                                definition_id: "definition-1".into(),
                                version: 1,
                            },
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
            principal: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                name: Some("State machine".into()),
                context: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinitionReference {
                                definition_id: "definition-1".into(),
                                version: 1,
                            },
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
            principal: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                name: Some("State machine".into()),
                context: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinitionReference {
                                definition_id: "definition-1".into(),
                                version: 1,
                            },
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
            principal: bot_principal("driver"),
            group_id: detail.group_id.clone(),
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
    fixture.groups.upsert(group).await.expect("store group");

    let detail = fixture
        .service
        .update(UpdateGroup {
            principal: bot_principal("driver"),
            group_id: "group-1".into(),
            patch: GroupPatch {
                name: Some("Renamed".into()),
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
    assert_eq!(detail.version, stored.version);
    let policy = stored.routing_policy.expect("routing policy");
    assert_eq!(policy.mode, RoutingMode::Structured);
    assert_eq!(policy.sender_routes, sender_routes);
    assert_eq!(
        policy.default_bot_final_delivery,
        DefaultDelivery::InjectObservers
    );
    assert_eq!(stored.label.as_deref(), Some("Renamed"));
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
            principal: bot_principal("outsider"),
            group_id: "group-1".into(),
        })
        .await;
    assert!(matches!(
        denied,
        Err(ApplicationError::Forbidden(_))
    ));

    let first = fixture
        .service
        .delete(DeleteGroup {
            principal: bot_principal("driver"),
            group_id: "group-1".into(),
        })
        .await
        .expect("first delete");
    assert!(first.deleted);

    let second = fixture
        .service
        .delete(DeleteGroup {
            principal: bot_principal("driver"),
            group_id: "group-1".into(),
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
            principal: bot_principal_in_tenant("driver", "tenant-b"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                name: Some("Cross-tenant collaboration".into()),
                context: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
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
async fn manager_worker_manager_can_update_and_delete_group() {
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
    group.originator = Some("driver".into());
    fixture.groups.upsert(group).await.expect("store group");

    fixture
        .service
        .update(UpdateGroup {
            principal: bot_principal("manager"),
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
            principal: bot_principal("manager"),
            group_id: "group-1".into(),
        })
        .await
        .expect("manager may delete");
    assert!(deleted.deleted);
}

#[tokio::test]
async fn delete_does_not_reveal_group_existence_to_unrelated_principal() {
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

    for group_id in ["group-1", "missing"] {
        let result = fixture
            .service
            .delete(DeleteGroup {
                principal: bot_principal("outsider"),
                group_id: group_id.into(),
            })
            .await
            .expect("existence-safe idempotent delete");
        assert!(!result.deleted);
    }
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
            principal: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                name: Some("Before".into()),
                context: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "helper".into(),
                    role: ParticipantRole::Consultant,
                }],
                collaboration:
                    CollaborationConfiguration::StateMachine(
                        bcs_service_api::application::v1::StateMachineConfiguration {
                            definition:
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "definition-1".into(),
                                    version: 1,
                                },
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
            principal: bot_principal("driver"),
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
async fn create_propagates_friendship_lookup_failure() {
    let friends = Arc::new(FriendCore::with_repo(Arc::new(FailingFriendRepo)));
    let fixture = Fixture::new_with_friends(friends).await;
    fixture.add_public_bot("requester").await;
    fixture.add_protected_bot("driver").await;

    let result = fixture
        .service
        .create(CreateGroup {
            principal: bot_principal("requester"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                name: None,
                context: None,
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

    assert!(matches!(
        result,
        Err(ApplicationError::Internal(message)) if message.contains("friend store unavailable")
    ));
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
            principal: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                name: None,
                context: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "helper".into(),
                    role: ParticipantRole::Consultant,
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
            principal: bot_principal("manager"),
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
async fn create_rejects_duplicate_participant_actor_ids() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "helper"] {
        fixture.add_public_bot(bot).await;
    }

    let result = fixture
        .service
        .create(CreateGroup {
            principal: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                name: None,
                context: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![
                    CreateParticipant {
                        actor_id: "helper".into(),
                        role: ParticipantRole::Consultant,
                    },
                    CreateParticipant {
                        actor_id: "helper".into(),
                        role: ParticipantRole::Observer,
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
            principal: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                name: None,
                context: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "manager".into(),
                    role: ParticipantRole::Manager,
                }],
                collaboration: CollaborationConfiguration::Chat(
                    ChatConfiguration {
                        delivery_policy: GroupDeliveryPolicy {
                            bot_final_delivery: BotFinalDelivery::SendToDriver,
                        },
                    },
                ),
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
            principal: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                name: None,
                context: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![
                    CreateParticipant {
                        actor_id: "manager".into(),
                        role: ParticipantRole::Manager,
                    },
                    CreateParticipant {
                        actor_id: "worker".into(),
                        role: ParticipantRole::Worker,
                    },
                ],
                collaboration:
                    CollaborationConfiguration::ManagerWorker(
                        Default::default(),
                    ),
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
                principal: human_principal_with_profile(
                    staff_no,
                    username,
                    display_name,
                    full_name,
                ),
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
            principal: human_principal_with_profile(
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
        principal: bot_principal("bot-a"),
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

    let self_dm = fixture
        .service
        .create(CreateGroup {
            principal: bot_principal("driver"),
            group: CreateGroupSpec::DirectMessage(CreateDirectMessageGroup {
                name: None,
                context: None,
                target_actor_id: "driver".into(),
            }),
        })
        .await;
    assert!(matches!(
        self_dm,
        Err(ApplicationError::InvalidInput { code, .. }) if code == "invalid_request"
    ));

    let public_with_protected = fixture
        .service
        .create(CreateGroup {
            principal: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                name: None,
                context: None,
                visibility: GroupVisibility::Public,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "protected".into(),
                    role: ParticipantRole::Consultant,
                }],
                collaboration: CollaborationConfiguration::Chat(
                    ChatConfiguration {
                        delivery_policy: GroupDeliveryPolicy {
                            bot_final_delivery: BotFinalDelivery::SendToDriver,
                        },
                    },
                ),
            }),
        })
        .await;
    assert!(matches!(
        public_with_protected,
        Err(ApplicationError::Conflict { code, .. }) if code == "non_public_participant"
    ));
}

#[tokio::test]
async fn missing_bot_caller_uses_the_documented_bot_not_found_code() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("target").await;

    let result = fixture
        .service
        .create(CreateGroup {
            principal: bot_principal("missing-caller"),
            group: CreateGroupSpec::DirectMessage(CreateDirectMessageGroup {
                name: None,
                context: None,
                target_actor_id: "target".into(),
            }),
        })
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::NotFound { code, .. }) if code == "bot_not_found"
    ));
}

#[tokio::test]
async fn deleting_dm_maps_the_legacy_rejection_to_contract_conflict() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;
    fixture.add_public_bot("peer").await;
    let detail = fixture
        .service
        .create(CreateGroup {
            principal: bot_principal("driver"),
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
            principal: bot_principal("driver"),
            group_id: detail.group_id,
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
        .list_bot_groups(ListBotGroups {
            principal: bot_principal("target"),
            bot_uuid: "target".into(),
            offset: 0,
            limit: 20,
            q: None,
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
