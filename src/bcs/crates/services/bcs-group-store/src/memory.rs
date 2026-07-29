//! In-memory group repository implementation.
//!
//! This repository is intended for tests and local single-node development.

use std::collections::HashMap;

use async_trait::async_trait;
use tokio::sync::RwLock;
use tracing::debug;

use bcs_service_api::port::repo::GroupRepoPort;
use bcs_service_api::{
    Group as DomainGroup, GroupKind, GroupMessage, GroupMutableFieldsPatch, GroupStatus,
    GroupStrategy, Participant, ParticipantMode, ServiceError, ServiceResult, ServiceSpec,
    Workspace, generated_group_id,
};
use bcs_service_api::{GroupMetricCount, GroupMetricsSnapshotPort};

/// In-memory implementation of [`GroupRepoPort`].
#[derive(Debug, Default)]
pub struct MemoryGroupRepo {
    groups: RwLock<HashMap<String, DomainGroup>>,
    message_counts: RwLock<HashMap<String, usize>>,
}

impl MemoryGroupRepo {
    /// Create a new group store.
    pub fn new() -> Self {
        Self::default()
    }
}

fn normalize_service_mode_for_metrics(mode: Option<&str>) -> Option<String> {
    match mode.map(str::trim).filter(|s| !s.is_empty()) {
        None => None,
        Some("master_slave") => Some("master_slave".to_string()),
        Some(_) => Some("other".to_string()),
    }
}

#[async_trait]
impl GroupMetricsSnapshotPort for MemoryGroupRepo {
    async fn group_counts(&self) -> ServiceResult<Vec<GroupMetricCount>> {
        let groups = self.groups.read().await;
        let mut counts: Vec<GroupMetricCount> = Vec::new();
        for group in groups.values() {
            let service_mode = normalize_service_mode_for_metrics(group.service_mode.as_deref());
            if let Some(existing) = counts.iter_mut().find(|count| {
                count.status == group.status
                    && count.kind == group.group_kind
                    && count.group_strategy == group.group_strategy
                    && count.service_mode == service_mode
            }) {
                existing.count = existing.count.saturating_add(1);
            } else {
                counts.push(GroupMetricCount {
                    status: group.status,
                    kind: group.group_kind,
                    group_strategy: group.group_strategy,
                    service_mode,
                    count: 1,
                });
            }
        }
        Ok(counts)
    }
}

#[async_trait]
impl GroupRepoPort for MemoryGroupRepo {
    async fn upsert(&self, group: DomainGroup) -> ServiceResult<()> {
        let mut groups = self.groups.write().await;
        debug!(group_id = %group.id, "Group upserted");
        groups.insert(group.id.clone(), group);
        Ok(())
    }

    async fn patch_mutable_fields(
        &self,
        id: &str,
        patch: GroupMutableFieldsPatch,
    ) -> ServiceResult<()> {
        let mut groups = self.groups.write().await;
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        if let Some(label) = patch.label {
            group.label = Some(label);
        }
        if let Some(context) = patch.context {
            group.context = Some(context);
        }
        if let Some(visibility) = patch.visibility {
            group.visibility = visibility;
        }
        if let Some(delivery) = patch.default_bot_final_delivery {
            group
                .routing_policy
                .get_or_insert_with(Default::default)
                .default_bot_final_delivery = delivery;
        }
        group.updated_at = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|duration| duration.as_millis() as u64)
            .unwrap_or(0);
        Ok(())
    }

    async fn patch_mutable_fields_if_version(
        &self,
        id: &str,
        expected_version: i32,
        patch: GroupMutableFieldsPatch,
    ) -> ServiceResult<DomainGroup> {
        let mut groups = self.groups.write().await;
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        if group.version != expected_version {
            return Err(ServiceError::Conflict(format!(
                "Group '{id}' changed while applying the patch"
            )));
        }
        if let Some(label) = patch.label {
            group.label = Some(label);
        }
        if let Some(context) = patch.context {
            group.context = Some(context);
        }
        if let Some(visibility) = patch.visibility {
            group.visibility = visibility;
        }
        if let Some(delivery) = patch.default_bot_final_delivery {
            group
                .routing_policy
                .get_or_insert_with(Default::default)
                .default_bot_final_delivery = delivery;
        }
        group.version = group
            .version
            .checked_add(1)
            .ok_or_else(|| ServiceError::InternalError(format!("Group '{id}' version overflow")))?;
        group.updated_at = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|duration| duration.as_millis() as u64)
            .unwrap_or(0);
        Ok(group.clone())
    }

    async fn get(&self, id: &str) -> Option<DomainGroup> {
        let groups = self.groups.read().await;
        groups.get(id).cloned()
    }

    async fn add_message(&self, id: &str, message: GroupMessage) -> ServiceResult<()> {
        let mut groups = self.groups.write().await;
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;

        // Update timestamp
        group.updated_at = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);

        group.messages.push(message);
        Ok(())
    }

    async fn add_participant(&self, id: &str, participant: Participant) -> ServiceResult<()> {
        let mut groups = self.groups.write().await;
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;

        // Check if already a member
        if !group
            .participants
            .iter()
            .any(|p| p.bot_uuid == participant.bot_uuid)
        {
            group.participants.push(participant);
            group.updated_at = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_millis() as u64)
                .unwrap_or(0);
        }
        Ok(())
    }

    async fn add_participant_with_visibility_guard(
        &self,
        id: &str,
        participant: Participant,
        actor_is_public: bool,
    ) -> ServiceResult<()> {
        let mut groups = self.groups.write().await;
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        if group
            .participants
            .iter()
            .any(|existing| existing.bot_uuid == participant.bot_uuid)
        {
            return Ok(());
        }
        if participant.is_bot() && group.visibility == "public" && !actor_is_public {
            return Err(ServiceError::ExistNonPublicBots {
                bots: vec![(participant.bot_uuid, participant.bot_name)],
            });
        }
        group.participants.push(participant);
        group.version = group
            .version
            .checked_add(1)
            .ok_or_else(|| ServiceError::InternalError(format!("Group '{id}' version overflow")))?;
        group.updated_at = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|duration| duration.as_millis() as u64)
            .unwrap_or(0);
        Ok(())
    }

    async fn remove_participant(&self, group_id: &str, bot_uuid: &str) -> ServiceResult<()> {
        let mut groups = self.groups.write().await;
        let group = groups
            .get_mut(group_id)
            .ok_or_else(|| ServiceError::GroupNotFound(group_id.to_string()))?;

        let initial_len = group.participants.len();
        group.participants.retain(|p| p.bot_uuid != bot_uuid);

        if group.participants.len() == initial_len {
            return Err(ServiceError::ParticipantNotFound(bot_uuid.to_string()));
        }

        group.updated_at = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);
        Ok(())
    }

    async fn update_participant_mode(
        &self,
        id: &str,
        actor_id: &str,
        mode: ParticipantMode,
    ) -> ServiceResult<()> {
        let mut groups = self.groups.write().await;
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;

        let participant = group
            .participants
            .iter_mut()
            .find(|p| p.bot_uuid == actor_id)
            .ok_or_else(|| ServiceError::BotNotFound(actor_id.to_string()))?;

        // Idempotent: skip if already at the desired mode.
        if participant.effective_mode() == mode {
            return Ok(());
        }
        participant.mode = Some(mode);
        group.updated_at = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);
        debug!(group_id = %id, actor_id = %actor_id, ?mode, "Participant mode updated");
        Ok(())
    }

    async fn update_workspace(&self, id: &str, workspace: Workspace) -> ServiceResult<()> {
        let mut groups = self.groups.write().await;
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;

        group.workspace = workspace;
        group.updated_at = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);
        Ok(())
    }

    async fn update_label(&self, id: &str, label: Option<String>) -> ServiceResult<()> {
        let mut groups = self.groups.write().await;
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;

        group.label = label;
        group.updated_at = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);
        Ok(())
    }

    async fn update_status(&self, id: &str, status: GroupStatus) -> ServiceResult<()> {
        let mut groups = self.groups.write().await;
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;

        group.status = status;
        group.updated_at = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);
        Ok(())
    }

    async fn update_service_spec(
        &self,
        id: &str,
        service_spec: Option<ServiceSpec>,
    ) -> ServiceResult<()> {
        let mut groups = self.groups.write().await;
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        group.service_spec = service_spec;
        group.updated_at = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);
        Ok(())
    }

    async fn delete(&self, id: &str) -> ServiceResult<Option<DomainGroup>> {
        let mut groups = self.groups.write().await;
        Ok(groups.remove(id))
    }

    async fn list(&self) -> Vec<DomainGroup> {
        self.list_paginated(0, u64::MAX).await
    }

    async fn list_paginated(&self, offset: u64, limit: u64) -> Vec<DomainGroup> {
        let groups = self.groups.read().await;
        let mut ordered = groups.values().cloned().collect::<Vec<_>>();
        DomainGroup::sort_by_updated_at_desc(&mut ordered);
        ordered
            .into_iter()
            .skip(offset as usize)
            .take(limit as usize)
            .collect()
    }

    async fn find_by_participant(&self, bot_uuid: &str) -> Vec<DomainGroup> {
        let groups = self.groups.read().await;
        groups
            .values()
            .filter(|g| g.participants.iter().any(|p| p.bot_uuid == bot_uuid))
            .cloned()
            .collect()
    }

    async fn find_by_participant_filtered(
        &self,
        bot_uuid: &str,
        kind: Option<GroupKind>,
        label_query: Option<&str>,
    ) -> Vec<DomainGroup> {
        let label_query = label_query.map(str::trim).filter(|q| !q.is_empty());
        let label_query_lower = label_query.map(str::to_lowercase);
        let groups = self.groups.read().await;
        groups
            .values()
            .filter(|g| g.participants.iter().any(|p| p.bot_uuid == bot_uuid))
            .filter(|g| kind.is_none_or(|kind| g.group_kind == kind))
            .filter(|g| {
                label_query_lower
                    .as_deref()
                    .is_none_or(|q| g.label.as_deref().unwrap_or("").to_lowercase().contains(q))
            })
            .cloned()
            .collect()
    }

    async fn count(&self) -> u64 {
        let groups = self.groups.read().await;
        groups.len() as u64
    }

    async fn count_by_kind(&self, kind: Option<GroupKind>) -> u64 {
        let groups = self.groups.read().await;
        match kind {
            None => groups.len() as u64,
            Some(kind) => groups.values().filter(|g| g.group_kind == kind).count() as u64,
        }
    }

    async fn count_by_participant(&self, bot_uuid: &str) -> u64 {
        let groups = self.groups.read().await;
        groups
            .values()
            .filter(|g| g.participants.iter().any(|p| p.bot_uuid == bot_uuid))
            .count() as u64
    }

    async fn find_by_participant_paginated(
        &self,
        bot_uuid: &str,
        offset: u64,
        limit: u64,
    ) -> Vec<DomainGroup> {
        let groups = self.groups.read().await;
        let mut ordered = groups
            .values()
            .filter(|g| g.participants.iter().any(|p| p.bot_uuid == bot_uuid))
            .cloned()
            .collect::<Vec<_>>();
        DomainGroup::sort_by_updated_at_desc(&mut ordered);
        ordered
            .into_iter()
            .skip(offset as usize)
            .take(limit as usize)
            .collect()
    }

    async fn list_paginated_by_kind(
        &self,
        kind: Option<GroupKind>,
        offset: u64,
        limit: u64,
    ) -> Vec<DomainGroup> {
        let groups = self.groups.read().await;
        let mut ordered = groups
            .values()
            .filter(|g| kind.is_none_or(|k| g.group_kind == k))
            .cloned()
            .collect::<Vec<_>>();
        DomainGroup::sort_by_updated_at_desc(&mut ordered);
        ordered
            .into_iter()
            .skip(offset as usize)
            .take(limit as usize)
            .collect()
    }

    async fn update_visibility(&self, id: &str, visibility: &str) -> ServiceResult<()> {
        let mut groups = self.groups.write().await;
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        group.visibility = visibility.to_string();
        Ok(())
    }

    async fn count_filtered(
        &self,
        kind: Option<GroupKind>,
        visibility: Option<&str>,
        label: Option<&str>,
    ) -> u64 {
        let groups = self.groups.read().await;
        groups
            .values()
            .filter(|g| kind.is_none_or(|k| g.group_kind == k))
            .filter(|g| visibility.is_none_or(|v| g.visibility == v))
            .filter(|g| {
                label
                    .map(str::trim)
                    .filter(|l| !l.is_empty())
                    .is_none_or(|l| {
                        g.label
                            .as_deref()
                            .unwrap_or("")
                            .to_lowercase()
                            .contains(&l.to_lowercase())
                    })
            })
            .count() as u64
    }

    async fn list_paginated_filtered(
        &self,
        offset: u64,
        limit: u64,
        kind: Option<GroupKind>,
        visibility: Option<&str>,
        label: Option<&str>,
    ) -> Vec<DomainGroup> {
        let groups = self.groups.read().await;
        let mut filtered: Vec<DomainGroup> = groups
            .values()
            .filter(|g| kind.is_none_or(|k| g.group_kind == k))
            .filter(|g| visibility.is_none_or(|v| g.visibility == v))
            .filter(|g| {
                label
                    .map(str::trim)
                    .filter(|l| !l.is_empty())
                    .is_none_or(|l| {
                        g.label
                            .as_deref()
                            .unwrap_or("")
                            .to_lowercase()
                            .contains(&l.to_lowercase())
                    })
            })
            .cloned()
            .collect();
        DomainGroup::sort_by_updated_at_desc(&mut filtered);
        filtered
            .into_iter()
            .skip(offset as usize)
            .take(limit as usize)
            .collect()
    }

    async fn find_dm_by_pair_key(&self, dm_pair_key: &str) -> Option<DomainGroup> {
        let groups = self.groups.read().await;
        groups
            .values()
            .find(|g| {
                g.group_kind == GroupKind::Dm && g.dm_pair_key.as_deref() == Some(dm_pair_key)
            })
            .cloned()
    }

    async fn insert_dm_group_if_absent(&self, group: DomainGroup) -> ServiceResult<bool> {
        let pair_key = group.dm_pair_key.clone().ok_or_else(|| {
            ServiceError::InternalError(
                "insert_dm_group_if_absent requires group.dm_pair_key".to_string(),
            )
        })?;
        let mut groups = self.groups.write().await;
        if groups
            .values()
            .any(|g| g.group_kind == GroupKind::Dm && g.dm_pair_key.as_deref() == Some(&pair_key))
        {
            return Ok(false);
        }
        groups.insert(group.id.clone(), group);
        Ok(true)
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
}

/// Helper functions for creating groups.
pub struct GroupBuilder {
    id: Option<String>,
    label: Option<String>,
    driver_bot: String,
    originator: Option<String>,
    participants: Vec<Participant>,
}

impl GroupBuilder {
    /// Create a new group builder.
    pub fn new(driver_bot: impl Into<String>) -> Self {
        Self {
            id: None,
            label: None,
            driver_bot: driver_bot.into(),
            originator: None,
            participants: Vec::new(),
        }
    }

    /// Set the group ID.
    pub fn id(mut self, id: impl Into<String>) -> Self {
        self.id = Some(id.into());
        self
    }

    /// Set the group label.
    pub fn label(mut self, label: impl Into<String>) -> Self {
        self.label = Some(label.into());
        self
    }

    /// Set the originator (defaults to driver_bot if not set).
    pub fn originator(mut self, originator: impl Into<String>) -> Self {
        self.originator = Some(originator.into());
        self
    }

    /// Add a participant.
    pub fn participant(mut self, participant: Participant) -> Self {
        self.participants.push(participant);
        self
    }

    /// Build the group.
    pub fn build(self) -> DomainGroup {
        let id = self
            .id
            .unwrap_or_else(|| generated_group_id(GroupKind::Normal));
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);

        DomainGroup {
            id,
            label: self.label,
            status: GroupStatus::Active,
            driver_bot: self.driver_bot,
            originator: self.originator,
            routing_policy: None,
            context: None,
            participants: self.participants,
            messages: Vec::new(),
            workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
            created_at: now,
            updated_at: now,
            group_kind: GroupKind::default(),
            dm_pair_key: None,
            group_strategy: GroupStrategy::Chat,
            service_spec: None,
            version: 1,
            record_status: "active".to_string(),
            visibility: "private".to_string(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_domain::{GroupMessageType, MessageRole, ParticipantRole};

    #[test]
    fn group_builder_uses_canonical_generated_id() {
        let group = GroupBuilder::new("driver").build();

        assert!(group.id.starts_with("bcs_grp_"));
        assert_eq!(group.id.chars().count(), 40);
    }

    #[tokio::test]
    async fn test_group_store_crud() {
        let store = MemoryGroupRepo::new();

        let session = GroupBuilder::new("driver")
            .id("test-group")
            .participant(Participant {
                bot_uuid: "driver".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
            })
            .build();

        // Create
        store.upsert(session.clone()).await.unwrap();

        // Read
        let retrieved = store.get("test-group").await;
        assert!(retrieved.is_some());
        assert_eq!(retrieved.unwrap().id, "test-group");

        // Add message
        let msg = GroupMessage {
            id: "msg-1".to_string(),
            timestamp: 0,
            sender: "user".to_string(),
            content: "Hello".to_string(),
            message_type: GroupMessageType::Bot,
            bot_name: None,
            role: MessageRole::User,
            history_meta: None,
            metadata: None,
            run_id: String::new(),
        };
        store.add_message("test-group", msg).await.unwrap();

        let updated = store.get("test-group").await.unwrap();
        assert_eq!(updated.messages.len(), 1);

        // Delete
        let deleted = store.delete("test-group").await.unwrap();
        assert!(deleted.is_some());

        let not_found = store.get("test-group").await;
        assert!(not_found.is_none());
    }

    #[tokio::test]
    async fn test_group_builder() {
        let session = GroupBuilder::new("driver")
            .id("custom-id")
            .label("Test Session")
            .participant(Participant {
                bot_uuid: "driver".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
            })
            .build();

        assert_eq!(session.id, "custom-id");
        assert_eq!(session.label, Some("Test Session".to_string()));
        assert_eq!(session.driver_bot, "driver");
        assert_eq!(session.participants.len(), 1);
    }

    #[tokio::test]
    async fn test_add_message_to_nonexistent_group() {
        let store = MemoryGroupRepo::new();

        let msg = GroupMessage {
            id: "msg-1".to_string(),
            timestamp: 0,
            sender: "user".to_string(),
            content: "Hello".to_string(),
            message_type: GroupMessageType::Bot,
            bot_name: None,
            role: MessageRole::User,
            history_meta: None,
            metadata: None,
            run_id: String::new(),
        };

        let result = store.add_message("nonexistent", msg).await;
        assert!(result.is_err());

        match result {
            Err(ServiceError::GroupNotFound(id)) => assert_eq!(id, "nonexistent"),
            _ => panic!("Expected GroupNotFound error"),
        }
    }

    #[tokio::test]
    async fn test_add_participant_duplicate_ignored() {
        let store = MemoryGroupRepo::new();

        let group = GroupBuilder::new("driver")
            .id("test-group")
            .participant(Participant {
                bot_uuid: "driver".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
            })
            .build();

        store.upsert(group).await.unwrap();

        // Add same participant again
        let duplicate = Participant {
            bot_uuid: "driver".to_string(),
            bot_name: None,
            kind: None,
            role: ParticipantRole::Consultant, // Different role
            actor_kind: bcs_service_api::ActorKind::default(),
            mode: None,
        };
        store
            .add_participant("test-group", duplicate)
            .await
            .unwrap();

        let retrieved = store.get("test-group").await.unwrap();
        assert_eq!(retrieved.participants.len(), 1); // Still only 1
        assert_eq!(retrieved.participants[0].role, ParticipantRole::Driver); // Original role preserved
    }

    #[tokio::test]
    async fn test_add_participant_to_nonexistent_group() {
        let store = MemoryGroupRepo::new();

        let participant = Participant {
            bot_uuid: "bot".to_string(),
            bot_name: None,
            kind: None,
            role: ParticipantRole::Consultant,
            actor_kind: bcs_service_api::ActorKind::default(),
            mode: None,
        };

        let result = store.add_participant("nonexistent", participant).await;
        assert!(result.is_err());
    }

    #[tokio::test]
    async fn test_update_workspace() {
        let store = MemoryGroupRepo::new();

        let group = GroupBuilder::new("driver").id("test-group").build();

        store.upsert(group).await.unwrap();

        let workspace = Workspace {
            tasks: vec![bcs_service_api::Task {
                id: "task-1".to_string(),
                description: "Test task".to_string(),
                assigned_to: Some("driver".to_string()),
                status: bcs_service_api::TaskStatus::InProgress,
            }],
            decisions: vec!["Decision 1".to_string()],
            notes: vec![],
            audit_log: vec![],
        };

        store
            .update_workspace("test-group", workspace.clone())
            .await
            .unwrap();

        let retrieved = store.get("test-group").await.unwrap();
        assert_eq!(retrieved.workspace.tasks.len(), 1);
        assert_eq!(retrieved.workspace.tasks[0].id, "task-1");
        assert_eq!(retrieved.workspace.decisions.len(), 1);
    }

    #[tokio::test]
    async fn test_update_workspace_nonexistent_group() {
        let store = MemoryGroupRepo::new();

        let workspace = Workspace::default();
        let result = store.update_workspace("nonexistent", workspace).await;
        assert!(result.is_err());
    }

    #[tokio::test]
    async fn test_update_label() {
        let store = MemoryGroupRepo::new();

        let session = GroupBuilder::new("driver").id("test-group").build();

        store.upsert(session).await.unwrap();

        store
            .update_label("test-group", Some("New Label".to_string()))
            .await
            .unwrap();

        let retrieved = store.get("test-group").await.unwrap();
        assert_eq!(retrieved.label, Some("New Label".to_string()));

        // Clear label
        store.update_label("test-group", None).await.unwrap();
        let retrieved = store.get("test-group").await.unwrap();
        assert!(retrieved.label.is_none());
    }

    #[tokio::test]
    async fn test_update_label_nonexistent_group() {
        let store = MemoryGroupRepo::new();
        let result = store
            .update_label("nonexistent", Some("Label".to_string()))
            .await;
        assert!(result.is_err());
    }

    #[tokio::test]
    async fn test_list_groups() {
        let store = MemoryGroupRepo::new();

        let session1 = GroupBuilder::new("driver1").id("session-1").build();
        let session2 = GroupBuilder::new("driver2").id("group-2").build();

        store.upsert(session1).await.unwrap();
        store.upsert(session2).await.unwrap();

        let sessions = store.list().await;
        assert_eq!(sessions.len(), 2);

        // Delete one
        assert!(store.delete("session-1").await.unwrap().is_some());

        let sessions = store.list().await;
        assert_eq!(sessions.len(), 1);
        assert_eq!(sessions[0].id, "group-2");
    }

    #[tokio::test]
    async fn test_group_timestamp_updates_on_message() {
        let store = MemoryGroupRepo::new();

        let session = GroupBuilder::new("driver").id("test-group").build();
        let original_updated_at = session.updated_at;
        store.upsert(session).await.unwrap();

        // Small delay to ensure timestamp difference
        tokio::time::sleep(tokio::time::Duration::from_millis(10)).await;

        let msg = GroupMessage {
            id: "msg-1".to_string(),
            timestamp: 0,
            sender: "user".to_string(),
            content: "Hello".to_string(),
            message_type: GroupMessageType::Bot,
            bot_name: None,
            role: MessageRole::User,
            history_meta: None,
            metadata: None,
            run_id: String::new(),
        };
        store.add_message("test-group", msg).await.unwrap();

        let retrieved = store.get("test-group").await.unwrap();
        assert!(retrieved.updated_at > original_updated_at);
    }

    // ========================================================================
    // Additional tests for BCS.md features
    // ========================================================================

    #[tokio::test]
    async fn test_update_group_status() {
        let store = MemoryGroupRepo::new();

        let session = GroupBuilder::new("driver").id("test-group").build();

        store.upsert(session).await.unwrap();

        // Update to completed status
        store
            .update_status("test-group", GroupStatus::Completed)
            .await
            .unwrap();

        let retrieved = store.get("test-group").await.unwrap();
        assert_eq!(retrieved.status, GroupStatus::Completed);

        // Update to closed status
        store
            .update_status("test-group", GroupStatus::Closed)
            .await
            .unwrap();
        let retrieved = store.get("test-group").await.unwrap();
        assert_eq!(retrieved.status, GroupStatus::Closed);
    }

    #[tokio::test]
    async fn test_update_status_nonexistent_group() {
        let store = MemoryGroupRepo::new();
        let result = store
            .update_status("nonexistent", GroupStatus::Completed)
            .await;
        assert!(result.is_err());
    }

    #[tokio::test]
    async fn test_group_originator_defaults_to_driver() {
        let session = GroupBuilder::new("driver-bot")
            .id("test-group")
            .participant(Participant {
                bot_uuid: "driver-bot".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
            })
            .build();

        // When originator is not set, it should default to driver_bot
        assert_eq!(session.originator(), "driver-bot");
        assert!(session.originator.is_none()); // The field itself is None
    }

    #[tokio::test]
    async fn test_group_originator_can_be_set_explicitly() {
        let session = GroupBuilder::new("driver-bot")
            .id("test-group")
            .originator("initiator-bot")
            .participant(Participant {
                bot_uuid: "driver-bot".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
            })
            .participant(Participant {
                bot_uuid: "initiator-bot".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
            })
            .build();

        assert_eq!(session.originator(), "initiator-bot");
        assert_eq!(session.originator, Some("initiator-bot".to_string()));
    }

    #[tokio::test]
    async fn test_group_multicast_message_to_all_participants() {
        // Test G1 scenario: broadcast to all participants
        let store = MemoryGroupRepo::new();

        let session = GroupBuilder::new("driver")
            .id("test-group")
            .participant(Participant {
                bot_uuid: "driver".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
            })
            .participant(Participant {
                bot_uuid: "dba".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
            })
            .participant(Participant {
                bot_uuid: "security".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
            })
            .build();

        store.upsert(session).await.unwrap();

        // Add a group message (broadcast style, no @mention)
        let msg = GroupMessage {
            id: "msg-1".to_string(),
            timestamp: 0,
            sender: "user".to_string(),
            content: "团队帮我评估一下这个方案".to_string(),
            message_type: GroupMessageType::Bot,
            bot_name: None,
            role: MessageRole::User,
            history_meta: None,
            metadata: None,
            run_id: String::new(),
        };
        store.add_message("test-group", msg).await.unwrap();

        let retrieved = store.get("test-group").await.unwrap();
        assert_eq!(retrieved.messages.len(), 1);
        assert_eq!(retrieved.participants.len(), 3);
    }

    #[tokio::test]
    async fn test_group_add_multiple_messages_transcript() {
        let store = MemoryGroupRepo::new();

        let session = GroupBuilder::new("driver")
            .id("test-group")
            .participant(Participant {
                bot_uuid: "driver".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
            })
            .participant(Participant {
                bot_uuid: "dba".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
            })
            .build();

        store.upsert(session).await.unwrap();

        let messages = vec![
            GroupMessage {
                id: "msg-1".to_string(),
                timestamp: 100,
                sender: "user".to_string(),
                content: "帮我排查数据库死锁".to_string(),
                message_type: GroupMessageType::Bot,
                bot_name: None,
                role: MessageRole::User,
                history_meta: None,
                metadata: None,
                run_id: String::new(),
            },
            GroupMessage {
                id: "msg-2".to_string(),
                timestamp: 200,
                sender: "driver".to_string(),
                content: "@dba 请分析死锁根因".to_string(),
                message_type: GroupMessageType::Bot,
                bot_name: None,
                role: MessageRole::Assistant,
                history_meta: None,
                metadata: None,
                run_id: String::new(),
            },
            GroupMessage {
                id: "msg-3".to_string(),
                timestamp: 300,
                sender: "dba".to_string(),
                content: "分析结果：加锁顺序不一致...".to_string(),
                message_type: GroupMessageType::Bot,
                bot_name: None,
                role: MessageRole::Assistant,
                history_meta: None,
                metadata: None,
                run_id: String::new(),
            },
        ];

        for msg in messages {
            store.add_message("test-group", msg).await.unwrap();
        }

        let retrieved = store.get("test-group").await.unwrap();
        assert_eq!(retrieved.messages.len(), 3);
        assert_eq!(retrieved.messages[0].sender, "user");
        assert_eq!(retrieved.messages[1].sender, "driver");
        assert_eq!(retrieved.messages[2].sender, "dba");
    }

    #[tokio::test]
    async fn test_group_upsert_updates_existing() {
        let store = MemoryGroupRepo::new();

        let session1 = GroupBuilder::new("driver")
            .id("test-group")
            .label("Initial Label")
            .build();
        store.upsert(session1).await.unwrap();

        let session2 = GroupBuilder::new("driver")
            .id("test-group")
            .label("Updated Label")
            .participant(Participant {
                bot_uuid: "new-participant".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
            })
            .build();
        store.upsert(session2).await.unwrap();

        let retrieved = store.get("test-group").await.unwrap();
        assert_eq!(retrieved.label, Some("Updated Label".to_string()));
    }

    #[tokio::test]
    async fn mutable_patch_preserves_unrelated_routing_fields() {
        let store = MemoryGroupRepo::new();
        let mut group = GroupBuilder::new("driver").id("test-group").build();
        group.routing_policy = Some(bcs_service_api::RoutingPolicy {
            mode: bcs_service_api::RoutingMode::Structured,
            default_bot_final_delivery: bcs_service_api::DefaultDelivery::SendToDriver,
            sender_routes: HashMap::from([("worker".to_string(), vec!["driver".to_string()])]),
        });
        store.upsert(group).await.unwrap();

        store
            .patch_mutable_fields(
                "test-group",
                GroupMutableFieldsPatch {
                    label: Some("Renamed".to_string()),
                    default_bot_final_delivery: Some(
                        bcs_service_api::DefaultDelivery::InjectObservers,
                    ),
                    ..Default::default()
                },
            )
            .await
            .unwrap();

        let stored = store.get("test-group").await.unwrap();
        let routing = stored.routing_policy.unwrap();
        assert_eq!(stored.label.as_deref(), Some("Renamed"));
        assert_eq!(routing.mode, bcs_service_api::RoutingMode::Structured);
        assert_eq!(
            routing.sender_routes.get("worker"),
            Some(&vec!["driver".to_string()])
        );
        assert_eq!(
            routing.default_bot_final_delivery,
            bcs_service_api::DefaultDelivery::InjectObservers
        );
    }

    #[tokio::test]
    async fn test_group_long_running_project() {
        let session = GroupBuilder::new("pm-bot")
            .id("project-group")
            .label("项目运行群")
            .originator("pm-bot")
            .participant(Participant {
                bot_uuid: "pm-bot".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
            })
            .participant(Participant {
                bot_uuid: "dev-bot".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
            })
            .participant(Participant {
                bot_uuid: "qa-bot".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
            })
            .build();

        assert_eq!(session.participants.len(), 3);
        assert_eq!(session.originator(), "pm-bot");
    }

    #[tokio::test]
    async fn test_group_workspace_persistence() {
        let store = MemoryGroupRepo::new();

        let session = GroupBuilder::new("driver").id("test-group").build();
        store.upsert(session).await.unwrap();

        let workspace = Workspace {
            decisions: vec![
                "决定使用方案A".to_string(),
                "安全审查由安全Bot负责".to_string(),
            ],
            tasks: vec![
                bcs_service_api::Task {
                    id: "task-1".to_string(),
                    description: "数据库死锁排查".to_string(),
                    assigned_to: Some("dba".to_string()),
                    status: bcs_service_api::TaskStatus::Completed,
                },
                bcs_service_api::Task {
                    id: "task-2".to_string(),
                    description: "安全审核".to_string(),
                    assigned_to: Some("security".to_string()),
                    status: bcs_service_api::TaskStatus::InProgress,
                },
            ],
            notes: vec!["需要注意性能影响".to_string()],
            audit_log: vec![bcs_service_api::AuditEntry {
                timestamp: 1234567890,
                action: "task_completed".to_string(),
                actor: "dba".to_string(),
                details: "Marked task-1 as completed".to_string(),
            }],
        };

        store
            .update_workspace("test-group", workspace)
            .await
            .unwrap();

        let retrieved = store.get("test-group").await.unwrap();
        assert_eq!(retrieved.workspace.decisions.len(), 2);
        assert_eq!(retrieved.workspace.tasks.len(), 2);
        assert_eq!(retrieved.workspace.audit_log.len(), 1);
        assert_eq!(
            retrieved.workspace.tasks[0].status,
            bcs_service_api::TaskStatus::Completed
        );
    }
}
