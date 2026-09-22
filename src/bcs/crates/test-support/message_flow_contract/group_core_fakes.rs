//! Fake and probe doubles for the `GroupCoreService` contract, including the
//! human-mention notify-policy probe used by the shared notification gate tests.
#![allow(dead_code)]

use std::collections::HashMap;

use async_trait::async_trait;
use bcs_service_api::{
    Group, GroupCoreService, GroupHumanNotifyPolicy, GroupMessage, GroupStatus, Participant,
    ParticipantMode, ServiceError, ServiceResult, Workspace,
    core::{GroupMutationCommand, GroupMutationKind},
};
use tokio::sync::RwLock;

#[derive(Default)]
pub struct FakeGroupCoreService {
    groups: RwLock<HashMap<String, Group>>,
    get_counts: RwLock<HashMap<String, usize>>,
    message_counts: RwLock<HashMap<String, usize>>,
    fail_add_message: RwLock<bool>,
    notify_policy: NotifyPolicyProbe,
}

/// Policy-read probe behind the shared human-notify gate: counts scoped
/// policy reads, can inject read failures, and can hold a read at a barrier
/// so tests can commit a Group patch between the early routing snapshot and
/// the notification decision — deterministic, without sleeps.
pub struct NotifyPolicyProbe {
    reads: tokio::sync::watch::Sender<usize>,
    _reads_rx: tokio::sync::watch::Receiver<usize>,
    fail_reads: std::sync::atomic::AtomicBool,
    gate: tokio::sync::Mutex<Option<tokio::sync::mpsc::Receiver<()>>>,
    observed: tokio::sync::Mutex<Option<GroupHumanNotifyPolicy>>,
}

impl Default for NotifyPolicyProbe {
    fn default() -> Self {
        let (reads, reads_rx) = tokio::sync::watch::channel(0usize);
        Self {
            reads,
            _reads_rx: reads_rx,
            fail_reads: std::sync::atomic::AtomicBool::new(false),
            gate: tokio::sync::Mutex::new(None),
            observed: tokio::sync::Mutex::new(None),
        }
    }
}

impl FakeGroupCoreService {
    pub async fn fail_add_message(&self) {
        *self.fail_add_message.write().await = true;
    }

    pub async fn get_count(&self, id: &str) -> usize {
        self.get_counts
            .read()
            .await
            .get(id)
            .copied()
            .unwrap_or_default()
    }

    pub async fn policy_read_count(&self) -> usize {
        *self.notify_policy._reads_rx.borrow()
    }

    /// Deterministic wait until at least `minimum` scoped policy reads were
    /// issued. Never sleeps.
    pub async fn wait_policy_reads(&self, minimum: usize) {
        let mut receiver = self.notify_policy.reads.subscribe();
        while *receiver.borrow_and_update() < minimum {
            if receiver.changed().await.is_err() {
                return;
            }
        }
    }

    pub fn fail_policy_reads(&self, fail: bool) {
        self.notify_policy
            .fail_reads
            .store(fail, std::sync::atomic::Ordering::SeqCst);
    }

    /// Pause the next policy read before it observes state; the returned
    /// sender releases it.
    pub async fn hold_next_policy_read(&self) -> tokio::sync::mpsc::Sender<()> {
        let (sender, receiver) = tokio::sync::mpsc::channel(1);
        *self.notify_policy.gate.lock().await = Some(receiver);
        sender
    }

    pub async fn observed_policy(&self) -> Option<GroupHumanNotifyPolicy> {
        self.notify_policy.observed.lock().await.clone()
    }
}

#[async_trait]
impl GroupCoreService for FakeGroupCoreService {
    async fn mutate(&self, command: GroupMutationCommand) -> ServiceResult<Group> {
        let mut groups = self.groups.write().await;
        let group = groups
            .get_mut(&command.group_id)
            .ok_or_else(|| ServiceError::GroupNotFound(command.group_id.clone()))?;
        match command.mutation {
            GroupMutationKind::UpdateStatus { status, .. } => {
                if group.status != status {
                    group.status = status;
                    group.version = group.version.saturating_add(1);
                }
            }
            _ => {
                return Err(ServiceError::InvalidOperation {
                    message: "unsupported eventful mutation in message-flow test fake".to_string(),
                    request_id: None,
                });
            }
        }
        Ok(group.clone())
    }

    async fn upsert(&self, group: Group) -> ServiceResult<()> {
        self.groups.write().await.insert(group.id.clone(), group);
        Ok(())
    }

    async fn get(&self, id: &str) -> Option<Group> {
        let mut counts = self.get_counts.write().await;
        *counts.entry(id.to_string()).or_default() += 1;
        drop(counts);
        self.groups.read().await.get(id).cloned()
    }

    async fn add_message(&self, id: &str, message: GroupMessage) -> ServiceResult<()> {
        if *self.fail_add_message.read().await {
            return Err(ServiceError::InternalError(
                "add_message failed".to_string(),
            ));
        }
        let mut groups = self.groups.write().await;
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        group.messages.push(message);
        Ok(())
    }

    async fn add_participant(&self, id: &str, participant: Participant) -> ServiceResult<()> {
        let mut groups = self.groups.write().await;
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        group.participants.push(participant);
        Ok(())
    }

    async fn remove_participant(&self, group_id: &str, bot_uuid: &str) -> ServiceResult<()> {
        let mut groups = self.groups.write().await;
        let group = groups
            .get_mut(group_id)
            .ok_or_else(|| ServiceError::GroupNotFound(group_id.to_string()))?;
        group.participants.retain(|p| p.bot_uuid != bot_uuid);
        Ok(())
    }

    async fn update_participant_mode(
        &self,
        group_id: &str,
        actor_id: &str,
        mode: ParticipantMode,
    ) -> ServiceResult<()> {
        let mut groups = self.groups.write().await;
        let group = groups
            .get_mut(group_id)
            .ok_or_else(|| ServiceError::GroupNotFound(group_id.to_string()))?;
        let participant = group
            .participants
            .iter_mut()
            .find(|p| p.bot_uuid == actor_id)
            .ok_or_else(|| ServiceError::BotNotFound(actor_id.to_string()))?;
        participant.mode = Some(mode);
        Ok(())
    }

    async fn update_workspace(&self, id: &str, workspace: Workspace) -> ServiceResult<()> {
        let mut groups = self.groups.write().await;
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        group.workspace = workspace;
        Ok(())
    }

    async fn update_label(&self, id: &str, label: Option<String>) -> ServiceResult<()> {
        let mut groups = self.groups.write().await;
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        group.label = label;
        Ok(())
    }

    async fn update_status(&self, id: &str, status: GroupStatus) -> ServiceResult<()> {
        let mut groups = self.groups.write().await;
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        group.status = status;
        Ok(())
    }

    async fn update_service_spec(
        &self,
        id: &str,
        service_spec: Option<bcs_service_api::ServiceSpec>,
    ) -> ServiceResult<()> {
        let mut groups = self.groups.write().await;
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        group.service_spec = service_spec;
        Ok(())
    }

    async fn terminate(&self, id: &str, _caller_bot_id: &str) -> ServiceResult<Group> {
        let mut groups = self.groups.write().await;
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        group.status = GroupStatus::Completed;
        Ok(group.clone())
    }

    async fn delete(&self, id: &str) -> ServiceResult<Option<Group>> {
        Ok(self.groups.write().await.remove(id))
    }

    async fn list(&self) -> Vec<Group> {
        self.groups.read().await.values().cloned().collect()
    }

    async fn list_paginated(&self, offset: u64, limit: u64) -> Vec<Group> {
        let mut groups = self.list().await;
        Group::sort_by_updated_at_desc(&mut groups);
        groups
            .into_iter()
            .skip(offset as usize)
            .take(limit as usize)
            .collect()
    }

    async fn find_by_participant(&self, bot_uuid: &str) -> Vec<Group> {
        self.list()
            .await
            .into_iter()
            .filter(|group| group.participants.iter().any(|p| p.bot_uuid == bot_uuid))
            .collect()
    }

    async fn count(&self) -> u64 {
        self.groups.read().await.len() as u64
    }

    async fn count_by_participant(&self, bot_uuid: &str) -> u64 {
        self.find_by_participant(bot_uuid).await.len() as u64
    }

    async fn find_by_participant_paginated(
        &self,
        bot_uuid: &str,
        offset: u64,
        limit: u64,
    ) -> Vec<Group> {
        let mut groups = self.find_by_participant(bot_uuid).await;
        Group::sort_by_updated_at_desc(&mut groups);
        groups
            .into_iter()
            .skip(offset as usize)
            .take(limit as usize)
            .collect()
    }

    async fn message_count(&self, id: &str) -> ServiceResult<usize> {
        Ok(*self.message_counts.read().await.get(id).unwrap_or(&0))
    }

    async fn increment_message_count(&self, id: &str) -> ServiceResult<()> {
        let mut counts = self.message_counts.write().await;
        *counts.entry(id.to_string()).or_insert(0) += 1;
        Ok(())
    }

    async fn reset_message_count(&self, id: &str) -> ServiceResult<()> {
        self.message_counts.write().await.insert(id.to_string(), 0);
        Ok(())
    }

    async fn create_or_reuse_actor_dm_group(
        &self,
        _id: &str,
        _actor_a: bcs_service_api::DmActorSpec,
        _actor_b: bcs_service_api::DmActorSpec,
        _legacy_driver_bot: &str,
        _originator_actor_id: &str,
        _label: Option<String>,
        _context: Option<String>,
    ) -> ServiceResult<(Group, bool)> {
        Err(ServiceError::InternalError(
            "dm group creation is not supported by FakeGroupCoreService".to_string(),
        ))
    }

    /// Mirror of the production memory store: one read-lock acquisition,
    /// mode/driver copied before release, no cache, no participants.
    async fn read_human_notify_policy(
        &self,
        group_id: &str,
    ) -> ServiceResult<Option<GroupHumanNotifyPolicy>> {
        self.notify_policy.reads.send_modify(|count| *count += 1);
        if let Some(mut receiver) = self.notify_policy.gate.lock().await.take() {
            let _ = receiver.recv().await;
        }
        if self
            .notify_policy
            .fail_reads
            .load(std::sync::atomic::Ordering::SeqCst)
        {
            return Err(ServiceError::InternalError(
                "injected policy read failure".to_string(),
            ));
        }
        let groups = self.groups.read().await;
        let policy = groups.get(group_id).map(|group| GroupHumanNotifyPolicy {
            mode: group.human_mention_notify_mode,
            driver_bot_id: group.driver_bot.clone(),
        });
        drop(groups);
        *self.notify_policy.observed.lock().await = policy.clone();
        Ok(policy)
    }
}
