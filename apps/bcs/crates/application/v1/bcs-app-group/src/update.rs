//! Field-scoped Group PATCH orchestration.
use super::*;
impl GroupServiceImpl {
    pub(crate) async fn update_impl(&self, command: UpdateGroup) -> Result<GroupDetail, ApplicationError> {
        let principal = self.resolve_principal(&command.caller).await?;
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
        let catalog_labels=self.normalize_catalog_labels(command.patch.catalog_labels.clone(),group.group_kind,group.group_strategy)?;
        let patch = command.patch;
        let mut persistence_patch = GroupMutableFieldsPatch::default();
        persistence_patch.catalog_labels=catalog_labels;
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
            .mutate_with_snapshot(GroupMutationCommand {
                group_id: command.group_id.clone(),
                actor: mutation_actor,
                correlation_id: None,
                trace_id: None,
                mutation: GroupMutationKind::PatchMutableFields(persistence_patch),
            })
            .await
            .map_err(map_service_error)?;
        self.project_detail_with_labels(persisted.group, state_machine_projection, persisted.catalog_labels)
            .await
    }

}
