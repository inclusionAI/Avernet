//! `GroupRepoPort` wiring for the MySQL-backed Group store.
//!
//! Method bodies live in the concern modules (`store_reads`, `store_writes`,
//! `store_eventful`, `store_lists`, `store_finds`, `store_dm`) as inherent
//! `pub(crate)` methods with a `_sql` suffix. The delegating bodies below call
//! those inherent methods; inherent candidates win method resolution, so these
//! one-line bodies do not recurse into themselves.
use super::*;

#[async_trait]
impl GroupRepoPort for MySqlGroupStore {
    async fn upsert(&self, group: Group) -> ServiceResult<()> {
        self.upsert_sql(group).await
    }

    async fn finalize_provisioning(&self, command: FinalizeGroupProvisioning) -> ServiceResult<()> {
        self.finalize_provisioning_sql(command).await
    }

    async fn commit_eventful_mutation(
        &self,
        command: CommitGroupEventfulMutation,
    ) -> ServiceResult<Group> {
        self.commit_eventful_mutation_sql(command).await
    }

    async fn patch_mutable_fields(
        &self,
        id: &str,
        patch: GroupMutableFieldsPatch,
    ) -> ServiceResult<()> {
        self.patch_mutable_fields_sql(id, patch).await
    }

    async fn get(&self, id: &str) -> Option<Group> {
        match self.try_get(id).await {
            Ok(group) => group,
            Err(error) => {
                warn!(group_id = %id, error = %error, "Failed to load Group");
                None
            }
        }
    }

    async fn read_human_notify_policy(
        &self,
        group_id: &str,
    ) -> ServiceResult<Option<GroupHumanNotifyPolicy>> {
        self.read_human_notify_policy_sql(group_id).await
    }

    async fn try_get(&self, id: &str) -> ServiceResult<Option<Group>> {
        // Check cache first
        {
            let cache = self.cache.read().await;
            if let Some(group) = cache.get(id) {
                return Ok(Some(group.clone()));
            }
        }
        // Cache miss — load from DB and populate cache
        let Some(group) = self.load_group_from_mysql(id).await? else {
            return Ok(None);
        };
        {
            let mut cache = self.cache.write().await;
            cache.insert(id.to_string(), group.clone());
        }
        Ok(Some(group))
    }

    async fn add_message(&self, id: &str, _message: GroupMessage) -> ServiceResult<()> {
        debug!(group_id = %id, "Message added to group (not persisted)");
        Ok(())
    }

    async fn add_participant(&self, id: &str, participant: Participant) -> ServiceResult<()> {
        self.add_participant_sql(id, participant).await
    }

    async fn add_participant_with_visibility_guard(
        &self,
        id: &str,
        participant: Participant,
        actor_is_public: bool,
    ) -> ServiceResult<()> {
        self.add_participant_with_visibility_guard_sql(id, participant, actor_is_public)
            .await
    }

    async fn remove_participant(&self, group_id: &str, bot_uuid: &str) -> ServiceResult<()> {
        self.remove_participant_sql(group_id, bot_uuid).await
    }

    async fn update_participant_mode(
        &self,
        group_id: &str,
        actor_id: &str,
        mode: ParticipantMode,
    ) -> ServiceResult<()> {
        self.update_participant_mode_sql(group_id, actor_id, mode).await
    }

    async fn update_participant_message_view_scope(
        &self,
        group_id: &str,
        actor_id: &str,
        message_view_scope: MessageViewScope,
    ) -> ServiceResult<()> {
        self.update_participant_message_view_scope_sql(group_id, actor_id, message_view_scope)
            .await
    }

    async fn update_workspace(&self, id: &str, workspace: Workspace) -> ServiceResult<()> {
        self.update_workspace_sql(id, workspace).await
    }

    async fn update_label(&self, id: &str, label: Option<String>) -> ServiceResult<()> {
        self.update_label_sql(id, label).await
    }

    async fn update_status(&self, id: &str, status: GroupStatus) -> ServiceResult<()> {
        self.update_status_sql(id, status).await
    }

    async fn update_service_spec(
        &self,
        id: &str,
        service_spec: Option<bcs_service_api::ServiceSpec>,
    ) -> ServiceResult<()> {
        self.update_service_spec_sql(id, service_spec).await
    }

    async fn delete(&self, id: &str) -> ServiceResult<Option<Group>> {
        self.delete_sql(id).await
    }

    async fn list(&self) -> Vec<Group> {
        self.load_all_groups_from_mysql().await
    }

    async fn list_paginated(&self, offset: u64, limit: u64) -> Vec<Group> {
        self.list_paginated_sql(offset, limit).await
    }

    async fn find_by_participant(&self, bot_uuid: &str) -> Vec<Group> {
        self.find_by_participant_sql(bot_uuid).await
    }

    async fn try_find_by_participant(&self, bot_uuid: &str) -> ServiceResult<Vec<Group>> {
        self.try_find_by_participant_sql(bot_uuid).await
    }

    async fn find_by_participant_filtered(
        &self,
        bot_uuid: &str,
        kind: Option<bcs_service_api::GroupKind>,
        label_query: Option<&str>,
    ) -> Vec<Group> {
        self.find_by_participant_filtered_sql(bot_uuid, kind, label_query)
            .await
    }

    async fn count(&self) -> u64 {
        self.count_sql().await
    }

    async fn count_by_kind(&self, kind: Option<bcs_service_api::GroupKind>) -> u64 {
        self.count_by_kind_sql(kind).await
    }

    async fn count_by_participant(&self, bot_uuid: &str) -> u64 {
        self.count_by_participant_sql(bot_uuid).await
    }

    async fn list_paginated_by_kind(
        &self,
        kind: Option<bcs_service_api::GroupKind>,
        offset: u64,
        limit: u64,
    ) -> Vec<Group> {
        self.list_paginated_by_kind_sql(kind, offset, limit).await
    }

    async fn find_by_participant_paginated(
        &self,
        bot_uuid: &str,
        offset: u64,
        limit: u64,
    ) -> Vec<Group> {
        self.find_by_participant_paginated_sql(bot_uuid, offset, limit)
            .await
    }

    async fn message_count(&self, id: &str) -> ServiceResult<usize> {
        let counts = self.message_counts.read().await;
        Ok(counts.get(id).copied().unwrap_or(0))
    }

    async fn increment_message_count(&self, id: &str) -> ServiceResult<()> {
        let mut counts = self.message_counts.write().await;
        *counts.entry(id.to_string()).or_insert(0) += 1;
        Ok(())
    }

    async fn reset_message_count(&self, id: &str) -> ServiceResult<()> {
        let mut counts = self.message_counts.write().await;
        counts.insert(id.to_string(), 0);
        Ok(())
    }

    async fn find_dm_by_pair_key(&self, dm_pair_key: &str) -> Option<Group> {
        self.find_dm_by_pair_key_sql(dm_pair_key).await
    }

    async fn insert_dm_group_if_absent(&self, group: Group) -> ServiceResult<bool> {
        self.insert_dm_group_if_absent_sql(group).await
    }

    async fn update_visibility(&self, id: &str, visibility: &str) -> ServiceResult<()> {
        self.update_visibility_sql(id, visibility).await
    }

    async fn count_filtered(
        &self,
        kind: Option<bcs_service_api::GroupKind>,
        visibility: Option<&str>,
        label: Option<&str>,
    ) -> u64 {
        self.count_filtered_sql(kind, visibility, label).await
    }

    async fn list_paginated_filtered(
        &self,
        offset: u64,
        limit: u64,
        kind: Option<bcs_service_api::GroupKind>,
        visibility: Option<&str>,
        label: Option<&str>,
    ) -> Vec<Group> {
        self.list_paginated_filtered_sql(offset, limit, kind, visibility, label)
            .await
    }
}
