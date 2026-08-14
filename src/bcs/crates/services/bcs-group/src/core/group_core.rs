use std::sync::Arc;

use async_trait::async_trait;
use bcs_group_store::MemoryGroupRepo;
use bcs_service_api::port::repo::GroupRepoPort;
use bcs_service_api::{
    ActorKind, DmActorSpec, Group, GroupCoreService, GroupKind, GroupMessage,
    GroupMutableFieldsPatch, GroupStatus, Participant, ParticipantMode, ParticipantRole,
    ServiceError, ServiceResult, ServiceSpec, Workspace,
};

/// Core group service implementation.
///
/// `GroupCore` owns group behavior and delegates persistence to a repository.
#[derive(Clone)]
pub struct GroupCore {
    repo: Arc<dyn GroupRepoPort>,
}

impl GroupCore {
    pub fn new() -> Self {
        Self::memory()
    }

    pub fn with_repo(repo: Arc<dyn GroupRepoPort>) -> Self {
        Self { repo }
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
