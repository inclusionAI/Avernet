//! `GroupManagementService` operations for the legacy Group service.

use super::*;

#[async_trait]
impl GroupManagementService for GroupManagement {

    async fn create_group(
        &self,
        cmd: GroupCreateCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        self.create_group_impl(cmd).await
    }

    async fn create_dm(&self, cmd: DmCreateCommand) -> Result<DmCreateResult, GroupUseCaseError> {
        self.create_dm_impl(cmd).await
    }

    async fn update_status(
        &self,
        cmd: GroupStatusCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        let status = parse_group_status(&cmd.status)?;
        let group = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;
        let caller = cmd
            .caller_actor_id
            .as_deref()
            .filter(|caller| !caller.is_empty())
            .ok_or_else(|| GroupUseCaseError::Unauthorized("caller is required".to_string()))?;
        if caller != group.driver_bot && caller != group.originator() {
            return Err(GroupUseCaseError::Forbidden(format!(
                "Only the group coordinator (originator: {} or driver: {}) can update status, not '{}'",
                group.originator(),
                group.driver_bot,
                caller
            )));
        }

        let updated = self
            .group
            .mutate(group_mutation_command(
                &cmd.group_id,
                caller,
                GroupMutationKind::UpdateStatus {
                    status,
                    reason: "status_updated".to_string(),
                },
            ))
            .await?;
        Ok(group_to_detail(updated))
    }

    async fn add_member(
        &self,
        cmd: GroupAddMemberCommand,
    ) -> Result<GroupAddMemberResult, GroupUseCaseError> {
        let (caller, group) = self.authorize_add_member(&cmd).await?;

        let bot = self
            .registry
            .get(&cmd.bot_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(cmd.bot_id.clone()))?;
        let role = default_added_member_role(group.group_strategy);

        if bot.actor_kind == ActorKind::Human {
            let allowed = match group.group_strategy {
                GroupStrategy::Chat | GroupStrategy::StateMachine => matches!(
                    role,
                    ParticipantRole::Consultant | ParticipantRole::Observer
                ),
                GroupStrategy::ManagerWorker => {
                    matches!(role, ParticipantRole::Worker | ParticipantRole::Observer)
                }
            };
            if !allowed {
                let strategy_name = match group.group_strategy {
                    GroupStrategy::Chat => "chat",
                    GroupStrategy::ManagerWorker => "manager_worker",
                    GroupStrategy::StateMachine => "state_machine",
                };
                return Err(GroupUseCaseError::InvalidProposal(format!(
                    "Human actors cannot have role '{}' in {} groups",
                    participant_role_to_wire(role),
                    strategy_name
                )));
            }
        }

        if bot.actor_kind == ActorKind::Bot {
            self.ensure_add_member_reachable(&group.driver_bot, &cmd.bot_id)
                .await?;
            if group.visibility == "public" && bot.capabilities.visibility != "public" {
                return Err(GroupUseCaseError::InvalidProposal(format!(
                    "Cannot add non-public bot '{}' to a public group",
                    cmd.bot_id
                )));
            }
        }

        let bot_name = bot.capabilities.name.clone();
        let mode = if bot.actor_kind == ActorKind::Human {
            Some(ParticipantMode::Present)
        } else {
            None
        };
        let participant = Participant {
            bot_uuid: cmd.bot_id.clone(),
            bot_name,
            kind: None,
            role,
            actor_kind: bot.actor_kind,
            mode,
            tags: Vec::new(),
            message_view_scope: cmd.message_view_scope.unwrap_or(MessageViewScope::Full),
        };
        if !participant
            .message_view_scope
            .is_valid_for(participant.actor_kind)
        {
            return Err(GroupUseCaseError::InvalidProposal(
                "Bot participants must use full message_view_scope".to_string(),
            ));
        }

        self.group
            .mutate(group_mutation_command(
                &cmd.group_id,
                &caller,
                GroupMutationKind::AddParticipant {
                    participant: participant.clone(),
                    actor_is_public: bot.actor_kind != ActorKind::Bot
                        || bot.capabilities.visibility == "public",
                },
            ))
            .await?;

        if bot.actor_kind == ActorKind::Bot {
            self.try_write_subscription_edge(&group.driver_bot, &bot)
                .await;
        }

        Ok(GroupAddMemberResult {
            group_id: cmd.group_id,
            member: GroupParticipantView {
                bot_uuid: participant.bot_uuid,
                bot_name: participant.bot_name,
                kind: participant.kind,
                role: participant_role_to_wire(participant.role).to_string(),
                actor_kind: participant.actor_kind,
                mode: participant.mode,
                tags: participant.tags,
                message_view_scope: participant.message_view_scope,
            },
        })
    }

    async fn remove_member(
        &self,
        cmd: GroupRemoveMemberCommand,
    ) -> Result<GroupRemoveMemberResult, GroupUseCaseError> {
        let caller = cmd
            .caller_actor_id
            .as_deref()
            .filter(|c| !c.is_empty())
            .ok_or_else(|| GroupUseCaseError::Unauthorized("caller is required".to_string()))?;

        let group = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;

        if group.group_kind == GroupKind::Dm {
            return Err(GroupUseCaseError::InvalidProposal(
                "DM groups cannot remove members".to_string(),
            ));
        }

        let is_coordinator = group.originator() == caller || group.driver_bot == caller || {
            if caller.starts_with("human_") {
                let staff_no = caller.trim_start_matches("human_");
                let owned = self.registry.list_bots_by_creator(staff_no).await;
                owned
                    .iter()
                    .any(|b| b.bot_uuid == group.driver_bot || b.bot_uuid == group.originator())
            } else {
                false
            }
        };
        let is_self = caller == cmd.bot_id;
        let is_owner = if caller.starts_with("human_") {
            let staff_no = caller.trim_start_matches("human_");
            self.registry
                .list_bots_by_creator(staff_no)
                .await
                .iter()
                .any(|b| b.bot_uuid == cmd.bot_id)
        } else {
            false
        };
        if !is_coordinator && !is_self && !is_owner {
            return Err(GroupUseCaseError::Forbidden(
                "Caller is not authorized to remove this member".to_string(),
            ));
        }

        if cmd.bot_id == group.driver_bot || cmd.bot_id == group.originator() {
            return Err(GroupUseCaseError::InvalidProposal(
                "Cannot remove the group driver/coordinator".to_string(),
            ));
        }

        if group.group_strategy == GroupStrategy::ManagerWorker {
            if let Some(manager) = group
                .participants
                .iter()
                .find(|p| p.role == ParticipantRole::Manager)
            {
                if cmd.bot_id == manager.bot_uuid {
                    return Err(GroupUseCaseError::InvalidProposal(
                        "Cannot remove the Manager bot from a ManagerWorker group".to_string(),
                    ));
                }
            }
        }

        if !group.participants.iter().any(|p| p.bot_uuid == cmd.bot_id) {
            return Err(ServiceError::ParticipantNotFound(cmd.bot_id.clone()).into());
        }

        self.group
            .mutate(group_mutation_command(
                &cmd.group_id,
                caller,
                GroupMutationKind::RemoveParticipant {
                    actor_id: cmd.bot_id.clone(),
                    reason: if is_self {
                        "participant_left"
                    } else {
                        "participant_removed"
                    }
                    .to_string(),
                },
            ))
            .await?;

        Ok(GroupRemoveMemberResult {
            group_id: cmd.group_id,
            removed_bot_uuid: cmd.bot_id,
        })
    }

    async fn delete_group(
        &self,
        cmd: GroupDeleteCommand,
    ) -> Result<GroupDeleteResult, GroupUseCaseError> {
        let Some(group) = self.group.try_get(&cmd.group_id).await? else {
            self.channel_binding_cleanup
                .delete_bindings_for_group(&cmd.group_id)
                .await?;
            return Ok(GroupDeleteResult {
                group_id: cmd.group_id,
                deleted: false,
            });
        };

        if group.group_kind == GroupKind::Dm {
            return Err(GroupUseCaseError::InvalidProposal(
                "DM groups cannot be deleted or left".to_string(),
            ));
        }
        self.ensure_group_coordinator(&group, &cmd.caller_actor_id, "delete this group")?;

        self.group
            .mutate(group_mutation_command(
                &cmd.group_id,
                &cmd.caller_actor_id,
                GroupMutationKind::Delete {
                    reason: "group_deleted".to_string(),
                },
            ))
            .await?;
        // Group deletion and subscription shutdown are already committed.
        // Binding cleanup is idempotent and retried by the not-found path
        // above; it cannot roll back the committed deletion.
        if let Err(cleanup_error) = self
            .channel_binding_cleanup
            .delete_bindings_for_group(&cmd.group_id)
            .await
        {
            return Err(cleanup_error.into());
        }
        Ok(GroupDeleteResult {
            group_id: cmd.group_id,
            deleted: true,
        })
    }

    async fn terminate_group(
        &self,
        cmd: GroupTerminateCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        let existing = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;
        if existing.group_kind == GroupKind::Dm {
            return Err(GroupUseCaseError::InvalidProposal(
                "DM groups cannot be terminated".to_string(),
            ));
        }
        if existing.driver_bot != cmd.caller_actor_id
            && existing.originator() != cmd.caller_actor_id
        {
            return Err(ServiceError::Unauthorized(format!(
                "Only the group coordinator can terminate Group '{}'",
                cmd.group_id
            ))
            .into());
        }
        let group = self
            .group
            .mutate(group_mutation_command(
                &cmd.group_id,
                &cmd.caller_actor_id,
                GroupMutationKind::UpdateStatus {
                    status: GroupStatus::Completed,
                    reason: "group_terminated".to_string(),
                },
            ))
            .await?;
        Ok(group_to_detail(group))
    }

    async fn update_label(
        &self,
        cmd: GroupUpdateLabelCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        let group = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;
        self.ensure_group_coordinator(&group, &cmd.caller_actor_id, "update label")?;

        let updated = self
            .group
            .mutate(group_mutation_command(
                &cmd.group_id,
                &cmd.caller_actor_id,
                GroupMutationKind::PatchMutableFields(GroupMutableFieldsPatch {
                    label: cmd.label.clone(),
                    ..GroupMutableFieldsPatch::default()
                }),
            ))
            .await?;
        Ok(group_to_detail(updated))
    }

    async fn update_visibility(
        &self,
        cmd: GroupUpdateVisibilityCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        let visibility = cmd.visibility.as_str();
        if visibility != "public" && visibility != "private" {
            return Err(GroupUseCaseError::InvalidProposal(
                "Invalid visibility value: must be 'public' or 'private'".to_string(),
            ));
        }

        let group = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;

        // Only coordinator (driver, originator, or driver's owner) can change visibility
        let is_coordinator = cmd.caller_actor_id == group.driver_bot
            || group.originator.as_deref() == Some(&cmd.caller_actor_id)
            || self
                .registry
                .list_bots_by_creator(&cmd.caller_actor_id)
                .await
                .iter()
                .any(|b| b.bot_uuid == group.driver_bot);
        if !is_coordinator {
            return Err(GroupUseCaseError::Forbidden(
                "Only the group coordinator can change visibility".to_string(),
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

        let updated = self
            .group
            .mutate(group_mutation_command(
                &cmd.group_id,
                &cmd.caller_actor_id,
                GroupMutationKind::PatchMutableFields(GroupMutableFieldsPatch {
                    visibility: Some(visibility.to_string()),
                    ..GroupMutableFieldsPatch::default()
                }),
            ))
            .await?;
        Ok(group_to_detail(updated))
    }

    async fn update_workspace(
        &self,
        cmd: GroupUpdateWorkspaceCommand,
    ) -> Result<GroupWorkspaceResult, GroupUseCaseError> {
        self.group
            .update_workspace(&cmd.group_id, cmd.workspace.clone())
            .await?;
        Ok(GroupWorkspaceResult {
            group_id: cmd.group_id,
            workspace: cmd.workspace,
        })
    }

    async fn update_routing_policy(
        &self,
        cmd: GroupRoutingPolicyCommand,
    ) -> Result<GroupRoutingPolicyResult, GroupUseCaseError> {
        let group = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;
        let existing_policy = group.routing_policy.clone().unwrap_or_default();
        let new_policy = bcs_service_api::RoutingPolicy {
            mode: cmd.mode.unwrap_or(existing_policy.mode),
            default_bot_final_delivery: cmd
                .default_bot_final_delivery
                .unwrap_or(existing_policy.default_bot_final_delivery),
            sender_routes: cmd.sender_routes.unwrap_or(existing_policy.sender_routes),
        };

        if !new_policy.sender_routes.is_empty() {
            let participant_ids = group.participant_ids();
            validate_sender_routes(&new_policy.sender_routes, &participant_ids)
                .map_err(|error| GroupUseCaseError::InvalidProposal(error.to_string()))?;
        }

        self.group
            .mutate(group_mutation_command(
                &cmd.group_id,
                cmd.caller_actor_id.as_deref().unwrap_or("system"),
                GroupMutationKind::UpdateRoutingPolicy(new_policy.clone()),
            ))
            .await?;
        Ok(GroupRoutingPolicyResult {
            group_id: cmd.group_id,
            routing_policy: new_policy,
        })
    }

    async fn update_participant_mode(
        &self,
        cmd: GroupParticipantModeCommand,
    ) -> Result<GroupParticipantModeResult, GroupUseCaseError> {
        let target = self
            .registry
            .get(&cmd.actor_id)
            .await
            .ok_or_else(|| GroupUseCaseError::ActorNotFound(cmd.actor_id.clone()))?;
        if !cmd.mode.is_valid_for(target.actor_kind) {
            return Err(GroupUseCaseError::InvalidParticipantMode {
                mode: cmd.mode,
                actor_kind: target.actor_kind,
            });
        }
        if cmd
            .message_view_scope
            .is_some_and(|scope| !scope.is_valid_for(target.actor_kind))
        {
            return Err(GroupUseCaseError::InvalidProposal(
                "Bot participants must use full message_view_scope".to_string(),
            ));
        }

        let group = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;
        let coordinator_scope_only = if let Err(self_error) = self
            .ensure_actor_self_or_creator(&cmd.caller_actor_id, &cmd.actor_id)
            .await
        {
            if cmd.message_view_scope.is_some() {
                self.ensure_group_coordinator(
                    &group,
                    &cmd.caller_actor_id,
                    "update participant message scope",
                )?;
                true
            } else {
                return Err(self_error);
            }
        } else {
            false
        };
        let existing_participant = group
            .participants
            .iter()
            .find(|participant| participant.bot_uuid == cmd.actor_id);
        if coordinator_scope_only && existing_participant.is_none() {
            return Err(ServiceError::ParticipantNotFound(cmd.actor_id.clone()).into());
        }
        let effective_mode = if coordinator_scope_only {
            existing_participant
                .and_then(|participant| participant.mode)
                .unwrap_or_else(|| ParticipantMode::default_for(target.actor_kind))
        } else {
            cmd.mode
        };
        let mutation = if existing_participant.is_some() && cmd.message_view_scope.is_some() {
            GroupMutationKind::UpdateParticipantMessageViewScope {
                actor_id: cmd.actor_id.clone(),
                message_view_scope: cmd.message_view_scope.expect("scope checked above"),
                mode: Some(effective_mode),
            }
        } else if existing_participant.is_some() {
            GroupMutationKind::UpdateParticipantMode {
                actor_id: cmd.actor_id.clone(),
                mode: effective_mode,
            }
        } else if target.actor_kind == ActorKind::Human {
            GroupMutationKind::AddParticipant {
                participant: Participant {
                    bot_uuid: cmd.actor_id.clone(),
                    bot_name: target.capabilities.name.clone(),
                    kind: None,
                    role: ParticipantRole::Observer,
                    actor_kind: ActorKind::Human,
                    mode: Some(effective_mode),
                    tags: Vec::new(),
                    message_view_scope: cmd.message_view_scope.unwrap_or(MessageViewScope::Full),
                },
                actor_is_public: true,
            }
        } else {
            return Err(ServiceError::ParticipantNotFound(cmd.actor_id.clone()).into());
        };
        let scope_change_lease = if existing_participant.is_some_and(|participant| {
            participant.actor_kind == ActorKind::Human
                && cmd.message_view_scope.is_some_and(|scope| {
                    scope != participant.message_view_scope
                })
        }) {
            Some(
                self.participant_view_bindings
                    .begin_scope_change(&cmd.group_id, &cmd.actor_id)
                    .await
                    .map_err(GroupUseCaseError::Service)?,
            )
        } else {
            None
        };
        let update_result = self
            .group
            .mutate(group_mutation_command(
                &cmd.group_id,
                &cmd.caller_actor_id,
                mutation,
            ))
            .await;
        let release_result = if let Some(lease) = scope_change_lease {
            self.participant_view_bindings
                .finish_scope_change(lease)
                .await
                .map_err(GroupUseCaseError::Service)
        } else {
            Ok(())
        };
        let updated_group = update_result?;
        release_result?;
        let message_view_scope = updated_group
            .participants
            .iter()
            .find(|participant| participant.bot_uuid == cmd.actor_id)
            .map(|participant| participant.message_view_scope)
            .ok_or_else(|| ServiceError::ParticipantNotFound(cmd.actor_id.clone()))?;

        Ok(GroupParticipantModeResult {
            group_id: cmd.group_id,
            actor_id: cmd.actor_id,
            mode: effective_mode,
            message_view_scope,
        })
    }

    async fn patch_group_settings(
        &self,
        cmd: GroupPatchSettingsCommand,
    ) -> Result<GroupPatchSettingsResult, GroupUseCaseError> {
        let group = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;

        let running_service_count = self
            .session_management
            .count_running_service(&cmd.group_id)
            .await
            .unwrap_or(0);

        if let Some(spec_patch) = cmd.service_spec.clone() {
            if let Err(error) = validate_service_spec_patch(
                group.service_spec.as_ref(),
                spec_patch.as_ref(),
                running_service_count,
            ) {
                let conflict = match error {
                    crate::core::ServiceSpecPatchError::CallbackConfigImmutable => {
                        GroupPatchSettingsConflict {
                            field: ServiceSpecPatchConflictField::CallbackConfig,
                            running_service_count,
                        }
                    }
                    crate::core::ServiceSpecPatchError::RouteFieldsLocked(count) => {
                        GroupPatchSettingsConflict {
                            field: ServiceSpecPatchConflictField::RouteFields,
                            running_service_count: count,
                        }
                    }
                };
                return Err(GroupUseCaseError::Conflict(
                    serde_json::to_string(&conflict)
                        .unwrap_or_else(|_| "service_spec patch rejected".to_string()),
                ));
            }

            self.group
                .mutate(group_mutation_command(
                    &cmd.group_id,
                    "system",
                    GroupMutationKind::UpdateServiceSpec(spec_patch),
                ))
                .await?;
        }

        let updated = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;

        Ok(GroupPatchSettingsResult {
            group_id: cmd.group_id,
            service_spec: updated.service_spec,
        })
    }
}
