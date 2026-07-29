use async_trait::async_trait;

use crate::types::{
    Group, GroupKind, GroupMessage, GroupMutableFieldsPatch, GroupStatus, Participant,
    ParticipantMode, ServiceResult, ServiceSpec, Workspace,
};

/// Repository contract for group persistence implementations.
///
/// This is intentionally independent from `GroupCoreService`: repositories own
/// persistence and row/domain mapping, while the core service owns group
/// behavior and orchestration.
#[async_trait]
pub trait GroupRepoPort: Send + Sync {
    async fn upsert(&self, group: Group) -> ServiceResult<()>;
    async fn patch_mutable_fields(
        &self,
        id: &str,
        patch: GroupMutableFieldsPatch,
    ) -> ServiceResult<()>;
    /// Apply a field-scoped patch only when the persisted Group version still
    /// matches the caller's snapshot, then return the persisted representation.
    async fn patch_mutable_fields_if_version(
        &self,
        id: &str,
        expected_version: i32,
        patch: GroupMutableFieldsPatch,
    ) -> ServiceResult<Group> {
        let _ = (id, expected_version, patch);
        Err(crate::ServiceError::InvalidOperation {
            message: "versioned mutable Group patch is not configured".to_string(),
            request_id: None,
        })
    }
    async fn get(&self, id: &str) -> Option<Group>;
    async fn try_get(&self, id: &str) -> ServiceResult<Option<Group>> {
        Ok(self.get(id).await)
    }
    async fn add_message(&self, id: &str, message: GroupMessage) -> ServiceResult<()>;
    async fn add_participant(&self, id: &str, participant: Participant) -> ServiceResult<()>;
    /// Add a participant while atomically preserving the invariant that a
    /// public Group may contain only public Bot actors.
    async fn add_participant_with_visibility_guard(
        &self,
        id: &str,
        participant: Participant,
        actor_is_public: bool,
    ) -> ServiceResult<()> {
        let _ = actor_is_public;
        self.add_participant(id, participant).await
    }
    async fn remove_participant(&self, group_id: &str, bot_uuid: &str) -> ServiceResult<()>;
    async fn update_participant_mode(
        &self,
        group_id: &str,
        actor_id: &str,
        mode: ParticipantMode,
    ) -> ServiceResult<()>;
    async fn update_workspace(&self, id: &str, workspace: Workspace) -> ServiceResult<()>;
    async fn update_label(&self, id: &str, label: Option<String>) -> ServiceResult<()>;
    async fn update_status(&self, id: &str, status: GroupStatus) -> ServiceResult<()>;
    /// Persist a `service_spec` patch onto the group. `Some(spec)` installs or
    /// replaces the spec; `None` removes it. Validation (route-field lock,
    /// callback_config immutability) is the caller's responsibility.
    async fn update_service_spec(
        &self,
        id: &str,
        service_spec: Option<ServiceSpec>,
    ) -> ServiceResult<()>;
    async fn delete(&self, id: &str) -> ServiceResult<Option<Group>>;
    async fn list(&self) -> Vec<Group>;
    /// List groups ordered by `updated_at` descending, then apply pagination.
    async fn list_paginated(&self, offset: u64, limit: u64) -> Vec<Group>;
    /// Find groups by participant. Return order is intentionally undefined;
    /// callers with externally visible ordering needs must sort explicitly.
    async fn find_by_participant(&self, bot_uuid: &str) -> Vec<Group>;
    async fn try_find_by_participant(&self, bot_uuid: &str) -> ServiceResult<Vec<Group>> {
        Ok(self.find_by_participant(bot_uuid).await)
    }
    async fn find_by_participant_filtered(
        &self,
        bot_uuid: &str,
        kind: Option<GroupKind>,
        label_query: Option<&str>,
    ) -> Vec<Group> {
        let label_query = label_query.map(str::trim).filter(|q| !q.is_empty());
        self.find_by_participant(bot_uuid)
            .await
            .into_iter()
            .filter(|group| kind.is_none_or(|kind| group.group_kind == kind))
            .filter(|group| {
                label_query.is_none_or(|q| {
                    group
                        .label
                        .as_deref()
                        .unwrap_or("")
                        .to_lowercase()
                        .contains(&q.to_lowercase())
                })
            })
            .collect()
    }
    async fn count(&self) -> u64;
    async fn count_by_participant(&self, bot_uuid: &str) -> u64;
    /// Find groups by participant ordered by `updated_at` descending, then
    /// apply pagination.
    async fn find_by_participant_paginated(
        &self,
        bot_uuid: &str,
        offset: u64,
        limit: u64,
    ) -> Vec<Group>;
    async fn message_count(&self, id: &str) -> ServiceResult<usize>;
    async fn increment_message_count(&self, id: &str) -> ServiceResult<()>;
    async fn reset_message_count(&self, id: &str) -> ServiceResult<()>;
    async fn count_by_kind(&self, kind: Option<GroupKind>) -> u64;
    /// List groups filtered by kind, ordered by `updated_at` descending, then
    /// apply pagination.
    async fn list_paginated_by_kind(
        &self,
        kind: Option<GroupKind>,
        offset: u64,
        limit: u64,
    ) -> Vec<Group>;
    async fn find_dm_by_pair_key(&self, dm_pair_key: &str) -> Option<Group>;
    async fn insert_dm_group_if_absent(&self, group: Group) -> ServiceResult<bool>;

    /// Update group visibility ("public" or "private").
    async fn update_visibility(&self, id: &str, visibility: &str) -> ServiceResult<()>;

    /// List groups filtered by kind, visibility, and label substring.
    /// Results are ordered by `updated_at` descending with pagination.
    async fn list_paginated_filtered(
        &self,
        offset: u64,
        limit: u64,
        kind: Option<GroupKind>,
        visibility: Option<&str>,
        label: Option<&str>,
    ) -> Vec<Group>;

    /// Count groups matching the given filters.
    async fn count_filtered(
        &self,
        kind: Option<GroupKind>,
        visibility: Option<&str>,
        label: Option<&str>,
    ) -> u64;
}
