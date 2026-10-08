//! In-memory group repository implementation.
//!
//! This repository is intended for tests and local single-node development.

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use tokio::sync::RwLock;
use tracing::debug;

use bcs_event_store::MemoryEventStore;
use bcs_service_api::port::repo::{
    CommitGroupEventfulMutation, FinalizeGroupProvisioning, GroupEventfulMutation, GroupRepoPort,
};
use bcs_service_api::types::MessageViewScope;
use bcs_service_api::{
    Group as DomainGroup, GroupHumanNotifyPolicy, GroupKind, GroupMessage,
    GroupMutableFieldsPatch, GroupStatus, GroupStrategy, HumanMentionNotifyMode, Participant,
    ParticipantMode, ServiceError, ServiceResult, ServiceSpec, Workspace, generated_group_id,
};
use bcs_service_api::{GroupMetricCount, GroupMetricsSnapshotPort};

/// In-memory implementation of [`GroupRepoPort`].
#[derive(Debug, Default)]
pub struct MemoryGroupRepo {
    groups: RwLock<HashMap<String, DomainGroup>>,
    message_counts: RwLock<HashMap<String, usize>>,
    event_store: Option<Arc<MemoryEventStore>>,
    event_env: Option<String>,
}

impl MemoryGroupRepo {
    /// Create a new group store.
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_event_store(
        mut self,
        event_store: Arc<MemoryEventStore>,
        env: impl Into<String>,
    ) -> Self {
        self.event_store = Some(event_store);
        self.event_env = Some(env.into());
        self
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
    async fn finalize_provisioning(&self, command: FinalizeGroupProvisioning) -> ServiceResult<()> {
        let event_store =
            self.event_store
                .as_ref()
                .ok_or_else(|| ServiceError::InvalidOperation {
                    message: "Memory Group provisioning requires the shared Memory Event Store"
                        .to_string(),
                    request_id: None,
                })?;
        let mut groups = self.groups.write().await;
        let group = groups
            .get_mut(&command.group_id)
            .ok_or_else(|| ServiceError::GroupNotFound(command.group_id.clone()))?;
        if group.record_status != "provisioning" {
            return Err(ServiceError::InvalidOperation {
                message: format!(
                    "Group '{}' is not awaiting provisioning finalization",
                    command.group_id
                ),
                request_id: None,
            });
        }
        let updated_at = command.finalized_at_ms;
        event_store
            .finalize_group_provisioning(
                &command.group_id,
                &command.subscription_ids,
                &command.events,
                command.finalized_at_ms,
                &command.env,
                || {
                    group.record_status = "active".to_string();
                    group.updated_at = updated_at;
                    Ok(())
                },
            )
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        Ok(())
    }

    async fn commit_eventful_mutation(
        &self,
        command: CommitGroupEventfulMutation,
    ) -> ServiceResult<DomainGroup> {
        let mut groups = self.groups.write().await;
        let current = groups
            .get(&command.group_id)
            .cloned()
            .ok_or_else(|| ServiceError::GroupNotFound(command.group_id.clone()))?;
        if current.version != command.expected_version {
            return Err(ServiceError::Conflict(format!(
                "Group '{}' expected version {}, found {}",
                command.group_id, command.expected_version, current.version
            )));
        }
        let deleting = matches!(&command.mutation, GroupEventfulMutation::Delete);
        if deleting && command.event.is_some() {
            return Err(ServiceError::InvalidOperation {
                message: "Group deletion is not part of the public Event Catalog".to_string(),
                request_id: None,
            });
        }
        let mut candidate = current;
        apply_memory_group_mutation(&mut candidate, &command.mutation, command.mutated_at_ms)?;

        if let Some(event) = command.event.as_ref() {
            let event_store =
                self.event_store
                    .as_ref()
                    .ok_or_else(|| ServiceError::InvalidOperation {
                        message:
                            "Eventful Memory Group mutation requires the shared Memory Event Store"
                                .to_string(),
                        request_id: None,
                    })?;
            event_store
                .commit_group_mutation(&command.group_id, event, || {
                    if deleting {
                        groups.remove(&command.group_id);
                    } else {
                        groups.insert(command.group_id.clone(), candidate.clone());
                    }
                    Ok(())
                })
                .await
                .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        } else if deleting {
            if let Some(event_store) = self.event_store.as_ref() {
                let env =
                    self.event_env
                        .as_deref()
                        .ok_or_else(|| ServiceError::InvalidOperation {
                            message: "Memory Group deletion requires an Event environment"
                                .to_string(),
                            request_id: None,
                        })?;
                event_store
                    .commit_group_deletion(&command.group_id, env, command.mutated_at_ms, || {
                        groups.remove(&command.group_id);
                        Ok(())
                    })
                    .await
                    .map_err(|error| ServiceError::InternalError(error.to_string()))?;
            } else {
                // Eventing is an optional capability in isolated tests and
                // embedded callers. Without an Event Store there can be no
                // subscriptions or pending deliveries to reconcile.
                groups.remove(&command.group_id);
            }
        } else {
            groups.insert(command.group_id.clone(), candidate.clone());
        }
        Ok(candidate)
    }

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
        if let Some(opening_message) = patch.opening_message {
            group.opening_message = opening_message;
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
        if let Some(mode) = patch.human_mention_notify_mode {
            group.human_mention_notify_mode = mode;
        }
        group.updated_at = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|duration| duration.as_millis() as u64)
            .unwrap_or(0);
        Ok(())
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

    async fn update_participant_message_view_scope(
        &self,
        id: &str,
        actor_id: &str,
        message_view_scope: MessageViewScope,
    ) -> ServiceResult<()> {
        let mut groups = self.groups.write().await;
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        let participant = group
            .participants
            .iter_mut()
            .find(|participant| participant.bot_uuid == actor_id)
            .ok_or_else(|| ServiceError::ParticipantNotFound(actor_id.to_string()))?;
        if !message_view_scope.is_valid_for(participant.actor_kind) {
            return Err(ServiceError::InvalidOperation {
                message: "Bot participants must use full message_view_scope".to_string(),
                request_id: None,
            });
        }
        participant.message_view_scope = message_view_scope;
        group.updated_at = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|duration| duration.as_millis() as u64)
            .unwrap_or(0);
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

    async fn read_human_notify_policy(
        &self,
        group_id: &str,
    ) -> ServiceResult<Option<GroupHumanNotifyPolicy>> {
        // One read-lock acquisition; copy mode/driver before releasing it so
        // the policy read never depends on cache state.
        let groups = self.groups.read().await;
        let Some(group) = groups.get(group_id) else {
            return Ok(None);
        };
        Ok(Some(GroupHumanNotifyPolicy {
            mode: group.human_mention_notify_mode,
            driver_bot_id: group.driver_bot.clone(),
        }))
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

#[path = "memory_builder.rs"]
pub mod builder;
pub use builder::GroupBuilder;

#[path = "memory_mutation.rs"]
mod mutation;

use mutation::apply_memory_group_mutation;

#[cfg(test)]
#[path = "memory_tests.rs"]
mod tests;
