//! Group create/provisioning orchestration for the legacy Group service.

use super::*;

pub(crate) fn state_machine_initial_session_input(
    context: Option<&str>,
    topic: Option<&str>,
) -> Option<serde_json::Value> {
    first_non_empty([context, topic]).map(|query| serde_json::json!({ "query": query }))
}

pub(crate) fn first_non_empty<const N: usize>(values: [Option<&str>; N]) -> Option<&str> {
    values
        .into_iter()
        .flatten()
        .map(str::trim)
        .find(|value| !value.is_empty())
}

impl GroupManagement {
    pub(crate) async fn create_group_impl(
        &self,
        cmd: GroupCreateCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        if cmd.group_kind == Some(GroupKind::Dm) {
            return Err(GroupUseCaseError::InvalidProposal(
                "DM groups must be created through create_dm".to_string(),
            ));
        }
        if !cmd.create_initial_session && cmd.provisioning {
            return Err(GroupUseCaseError::InvalidProposal(
                "create_initial_session=false is not supported while provisioning a group"
                    .to_string(),
            ));
        }
        validate_service_spec_callback_urls(&self.outbound_url_guard, cmd.service_spec.as_ref())?;

        let originator = cmd
            .originator
            .clone()
            .unwrap_or_else(|| cmd.driver_bot_id.clone());

        if self.v1_openapi_create_policy {
            cmd.caller_actor_id
                .as_deref()
                .filter(|caller| !caller.is_empty())
                .ok_or_else(|| GroupUseCaseError::Unauthorized("caller is required".to_string()))?;
        } else {
            self.authorize_originator(cmd.caller_actor_id.as_deref(), &originator)
                .await?;
        }

        let is_human_originator = originator.starts_with("human_");

        let group_id = cmd
            .group_id
            .unwrap_or_else(|| generated_group_id(GroupKind::Normal));
        let mut requested = Vec::new();
        for participant in cmd.participants {
            requested.push((
                participant.bot_id,
                participant.role,
                participant.tags,
                participant.message_view_scope,
            ));
        }
        for bot_id in cmd.member_bot_ids {
            requested.push((bot_id, None, Vec::new(), None));
        }
        if !requested
            .iter()
            .any(|(bot_id, _, _, _)| bot_id == &cmd.driver_bot_id)
        {
            requested.push((
                cmd.driver_bot_id.clone(),
                Some("driver".to_string()),
                Vec::new(),
                None,
            ));
        }

        let mut seen = HashSet::new();
        let mut participants = Vec::with_capacity(requested.len());
        let mut participant_ids = Vec::with_capacity(requested.len());
        let mut subscription_targets = Vec::new();
        for (bot_id, role, tags, message_view_scope) in requested {
            if !seen.insert(bot_id.clone()) {
                continue;
            }
            let bot = if self.v1_openapi_create_policy {
                self.registry.try_get(&bot_id).await?
            } else {
                self.registry.get(&bot_id).await
            }
            .ok_or_else(|| ServiceError::BotNotFound(bot_id.clone()))?;
            if bot.actor_kind == ActorKind::Bot {
                if self.v1_openapi_create_policy {
                    // Originator-anchored (aligned with legacy): every Bot
                    // participant except the originator itself — including the
                    // driver when it differs from the originator — must be
                    // reachable from the originator. A Human originator reaches
                    // a bot via public visibility or ownership (`created_by`);
                    // a Bot originator reaches it via public visibility or
                    // friendship.
                    if bot_id != originator {
                        if is_human_originator {
                            let staff_no = originator.trim_start_matches("human_");
                            if bot.capabilities.visibility != "public"
                                && bot.created_by.as_deref() != Some(staff_no)
                            {
                                return Err(GroupUseCaseError::Forbidden(format!(
                                    "Bot '{}' is neither public nor owned by human '{}'",
                                    bot_id, staff_no
                                )));
                            }
                        } else {
                            self.ensure_v1_reachable(&originator, &bot).await?;
                        }
                        if bot.capabilities.visibility == "public" {
                            subscription_targets.push(bot.clone());
                        }
                    }
                } else if bot_id != originator {
                    if is_human_originator {
                        let staff_no = originator.trim_start_matches("human_");
                        if bot.capabilities.visibility != "public"
                            && bot.created_by.as_deref() != Some(staff_no)
                        {
                            return Err(GroupUseCaseError::Forbidden(format!(
                                "Bot '{}' is neither public nor owned by human '{}'",
                                bot_id, staff_no
                            )));
                        }
                    } else {
                        self.ensure_reachable(&originator, &bot_id).await?;
                    }
                    if bot.capabilities.visibility == "public" {
                        subscription_targets.push(bot.clone());
                    }
                }
            }
            let role = participant_role(role.as_deref(), bot_id == cmd.driver_bot_id)?;
            let mode = match bot.actor_kind {
                ActorKind::Human => ParticipantMode::Present,
                ActorKind::Bot => ParticipantMode::default_for(ActorKind::Bot),
            };
            let message_view_scope = message_view_scope.unwrap_or(MessageViewScope::Full);
            if !message_view_scope.is_valid_for(bot.actor_kind) {
                return Err(GroupUseCaseError::InvalidProposal(
                    "Bot participants must use full message_view_scope".to_string(),
                ));
            }
            participants.push(Participant {
                bot_uuid: bot_id.clone(),
                bot_name: bot.capabilities.name,
                kind: None,
                role,
                actor_kind: bot.actor_kind,
                mode: Some(mode),
                tags,
                message_view_scope,
            });
            participant_ids.push(bot_id);
        }
        let requested_strategy = cmd.group_strategy.unwrap_or_default();
        if let Some(opening_message) = &cmd.opening_message {
            let scope = match requested_strategy {
                GroupStrategy::StateMachine => OpeningMessageScope::StateMachineRun,
                GroupStrategy::Chat | GroupStrategy::ManagerWorker => OpeningMessageScope::Session,
            };
            opening_message.validate_for(scope).map_err(|error| {
                GroupUseCaseError::InvalidProposal(format!("invalid_opening_message: {error}"))
            })?;
        }
        validate_participants_for_strategy(requested_strategy, &participants)?;
        validate_human_constraints(requested_strategy, &participants, &cmd.driver_bot_id)?;
        self.ensure_manager_worker_accepts_participants(requested_strategy, &participants)
            .await?;

        self.ensure_limits(&cmd.driver_bot_id, &participant_ids)
            .await?;

        if let Some(policy) = &cmd.routing_policy {
            if !policy.sender_routes.is_empty() {
                let participant_refs: Vec<&str> =
                    participant_ids.iter().map(String::as_str).collect();
                validate_sender_routes(&policy.sender_routes, &participant_refs)
                    .map_err(|error| GroupUseCaseError::InvalidProposal(error.to_string()))?;
            }
        }

        let mut group = DomainGroup::new(&group_id, cmd.driver_bot_id.clone(), participants);
        group.originator = cmd
            .originator
            .clone()
            .or_else(|| cmd.caller_actor_id.clone())
            .or_else(|| Some(cmd.driver_bot_id.clone()));
        group.label = match (cmd.topic.as_ref(), cmd.label.as_ref()) {
            (Some(topic), _) => Some(format!("Group: {}", topic)),
            (None, Some(label)) => Some(label.clone()),
            (None, None) => {
                let now = chrono::Utc::now()
                    .with_timezone(&chrono::FixedOffset::east_opt(8 * 3600).expect("UTC+8"))
                    .format("%Y%m%d%H%M")
                    .to_string();
                Some(format!("{}-{}", cmd.driver_bot_id, now))
            }
        };
        group.context = cmd.context.clone();
        group.opening_message = cmd.opening_message;
        group.routing_policy = cmd.routing_policy;
        group.group_kind = cmd.group_kind.unwrap_or(GroupKind::Normal);
        group.service_spec = cmd.service_spec.clone();
        group.group_strategy = requested_strategy;
        if cmd.provisioning {
            group.record_status = "provisioning".to_string();
        }

        let visibility = cmd.visibility.as_deref().unwrap_or("private").to_string();
        if visibility != "public" && visibility != "private" {
            return Err(GroupUseCaseError::InvalidProposal(
                "Invalid visibility value: must be 'public' or 'private'".to_string(),
            ));
        }
        if visibility == "public" {
            if group.group_kind == GroupKind::Dm {
                return Err(GroupUseCaseError::InvalidProposal(
                    "DM groups cannot be set to public".to_string(),
                ));
            }
            self.ensure_all_bots_public(&group.participants).await?;
        }
        group.visibility = visibility;

        self.group.upsert(group.clone()).await?;

        for target in &subscription_targets {
            self.try_write_subscription_edge(&cmd.driver_bot_id, target)
                .await;
        }

        if !cmd.create_initial_session {
            return Ok(group_to_detail_with_context(group, 0));
        }

        let initial_session_kind = match requested_strategy {
            GroupStrategy::StateMachine => SessionKind::ServiceInvocation,
            GroupStrategy::Chat | GroupStrategy::ManagerWorker => SessionKind::Chat,
        };
        let initial_session_title = Some("新会话".to_string());
        let initial_session_input = match requested_strategy {
            GroupStrategy::StateMachine => {
                state_machine_initial_session_input(cmd.context.as_deref(), cmd.topic.as_deref())
            }
            GroupStrategy::Chat | GroupStrategy::ManagerWorker => None,
        };
        let mut initial_session_participants = group.participants.clone();
        if requested_strategy == GroupStrategy::StateMachine
            && let Some(human_actor_id) = cmd
                .caller_actor_id
                .as_deref()
                .filter(|actor_id| actor_id.starts_with("human_"))
        {
            // COSEC: caller_actor_id is supplied by the authenticated application
            // boundary. Do not derive this participant from request YAML or bindings.
            initial_session_participants
                .retain(|participant| participant.bot_uuid != human_actor_id);
            let mut participant = Participant::human(human_actor_id, ParticipantRole::Observer);
            participant.mode = Some(ParticipantMode::Present);
            initial_session_participants.push(participant);
        }
        // `目标` (reason) sourcing mirrors the create-session HTTP path:
        // session input (as text) → group.context → group.label, empty when
        // all are absent (the `目标` line is then omitted). Computed before the
        // create call because `initial_session_input` is moved into it below.
        let reason = bcs_service_api::resolve_session_topic(
            initial_session_input.as_ref(),
            group.context.as_deref(),
            group.label.as_deref(),
        )
        .unwrap_or_default();
        let initial_session_id;
        let mut initial_run = None;
        let context_injected = match self
            .session_management
            .create_or_reactivate(bcs_service_api::CreateOrReactivateCommand {
                group_id: group.id.clone(),
                session_id: None,
                params: bcs_service_api::NewSessionParams {
                    session_kind: initial_session_kind,
                    participants: initial_session_participants,
                    group_version: Some(group.version),
                    input: initial_session_input,
                    session_title: initial_session_title,
                    created_by: Some(originator.clone()),
                    caller_principal: cmd.caller_actor_id.clone(),
                    ..Default::default()
                },
            })
            .await
        {
            Ok(outcome) => {
                initial_session_id = Some(outcome.session.id.clone());
                tracing::info!(
                    group_id = %group.id,
                    session_id = %outcome.session.id,
                    session_kind = ?outcome.session.session_kind,
                    "auto-created initial session for new group"
                );
                if requested_strategy == GroupStrategy::StateMachine {
                    0
                } else {
                    let sid = outcome.session.id.clone();
                    let gid = group.id.clone();
                    let session_participants = outcome.session.participants.clone();
                    match self
                        .system_message
                        .notify_with_outcome(
                            &gid,
                            SystemMessageEvent::SessionContext {
                                group_id: gid.clone(),
                                session_id: sid.clone(),
                                reason,
                                session_input: None,
                                task_ledger: None,
                                driver_delivery: None,
                            },
                            &sid,
                            &session_participants,
                        )
                        .await
                    {
                        Ok(dispatch) => {
                            let bootstrap_responder = match requested_strategy {
                                GroupStrategy::Chat => Some(group.driver_bot.as_str()),
                                GroupStrategy::ManagerWorker => group
                                    .participants
                                    .iter()
                                    .find(|participant| {
                                        participant.role == ParticipantRole::Manager
                                    })
                                    .map(|participant| participant.bot_uuid.as_str()),
                                GroupStrategy::StateMachine => None,
                            };
                            if let Some(bot_uuid) = bootstrap_responder
                                && let Some(result) =
                                    dispatch.recipient_results.iter().find(|result| {
                                        result.recipient_id == bot_uuid
                                            && result.delivery_type == DeliveryType::Send
                                    })
                            {
                                initial_run = Some(InitialGroupRun {
                                    run_id: result.run_id.clone(),
                                    bot_uuid: bot_uuid.to_string(),
                                    activity_kind: InitialGroupRunActivityKind::GroupBootstrap,
                                    state: if result.delivered {
                                        InitialGroupRunState::Running
                                    } else if result.accepted() {
                                        InitialGroupRunState::Queued
                                    } else {
                                        InitialGroupRunState::Failed
                                    },
                                    started_at: chrono::Utc::now().to_rfc3339(),
                                });
                            }
                            dispatch.successful_deliveries as u64
                        }
                        Err(error) => {
                            warn!(
                                group_id = %gid,
                                session_id = %sid,
                                error = %error,
                                "failed to deliver initial group context"
                            );
                            0
                        }
                    }
                }
            }
            Err(error) => {
                warn!(
                    group_id = %group.id,
                    error = %error,
                    "failed to auto-create initial session for new group"
                );
                if let Err(rollback_error) = self.group.delete(&group.id).await {
                    warn!(
                        group_id = %group.id,
                        error = %rollback_error,
                        "failed to roll back group after initial session creation failure"
                    );
                }
                return Err(GroupUseCaseError::Service(ServiceError::InternalError(
                    format!("failed to auto-create initial session for new group: {error}"),
                )));
            }
        };

        let mut detail = group_to_detail_with_context(group, context_injected);
        detail.latest_running_session_id = initial_session_id;
        detail.initial_run = initial_run;
        Ok(detail)
    }

    pub(crate) async fn create_dm_impl(&self, cmd: DmCreateCommand) -> Result<DmCreateResult, GroupUseCaseError> {
        let caller = cmd
            .caller_actor_id
            .as_deref()
            .filter(|caller| !caller.is_empty())
            .ok_or_else(|| GroupUseCaseError::Unauthorized("caller is required".to_string()))?;

        let caller_actor = if self.v1_openapi_create_policy {
            self.registry.try_get(caller).await?
        } else {
            self.registry.get(caller).await
        }
        .ok_or_else(|| GroupUseCaseError::ActorNotFound(caller.to_string()))?;
        let target = if self.v1_openapi_create_policy {
            self.registry.try_get(&cmd.target_actor_id).await?
        } else {
            self.registry.get(&cmd.target_actor_id).await
        }
        .ok_or_else(|| GroupUseCaseError::ActorNotFound(cmd.target_actor_id.clone()))?;
        if target.actor_kind != ActorKind::Bot {
            return Err(GroupUseCaseError::InvalidProposal(
                "DM target must be a Bot actor".to_string(),
            ));
        }

        let group_id = cmd
            .group_id
            .unwrap_or_else(|| generated_group_id(GroupKind::Dm));
        let label = dm_label(cmd.label, cmd.topic.as_deref(), caller, &target.bot_uuid);

        let (actor_a, actor_b, legacy_driver_bot, originator_actor_id) = match caller_actor
            .actor_kind
        {
            ActorKind::Human => {
                if let Some(driver_bot) = cmd.driver_bot.as_deref() {
                    if driver_bot != target.bot_uuid {
                        return Err(GroupUseCaseError::InvalidProposal(
                            "driver_bot must match target_actor_id for Human-Bot DM".to_string(),
                        ));
                    }
                }
                self.ensure_human_can_dm_bot(caller, &target).await?;
                (
                    DmActorSpec {
                        actor_id: caller.to_string(),
                        actor_kind: ActorKind::Human,
                        display_name: caller_actor.capabilities.name.clone(),
                    },
                    DmActorSpec {
                        actor_id: target.bot_uuid.clone(),
                        actor_kind: ActorKind::Bot,
                        display_name: target.capabilities.name.clone(),
                    },
                    target.bot_uuid.clone(),
                    caller.to_string(),
                )
            }
            ActorKind::Bot => {
                if self.v1_openapi_create_policy {
                    self.ensure_v1_reachable(caller, &target).await?;
                } else {
                    self.ensure_reachable(caller, &target.bot_uuid).await?;
                }
                (
                    DmActorSpec {
                        actor_id: caller_actor.bot_uuid.clone(),
                        actor_kind: ActorKind::Bot,
                        display_name: caller_actor.capabilities.name.clone(),
                    },
                    DmActorSpec {
                        actor_id: target.bot_uuid.clone(),
                        actor_kind: ActorKind::Bot,
                        display_name: target.capabilities.name.clone(),
                    },
                    caller_actor.bot_uuid.clone(),
                    caller_actor.bot_uuid,
                )
            }
        };

        let (group, created) = self
            .group
            .create_or_reuse_actor_dm_group_with_record_status(
                &group_id,
                actor_a,
                actor_b,
                &legacy_driver_bot,
                &originator_actor_id,
                label,
                cmd.context,
                if cmd.provisioning {
                    "provisioning"
                } else {
                    "active"
                },
            )
            .await?;

        Ok(DmCreateResult {
            group: group_to_detail_with_context(group, 0),
            created,
        })
    }
}

pub(crate) fn dm_label(
    label: Option<String>,
    topic: Option<&str>,
    source_actor_id: &str,
    target_actor_id: &str,
) -> Option<String> {
    match (topic, label) {
        (Some(topic), _) => Some(format!("DM: {}", topic)),
        (None, Some(label)) => Some(label),
        (None, None) => Some(format!("DM: {} - {}", source_actor_id, target_actor_id))
    }
}
