//! V1 Group detail/summary projections for the BCN OpenAPI v1 facade.

use super::*;

impl GroupServiceImpl {
    pub(crate) async fn project_detail_with_state_machine(
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
            human_mention_notify_mode: group.human_mention_notify_mode,
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
                human_mention_notify_mode: common.human_mention_notify_mode,
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
            opening_message: group.opening_message,
            originator_actor_id: common.originator_actor_id,
            participants: common.participants,
            driver_bot_uuid: group.driver_bot,
            collaboration,
            human_mention_notify_mode: common.human_mention_notify_mode,
            created_at: common.created_at,
            updated_at: common.updated_at,
        }))
    }

    pub(crate) async fn project_detail(&self, group: DomainGroup) -> Result<GroupDetail, ApplicationError> {
        self.project_detail_with_state_machine(group, None).await
    }

    pub(crate) async fn load_state_machine_configuration(
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
            definition: StateMachineDefinition::Reference(StateMachineDefinitionReference {
                definition_id: definition.id,
                version: definition.version,
            }),
            participant_bindings,
        })
    }

    pub(crate) async fn project_summary(
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
                context: group.context,
                status,
                visibility,
                membership,
                originator_actor_id,
                participant_count: group.participants.len(),
                peer_actor,
                human_mention_notify_mode: group.human_mention_notify_mode,
                created_at: group.created_at,
                updated_at: group.updated_at,
            }));
        }

        Ok(GroupSummary::Normal(NormalGroupSummary {
            group_id: group.id,
            version: group.version,
            name: group.label,
            context: group.context,
            status,
            visibility,
            membership,
            originator_actor_id,
            participant_count: group.participants.len(),
            driver_bot_uuid: group.driver_bot,
            strategy: project_strategy(group.group_strategy),
            human_mention_notify_mode: group.human_mention_notify_mode,
            created_at: group.created_at,
            updated_at: group.updated_at,
        }))
    }
}
