//! `GroupQueryService` implementation for the legacy Group service.

use super::*;

#[async_trait]
impl GroupQueryService for GroupManagement {
    async fn list_groups(
        &self,
        cmd: GroupListCommand,
    ) -> Result<GroupListResult, GroupUseCaseError> {
        let total = self
            .group
            .count_filtered(
                cmd.group_kind,
                cmd.visibility.as_deref(),
                cmd.label.as_deref(),
            )
            .await;
        let mut groups = self
            .group
            .list_paginated_filtered(
                cmd.offset,
                cmd.limit,
                cmd.group_kind,
                cmd.visibility.as_deref(),
                cmd.label.as_deref(),
            )
            .await;
        for group in &mut groups {
            backfill_bot_names(self.registry.as_ref(), group).await;
        }
        Ok(GroupListResult {
            items: groups.into_iter().map(group_to_list_entry).collect(),
            total,
            offset: cmd.offset,
            limit: cmd.limit,
        })
    }

    async fn get_group(
        &self,
        cmd: GroupDetailCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        let mut group = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;
        backfill_bot_names(self.registry.as_ref(), &mut group).await;
        Ok(group_to_detail(group))
    }

    async fn list_bot_groups(
        &self,
        cmd: bcs_service_api::BotGroupListCommand,
    ) -> Result<GroupListResult, GroupUseCaseError> {
        // Contract-level absent filtering remains in the application layer:
        // the core query returns participant membership, not a mode-aware
        // predicate. Keep filtering and ordering before pagination so `total`
        // and `items` describe only visible groups.
        let mut filtered = self
            .group
            .find_by_participant_filtered(&cmd.bot_id, cmd.group_kind, cmd.q.as_deref())
            .await
            .into_iter()
            .filter(|group| group_has_non_absent_participant(group, &cmd.bot_id))
            .collect::<Vec<_>>();
        DomainGroup::sort_by_updated_at_desc(&mut filtered);
        let total = filtered.len() as u64;
        let mut page = filtered
            .into_iter()
            .skip(to_usize(cmd.offset))
            .take(to_usize(cmd.limit))
            .collect::<Vec<_>>();
        for group in &mut page {
            backfill_bot_names(self.registry.as_ref(), group).await;
        }
        let items = page.into_iter().map(group_to_list_entry).collect();
        Ok(GroupListResult {
            items,
            total,
            offset: cmd.offset,
            limit: cmd.limit,
        })
    }

    async fn get_workspace(
        &self,
        cmd: GroupWorkspaceQueryCommand,
    ) -> Result<GroupWorkspaceResult, GroupUseCaseError> {
        let group = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;
        Ok(GroupWorkspaceResult {
            group_id: cmd.group_id,
            workspace: group.workspace,
        })
    }
}
