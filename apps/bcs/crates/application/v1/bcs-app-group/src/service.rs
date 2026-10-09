//! `GroupService` use-case orchestration for the V1 Group facade.

use super::*;

#[async_trait]
impl GroupService for GroupServiceImpl {
    async fn list_groups(
        &self,
        command: ListGroups,
    ) -> Result<Page<GroupSummary>, ApplicationError> {
        let view_actor_id = self
            .resolve_view_actor(&command.caller, command.view_bot_id.as_deref())
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
            .try_find_by_participant(&view_actor_id)
            .await
            .map_err(map_service_error)?
            .into_iter()
            .filter(|group| group.record_status == "active")
            .map(|group| (group.id.clone(), (group, Membership::Direct)))
            .collect::<HashMap<_, _>>();
        let direct_group_ids = direct.keys().cloned().collect::<HashSet<_>>();
        let mut related = match command.membership {
            MembershipFilter::All | MembershipFilter::Direct => direct,
            MembershipFilter::SessionOnly => HashMap::new(),
        };
        if command.membership != MembershipFilter::Direct {
            let session_group_ids = self
                .sessions
                .list_group_ids_by_session_participant(&view_actor_id)
                .await
                .map_err(|error| ApplicationError::internal(error.to_string()))?;
            for group_id in session_group_ids {
                if direct_group_ids.contains(&group_id) {
                    continue;
                }
                if let Some(group) = self
                    .groups
                    .try_get(&group_id)
                    .await
                    .map_err(map_service_error)?
                    .filter(|group| group.record_status == "active")
                {
                    related.insert(group_id, (group, Membership::SessionOnly));
                }
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
            .filter(|(group, _)| {
                command.visibility.is_none_or(|visibility| {
                    group.visibility == visibility_name(visibility)
                })
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
        // V1 contract (`api-contracts/v1/openapi/groups.yaml`) declares
        // `created_at DESC, group_id ASC`. Legacy HTTP endpoints keep
        // `updated_at` sort, so we use a dedicated comparator here.
        groups.sort_by(|(left, _), (right, _)| {
            DomainGroup::cmp_by_created_at_desc_group_id_asc(left, right)
        });
        let total = groups.len() as u64;
        let page = groups
            .into_iter()
            .skip(saturating_usize(command.offset))
            .take(saturating_usize(command.limit))
            .collect::<Vec<_>>();
        let mut items = Vec::with_capacity(page.len());
        for (group, membership) in page {
            items.push(
                self.project_summary(group, &view_actor_id, membership)
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

    async fn list_public_groups(
        &self,
        command: ListPublicGroups,
    ) -> Result<Page<GroupSummary>, ApplicationError> {
        if command.limit == 0 || command.limit > 100 {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "limit must be between 1 and 100",
            ));
        }
        let q = command
            .q
            .as_deref()
            .map(str::trim)
            .filter(|query| !query.is_empty());
        let n = self
            .groups
            .count_filtered(None, Some("public"), q)
            .await;
        const SAFETY_CAP: u64 = 1000;
        if n > SAFETY_CAP {
            tracing::warn!(
                count = n,
                cap = SAFETY_CAP,
                "public group plaza candidate set truncated to SAFETY_CAP"
            );
        }
        let candidates = self
            .groups
            .list_paginated_filtered(0, n.min(SAFETY_CAP), None, Some("public"), q)
            .await;
        let filtered = candidates
            .into_iter()
            .filter(|group| group.record_status == "active")
            .filter(|group| {
                command.strategy.is_none_or(|strategy| {
                    group.group_kind == GroupKind::Normal
                        && project_strategy(group.group_strategy) == strategy
                })
            })
            .collect::<Vec<_>>();
        let total = filtered.len() as u64;
        let page = filtered
            .into_iter()
            .skip(saturating_usize(command.offset))
            .take(saturating_usize(command.limit))
            .collect::<Vec<_>>();
        let mut items = Vec::with_capacity(page.len());
        for group in page {
            items.push(self.project_summary(group, "", Membership::None).await?);
        }
        Ok(Page {
            items,
            total,
            offset: command.offset,
            limit: command.limit,
        })
    }

    async fn create(&self, command: CreateGroup) -> Result<GroupDetail, ApplicationError> {
        Ok(self
            .create_with_event_subscriptions(command, Vec::new())
            .await?
            .group)
    }

    async fn create_with_outcome(
        &self,
        command: CreateGroup,
    ) -> Result<CreateGroupOutcome, ApplicationError> {
        self.create_with_event_subscriptions(command, Vec::new())
            .await
    }

    async fn create_with_event_subscriptions(
        &self,
        command: CreateGroup,
        event_subscriptions: Vec<InlineGroupEventSubscriptionRequest>,
    ) -> Result<CreateGroupOutcome, ApplicationError> {
        let principal = select_principal(&command.caller, IdentityPolicy::HumanOrOwnedBot)?;
        let Some(provisioner) = self.event_subscription_provisioner.as_ref().cloned() else {
            if event_subscriptions.is_empty() {
                return self.create_without_eventing(command, principal).await;
            }
            return Err(ApplicationError::internal(
                "Group Event Subscription provisioning is not configured",
            ));
        };
        let CreateGroup { caller, group } = command;
        let state_machine = matches!(
            &group,
            CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                collaboration: CollaborationConfiguration::StateMachine(_),
                ..
            })
        );
        let group_kind = match &group {
            CreateGroupSpec::Collaboration(_) => GroupKind::Normal,
            CreateGroupSpec::DirectMessage(_) => GroupKind::Dm,
        };
        let group_id = generated_group_id(group_kind);
        let prepared = provisioner
            .prepare(&caller, &group_id, event_subscriptions)
            .await?;
        let creation = match group {
            CreateGroupSpec::Collaboration(request) => self
                .create_collaboration(principal, request, Some(group_id.clone()), true)
                .await
                .map(|(group, initial_session_id, initial_run)| {
                    (
                        CreateGroupOutcome {
                            group,
                            created: true,
                            initial_session_id: initial_session_id.clone(),
                            initial_run,
                            event_subscriptions: Vec::new(),
                        },
                        initial_session_id,
                    )
                }),
            CreateGroupSpec::DirectMessage(request) => self
                .create_dm(principal, request, Some(group_id.clone()), true)
                .await
                .map(|outcome| (outcome, None)),
        };
        let (mut outcome, created_initial_session_id) = match creation {
            Ok(creation) => creation,
            Err(error) => {
                let rollback = self
                    .rollback_provisioning_creation_by_id(&group_id, state_machine)
                    .await;
                return Err(self
                    .provisioning_error_with_compensation(
                        provisioner.as_ref(),
                        &prepared,
                        error,
                        rollback,
                    )
                    .await);
            }
        };

        if !outcome.created {
            provisioner.cancel(&prepared, "existing_dm_reused").await?;
            let existing_id = match &outcome.group {
                GroupDetail::Collaboration(group) => &group.group_id,
                GroupDetail::DirectMessage(group) => &group.group_id,
            };
            let existing = self
                .groups
                .try_get(existing_id)
                .await
                .map_err(map_service_error)?
                .ok_or_else(|| {
                    ApplicationError::internal("Reused DM Group disappeared before response")
                })?;
            if existing.record_status != "active" {
                return Err(ApplicationError::conflict(
                    "group_provisioning_in_progress",
                    "The canonical DM Group is still being provisioned",
                ));
            }
            return Ok(outcome);
        }

        let domain_group = match self.groups.try_get(&group_id).await {
            Ok(Some(group)) => group,
            Ok(None) => {
                let error = ApplicationError::internal(
                    "Provisioning Group disappeared before Event finalization",
                );
                let rollback = self
                    .rollback_provisioning_creation_by_id(&group_id, state_machine)
                    .await;
                return Err(self
                    .provisioning_error_with_compensation(
                        provisioner.as_ref(),
                        &prepared,
                        error,
                        rollback,
                    )
                    .await);
            }
            Err(error) => {
                let rollback = self
                    .rollback_provisioning_creation_by_id(&group_id, state_machine)
                    .await;
                return Err(self
                    .provisioning_error_with_compensation(
                        provisioner.as_ref(),
                        &prepared,
                        map_service_error(error),
                        rollback,
                    )
                    .await);
            }
        };
        let initial_session = if domain_group.group_kind == GroupKind::Normal {
            let Some(initial_session_id) = created_initial_session_id.as_deref() else {
                let error = ApplicationError::internal(format!(
                    "Provisioning Group '{group_id}' did not create an initial Session"
                ));
                let rollback = self
                    .rollback_provisioning_creation(&domain_group, None)
                    .await;
                return Err(self
                    .provisioning_error_with_compensation(
                        provisioner.as_ref(),
                        &prepared,
                        error,
                        rollback,
                    )
                    .await);
            };
            match self.sessions.get(initial_session_id).await {
                Ok(Some(session)) => Some(session),
                Ok(None) => {
                    let error = ApplicationError::internal(format!(
                        "Provisioning initial Session '{initial_session_id}' disappeared"
                    ));
                    let rollback = self
                        .rollback_provisioning_creation(&domain_group, Some(initial_session_id))
                        .await;
                    return Err(self
                        .provisioning_error_with_compensation(
                            provisioner.as_ref(),
                            &prepared,
                            error,
                            rollback,
                        )
                        .await);
                }
                Err(error) => {
                    let rollback = self
                        .rollback_provisioning_creation(&domain_group, Some(initial_session_id))
                        .await;
                    return Err(self
                        .provisioning_error_with_compensation(
                            provisioner.as_ref(),
                            &prepared,
                            ApplicationError::internal(error.to_string()),
                            rollback,
                        )
                        .await);
                }
            }
        } else {
            None
        };

        if let Err(error) = provisioner
            .finalize(&prepared, &domain_group, initial_session.as_ref())
            .await
        {
            let rollback = self
                .rollback_provisioning_creation(
                    &domain_group,
                    initial_session.as_ref().map(|session| session.id.as_str()),
                )
                .await;
            return Err(self
                .provisioning_error_with_compensation(
                    provisioner.as_ref(),
                    &prepared,
                    error,
                    rollback,
                )
                .await);
        }
        outcome.event_subscriptions = provisioner.load_activated(&prepared).await?;
        Ok(outcome)
    }

    async fn get(&self, query: GetGroup) -> Result<GroupDetail, ApplicationError> {
        let group = self
            .load_group_detail_for_caller(&query.caller, &query.group_id)
            .await?;
        self.project_detail(group).await
    }

    async fn update(&self, command: UpdateGroup) -> Result<GroupDetail, ApplicationError> {
        let principal = select_principal(&command.caller, IdentityPolicy::HumanOrOwnedBot)?;
        let mutation_actor = event_actor_for_principal(&principal);
        if command.patch.is_empty() {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "at least one mutable field is required",
            ));
        }
        let mut group = self
            .load_manageable_group(&principal, &command.group_id)
            .await?;
        if group.group_kind == GroupKind::Dm && command.patch.opening_message.is_some() {
            return Err(ApplicationError::invalid(
                "invalid_opening_message",
                "opening_message is not supported for DM Groups",
            ));
        }
        if group.group_kind == GroupKind::Dm
            && (command.patch.delivery_policy.is_some()
                || command.patch.visibility == Some(GroupVisibility::Public))
        {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "DM Groups do not expose delivery policy or public visibility",
            ));
        }
        if command.patch.delivery_policy.is_some()
            && group.group_strategy != GroupStrategy::Chat
        {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "delivery_policy may be updated only for Chat Groups",
            ));
        }
        if let Some(opening_message) = &command.patch.opening_message {
            if let Some(opening_message) = opening_message {
                opening_message
                    .validate_for(opening_message_scope(group.group_strategy))
                    .map_err(|error| {
                    ApplicationError::invalid("invalid_opening_message", error.to_string())
                })?;
            }
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
        if let Some(opening_message) = patch.opening_message {
            group.opening_message = opening_message.clone();
            persistence_patch.opening_message = Some(opening_message);
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
        if let Some(mode) = patch.human_mention_notify_mode {
            group.human_mention_notify_mode = mode;
            persistence_patch.human_mention_notify_mode = Some(mode);
        }
        let state_machine_projection = if group.group_strategy == GroupStrategy::StateMachine {
            Some(
                self.load_state_machine_configuration(&command.group_id)
                    .await?,
            )
        } else {
            None
        };
        let persisted = self
            .groups
            .mutate(GroupMutationCommand {
                group_id: command.group_id.clone(),
                actor: mutation_actor,
                correlation_id: None,
                trace_id: None,
                mutation: GroupMutationKind::PatchMutableFields(persistence_patch),
            })
            .await
            .map_err(map_service_error)?;
        self.project_detail_with_state_machine(persisted, state_machine_projection)
            .await
    }

    async fn delete(&self, command: DeleteGroup) -> Result<DeleteResult, ApplicationError> {
        let principal = select_principal(&command.caller, IdentityPolicy::HumanOrOwnedBot)?;
        let Some(group) = self
            .groups
            .try_get(&command.group_id)
            .await
            .map_err(map_service_error)?
        else {
            let deletion_error = self
                .management
                .delete_group(GroupDeleteCommand {
                    caller_actor_id: principal.actor_id(),
                    group_id: command.group_id.clone(),
                })
                .await
                .err()
                .map(map_delete_group_error);
            let runtime_cleanup_error = if let Some(runtime) = self.collaboration_runtime.as_ref() {
                cleanup_deleted_group_runtime(runtime.as_ref(), &command.group_id).await
            } else {
                None
            };
            match (deletion_error, runtime_cleanup_error) {
                (Some(deletion_error), Some(runtime_cleanup_error)) => {
                    return Err(ApplicationError::internal(format!(
                        "Group cleanup failed: {deletion_error}; runtime cleanup failed: {runtime_cleanup_error}"
                    )));
                }
                (Some(deletion_error), None) => return Err(deletion_error),
                (None, Some(runtime_cleanup_error)) => {
                    return Err(ApplicationError::internal(format!(
                        "Group runtime cleanup failed: {runtime_cleanup_error}"
                    )));
                }
                (None, None) => {}
            }
            return Ok(DeleteResult {
                deleted: false,
            });
        };
        let manage_actor_id = if let Some(requested) = command.acting_bot_id.as_deref() {
            let acting_actor_id = match &principal {
                Principal::Human(_) => {
                    self.resolve_view_actor(&command.caller, Some(requested)).await?
                }
                Principal::Bot(bot) if requested == bot.bot_uuid => requested.to_string(),
                Principal::Bot(_) => {
                    return Err(ApplicationError::forbidden(
                        "The explicit acting Bot must identify the authenticated Bot",
                    ));
                }
            };
            let management_actor_ids = Self::group_management_actor_ids(&group);
            if !management_actor_ids
                .iter()
                .any(|actor_id| actor_id == &acting_actor_id)
            {
                return Err(ApplicationError::forbidden(
                    "Principal cannot delete the group",
                ));
            }
            acting_actor_id
        } else {
            let Some(manage_actor_id) = self.resolve_group_manage_actor(&principal, &group).await? else {
                return Err(ApplicationError::forbidden(
                    "Principal cannot delete the group",
                ));
            };
            manage_actor_id
        };
        let state_machine_runtime = if group.group_strategy == GroupStrategy::StateMachine {
            Some(self.collaboration_runtime.as_ref().ok_or_else(|| {
                ApplicationError::internal(
                    "StateMachine Group deletion requires CollaborationRuntimeService",
                )
            })?)
        } else {
            None
        };
        let group_id = command.group_id.clone();
        let result = match self
            .management
            .delete_group(GroupDeleteCommand {
                caller_actor_id: manage_actor_id,
                group_id: command.group_id,
            })
            .await
        {
            Ok(result) => result,
            Err(error) => {
                let deletion_error = map_delete_group_error(error);
                match self.groups.try_get(&group_id).await {
                    Ok(None) => {
                        if let Some(runtime) = state_machine_runtime
                            && let Some(runtime_cleanup_error) =
                                cleanup_deleted_group_runtime(runtime.as_ref(), &group_id).await
                        {
                            return Err(ApplicationError::internal(format!(
                                "Group deletion failed after commit: {deletion_error}; runtime cleanup failed: {runtime_cleanup_error}"
                            )));
                        }
                    }
                    Ok(Some(_)) => {}
                    Err(verification_error) => {
                        return Err(ApplicationError::internal(format!(
                            "Group deletion failed: {deletion_error}; failed to verify commit state: {verification_error}"
                        )));
                    }
                }
                return Err(deletion_error);
            }
        };
        if result.deleted
            && let Some(runtime) = state_machine_runtime
            && let Some(runtime_cleanup_error) =
                cleanup_deleted_group_runtime(runtime.as_ref(), &result.group_id).await
        {
            return Err(ApplicationError::internal(format!(
                "Group runtime cleanup failed: {runtime_cleanup_error}"
            )));
        }
        Ok(DeleteResult {
            deleted: result.deleted,
        })
    }

    async fn add_participant(
        &self,
        command: AddGroupParticipant,
    ) -> Result<V1Participant, ApplicationError> {
        let principal = require_human(&command.caller)?;
        let group = self
            .load_readable_group(&principal, &command.group_id)
            .await?;
        let Some(manage_actor_id) = self.resolve_group_manage_actor(&principal, &group).await? else {
            return Err(ApplicationError::forbidden(
                "Principal cannot manage the group",
            ));
        };
        // `AddGroupParticipant` carries no `actor_kind`; resolve it from the
        // registry so legacy `add_member` gets the target Actor ID for both Bot
        // and Human participants. If the V1 caller references a Human actor that
        // has not been materialized yet, create the legacy Human actor at this
        // boundary before delegating.
        let target_actor = self
            .registry
            .try_get(&command.actor_id)
            .await
            .map_err(map_service_error)?;
        if target_actor.is_none() && let Some(staff_no) = command.actor_id.strip_prefix("human_") {
            self.registry
                .ensure_human_actor(staff_no, staff_no)
                .await
                .map_err(map_service_error)?;
        }
        let bot_id = command.actor_id.clone();
        let legacy_human_actor_id = match &principal {
            Principal::Human(human) => {
                let human_actor_id = format!("human_{}", human.subject.id);
                if manage_actor_id == human_actor_id {
                    None
                } else {
                    let manage_actor = self
                        .registry
                        .try_get(&manage_actor_id)
                        .await
                        .map_err(map_service_error)?;
                    manage_actor
                        .filter(|actor| {
                            actor.actor_kind == ActorKind::Bot
                                && actor.created_by.as_deref() == Some(human.subject.id.as_str())
                        })
                        .map(|_| human_actor_id)
                }
            }
            Principal::Bot(_) => None,
        };
        let result = self
            .management
            .add_member(GroupAddMemberCommand {
                caller_actor_id: Some(manage_actor_id),
                human_actor_id: legacy_human_actor_id,
                group_id: command.group_id.clone(),
                bot_id,
                message_view_scope: command.message_view_scope,
            })
            .await
            .map_err(map_group_error)?;
        Ok(participant_view_to_v1(result.member))
    }

    async fn update_participant(
        &self,
        command: UpdateGroupParticipant,
    ) -> Result<V1Participant, ApplicationError> {
        if command.mode.is_none() && command.message_view_scope.is_none() {
            return Err(ApplicationError::invalid(
                "empty_participant_update",
                "At least one of mode or message_view_scope must be provided",
            ));
        }
        let principal = require_human(&command.caller)?;
        let mutation_actor = event_actor_for_principal(&principal);
        let mut group = self
            .load_readable_group(&principal, &command.group_id)
            .await?;
        // Design §8.7: the target Actor may update its own participant mode
        // (self-service) in addition to the driver/originator/manager path.
        let is_self = self.principal_can_act_as(&principal, &command.actor_id).await?;
        if !is_self && self.resolve_group_manage_actor(&principal, &group).await?.is_none() {
            return Err(if command.message_view_scope.is_some() {
                ApplicationError::forbidden_code(
                    "message_view_scope_forbidden",
                    "A Human may only update its own scope unless it manages the Group",
                )
            } else {
                ApplicationError::forbidden("Principal cannot manage the group")
            });
        }
        let target = group
            .participants
            .iter()
            .find(|participant| participant.bot_uuid == command.actor_id)
            .cloned()
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "participant_not_found",
                    format!("Participant '{}' not found", command.actor_id),
                )
            })?;
        if let Some(mode) = command.mode {
            if !mode.is_valid_for(target.actor_kind) {
                return Err(ApplicationError::invalid(
                    "invalid_participant_mode",
                    format!(
                        "Participant mode '{mode:?}' is invalid for actor kind '{:?}'",
                        target.actor_kind
                    ),
                ));
            }
            if command.message_view_scope.is_none() {
                group = self
                    .groups
                    .mutate(GroupMutationCommand {
                        group_id: command.group_id.clone(),
                        actor: mutation_actor.clone(),
                        correlation_id: None,
                        trace_id: None,
                        mutation: GroupMutationKind::UpdateParticipantMode {
                            actor_id: command.actor_id.clone(),
                            mode,
                        },
                    })
                    .await
                    .map_err(map_service_error)?;
            }
        }
        if let Some(message_view_scope) = command.message_view_scope {
            if !message_view_scope.is_valid_for(target.actor_kind) {
                return Err(ApplicationError::invalid(
                    "invalid_message_view_scope",
                    "Bot participants must use full message_view_scope",
                ));
            }
            let scope_change_lease = if target.actor_kind == ActorKind::Human
                && target.message_view_scope != message_view_scope
            {
                Some(
                    self.participant_view_bindings
                        .begin_scope_change(&command.group_id, &command.actor_id)
                        .await
                        .map_err(map_service_error)?,
                )
            } else {
                None
            };
            let update_result = self
                .groups
                .mutate(GroupMutationCommand {
                    group_id: command.group_id.clone(),
                    actor: mutation_actor,
                    correlation_id: None,
                    trace_id: None,
                    mutation: GroupMutationKind::UpdateParticipantMessageViewScope {
                        actor_id: command.actor_id.clone(),
                        message_view_scope,
                        mode: command.mode,
                    },
                })
                .await
                .map_err(map_service_error);
            let release_result = if let Some(lease) = scope_change_lease {
                self.participant_view_bindings
                    .finish_scope_change(lease)
                    .await
                    .map_err(map_service_error)
            } else {
                Ok(())
            };
            group = update_result?;
            release_result?;
        }
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
        let principal = require_human(&command.caller)?;
        let group = self
            .load_readable_group(&principal, &command.group_id)
            .await?;
        // Design §8.7: the target Actor may leave the group (self-service
        // delete) in addition to the driver/originator/manager path. The legacy
        // `remove_member` still rejects driver/originator removal, preserving
        // the role invariant; non-driver self-leave proceeds.
        let is_self = self.principal_can_act_as(&principal, &command.actor_id).await?;
        let effective_caller_actor_id = if is_self {
            command.actor_id.clone()
        } else if let Some(manage_actor_id) = self.resolve_group_manage_actor(&principal, &group).await? {
            manage_actor_id
        } else {
            return Err(ApplicationError::forbidden(
                "Principal cannot manage the group",
            ));
        };
        // Phase one: target is a Bot actor (legacy `remove_member` uses bot_id).
        // The V1 contract treats an already-removed/missing participant as
        // idempotent success, so swallow `ParticipantNotFound` into
        // `DeleteResult { deleted: false }` (mirroring the group-level `delete`
        // facade's not-found handling) instead of surfacing a 404.
        match self
            .management
            .remove_member(GroupRemoveMemberCommand {
                caller_actor_id: Some(effective_caller_actor_id),
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
