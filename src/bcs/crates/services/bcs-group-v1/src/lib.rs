//! Versioned Group application facade for the BCN V1 API.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::application::v1::{
    Actor, AddGroupParticipant, ApplicationError, BotFinalDelivery, ChatConfiguration,
    CollaborationConfiguration, CollaborationGroupDetail, CreateCollaborationGroup,
    CreateDirectMessageGroup, CreateGroup, CreateGroupOutcome, CreateGroupSpec, CreateParticipant,
    DeleteGroup, DeleteGroupParticipant, DeleteResult, DirectMessageGroupDetail,
    DirectMessageGroupSummary, GetGroup, GroupDetail, GroupKindFilter, GroupService, GroupStatus,
    GroupStrategy as V1GroupStrategy, GroupSummary, GroupVisibility, HumanPrincipal, ListBotGroups,
    ManagerWorkerConfiguration, Membership, MembershipFilter, NormalGroupSummary, Page,
    Participant as V1Participant, Principal, StateMachineConfiguration, StateMachineDefinitionReference,
    StateMachineParticipantBinding, UpdateGroup, UpdateGroupParticipant,
};
use bcs_service_api::{
    ActorKind, ActorStatus, AuthenticatedHumanCaller, BotRegistryCoreService,
    CollaborationDefinitionRef, CollaborationRuntimeError, CollaborationRuntimeService,
    ConfigureGroupRuntimeCommand, DefaultDelivery, DmCreateCommand, FriendCoreService,
    Group as DomainGroup, GroupAddMemberCommand, GroupCoreService, GroupCreateCommand,
    GroupCreateParticipantCommand, GroupDeleteCommand, GroupKind, GroupManagementService,
    GroupMutableFieldsPatch, GroupParticipantModeCommand, GroupParticipantView,
    GroupRemoveMemberCommand, GroupStrategy, GroupUseCaseError, ParticipantMode,
    RelationCoreService, RoutingMode, RoutingPolicy, RuntimeParticipantBinding, ServiceError,
    SessionManagementService, StartStateMachineRunCommand,
};
use serde_json::Value;

#[derive(Debug, Clone)]
pub struct GroupServiceConfig {
    pub relation_env: String,
}

/// OpenAPI v1 Group facade.
///
/// It owns Principal-based resource authorization and V1 projections while
/// delegating existing group creation/deletion side effects to the legacy-
/// compatible application service. No HTTP type crosses this boundary.
pub struct GroupServiceImpl {
    groups: Arc<dyn GroupCoreService>,
    registry: Arc<dyn BotRegistryCoreService>,
    friends: Arc<dyn FriendCoreService>,
    relation: Arc<dyn RelationCoreService>,
    sessions: Arc<dyn SessionManagementService>,
    management: Arc<dyn GroupManagementService>,
    collaboration_runtime: Option<Arc<dyn CollaborationRuntimeService>>,
    config: GroupServiceConfig,
}

impl GroupServiceImpl {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        groups: Arc<dyn GroupCoreService>,
        registry: Arc<dyn BotRegistryCoreService>,
        friends: Arc<dyn FriendCoreService>,
        relation: Arc<dyn RelationCoreService>,
        sessions: Arc<dyn SessionManagementService>,
        management: Arc<dyn GroupManagementService>,
        config: GroupServiceConfig,
    ) -> Self {
        Self {
            groups,
            registry,
            friends,
            relation,
            sessions,
            management,
            collaboration_runtime: None,
            config,
        }
    }

    pub fn with_collaboration_runtime(
        mut self,
        collaboration_runtime: Arc<dyn CollaborationRuntimeService>,
    ) -> Self {
        self.collaboration_runtime = Some(collaboration_runtime);
        self
    }

    async fn load_bot(
        &self,
        bot_uuid: &str,
    ) -> Result<bcs_service_api::RegisteredBot, ApplicationError> {
        self.registry
            .try_get(bot_uuid)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "bot_not_found",
                    format!("Bot '{bot_uuid}' was not found"),
                )
            })
    }

    async fn authorize_bot_resource(
        &self,
        principal: &Principal,
        bot_uuid: &str,
    ) -> Result<(), ApplicationError> {
        match principal {
            Principal::Bot(bot) if bot.bot_uuid == bot_uuid => {
                self.load_bot(bot_uuid).await?;
                Ok(())
            }
            Principal::Bot(_) => Err(ApplicationError::forbidden(
                "Bot Principal may query only its own bot_uuid",
            )),
            Principal::Human(human) => {
                let bot = self.load_bot(bot_uuid).await?;
                if bot.created_by.as_deref() == Some(human.subject.id.as_str()) {
                    return Ok(());
                }
                let creator_edge = self
                    .relation
                    .get_edge(&principal.actor_id(), bot_uuid, &self.config.relation_env)
                    .await
                    .map_err(map_service_error)?;
                if creator_edge.is_some_and(|edge| edge.is_creator) {
                    return Ok(());
                }
                Err(ApplicationError::forbidden(format!(
                    "Human Principal cannot manage Bot '{bot_uuid}'"
                )))
            }
        }
    }

    async fn ensure_collaboration_eligible(
        &self,
        principal: &Principal,
        bot_uuid: &str,
        field_name: &str,
    ) -> Result<(), ApplicationError> {
        let bot = self.load_bot(bot_uuid).await?;
        if bot.actor_kind != ActorKind::Bot {
            return Err(ApplicationError::invalid(
                "invalid_participant",
                format!("{field_name} must identify a Bot Actor"),
            ));
        }
        if bot.status == ActorStatus::Hidden {
            return Err(ApplicationError::forbidden(format!(
                "Bot '{bot_uuid}' is hidden and cannot collaborate"
            )));
        }
        let principal_actor_id = principal.actor_id();
        if principal_actor_id == bot_uuid || bot.capabilities.visibility == "public" {
            return Ok(());
        }

        if let Principal::Human(human) = principal {
            if bot.created_by.as_deref() == Some(human.subject.id.as_str()) {
                return Ok(());
            }
            let creator_edge = self
                .relation
                .get_edge(&principal_actor_id, bot_uuid, &self.config.relation_env)
                .await
                .map_err(map_service_error)?;
            if creator_edge.is_some_and(|edge| edge.is_creator) {
                return Ok(());
            }
        }

        if self
            .friends
            .try_are_friends(&principal_actor_id, bot_uuid)
            .await
            .map_err(map_service_error)?
        {
            return Ok(());
        }

        Err(ApplicationError::forbidden(format!(
            "Bot '{bot_uuid}' is not collaboration-eligible for this Principal"
        )))
    }

    async fn can_read_group(
        &self,
        principal: &Principal,
        group: &DomainGroup,
    ) -> Result<bool, ApplicationError> {
        let principal_actor_id = principal.actor_id();
        if group
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == principal_actor_id)
        {
            return Ok(true);
        }
        let session_group_ids = self
            .sessions
            .list_group_ids_by_session_participant(&principal_actor_id)
            .await
            .map_err(|error| ApplicationError::internal(error.to_string()))?;
        Ok(session_group_ids.iter().any(|id| id == &group.id))
    }

    fn can_manage_group(principal: &Principal, group: &DomainGroup) -> bool {
        let principal_actor_id = principal.actor_id();
        principal_actor_id == group.driver_bot
            || principal_actor_id == group.originator()
            || (group.group_strategy == GroupStrategy::ManagerWorker
                && group.participants.iter().any(|participant| {
                    participant.bot_uuid == principal_actor_id
                        && participant.role == bcs_service_api::ParticipantRole::Manager
                }))
    }

    async fn load_readable_group(
        &self,
        principal: &Principal,
        group_id: &str,
    ) -> Result<DomainGroup, ApplicationError> {
        let group = self
            .groups
            .try_get(group_id)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "group_not_found",
                    format!("Group '{group_id}' was not found"),
                )
            })?;
        if !self.can_read_group(principal, &group).await? {
            return Err(ApplicationError::forbidden(
                "Principal has no readable relation to this Group",
            ));
        }
        Ok(group)
    }

    async fn load_manageable_group(
        &self,
        principal: &Principal,
        group_id: &str,
    ) -> Result<DomainGroup, ApplicationError> {
        let group = self
            .groups
            .try_get(group_id)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "group_not_found",
                    format!("Group '{group_id}' was not found"),
                )
            })?;
        if !Self::can_manage_group(principal, &group) {
            return Err(ApplicationError::forbidden(
                "Only the Group originator or driver may manage this Group",
            ));
        }
        Ok(group)
    }

    async fn project_detail_with_state_machine(
        &self,
        mut group: DomainGroup,
        state_machine_override: Option<StateMachineConfiguration>,
    ) -> Result<GroupDetail, ApplicationError> {
        bcs_service_api::backfill_bot_names(self.registry.as_ref(), &mut group).await;
        let participants = group
            .participants
            .iter()
            .map(project_participant)
            .collect::<Vec<_>>();
        let common = DetailCommon {
            group_id: group.id.clone(),
            version: group.version,
            name: group.label.clone(),
            status: project_status(group.status),
            visibility: project_visibility(&group.visibility)?,
            context: group.context.clone(),
            originator_actor_id: group.originator().to_string(),
            participants,
            created_at: group.created_at,
            updated_at: group.updated_at,
        };

        if group.group_kind == GroupKind::Dm {
            if common.participants.len() != 2 {
                return Err(ApplicationError::internal(format!(
                    "DM Group '{}' does not contain exactly two participants",
                    group.id
                )));
            }
            return Ok(GroupDetail::DirectMessage(DirectMessageGroupDetail {
                group_id: common.group_id,
                version: common.version,
                name: common.name,
                status: common.status,
                visibility: common.visibility,
                context: common.context,
                originator_actor_id: common.originator_actor_id,
                participants: common.participants,
                created_at: common.created_at,
                updated_at: common.updated_at,
            }));
        }

        let collaboration = match group.group_strategy {
            GroupStrategy::Chat => CollaborationConfiguration::Chat(ChatConfiguration {
                delivery_policy: bcs_service_api::application::v1::GroupDeliveryPolicy {
                    bot_final_delivery: project_delivery(
                        group
                            .routing_policy
                            .as_ref()
                            .map(|policy| policy.default_bot_final_delivery)
                            .unwrap_or_default(),
                    ),
                },
            }),
            GroupStrategy::ManagerWorker => {
                CollaborationConfiguration::ManagerWorker(ManagerWorkerConfiguration::default())
            }
            GroupStrategy::StateMachine => {
                let configuration = match state_machine_override {
                    Some(configuration) => configuration,
                    None => self.load_state_machine_configuration(&group.id).await?,
                };
                CollaborationConfiguration::StateMachine(configuration)
            }
        };

        Ok(GroupDetail::Collaboration(CollaborationGroupDetail {
            group_id: common.group_id,
            version: common.version,
            name: common.name,
            status: common.status,
            visibility: common.visibility,
            context: common.context,
            originator_actor_id: common.originator_actor_id,
            participants: common.participants,
            driver_bot_uuid: group.driver_bot,
            collaboration,
            created_at: common.created_at,
            updated_at: common.updated_at,
        }))
    }

    async fn project_detail(&self, group: DomainGroup) -> Result<GroupDetail, ApplicationError> {
        self.project_detail_with_state_machine(group, None).await
    }

    async fn load_state_machine_configuration(
        &self,
        group_id: &str,
    ) -> Result<StateMachineConfiguration, ApplicationError> {
        let runtime = self.collaboration_runtime.as_ref().ok_or_else(|| {
            ApplicationError::internal(
                "StateMachine Group projection requires CollaborationRuntimeService",
            )
        })?;
        let view = runtime
            .get_group_collaboration_definition(group_id)
            .await
            .map_err(map_runtime_error)?;
        let definition = view.default_definition.ok_or_else(|| {
            ApplicationError::conflict(
                "state_machine_definition_missing",
                "StateMachine Group has no default definition",
            )
        })?;
        let participant_bindings = view
            .participant_bindings
            .into_iter()
            .map(|(binding, value)| StateMachineParticipantBinding {
                binding,
                actor_ids: value.bot_ids,
            })
            .collect();
        Ok(StateMachineConfiguration {
            definition: StateMachineDefinitionReference {
                definition_id: definition.id,
                version: definition.version,
            },
            participant_bindings,
        })
    }

    async fn project_summary(
        &self,
        mut group: DomainGroup,
        target_bot_uuid: &str,
        membership: Membership,
    ) -> Result<GroupSummary, ApplicationError> {
        bcs_service_api::backfill_bot_names(self.registry.as_ref(), &mut group).await;
        let status = project_status(group.status);
        let visibility = project_visibility(&group.visibility)?;
        let originator_actor_id = group.originator().to_string();
        if group.group_kind == GroupKind::Dm {
            let peer_actor = group
                .participants
                .iter()
                .any(|participant| participant.bot_uuid == target_bot_uuid)
                .then(|| {
                    group
                        .participants
                        .iter()
                        .find(|participant| participant.bot_uuid != target_bot_uuid)
                        .map(|participant| Actor {
                            actor_id: participant.bot_uuid.clone(),
                            actor_kind: participant.actor_kind,
                            name: participant.bot_name.clone(),
                        })
                })
                .flatten();
            return Ok(GroupSummary::DirectMessage(DirectMessageGroupSummary {
                group_id: group.id,
                version: group.version,
                name: group.label,
                status,
                visibility,
                membership,
                originator_actor_id,
                participant_count: group.participants.len(),
                peer_actor,
                created_at: group.created_at,
                updated_at: group.updated_at,
            }));
        }

        Ok(GroupSummary::Normal(NormalGroupSummary {
            group_id: group.id,
            version: group.version,
            name: group.label,
            status,
            visibility,
            membership,
            originator_actor_id,
            participant_count: group.participants.len(),
            driver_bot_uuid: group.driver_bot,
            strategy: project_strategy(group.group_strategy),
            created_at: group.created_at,
            updated_at: group.updated_at,
        }))
    }

    async fn create_collaboration(
        &self,
        principal: Principal,
        mut request: CreateCollaborationGroup,
    ) -> Result<GroupDetail, ApplicationError> {
        self.ensure_collaboration_eligible(
            &principal,
            &request.driver_bot_uuid,
            "driver_bot_uuid",
        )
        .await?;
        if request
            .participants
            .iter()
            .any(|participant| participant.actor_id.is_empty())
        {
            return Err(ApplicationError::invalid(
                "invalid_participant",
                "participant actor_id cannot be empty",
            ));
        }
        let mut participant_actor_ids = HashSet::new();
        if request
            .participants
            .iter()
            .any(|participant| !participant_actor_ids.insert(participant.actor_id.as_str()))
        {
            return Err(ApplicationError::invalid(
                "invalid_participant",
                "participant actor_id values must be unique",
            ));
        }

        let principal_actor_id = principal.actor_id();
        if let Principal::Human(human) = &principal
            && request
                .participants
                .iter()
                .any(|participant| participant.actor_id == principal_actor_id)
        {
            self.registry
                .ensure_human_actor(&human.subject.id, &human_display_name(human))
                .await
                .map_err(map_service_error)?;
        }
        let authenticated_human = match &principal {
            Principal::Human(human) => Some(AuthenticatedHumanCaller {
                actor_id: principal_actor_id.clone(),
                display_name: Some(human_display_name(human)),
            }),
            Principal::Bot(_) => None,
        };
        let principal_is_participant = request
            .participants
            .iter()
            .any(|participant| participant.actor_id == principal_actor_id)
            || request.driver_bot_uuid == principal_actor_id;
        let originator = if principal_is_participant {
            principal_actor_id.clone()
        } else {
            request.driver_bot_uuid.clone()
        };
        let (strategy, routing_policy, state_machine) =
            map_create_collaboration(request.collaboration.clone());
        let lead_role = strategy.lead_role();
        if request
            .participants
            .iter()
            .any(|participant| !strategy.allows_role(participant.role))
        {
            return Err(ApplicationError::invalid(
                "invalid_participant",
                "Participant role is not allowed by the selected collaboration strategy",
            ));
        }
        match request
            .participants
            .iter()
            .find(|participant| participant.actor_id == request.driver_bot_uuid)
        {
            Some(driver) if driver.role != lead_role => {
                return Err(ApplicationError::invalid(
                    "invalid_participant",
                    "driver_bot_uuid must have the strategy lead role",
                ));
            }
            None => request.participants.push(CreateParticipant {
                actor_id: request.driver_bot_uuid.clone(),
                role: lead_role,
            }),
            _ => {}
        }
        if request.participants.iter().any(|participant| {
            participant.actor_id != request.driver_bot_uuid && participant.role == lead_role
        }) {
            return Err(ApplicationError::invalid(
                "invalid_participant",
                "Only driver_bot_uuid may have the strategy lead role",
            ));
        }
        if state_machine.is_some() && self.collaboration_runtime.is_none() {
            return Err(ApplicationError::internal(
                "StateMachine creation requires CollaborationRuntimeService",
            ));
        }
        if let Some(state_machine) = &state_machine {
            let mut binding_names = HashSet::new();
            if state_machine
                .participant_bindings
                .iter()
                .any(|binding| !binding_names.insert(binding.binding.as_str()))
            {
                return Err(ApplicationError::invalid(
                    "invalid_participant_binding",
                    "StateMachine participant binding names must be unique",
                ));
            }
            let canonical_actor_ids = request
                .participants
                .iter()
                .map(|participant| participant.actor_id.as_str())
                .chain(std::iter::once(request.driver_bot_uuid.as_str()))
                .collect::<HashSet<_>>();
            if state_machine
                .participant_bindings
                .iter()
                .flat_map(|binding| binding.actor_ids.iter())
                .any(|actor_id| !canonical_actor_ids.contains(actor_id.as_str()))
            {
                return Err(ApplicationError::invalid(
                    "invalid_participant_binding",
                    "StateMachine participant bindings must reference Group participants",
                ));
            }
            let bound_actor_ids = state_machine
                .participant_bindings
                .iter()
                .flat_map(|binding| binding.actor_ids.iter())
                .collect::<HashSet<_>>();
            for actor_id in bound_actor_ids {
                let actor = self.load_bot(actor_id).await?;
                if actor.actor_kind != ActorKind::Bot {
                    return Err(ApplicationError::invalid(
                        "invalid_participant_binding",
                        "StateMachine participant bindings may reference only Bot actors",
                    ));
                }
            }
        }

        let participants = request
            .participants
            .into_iter()
            .map(|participant| GroupCreateParticipantCommand {
                bot_id: participant.actor_id,
                role: Some(role_name(participant.role).to_string()),
            })
            .collect::<Vec<_>>();
        let created = self
            .management
            .create_group(GroupCreateCommand {
                group_id: None,
                caller_actor_id: Some(principal_actor_id.clone()),
                driver_bot_id: request.driver_bot_uuid,
                label: request.name,
                topic: None,
                context: request.context,
                routing_policy,
                participants,
                member_bot_ids: Vec::new(),
                group_kind: Some(GroupKind::Normal),
                service_spec: None,
                group_strategy: Some(strategy),
                originator: Some(originator),
                visibility: Some(visibility_name(request.visibility).to_string()),
            })
            .await
            .map_err(map_group_error)?;

        if let Some(state_machine) = state_machine {
            let response_state_machine = state_machine.clone();
            let runtime = self
                .collaboration_runtime
                .as_ref()
                .expect("StateMachine runtime checked before Group creation");
            let participant_bindings = state_machine
                .participant_bindings
                .into_iter()
                .map(|binding| {
                    (
                        binding.binding,
                        RuntimeParticipantBinding {
                            source: "manual".to_string(),
                            bot_ids: binding.actor_ids,
                            extensions: Default::default(),
                        },
                    )
                })
                .collect::<BTreeMap<_, _>>();
            let configured = match runtime
                .configure_group_runtime(ConfigureGroupRuntimeCommand {
                    group_id: created.group_id.clone(),
                    definition_yaml: None,
                    definition: None,
                    definition_ref: Some(CollaborationDefinitionRef {
                        id: state_machine.definition.definition_id,
                        version: state_machine.definition.version,
                    }),
                    participant_bindings,
                    auto_start_on_service_invocation: true,
                })
                .await
            {
                Ok(configured) => configured,
                Err(error) => {
                    let (session_cleanup_error, group_cleanup_error) = self
                        .rollback_state_machine_creation(
                            runtime.as_ref(),
                            &created.group_id,
                            created.latest_running_session_id.as_deref(),
                        )
                        .await;
                    return Err(map_runtime_and_rollback_error(
                        error,
                        session_cleanup_error,
                        group_cleanup_error,
                    ));
                }
            };
            if configured.requires_human_input_channel {
                let group = self
                    .groups
                    .try_get(&created.group_id)
                    .await
                    .map_err(map_service_error)?
                    .ok_or_else(|| {
                        ApplicationError::internal(
                            "created Group disappeared before deferred-run projection",
                        )
                    })?;
                return self
                    .project_detail_with_state_machine(group, Some(response_state_machine))
                    .await;
            }

            let session_id = match created.latest_running_session_id.clone() {
                Some(session_id) => session_id,
                None => {
                    let error = CollaborationRuntimeError::Internal(
                        ServiceError::InternalError(
                            "StateMachine Group creation did not produce an initial ServiceInvocation session"
                                .to_string(),
                        ),
                    );
                    let (session_cleanup_error, group_cleanup_error) = self
                        .rollback_state_machine_creation(runtime.as_ref(), &created.group_id, None)
                        .await;
                    return Err(map_runtime_and_rollback_error(
                        error,
                        session_cleanup_error,
                        group_cleanup_error,
                    ));
                }
            };
            let session = match self.sessions.get(&session_id).await {
                Ok(Some(session)) => session,
                Ok(None) => {
                    let error = CollaborationRuntimeError::Internal(ServiceError::InternalError(
                        "StateMachine initial ServiceInvocation session disappeared before start"
                            .to_string(),
                    ));
                    let (session_cleanup_error, group_cleanup_error) = self
                        .rollback_state_machine_creation(
                            runtime.as_ref(),
                            &created.group_id,
                            Some(&session_id),
                        )
                        .await;
                    return Err(map_runtime_and_rollback_error(
                        error,
                        session_cleanup_error,
                        group_cleanup_error,
                    ));
                }
                Err(error) => {
                    let error = CollaborationRuntimeError::Internal(ServiceError::InternalError(
                        error.to_string(),
                    ));
                    let (session_cleanup_error, group_cleanup_error) = self
                        .rollback_state_machine_creation(
                            runtime.as_ref(),
                            &created.group_id,
                            Some(&session_id),
                        )
                        .await;
                    return Err(map_runtime_and_rollback_error(
                        error,
                        session_cleanup_error,
                        group_cleanup_error,
                    ));
                }
            };
            if let Err(error) = runtime
                .start_state_machine_run(StartStateMachineRunCommand {
                    group_id: created.group_id.clone(),
                    session_id: Some(session.id),
                    definition_yaml: None,
                    definition: None,
                    definition_ref: None,
                    participant_bindings: None,
                    input: session.input.unwrap_or(Value::Null),
                    caller_id: Some(principal_actor_id),
                    authenticated_human,
                })
                .await
            {
                let (session_cleanup_error, group_cleanup_error) = self
                    .rollback_state_machine_creation(
                        runtime.as_ref(),
                        &created.group_id,
                        Some(&session_id),
                    )
                    .await;
                return Err(map_runtime_and_rollback_error(
                    error,
                    session_cleanup_error,
                    group_cleanup_error,
                ));
            }

            let group = self
                .groups
                .try_get(&created.group_id)
                .await
                .map_err(map_service_error)?
                .ok_or_else(|| {
                    ApplicationError::internal("created Group disappeared before projection")
                })?;
            return self
                .project_detail_with_state_machine(group, Some(response_state_machine))
                .await;
        }

        let group = self
            .groups
            .try_get(&created.group_id)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::internal("created Group disappeared before projection")
            })?;
        self.project_detail(group).await
    }

    async fn rollback_state_machine_creation(
        &self,
        runtime: &dyn CollaborationRuntimeService,
        group_id: &str,
        session_id: Option<&str>,
    ) -> (Option<String>, Option<String>) {
        let mut runtime_cleanup_errors = Vec::new();
        if let Err(error) = runtime
            .cancel_group_runs(group_id, "state_machine_creation_failed")
            .await
        {
            runtime_cleanup_errors.push(format!("run cancellation: {error}"));
        }
        if let Err(error) = runtime.delete_group_runtime_state(group_id).await {
            runtime_cleanup_errors.push(format!("runtime state: {error}"));
        }
        if let Some(session_id) = session_id
            && let Err(error) = self.sessions.delete(session_id).await
        {
            runtime_cleanup_errors.push(format!("initial session: {error}"));
        }
        let runtime_cleanup_error =
            (!runtime_cleanup_errors.is_empty()).then(|| runtime_cleanup_errors.join("; "));
        let group_cleanup_error = self
            .groups
            .delete(group_id)
            .await
            .err()
            .map(|cleanup| cleanup.to_string());
        (runtime_cleanup_error, group_cleanup_error)
    }

    async fn create_dm(
        &self,
        principal: Principal,
        request: CreateDirectMessageGroup,
    ) -> Result<CreateGroupOutcome, ApplicationError> {
        self.ensure_collaboration_eligible(
            &principal,
            &request.target_actor_id,
            "target_actor_id",
        )
        .await?;
        if let Principal::Human(human) = &principal {
            self.registry
                .ensure_human_actor(&human.subject.id, &human_display_name(human))
                .await
                .map_err(map_service_error)?;
        }
        let result = self
            .management
            .create_dm(DmCreateCommand {
                group_id: None,
                caller_actor_id: Some(principal.actor_id()),
                driver_bot: None,
                target_actor_id: request.target_actor_id,
                label: request.name,
                topic: None,
                context: request.context,
            })
            .await
            .map_err(map_group_error)?;
        let group = self
            .groups
            .try_get(&result.group.group_id)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::internal("created DM Group disappeared before projection")
            })?;
        Ok(CreateGroupOutcome {
            group: self.project_detail(group).await?,
            created: result.created,
        })
    }
}

#[async_trait]
impl GroupService for GroupServiceImpl {
    async fn list_bot_groups(
        &self,
        command: ListBotGroups,
    ) -> Result<Page<GroupSummary>, ApplicationError> {
        self.authorize_bot_resource(&command.principal, &command.bot_uuid)
            .await?;
        if command.limit == 0 || command.limit > 100 {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "limit must be between 1 and 100",
            ));
        }
        if command.kind == GroupKindFilter::Dm && command.strategy.is_some() {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "kind=dm cannot be combined with strategy",
            ));
        }

        let direct = self
            .groups
            .try_find_by_participant(&command.bot_uuid)
            .await
            .map_err(map_service_error)?
            .into_iter()
            .map(|group| (group.id.clone(), (group, Membership::Direct)))
            .collect::<HashMap<_, _>>();
        let session_group_ids = self
            .sessions
            .list_group_ids_by_session_participant(&command.bot_uuid)
            .await
            .map_err(|error| ApplicationError::internal(error.to_string()))?;
        let mut related = direct;
        for group_id in session_group_ids {
            if related.contains_key(&group_id) {
                continue;
            }
            if let Some(group) = self
                .groups
                .try_get(&group_id)
                .await
                .map_err(map_service_error)?
            {
                related.insert(group_id, (group, Membership::SessionOnly));
            }
        }

        let q = command
            .q
            .as_deref()
            .map(str::trim)
            .filter(|query| !query.is_empty())
            .map(str::to_lowercase);
        let mut groups = related
            .into_values()
            .filter(|(_, membership)| match command.membership {
                MembershipFilter::All => true,
                MembershipFilter::Direct => *membership == Membership::Direct,
                MembershipFilter::SessionOnly => *membership == Membership::SessionOnly,
            })
            .filter(|(group, _)| match command.kind {
                GroupKindFilter::Normal => group.group_kind == GroupKind::Normal,
                GroupKindFilter::Dm => group.group_kind == GroupKind::Dm,
                GroupKindFilter::All => true,
            })
            .filter(|(group, _)| {
                command.strategy.is_none_or(|strategy| {
                    group.group_kind == GroupKind::Normal
                        && project_strategy(group.group_strategy) == strategy
                })
            })
            .filter(|(group, _)| {
                q.as_ref().is_none_or(|query| {
                    group
                        .label
                        .as_deref()
                        .unwrap_or("")
                        .to_lowercase()
                        .contains(query)
                })
            })
            .collect::<Vec<_>>();
        groups.sort_by(|(left, _), (right, _)| DomainGroup::cmp_by_updated_at_desc(left, right));
        let total = groups.len() as u64;
        let page = groups
            .into_iter()
            .skip(saturating_usize(command.offset))
            .take(saturating_usize(command.limit))
            .collect::<Vec<_>>();
        let mut items = Vec::with_capacity(page.len());
        for (group, membership) in page {
            items.push(
                self.project_summary(group, &command.bot_uuid, membership)
                    .await?,
            );
        }
        Ok(Page {
            items,
            total,
            offset: command.offset,
            limit: command.limit,
        })
    }

    async fn create(&self, command: CreateGroup) -> Result<GroupDetail, ApplicationError> {
        Ok(self.create_with_outcome(command).await?.group)
    }

    async fn create_with_outcome(
        &self,
        command: CreateGroup,
    ) -> Result<CreateGroupOutcome, ApplicationError> {
        match command.group {
            CreateGroupSpec::Collaboration(request) => Ok(CreateGroupOutcome {
                group: self
                    .create_collaboration(command.principal, request)
                    .await?,
                created: true,
            }),
            CreateGroupSpec::DirectMessage(request) => {
                self.create_dm(command.principal, request).await
            }
        }
    }

    async fn get(&self, query: GetGroup) -> Result<GroupDetail, ApplicationError> {
        let group = self
            .load_readable_group(&query.principal, &query.group_id)
            .await?;
        self.project_detail(group).await
    }

    async fn update(&self, command: UpdateGroup) -> Result<GroupDetail, ApplicationError> {
        if command.patch.is_empty() {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "at least one mutable field is required",
            ));
        }
        let mut group = self
            .load_manageable_group(&command.principal, &command.group_id)
            .await?;
        if group.group_kind == GroupKind::Dm {
            if command.patch.delivery_policy.is_some()
                || command.patch.visibility == Some(GroupVisibility::Public)
            {
                return Err(ApplicationError::invalid(
                    "invalid_request",
                    "DM Groups do not expose delivery policy or public visibility",
                ));
            }
        }
        if command.patch.delivery_policy.is_some()
            && group.group_strategy != GroupStrategy::Chat
        {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "delivery_policy may be updated only for Chat Groups",
            ));
        }
        let patch = command.patch;
        let mut persistence_patch = GroupMutableFieldsPatch::default();
        if let Some(name) = patch.name {
            group.label = Some(name.clone());
            persistence_patch.label = Some(name);
        }
        if let Some(context) = patch.context {
            group.context = Some(context.clone());
            persistence_patch.context = Some(context);
        }
        if let Some(visibility) = patch.visibility {
            if visibility == GroupVisibility::Public {
                for participant in &group.participants {
                    if participant.actor_kind != ActorKind::Bot {
                        continue;
                    }
                    let bot = self.load_bot(&participant.bot_uuid).await?;
                    if bot.capabilities.visibility != "public" {
                        return Err(ApplicationError::conflict(
                            "non_public_participant",
                            "All Bot participants must be public before Group visibility is public",
                        ));
                    }
                }
            }
            group.visibility = visibility_name(visibility).to_string();
            persistence_patch.visibility = Some(group.visibility.clone());
        }
        if let Some(delivery_policy) = patch.delivery_policy {
            let policy = group
                .routing_policy
                .get_or_insert_with(RoutingPolicy::default);
            policy.default_bot_final_delivery =
                persist_delivery(delivery_policy.bot_final_delivery);
            persistence_patch.default_bot_final_delivery = Some(policy.default_bot_final_delivery);
        }
        let state_machine_projection = if group.group_strategy == GroupStrategy::StateMachine {
            Some(
                self.load_state_machine_configuration(&command.group_id)
                    .await?,
            )
        } else {
            None
        };
        self.groups
            .patch_mutable_fields(&command.group_id, persistence_patch)
            .await
            .map_err(map_service_error)?;
        let persisted = self
            .groups
            .try_get(&command.group_id)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                map_service_error(ServiceError::GroupNotFound(command.group_id.clone()))
            })?;
        self.project_detail_with_state_machine(persisted, state_machine_projection)
            .await
    }

    async fn delete(&self, command: DeleteGroup) -> Result<DeleteResult, ApplicationError> {
        let Some(group) = self
            .groups
            .try_get(&command.group_id)
            .await
            .map_err(map_service_error)?
        else {
            if let Some(runtime) = self.collaboration_runtime.as_ref() {
                runtime
                    .cancel_group_runs(&command.group_id, "group_deleted")
                    .await
                    .map_err(map_runtime_error)?;
                runtime
                    .delete_group_runtime_state(&command.group_id)
                    .await
                    .map_err(map_runtime_error)?;
            }
            return Ok(DeleteResult {
                deleted: false,
            });
        };
        if !Self::can_manage_group(&command.principal, &group) {
            return Ok(DeleteResult {
                deleted: false,
            });
        }
        let state_machine_runtime = if group.group_strategy == GroupStrategy::StateMachine {
            Some(self.collaboration_runtime.as_ref().ok_or_else(|| {
                ApplicationError::internal(
                    "StateMachine Group deletion requires CollaborationRuntimeService",
                )
            })?)
        } else {
            None
        };
        let result = self
            .management
            .delete_group(GroupDeleteCommand {
                caller_actor_id: command.principal.actor_id(),
                group_id: command.group_id,
            })
            .await
            .map_err(map_delete_group_error)?;
        if result.deleted && let Some(runtime) = state_machine_runtime {
            runtime
                .cancel_group_runs(&result.group_id, "group_deleted")
                .await
                .map_err(map_runtime_error)?;
            runtime
                .delete_group_runtime_state(&result.group_id)
                .await
                .map_err(map_runtime_error)?;
        }
        Ok(DeleteResult {
            deleted: result.deleted,
        })
    }

    async fn add_participant(
        &self,
        command: AddGroupParticipant,
    ) -> Result<V1Participant, ApplicationError> {
        let group = self
            .load_readable_group(&command.principal, &command.group_id)
            .await?;
        if !Self::can_manage_group(&command.principal, &group) {
            return Err(ApplicationError::forbidden(
                "Principal cannot manage the group",
            ));
        }
        // `AddGroupParticipant` carries no `actor_kind`; resolve it from the
        // registry so legacy `add_member` gets the right Bot/Human split.
        let actor_kind = if self
            .registry
            .try_get(&command.actor_id)
            .await
            .map_err(map_service_error)?
            .is_some()
        {
            ActorKind::Bot
        } else {
            ActorKind::Human
        };
        let (bot_id, human_actor_id) = match actor_kind {
            ActorKind::Bot => (command.actor_id.clone(), None),
            ActorKind::Human => (String::new(), Some(command.actor_id.clone())),
        };
        let result = self
            .management
            .add_member(GroupAddMemberCommand {
                caller_actor_id: Some(command.principal.actor_id()),
                human_actor_id,
                group_id: command.group_id.clone(),
                bot_id,
                role: Some(role_name(command.role).to_string()),
            })
            .await
            .map_err(map_group_error)?;
        Ok(participant_view_to_v1(result.member))
    }

    async fn update_participant(
        &self,
        command: UpdateGroupParticipant,
    ) -> Result<V1Participant, ApplicationError> {
        let group = self
            .load_readable_group(&command.principal, &command.group_id)
            .await?;
        // Design §8.7: the target Actor may update its own participant mode
        // (self-service) in addition to the driver/originator/manager path.
        let is_self = command.principal.actor_id() == command.actor_id;
        if !is_self && !Self::can_manage_group(&command.principal, &group) {
            return Err(ApplicationError::forbidden(
                "Principal cannot manage the group",
            ));
        }
        self.management
            .update_participant_mode(GroupParticipantModeCommand {
                caller_actor_id: command.principal.actor_id(),
                group_id: command.group_id.clone(),
                actor_id: command.actor_id.clone(),
                mode: command.mode,
            })
            .await
            .map_err(map_group_error)?;
        // Reload the group and project the updated participant via the shared
        // domain Participant -> V1 Participant projection used by get/create.
        let group = self
            .groups
            .try_get(&command.group_id)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "group_not_found",
                    format!("Group '{}' was not found", command.group_id),
                )
            })?;
        group
            .participants
            .iter()
            .find(|p| p.bot_uuid == command.actor_id)
            .map(project_participant)
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "participant_not_found",
                    format!("Participant '{}' not found", command.actor_id),
                )
            })
    }

    async fn delete_participant(
        &self,
        command: DeleteGroupParticipant,
    ) -> Result<DeleteResult, ApplicationError> {
        let group = self
            .load_readable_group(&command.principal, &command.group_id)
            .await?;
        // Design §8.7: the target Actor may leave the group (self-service
        // delete) in addition to the driver/originator/manager path. The legacy
        // `remove_member` still rejects driver/originator removal, preserving
        // the role invariant; non-driver self-leave proceeds.
        let is_self = command.principal.actor_id() == command.actor_id;
        if !is_self && !Self::can_manage_group(&command.principal, &group) {
            return Err(ApplicationError::forbidden(
                "Principal cannot manage the group",
            ));
        }
        // Phase one: target is a Bot actor (legacy `remove_member` uses bot_id).
        // The V1 contract treats an already-removed/missing participant as
        // idempotent success, so swallow `ParticipantNotFound` into
        // `DeleteResult { deleted: false }` (mirroring the group-level `delete`
        // facade's not-found handling) instead of surfacing a 404.
        match self
            .management
            .remove_member(GroupRemoveMemberCommand {
                caller_actor_id: Some(command.principal.actor_id()),
                group_id: command.group_id.clone(),
                bot_id: command.actor_id.clone(),
            })
            .await
        {
            Ok(_) => Ok(DeleteResult { deleted: true }),
            Err(GroupUseCaseError::Service(ServiceError::ParticipantNotFound(_))) => {
                Ok(DeleteResult { deleted: false })
            }
            Err(error) => Err(map_group_error(error)),
        }
    }
}

#[derive(Debug)]
struct DetailCommon {
    group_id: String,
    version: i32,
    name: Option<String>,
    status: GroupStatus,
    visibility: GroupVisibility,
    context: Option<String>,
    originator_actor_id: String,
    participants: Vec<V1Participant>,
    created_at: u64,
    updated_at: u64,
}

fn project_participant(participant: &bcs_service_api::Participant) -> V1Participant {
    V1Participant {
        actor_id: participant.bot_uuid.clone(),
        actor_kind: participant.actor_kind,
        name: participant.bot_name.clone(),
        role: participant.role,
        mode: participant.effective_mode(),
    }
}

/// Project a legacy `GroupParticipantView` (returned by `add_member`) into the
/// V1 `Participant` shape. The view carries `role` as a wire string, so it is
/// parsed back into the typed enum; a missing `mode` falls back to the
/// kind-aware default, mirroring `Participant::effective_mode`.
fn participant_view_to_v1(view: GroupParticipantView) -> V1Participant {
    V1Participant {
        actor_id: view.bot_uuid,
        actor_kind: view.actor_kind,
        name: view.bot_name,
        role: parse_participant_role(&view.role),
        mode: view
            .mode
            .unwrap_or_else(|| ParticipantMode::default_for(view.actor_kind)),
    }
}

fn parse_participant_role(role: &str) -> bcs_service_api::ParticipantRole {
    match role {
        "driver" => bcs_service_api::ParticipantRole::Driver,
        "manager" => bcs_service_api::ParticipantRole::Manager,
        "worker" => bcs_service_api::ParticipantRole::Worker,
        "observer" => bcs_service_api::ParticipantRole::Observer,
        _ => bcs_service_api::ParticipantRole::Consultant,
    }
}

fn project_status(status: bcs_service_api::GroupStatus) -> GroupStatus {
    match status {
        bcs_service_api::GroupStatus::Active => GroupStatus::Active,
        bcs_service_api::GroupStatus::Completed => GroupStatus::Completed,
        bcs_service_api::GroupStatus::Error => GroupStatus::Error,
        bcs_service_api::GroupStatus::Closed => GroupStatus::Closed,
        bcs_service_api::GroupStatus::Inactive => GroupStatus::Inactive,
    }
}

fn project_visibility(visibility: &str) -> Result<GroupVisibility, ApplicationError> {
    match visibility {
        "private" => Ok(GroupVisibility::Private),
        "public" => Ok(GroupVisibility::Public),
        other => Err(ApplicationError::internal(format!(
            "stored Group has unsupported visibility '{other}'"
        ))),
    }
}

fn project_strategy(strategy: GroupStrategy) -> V1GroupStrategy {
    match strategy {
        GroupStrategy::Chat => V1GroupStrategy::Chat,
        GroupStrategy::ManagerWorker => V1GroupStrategy::ManagerWorker,
        GroupStrategy::StateMachine => V1GroupStrategy::StateMachine,
    }
}

fn project_delivery(delivery: DefaultDelivery) -> BotFinalDelivery {
    match delivery {
        DefaultDelivery::SendToDriver => BotFinalDelivery::SendToDriver,
        DefaultDelivery::InjectObservers => BotFinalDelivery::InjectObservers,
    }
}

fn persist_delivery(delivery: BotFinalDelivery) -> DefaultDelivery {
    match delivery {
        BotFinalDelivery::SendToDriver => DefaultDelivery::SendToDriver,
        BotFinalDelivery::InjectObservers => DefaultDelivery::InjectObservers,
    }
}

fn map_create_collaboration(
    collaboration: CollaborationConfiguration,
) -> (
    GroupStrategy,
    Option<RoutingPolicy>,
    Option<StateMachineConfiguration>,
) {
    match collaboration {
        CollaborationConfiguration::Chat(configuration) => (
            GroupStrategy::Chat,
            Some(RoutingPolicy {
                mode: RoutingMode::Hybrid,
                default_bot_final_delivery: persist_delivery(
                    configuration.delivery_policy.bot_final_delivery,
                ),
                sender_routes: HashMap::new(),
            }),
            None,
        ),
        CollaborationConfiguration::ManagerWorker(_) => (GroupStrategy::ManagerWorker, None, None),
        CollaborationConfiguration::StateMachine(configuration) => {
            (GroupStrategy::StateMachine, None, Some(configuration))
        }
    }
}

fn role_name(role: bcs_service_api::ParticipantRole) -> &'static str {
    match role {
        bcs_service_api::ParticipantRole::Driver => "driver",
        bcs_service_api::ParticipantRole::Consultant => "consultant",
        bcs_service_api::ParticipantRole::Manager => "manager",
        bcs_service_api::ParticipantRole::Worker => "worker",
        bcs_service_api::ParticipantRole::Observer => "observer",
    }
}

fn human_display_name(human: &HumanPrincipal) -> String {
    human
        .subject
        .display_name
        .clone()
        .or_else(|| human.subject.full_name.clone())
        .unwrap_or_else(|| human.subject.username.clone())
}

fn visibility_name(visibility: GroupVisibility) -> &'static str {
    match visibility {
        GroupVisibility::Private => "private",
        GroupVisibility::Public => "public",
    }
}

fn saturating_usize(value: u64) -> usize {
    usize::try_from(value).unwrap_or(usize::MAX)
}

fn map_group_error(error: GroupUseCaseError) -> ApplicationError {
    match error {
        GroupUseCaseError::Unauthorized(_) => ApplicationError::Unauthenticated,
        GroupUseCaseError::Forbidden(message) => ApplicationError::forbidden(message),
        GroupUseCaseError::InvalidGroupId(message)
        | GroupUseCaseError::InvalidGroupStatus(message)
        | GroupUseCaseError::InvalidProposal(message) => {
            ApplicationError::invalid("invalid_request", message)
        }
        GroupUseCaseError::ProposalNotFound(message)
        | GroupUseCaseError::ProposalExpired(message) => {
            ApplicationError::not_found("not_found", message)
        }
        GroupUseCaseError::InvalidHistoryLimit(limit) => {
            ApplicationError::invalid("invalid_request", format!("invalid history limit {limit}"))
        }
        GroupUseCaseError::ActorNotFound(actor_id) => {
            ApplicationError::not_found("bot_not_found", format!("Bot '{actor_id}' was not found"))
        }
        GroupUseCaseError::InvalidParticipantMode { .. } => {
            ApplicationError::invalid("invalid_participant", error.to_string())
        }
        GroupUseCaseError::Conflict(message) => ApplicationError::conflict("conflict", message),
        GroupUseCaseError::Service(error) => map_service_error(error),
    }
}

fn map_delete_group_error(error: GroupUseCaseError) -> ApplicationError {
    match error {
        GroupUseCaseError::InvalidProposal(message)
            if message == "DM groups cannot be deleted or left" =>
        {
            ApplicationError::conflict("conflict", message)
        }
        other => map_group_error(other),
    }
}

fn map_service_error(error: ServiceError) -> ApplicationError {
    match error {
        ServiceError::GroupNotFound(id) => {
            ApplicationError::not_found("group_not_found", format!("Group '{id}' was not found"))
        }
        ServiceError::BotNotFound(id) | ServiceError::BotNotRegistered(id) => {
            ApplicationError::not_found("bot_not_found", format!("Bot '{id}' was not found"))
        }
        ServiceError::ParticipantNotFound(id) => ApplicationError::not_found(
            "participant_not_found",
            format!("Participant '{id}' was not found"),
        ),
        ServiceError::Unauthorized(_) => ApplicationError::Unauthenticated,
        ServiceError::Forbidden(message) => ApplicationError::forbidden(message),
        ServiceError::Conflict(message) => ApplicationError::conflict("conflict", message),
        ServiceError::InvalidOperation { message, .. }
        | ServiceError::SessionInvalidParams(message) => {
            ApplicationError::invalid("invalid_request", message)
        }
        ServiceError::ExistNonPublicBots { .. } => ApplicationError::conflict(
            "non_public_participant",
            "All Bot participants must be public for this operation",
        ),
        ServiceError::BotHidden(id) => {
            ApplicationError::forbidden(format!("Bot '{id}' is hidden and cannot collaborate"))
        }
        ServiceError::PrivateBotCannotCollaborate => {
            ApplicationError::forbidden("Private Bot cannot collaborate")
        }
        ServiceError::NotFriends(_) => {
            ApplicationError::forbidden("Actors are not collaboration-eligible")
        }
        other => ApplicationError::internal(other.to_string()),
    }
}

fn map_runtime_error(error: CollaborationRuntimeError) -> ApplicationError {
    match error {
        CollaborationRuntimeError::DefinitionNotFound(id, version) => ApplicationError::not_found(
            "collaboration_definition_not_found",
            format!("Collaboration definition '{id}@{version}' was not found"),
        ),
        CollaborationRuntimeError::InvalidDefinition(message)
        | CollaborationRuntimeError::InvalidParticipantBinding(message)
        | CollaborationRuntimeError::InvalidRequest(message) => {
            ApplicationError::invalid("invalid_request", message)
        }
        CollaborationRuntimeError::Unauthenticated => ApplicationError::Unauthenticated,
        CollaborationRuntimeError::Forbidden(message) => ApplicationError::forbidden(message),
        CollaborationRuntimeError::Conflict(message) => {
            ApplicationError::conflict("conflict", message)
        }
        other => ApplicationError::internal(other.to_string()),
    }
}

fn map_runtime_and_rollback_error(
    runtime_error: CollaborationRuntimeError,
    session_cleanup_error: Option<String>,
    group_cleanup_error: Option<String>,
) -> ApplicationError {
    if session_cleanup_error.is_none() && group_cleanup_error.is_none() {
        return map_runtime_error(runtime_error);
    }
    ApplicationError::internal(format!(
        "StateMachine runtime configuration failed: {runtime_error}; rollback failed: session cleanup: {}; group cleanup: {}",
        session_cleanup_error.as_deref().unwrap_or("ok"),
        group_cleanup_error.as_deref().unwrap_or("ok"),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn assert_code(error: ApplicationError, expected: &str) {
        assert_eq!(error.code(), expected);
    }

    #[test]
    fn group_use_case_errors_map_to_stable_v1_codes() {
        assert_code(
            map_group_error(GroupUseCaseError::Unauthorized("missing principal".into())),
            "unauthenticated",
        );
        assert_code(
            map_group_error(GroupUseCaseError::Forbidden("denied".into())),
            "forbidden",
        );
        assert_code(
            map_group_error(GroupUseCaseError::InvalidGroupId("bad id".into())),
            "invalid_request",
        );
        assert_code(
            map_group_error(GroupUseCaseError::InvalidGroupStatus("closed".into())),
            "invalid_request",
        );
        assert_code(
            map_group_error(GroupUseCaseError::InvalidProposal("bad proposal".into())),
            "invalid_request",
        );
        assert_code(
            map_group_error(GroupUseCaseError::ProposalNotFound("proposal-1".into())),
            "not_found",
        );
        assert_code(
            map_group_error(GroupUseCaseError::ProposalExpired("proposal-2".into())),
            "not_found",
        );
        assert_code(
            map_group_error(GroupUseCaseError::InvalidHistoryLimit(0)),
            "invalid_request",
        );
        assert_code(
            map_group_error(GroupUseCaseError::InvalidParticipantMode {
                mode: ParticipantMode::Auto,
                actor_kind: ActorKind::Human,
            }),
            "invalid_participant",
        );
        assert_code(
            map_group_error(GroupUseCaseError::Conflict("version mismatch".into())),
            "conflict",
        );
        assert_code(
            map_group_error(GroupUseCaseError::Service(ServiceError::BotNotFound(
                "bot-1".into(),
            ))),
            "bot_not_found",
        );
    }

    #[test]
    fn service_errors_map_to_stable_v1_codes() {
        assert_code(
            map_service_error(ServiceError::GroupNotFound("group-1".into())),
            "group_not_found",
        );
        assert_code(
            map_service_error(ServiceError::BotNotFound("bot-1".into())),
            "bot_not_found",
        );
        assert_code(
            map_service_error(ServiceError::BotNotRegistered("bot-2".into())),
            "bot_not_found",
        );
        assert_code(
            map_service_error(ServiceError::ParticipantNotFound("bot-3".into())),
            "participant_not_found",
        );
        assert_code(
            map_service_error(ServiceError::Unauthorized("missing principal".into())),
            "unauthenticated",
        );
        assert_code(
            map_service_error(ServiceError::Forbidden("denied".into())),
            "forbidden",
        );
        assert_code(
            map_service_error(ServiceError::Conflict("version mismatch".into())),
            "conflict",
        );
        assert_code(
            map_service_error(ServiceError::InvalidOperation {
                message: "bad patch".into(),
                request_id: Some("request-1".into()),
            }),
            "invalid_request",
        );
        assert_code(
            map_service_error(ServiceError::SessionInvalidParams("bad session".into())),
            "invalid_request",
        );
        assert_code(
            map_service_error(ServiceError::ExistNonPublicBots { bots: Vec::new() }),
            "non_public_participant",
        );
        assert_code(
            map_service_error(ServiceError::BotHidden("bot-4".into())),
            "forbidden",
        );
        assert_code(
            map_service_error(ServiceError::PrivateBotCannotCollaborate),
            "forbidden",
        );
        assert_code(
            map_service_error(ServiceError::NotFriends(vec!["bot-5".into()])),
            "forbidden",
        );
        assert_code(
            map_service_error(ServiceError::InternalError("database unavailable".into())),
            "internal_error",
        );
    }

    #[test]
    fn collaboration_runtime_errors_map_to_stable_v1_codes() {
        assert_code(
            map_runtime_error(CollaborationRuntimeError::DefinitionNotFound(
                "definition-1".into(),
                1,
            )),
            "collaboration_definition_not_found",
        );
        assert_code(
            map_runtime_error(CollaborationRuntimeError::InvalidDefinition(
                "bad definition".into(),
            )),
            "invalid_request",
        );
        assert_code(
            map_runtime_error(CollaborationRuntimeError::InvalidParticipantBinding(
                "bad binding".into(),
            )),
            "invalid_request",
        );
        assert_code(
            map_runtime_error(CollaborationRuntimeError::InvalidRequest("bad request".into())),
            "invalid_request",
        );
        assert_code(
            map_runtime_error(CollaborationRuntimeError::Unauthenticated),
            "unauthenticated",
        );
        assert_code(
            map_runtime_error(CollaborationRuntimeError::Forbidden("denied".into())),
            "forbidden",
        );
        assert_code(
            map_runtime_error(CollaborationRuntimeError::Conflict("run active".into())),
            "conflict",
        );
        assert_code(
            map_runtime_error(CollaborationRuntimeError::RunNotFound("run-1".into())),
            "internal_error",
        );
    }

    #[test]
    fn runtime_rollback_failure_preserves_primary_and_cleanup_errors() {
        let error = map_runtime_and_rollback_error(
            CollaborationRuntimeError::InvalidRequest("bad definition".to_string()),
            Some("session store unavailable".to_string()),
            Some("group store unavailable".to_string()),
        );

        assert!(matches!(
            error,
            ApplicationError::Internal(message)
                if message.contains("bad definition")
                    && message.contains("session store unavailable")
                    && message.contains("group store unavailable")
        ));
    }

    #[test]
    fn successful_runtime_rollback_keeps_client_error_classification() {
        let error = map_runtime_and_rollback_error(
            CollaborationRuntimeError::InvalidParticipantBinding("unknown actor".to_string()),
            None,
            None,
        );

        assert!(matches!(
            error,
            ApplicationError::InvalidInput { code, message }
                if code == "invalid_request" && message == "unknown actor"
        ));
    }
}
