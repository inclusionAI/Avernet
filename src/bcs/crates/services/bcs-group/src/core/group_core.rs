use std::collections::BTreeMap;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_group_store::MemoryGroupRepo;
use bcs_service_api::core::{GroupMutationCommand, GroupMutationKind};
use bcs_service_api::port::repo::{
    CommitGroupEventfulMutation, FinalizeGroupProvisioning, GroupEventfulMutation, GroupRepoPort,
};
use bcs_service_api::port::{EventRecordFactoryPort, NewEvent};
use bcs_service_api::types::{EVENT_SCHEMA_VERSION_V1, EventScope, EventSubject};
use bcs_service_api::{
    ActorKind, DmActorSpec, Group, GroupCoreService, GroupKind, GroupMessage,
    GroupMutableFieldsPatch, GroupStatus, Participant, ParticipantMode, ParticipantRole,
    ServiceError, ServiceResult, ServiceSpec, Workspace,
};
use chrono::{SecondsFormat, Utc};
use serde_json::{Value, json};

/// Core group service implementation.
///
/// `GroupCore` owns group behavior and delegates persistence to a repository.
#[derive(Clone)]
pub struct GroupCore {
    repo: Arc<dyn GroupRepoPort>,
    event_record_factory: Option<Arc<dyn EventRecordFactoryPort>>,
}

impl GroupCore {
    pub fn new() -> Self {
        Self::memory()
    }

    pub fn with_repo(repo: Arc<dyn GroupRepoPort>) -> Self {
        Self {
            repo,
            event_record_factory: None,
        }
    }

    pub fn with_event_record_factory(
        mut self,
        event_record_factory: Arc<dyn EventRecordFactoryPort>,
    ) -> Self {
        self.event_record_factory = Some(event_record_factory);
        self
    }

    pub fn memory() -> Self {
        Self::with_repo(Arc::new(MemoryGroupRepo::new()))
    }
}

impl Default for GroupCore {
    fn default() -> Self {
        Self::memory()
    }
}

#[async_trait]
impl GroupCoreService for GroupCore {
    async fn upsert(&self, group: Group) -> ServiceResult<()> {
        self.repo.upsert(group).await
    }

    async fn finalize_provisioning(&self, command: FinalizeGroupProvisioning) -> ServiceResult<()> {
        self.repo.finalize_provisioning(command).await
    }

    async fn mutate(&self, command: GroupMutationCommand) -> ServiceResult<Group> {
        let current = self
            .repo
            .try_get(&command.group_id)
            .await?
            .ok_or_else(|| ServiceError::GroupNotFound(command.group_id.clone()))?;
        let next_version = current.version.checked_add(1).ok_or_else(|| {
            ServiceError::Conflict(format!("Group '{}' version overflow", current.id))
        })?;
        let Some(prepared) = prepare_group_mutation(&current, &command.mutation, next_version)?
        else {
            return Ok(current);
        };
        let mutated_at = Utc::now();
        let mutated_at_ms = u64::try_from(mutated_at.timestamp_millis()).map_err(|_| {
            ServiceError::InternalError("Group mutation timestamp is out of range".to_string())
        })?;
        let event = match (prepared.event, self.event_record_factory.as_ref()) {
            (Some(event), Some(factory)) => factory
                .prepare(NewEvent {
                    event_id: format!("evt_{}", uuid::Uuid::new_v4()),
                    event_type: event.event_type.to_string(),
                    schema_version: EVENT_SCHEMA_VERSION_V1.to_string(),
                    producer: "bcs-group".to_string(),
                    producer_key: format!("{}:{}:v{}", event.event_type, current.id, next_version),
                    occurred_at: mutated_at.to_rfc3339_opts(SecondsFormat::Millis, true),
                    subject: event.subject,
                    scope: EventScope {
                        group_id: Some(current.id.clone()),
                        ..EventScope::default()
                    },
                    stream_key: format!("group:{}", current.id),
                    actor: Some(command.actor),
                    correlation_id: command.correlation_id,
                    causation_event_id: None,
                    trace_id: command.trace_id,
                    data: event.data,
                })
                .map_err(|error| ServiceError::InternalError(error.to_string()))?,
            _ => None,
        };
        self.repo
            .commit_eventful_mutation(CommitGroupEventfulMutation {
                group_id: current.id.clone(),
                expected_version: current.version,
                mutated_at_ms,
                mutation: prepared.mutation,
                event,
            })
            .await
    }

    async fn patch_mutable_fields(
        &self,
        id: &str,
        patch: GroupMutableFieldsPatch,
    ) -> ServiceResult<()> {
        self.repo.patch_mutable_fields(id, patch).await
    }

    async fn get(&self, id: &str) -> Option<Group> {
        self.repo.get(id).await
    }

    async fn try_get(&self, id: &str) -> ServiceResult<Option<Group>> {
        self.repo.try_get(id).await
    }

    async fn add_message(&self, id: &str, message: GroupMessage) -> ServiceResult<()> {
        self.repo.add_message(id, message).await
    }

    async fn add_participant(&self, id: &str, participant: Participant) -> ServiceResult<()> {
        self.repo.add_participant(id, participant).await
    }

    async fn add_participant_with_visibility_guard(
        &self,
        id: &str,
        participant: Participant,
        actor_is_public: bool,
    ) -> ServiceResult<()> {
        self.repo
            .add_participant_with_visibility_guard(id, participant, actor_is_public)
            .await
    }

    async fn remove_participant(&self, group_id: &str, bot_uuid: &str) -> ServiceResult<()> {
        self.repo.remove_participant(group_id, bot_uuid).await
    }

    async fn update_participant_mode(
        &self,
        group_id: &str,
        actor_id: &str,
        mode: ParticipantMode,
    ) -> ServiceResult<()> {
        self.repo
            .update_participant_mode(group_id, actor_id, mode)
            .await
    }

    async fn update_workspace(&self, id: &str, workspace: Workspace) -> ServiceResult<()> {
        self.repo.update_workspace(id, workspace).await
    }

    async fn update_label(&self, id: &str, label: Option<String>) -> ServiceResult<()> {
        self.repo.update_label(id, label).await
    }

    async fn update_status(&self, id: &str, status: GroupStatus) -> ServiceResult<()> {
        self.repo.update_status(id, status).await
    }

    async fn update_service_spec(
        &self,
        id: &str,
        service_spec: Option<ServiceSpec>,
    ) -> ServiceResult<()> {
        self.repo.update_service_spec(id, service_spec).await
    }

    async fn terminate(&self, id: &str, caller_bot_id: &str) -> ServiceResult<Group> {
        let group = self
            .repo
            .get(id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;

        if group.driver_bot != caller_bot_id && group.originator() != caller_bot_id {
            return Err(ServiceError::Unauthorized(format!(
                "Only the group coordinator (originator: {} or driver: {}) can terminate group, caller is {}",
                group.originator(),
                group.driver_bot,
                caller_bot_id
            )));
        }

        self.repo.update_status(id, GroupStatus::Completed).await?;
        self.repo
            .get(id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))
    }

    async fn delete(&self, id: &str) -> ServiceResult<Option<Group>> {
        self.repo.delete(id).await
    }

    async fn list(&self) -> Vec<Group> {
        self.repo.list().await
    }

    async fn list_paginated(&self, offset: u64, limit: u64) -> Vec<Group> {
        self.repo.list_paginated(offset, limit).await
    }

    async fn find_by_participant(&self, bot_uuid: &str) -> Vec<Group> {
        self.repo.find_by_participant(bot_uuid).await
    }

    async fn try_find_by_participant(&self, bot_uuid: &str) -> ServiceResult<Vec<Group>> {
        self.repo.try_find_by_participant(bot_uuid).await
    }

    async fn find_by_participant_filtered(
        &self,
        bot_uuid: &str,
        kind: Option<GroupKind>,
        label_query: Option<&str>,
    ) -> Vec<Group> {
        self.repo
            .find_by_participant_filtered(bot_uuid, kind, label_query)
            .await
    }

    async fn count(&self) -> u64 {
        self.repo.count().await
    }

    async fn count_by_participant(&self, bot_uuid: &str) -> u64 {
        self.repo.count_by_participant(bot_uuid).await
    }

    async fn find_by_participant_paginated(
        &self,
        bot_uuid: &str,
        offset: u64,
        limit: u64,
    ) -> Vec<Group> {
        self.repo
            .find_by_participant_paginated(bot_uuid, offset, limit)
            .await
    }

    async fn message_count(&self, id: &str) -> ServiceResult<usize> {
        self.repo.message_count(id).await
    }

    async fn increment_message_count(&self, id: &str) -> ServiceResult<()> {
        self.repo.increment_message_count(id).await
    }

    async fn reset_message_count(&self, id: &str) -> ServiceResult<()> {
        self.repo.reset_message_count(id).await
    }

    async fn count_by_kind(&self, kind: Option<GroupKind>) -> u64 {
        self.repo.count_by_kind(kind).await
    }

    async fn list_paginated_by_kind(
        &self,
        kind: Option<GroupKind>,
        offset: u64,
        limit: u64,
    ) -> Vec<Group> {
        self.repo.list_paginated_by_kind(kind, offset, limit).await
    }

    async fn update_visibility(&self, id: &str, visibility: &str) -> ServiceResult<()> {
        self.repo.update_visibility(id, visibility).await
    }

    async fn count_filtered(
        &self,
        kind: Option<GroupKind>,
        visibility: Option<&str>,
        label: Option<&str>,
    ) -> u64 {
        self.repo.count_filtered(kind, visibility, label).await
    }

    async fn list_paginated_filtered(
        &self,
        offset: u64,
        limit: u64,
        kind: Option<GroupKind>,
        visibility: Option<&str>,
        label: Option<&str>,
    ) -> Vec<Group> {
        self.repo
            .list_paginated_filtered(offset, limit, kind, visibility, label)
            .await
    }

    async fn find_dm_by_pair_key(&self, dm_pair_key: &str) -> Option<Group> {
        self.repo.find_dm_by_pair_key(dm_pair_key).await
    }

    async fn create_or_reuse_actor_dm_group(
        &self,
        id: &str,
        actor_a: DmActorSpec,
        actor_b: DmActorSpec,
        legacy_driver_bot: &str,
        originator_actor_id: &str,
        label: Option<String>,
        context: Option<String>,
    ) -> ServiceResult<(Group, bool)> {
        self.create_or_reuse_actor_dm_group_with_record_status(
            id,
            actor_a,
            actor_b,
            legacy_driver_bot,
            originator_actor_id,
            label,
            context,
            "active",
        )
        .await
    }

    async fn create_or_reuse_actor_dm_group_with_record_status(
        &self,
        id: &str,
        actor_a: DmActorSpec,
        actor_b: DmActorSpec,
        legacy_driver_bot: &str,
        originator_actor_id: &str,
        label: Option<String>,
        context: Option<String>,
        record_status: &str,
    ) -> ServiceResult<(Group, bool)> {
        if actor_a.actor_id == actor_b.actor_id {
            return Err(ServiceError::InvalidOperation {
                message: "DM requires two distinct actors".to_string(),
                request_id: None,
            });
        }

        let bot_count = [actor_a.actor_kind, actor_b.actor_kind]
            .into_iter()
            .filter(|kind| *kind == ActorKind::Bot)
            .count();
        if bot_count == 0 {
            return Err(ServiceError::InvalidOperation {
                message: "Human-Human DM is not supported".to_string(),
                request_id: None,
            });
        }

        let pair_key = Group::compute_dm_pair_key(&actor_a.actor_id, &actor_b.actor_id);

        if let Some(existing) = self.repo.find_dm_by_pair_key(&pair_key).await {
            return Ok((existing, false));
        }

        let actors = [actor_a, actor_b];
        let legacy_driver_is_bot = actors
            .iter()
            .any(|actor| actor.actor_kind == ActorKind::Bot && actor.actor_id == legacy_driver_bot);
        let effective_driver_bot = if legacy_driver_is_bot {
            legacy_driver_bot.to_string()
        } else {
            actors
                .iter()
                .find(|actor| actor.actor_kind == ActorKind::Bot)
                .map(|actor| actor.actor_id.clone())
                .ok_or_else(|| ServiceError::InvalidOperation {
                    message: "DM requires at least one Bot participant".to_string(),
                    request_id: None,
                })?
        };

        let participants = actors
            .iter()
            .map(|actor| {
                let role = match actor.actor_kind {
                    ActorKind::Human => ParticipantRole::Observer,
                    ActorKind::Bot if actor.actor_id == effective_driver_bot => {
                        ParticipantRole::Driver
                    }
                    ActorKind::Bot => ParticipantRole::Consultant,
                };
                let mode = match actor.actor_kind {
                    ActorKind::Human => ParticipantMode::Present,
                    ActorKind::Bot => ParticipantMode::Auto,
                };
                Participant {
                    bot_uuid: actor.actor_id.clone(),
                    bot_name: actor.display_name.clone(),
                    kind: None,
                    role,
                    actor_kind: actor.actor_kind,
                    mode: Some(mode),
                }
            })
            .collect();

        let mut group = Group::new(id, effective_driver_bot, participants);
        group.label = label;
        group.context = context;
        group.originator = Some(originator_actor_id.to_string());
        group.group_kind = GroupKind::Dm;
        group.dm_pair_key = Some(pair_key.clone());
        group.record_status = record_status.to_string();

        if self.repo.insert_dm_group_if_absent(group.clone()).await? {
            return Ok((group, true));
        }

        self.repo
            .find_dm_by_pair_key(&pair_key)
            .await
            .map(|existing| (existing, false))
            .ok_or_else(|| {
                ServiceError::InternalError(format!(
                    "create_or_reuse_dm_group: lost race on pair_key {} but refetch returned None",
                    pair_key
                ))
            })
    }

    async fn create_or_reuse_dm_group(
        &self,
        id: &str,
        driver_bot: &str,
        bot_a: &str,
        bot_b: &str,
        label: Option<String>,
    ) -> ServiceResult<(Group, bool)> {
        self.create_or_reuse_actor_dm_group(
            id,
            DmActorSpec {
                actor_id: bot_a.to_string(),
                actor_kind: ActorKind::Bot,
                display_name: None,
            },
            DmActorSpec {
                actor_id: bot_b.to_string(),
                actor_kind: ActorKind::Bot,
                display_name: None,
            },
            driver_bot,
            driver_bot,
            label,
            None,
        )
        .await
    }
}

struct PreparedGroupMutation {
    event: Option<PreparedGroupEvent>,
    mutation: GroupEventfulMutation,
}

struct PreparedGroupEvent {
    event_type: &'static str,
    subject: EventSubject,
    data: BTreeMap<String, Value>,
}

fn prepare_group_mutation(
    group: &Group,
    mutation: &GroupMutationKind,
    next_version: i32,
) -> ServiceResult<Option<PreparedGroupMutation>> {
    let participant_subject = |actor_id: &str| EventSubject {
        subject_type: "participant".to_string(),
        id: actor_id.to_string(),
    };
    let mut data = BTreeMap::new();
    let prepared = match mutation {
        GroupMutationKind::PatchMutableFields(patch) => {
            let mut changed_fields = Vec::new();
            if let Some(label) = &patch.label
                && group.label.as_ref() != Some(label)
            {
                changed_fields.push("name");
            }
            if let Some(context) = &patch.context
                && group.context.as_ref() != Some(context)
            {
                changed_fields.push("context");
            }
            if let Some(visibility) = &patch.visibility
                && group.visibility != *visibility
            {
                changed_fields.push("visibility");
            }
            if let Some(delivery) = patch.default_bot_final_delivery {
                let current = group
                    .routing_policy
                    .as_ref()
                    .map(|policy| policy.default_bot_final_delivery)
                    .unwrap_or_default();
                if current != delivery {
                    changed_fields.push("delivery_policy");
                }
            }
            if changed_fields.is_empty() {
                return Ok(None);
            }
            PreparedGroupMutation {
                event: None,
                mutation: GroupEventfulMutation::PatchMutableFields(patch.clone()),
            }
        }
        GroupMutationKind::UpdateStatus { status, reason } => {
            if group.status == *status {
                return Ok(None);
            }
            validate_reason(reason)?;
            PreparedGroupMutation {
                event: None,
                mutation: GroupEventfulMutation::UpdateStatus(*status),
            }
        }
        GroupMutationKind::AddParticipant {
            participant,
            actor_is_public,
        } => {
            if group
                .participants
                .iter()
                .any(|existing| existing.bot_uuid == participant.bot_uuid)
            {
                return Ok(None);
            }
            data.insert("actor_id".to_string(), json!(participant.bot_uuid));
            data.insert(
                "actor_type".to_string(),
                json!(actor_kind_name(participant.actor_kind)),
            );
            data.insert(
                "role".to_string(),
                json!(participant_role_name(participant.role)),
            );
            data.insert(
                "mode".to_string(),
                json!(participant_mode_name(participant.effective_mode())),
            );
            data.insert("group_version".to_string(), json!(next_version));
            PreparedGroupMutation {
                event: Some(PreparedGroupEvent {
                    event_type: "group.participant.added",
                    subject: participant_subject(&participant.bot_uuid),
                    data,
                }),
                mutation: GroupEventfulMutation::AddParticipant {
                    participant: participant.clone(),
                    actor_is_public: *actor_is_public,
                },
            }
        }
        GroupMutationKind::RemoveParticipant { actor_id, reason } => {
            validate_reason(reason)?;
            let participant = group
                .participants
                .iter()
                .find(|participant| participant.bot_uuid == *actor_id)
                .ok_or_else(|| ServiceError::ParticipantNotFound(actor_id.clone()))?;
            data.insert("actor_id".to_string(), json!(actor_id));
            data.insert(
                "actor_type".to_string(),
                json!(actor_kind_name(participant.actor_kind)),
            );
            data.insert(
                "previous_role".to_string(),
                json!(participant_role_name(participant.role)),
            );
            data.insert("reason".to_string(), json!(reason));
            data.insert("group_version".to_string(), json!(next_version));
            PreparedGroupMutation {
                event: Some(PreparedGroupEvent {
                    event_type: "group.participant.removed",
                    subject: participant_subject(actor_id),
                    data,
                }),
                mutation: GroupEventfulMutation::RemoveParticipant {
                    actor_id: actor_id.clone(),
                },
            }
        }
        GroupMutationKind::UpdateParticipantMode { actor_id, mode } => {
            let participant = group
                .participants
                .iter()
                .find(|participant| participant.bot_uuid == *actor_id)
                .ok_or_else(|| ServiceError::ParticipantNotFound(actor_id.clone()))?;
            let before = participant.effective_mode();
            if before == *mode {
                return Ok(None);
            }
            PreparedGroupMutation {
                event: None,
                mutation: GroupEventfulMutation::UpdateParticipantMode {
                    actor_id: actor_id.clone(),
                    mode: *mode,
                },
            }
        }
        GroupMutationKind::UpdateRoutingPolicy(policy) => {
            if serde_json::to_value(&group.routing_policy)
                .map_err(|error| ServiceError::InternalError(error.to_string()))?
                == serde_json::to_value(Some(policy))
                    .map_err(|error| ServiceError::InternalError(error.to_string()))?
            {
                return Ok(None);
            }
            PreparedGroupMutation {
                event: None,
                mutation: GroupEventfulMutation::UpdateRoutingPolicy(policy.clone()),
            }
        }
        GroupMutationKind::UpdateServiceSpec(service_spec) => {
            if serde_json::to_value(&group.service_spec)
                .map_err(|error| ServiceError::InternalError(error.to_string()))?
                == serde_json::to_value(service_spec)
                    .map_err(|error| ServiceError::InternalError(error.to_string()))?
            {
                return Ok(None);
            }
            PreparedGroupMutation {
                event: None,
                mutation: GroupEventfulMutation::UpdateServiceSpec(service_spec.clone()),
            }
        }
        GroupMutationKind::Delete { reason } => {
            validate_reason(reason)?;
            PreparedGroupMutation {
                event: None,
                mutation: GroupEventfulMutation::Delete,
            }
        }
    };
    Ok(Some(prepared))
}

fn validate_reason(reason: &str) -> ServiceResult<()> {
    if reason.trim().is_empty() {
        return Err(ServiceError::InvalidOperation {
            message: "Group mutation reason must not be empty".to_string(),
            request_id: None,
        });
    }
    Ok(())
}

fn actor_kind_name(kind: ActorKind) -> &'static str {
    match kind {
        ActorKind::Bot => "bot",
        ActorKind::Human => "human",
    }
}

fn participant_role_name(role: ParticipantRole) -> &'static str {
    match role {
        ParticipantRole::Driver => "driver",
        ParticipantRole::Consultant => "consultant",
        ParticipantRole::Manager => "manager",
        ParticipantRole::Worker => "worker",
        ParticipantRole::Observer => "observer",
    }
}

fn participant_mode_name(mode: ParticipantMode) -> &'static str {
    match mode {
        ParticipantMode::Auto => "auto",
        ParticipantMode::Muted => "muted",
        ParticipantMode::Present => "present",
        ParticipantMode::Absent => "absent",
    }
}
