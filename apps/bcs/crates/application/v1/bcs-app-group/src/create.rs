//! Group creation and Event Subscription provisioning flows for the V1 facade.

use super::*;

impl GroupServiceImpl {
    pub(crate) async fn create_collaboration(
        &self,
        principal: Principal,
        mut request: CreateCollaborationGroup,
        group_id: Option<String>,
        provisioning: bool,
    ) -> Result<(GroupDetail, Option<String>, Option<bcs_service_api::InitialGroupRun>), ApplicationError> {
        let originator = request
            .originator
            .take()
            .unwrap_or_else(|| principal.actor_id());
        self.authorize_originator(&principal, &originator)
            .await?;
        // Structural validity only: the driver must be a registered Bot Actor.
        // The driver↔originator reachability gate runs in the core loop
        // (GroupManagement::create_group), where every Bot participant —
        // including the driver when it differs from the originator — is
        // anchored on the originator (public, owned-by-human, or a friend of a
        // bot originator).
        self.ensure_driver_is_registered_bot(&request.driver_bot_uuid)
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
        if let Principal::Human(human) = &principal {
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
        let (strategy, routing_policy, state_machine) =
            map_create_collaboration(request.collaboration.clone());
        if let Some(opening_message) = &request.opening_message {
            opening_message.validate_for(opening_message_scope(strategy)).map_err(|error| {
                ApplicationError::invalid("invalid_opening_message", error.to_string())
            })?;
        }
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
                tags: Vec::new(),
                message_view_scope: None,
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
                tags: normalize_participant_tags(participant.tags),
                message_view_scope: participant.message_view_scope,
            })
            .collect::<Vec<_>>();
        let created = self
            .management
            .create_group(GroupCreateCommand {
                create_initial_session: true,
                group_id,
                caller_actor_id: Some(principal_actor_id.clone()),
                driver_bot_id: request.driver_bot_uuid,
                label: request.name,
                topic: None,
                context: request.context,
                opening_message: request.opening_message,
                routing_policy,
                participants,
                member_bot_ids: Vec::new(),
                group_kind: Some(GroupKind::Normal),
                service_spec: None,
                group_strategy: Some(strategy),
                originator: Some(originator),
                visibility: Some(visibility_name(request.visibility).to_string()),
                provisioning,
            })
            .await
            .map_err(map_group_error)?;
        let initial_session_id = created.latest_running_session_id.clone();
        let initial_run = created.initial_run.clone();

        if let Some(state_machine) = state_machine {
            let mut response_state_machine = state_machine.clone();
            let runtime = self.collaboration_runtime.as_ref().ok_or_else(|| {
                ApplicationError::internal(
                    "StateMachine creation requires CollaborationRuntimeService",
                )
            })?;
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
            let (definition_yaml, definition_ref) = match state_machine.definition {
                StateMachineDefinition::Reference(reference) => (
                    None,
                    Some(CollaborationDefinitionRef {
                        id: reference.definition_id,
                        version: reference.version,
                    }),
                ),
                StateMachineDefinition::Content(content) => (Some(content.content_yaml), None),
            };
            let configured = match runtime
                .configure_group_runtime(ConfigureGroupRuntimeCommand {
                    group_id: created.group_id.clone(),
                    definition_yaml,
                    definition: None,
                    definition_ref,
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
            if let Some(definition) = configured.default_definition.clone() {
                response_state_machine.definition = StateMachineDefinition::Reference(
                    StateMachineDefinitionReference {
                        definition_id: definition.id,
                        version: definition.version,
                    },
                );
            }
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
                let detail = self
                    .project_detail_with_state_machine(group, Some(response_state_machine))
                    .await?;
                return Ok((detail, initial_session_id, initial_run));
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
                    opening_message_override: None,
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
            let detail = self
                .project_detail_with_state_machine(group, Some(response_state_machine))
                .await?;
            return Ok((detail, initial_session_id, initial_run));
        }

        let group = self
            .groups
            .try_get(&created.group_id)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::internal("created Group disappeared before projection")
            })?;
        Ok((self.project_detail(group).await?, initial_session_id, initial_run))
    }

    pub(crate) async fn rollback_state_machine_creation(
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

    pub(crate) async fn create_dm(
        &self,
        principal: Principal,
        request: CreateDirectMessageGroup,
        group_id: Option<String>,
        provisioning: bool,
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
                group_id,
                caller_actor_id: Some(principal.actor_id()),
                driver_bot: None,
                target_actor_id: request.target_actor_id,
                label: request.name,
                topic: None,
                context: request.context,
                provisioning,
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
            initial_session_id: None,
            initial_run: None,
            event_subscriptions: Vec::new(),
        })
    }

    pub(crate) async fn rollback_provisioning_creation(
        &self,
        group: &DomainGroup,
        initial_session_id: Option<&str>,
    ) -> Option<String> {
        let mut errors = Vec::new();
        if group.group_strategy == GroupStrategy::StateMachine
            && let Some(runtime) = self.collaboration_runtime.as_ref()
        {
            if let Err(error) = runtime
                .cancel_group_runs(&group.id, "group_provisioning_failed")
                .await
            {
                errors.push(format!("run cancellation: {error}"));
            }
            if let Err(error) = runtime.delete_group_runtime_state(&group.id).await {
                errors.push(format!("runtime state: {error}"));
            }
        }
        if let Some(session_id) = initial_session_id
            && let Err(error) = self.sessions.delete(session_id).await
        {
            errors.push(format!("initial session: {error}"));
        }
        if let Err(error) = self.groups.delete(&group.id).await {
            errors.push(format!("group: {error}"));
        }
        (!errors.is_empty()).then(|| errors.join("; "))
    }

    pub(crate) async fn rollback_provisioning_creation_by_id(
        &self,
        group_id: &str,
        state_machine: bool,
    ) -> Option<String> {
        let mut errors = Vec::new();
        if state_machine && let Some(runtime) = self.collaboration_runtime.as_ref() {
            if let Err(error) = runtime
                .cancel_group_runs(group_id, "group_provisioning_failed")
                .await
            {
                errors.push(format!("run cancellation: {error}"));
            }
            if let Err(error) = runtime.delete_group_runtime_state(group_id).await {
                errors.push(format!("runtime state: {error}"));
            }
        }
        match self
            .sessions
            .list_by_group(group_id, None, 0, 100, None, None)
            .await
        {
            Ok(sessions) => {
                for session in sessions {
                    if let Err(error) = self.sessions.delete(&session.id).await {
                        errors.push(format!("initial session '{}': {error}", session.id));
                    }
                }
            }
            Err(error) => errors.push(format!("initial session lookup: {error}")),
        }
        if let Err(error) = self.groups.delete(group_id).await {
            errors.push(format!("group: {error}"));
        }
        (!errors.is_empty()).then(|| errors.join("; "))
    }

    pub(crate) async fn provisioning_error_with_compensation(
        &self,
        provisioner: &dyn GroupEventSubscriptionProvisioner,
        prepared: &PreparedGroupEventSubscriptions,
        original: ApplicationError,
        rollback_error: Option<String>,
    ) -> ApplicationError {
        let cancel_error = provisioner
            .cancel(prepared, "group_provisioning_failed")
            .await
            .err()
            .map(|error| error.to_string());
        if rollback_error.is_none() && cancel_error.is_none() {
            return original;
        }
        ApplicationError::internal(format!(
            "{original}; provisioning compensation failed: {}{}",
            rollback_error.unwrap_or_else(|| "none".to_string()),
            cancel_error.map_or_else(String::new, |error| format!("; subscription: {error}"))
        ))
    }

    pub(crate) async fn create_without_eventing(
        &self,
        command: CreateGroup,
        principal: Principal,
    ) -> Result<CreateGroupOutcome, ApplicationError> {
        match command.group {
            CreateGroupSpec::Collaboration(request) => {
                let (group, initial_session_id, initial_run) = self
                    .create_collaboration(principal, request, None, false)
                    .await?;
                Ok(CreateGroupOutcome {
                    group,
                    created: true,
                    initial_session_id,
                    initial_run,
                    event_subscriptions: Vec::new(),
                })
            }
            CreateGroupSpec::DirectMessage(request) => {
                self.create_dm(principal, request, None, false).await
            }
        }
    }
}
