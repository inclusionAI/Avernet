//! Coalesced, ordered publication of BCS discovery Cards.

use std::collections::{HashSet, VecDeque};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use async_trait::async_trait;
use bcs_fuse_client::{CardResourceType, CardWriteRequest, FuseClient};
use bcs_service_api::lifecycle::{LifecycleError, ServiceLifecycle};
use bcs_service_api::port::{BotCatalogCleanupPort, CatalogChangePort, LeaderElectionPort};
use bcs_service_api::{ActorKind, BotRegistryCoreService, Group, GroupCoreService, GroupKind, RegisteredBot};
use serde_json::json;
use tokio::task::JoinHandle;

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
enum Resource { Agent(String), Group(String) }

/// Holds only identities, so retries and concurrent mutations never reuse an
/// old Card payload. One worker serializes all requests to the remote catalog.
#[derive(Default)]
pub struct CardChangeQueue {
    pending: Mutex<Pending>,
}

#[derive(Default)]
struct Pending {
    ids: HashSet<Resource>,
    order: VecDeque<Resource>,
}

impl CatalogChangePort for CardChangeQueue {
    fn agent_changed(&self, id: &str) {
        self.retry(Resource::Agent(id.into()));
    }
    fn group_changed(&self, id: &str) {
        self.retry(Resource::Group(id.into()));
    }
}

#[async_trait]
impl BotCatalogCleanupPort for CardChangeQueue {
    async fn delete_bot(&self, id: &str) -> bcs_service_api::ServiceResult<()> {
        self.agent_changed(id);
        Ok(())
    }
}

impl CardChangeQueue {
    fn take_batch(&self) -> Vec<Resource> {
        let mut pending = self.pending.lock().expect("Card change queue poisoned");
        (0..100).filter_map(|_| {
            let resource = pending.order.pop_front()?;
            pending.ids.remove(&resource);
            Some(resource)
        }).collect()
    }

    fn retry(&self, resource: Resource) {
        let mut pending = self.pending.lock().expect("Card change queue poisoned");
        if pending.ids.insert(resource.clone()) { pending.order.push_back(resource); }
    }
}

pub fn agent_card(bot: &RegisteredBot) -> Option<CardWriteRequest> {
    if bot.actor_kind == ActorKind::Human
        || !matches!(bot.capabilities.visibility.as_str(), "public" | "protected") {
        return None;
    }
    let caps = &bot.capabilities;
    // Fuse's default converter indexes description and skill text, not root domains.
    // Include domains here as well so domain-only Agents remain searchable.
    let description = std::iter::once(caps.summary.as_deref().unwrap_or_default())
        .chain(caps.domains.iter().map(String::as_str))
        .filter(|part| !part.is_empty()).collect::<Vec<_>>().join("\n");
    Some(CardWriteRequest {
        resource_type: CardResourceType::Agent,
        card: json!({
            "name": caps.name.as_deref().filter(|name| !name.trim().is_empty()).unwrap_or(&bot.bot_uuid),
            "description": description,
            "skills": caps.skills.iter().map(|skill| json!({
                "id": skill.name, "name": skill.name,
                "description": skill.description.as_deref().unwrap_or_default(),
                "tags": caps.domains,
            })).collect::<Vec<_>>(),
            "domains": caps.domains,
            "visibility": caps.visibility,
        }),
    })
}

pub fn group_card(group: &Group) -> Option<CardWriteRequest> {
    if group.group_kind != GroupKind::Normal || group.visibility != "public"
        || group.record_status != "active" {
        return None;
    }
    Some(CardWriteRequest {
        resource_type: CardResourceType::Group,
        card: json!({
            "groupId": group.id,
            "name": group.label.as_deref().filter(|name| !name.trim().is_empty()).unwrap_or(&group.id),
            "description": group.context.as_deref().unwrap_or_default(),
            "skills": [],
        }),
    })
}

pub struct FuseCardSyncLifecycle {
    queue: Arc<CardChangeQueue>,
    client: Arc<FuseClient>,
    bots: Arc<dyn BotRegistryCoreService>,
    groups: Arc<dyn GroupCoreService>,
    leader: Arc<dyn LeaderElectionPort>,
    retry_delay: Duration,
    task: Mutex<Option<JoinHandle<()>>>,
}

impl FuseCardSyncLifecycle {
    pub fn new(
        queue: Arc<CardChangeQueue>, client: Arc<FuseClient>,
        bots: Arc<dyn BotRegistryCoreService>, groups: Arc<dyn GroupCoreService>,
        leader: Arc<dyn LeaderElectionPort>, retry_base_ms: u64,
    ) -> Self {
        Self { queue, client, bots, groups, leader,
            retry_delay: Duration::from_millis(retry_base_ms.clamp(1_000, 30_000)),
            task: Mutex::new(None) }
    }
}

async fn publish(
    client: &FuseClient, bots: &dyn BotRegistryCoreService,
    groups: &dyn GroupCoreService, resource: &Resource,
) -> Result<(), String> {
    let (id, card) = match resource {
        Resource::Agent(id) => (id, bots.try_get(id).await.map_err(|e| e.to_string())?
            .as_ref().and_then(agent_card)),
        Resource::Group(id) => (id, groups.try_get(id).await.map_err(|e| e.to_string())?
            .as_ref().and_then(group_card)),
    };
    match card {
        Some(card) => client.put_card(id, &card).await.map(|_| ()),
        None => client.delete_card(id).await,
    }.map_err(|e| e.to_string())
}

#[async_trait]
impl ServiceLifecycle for FuseCardSyncLifecycle {
    async fn initialize(&self) -> Result<(), LifecycleError> {
        let mut task = self.task.lock().expect("Card sync lifecycle poisoned");
        if task.is_some() { return Ok(()); }
        let (queue, client, bots, groups, leader, retry_delay) = (
            self.queue.clone(), self.client.clone(), self.bots.clone(),
            self.groups.clone(), self.leader.clone(), self.retry_delay,
        );
        *task = Some(tokio::spawn(async move {
            let mut was_leader = false;
            let mut bot_offset = 0;
            let mut group_offset = 0;
            let mut scan_bots = true;
            let mut scan_groups = true;
            let mut backoff = retry_delay;
            loop {
                let mut failed = false;
                let is_leader = leader.is_leader().await.unwrap_or(false);
                if !is_leader { was_leader = false; }
                if is_leader {
                    if !was_leader {
                        bot_offset = 0; group_offset = 0;
                        scan_bots = true; scan_groups = true;
                    }
                    was_leader = true;
                    // Backfill bounded pages on startup / master acquisition.
                    if scan_bots {
                        let (page, _) = bots.list_bots_by_name_and_cooperatable_with(
                            "", "", false, &HashSet::new(), bot_offset, 100,
                        ).await;
                        scan_bots = page.len() == 100;
                        bot_offset += page.len();
                        for (bot, _) in page { queue.agent_changed(&bot.bot_uuid); }
                    }
                    if scan_groups {
                        let page = groups.list_paginated(group_offset, 100).await;
                        scan_groups = page.len() == 100;
                        group_offset += page.len() as u64;
                        for group in page { queue.group_changed(&group.id); }
                    }
                    for resource in queue.take_batch() {
                        // Stop sending when this instance loses the existing master lease.
                        if !leader.is_leader().await.unwrap_or(false) {
                            queue.retry(resource);
                            continue;
                        }
                        if let Err(error) = publish(client.as_ref(), bots.as_ref(), groups.as_ref(), &resource).await {
                            tracing::warn!(?resource, %error, "Card sync failed; queued for retry");
                            failed = true;
                            queue.retry(resource);
                        }
                    }
                }
                tokio::time::sleep(backoff).await;
                backoff = if failed { backoff.saturating_mul(2).min(Duration::from_secs(30)) } else { retry_delay };
            }
        }));
        Ok(())
    }

    async fn shutdown(&self) -> Result<(), LifecycleError> {
        let task = self.task.lock().expect("Card sync lifecycle poisoned").take();
        if let Some(task) = task { task.abort(); let _ = task.await; }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_service_api::{ActorStatus, BotCapabilities, GroupStatus};

    #[test]
    fn projections_preserve_discovery_visibility_and_clearable_fields() {
        let mut bot = RegisteredBot {
            bot_uuid: "agent".into(), capabilities: BotCapabilities {
                name: Some("Billing".into()), visibility: "protected".into(), ..Default::default()
            }, env: None, created_by: None, actor_kind: ActorKind::Bot, status: ActorStatus::Online,
        };
        let card = agent_card(&bot).unwrap();
        assert_eq!(card.card["description"], "");
        assert_eq!(card.card["skills"], json!([]));
        bot.capabilities.domains = vec!["invoicing".into()];
        assert_eq!(agent_card(&bot).unwrap().card["description"], "invoicing");
        bot.capabilities.visibility = "private".into();
        assert!(agent_card(&bot).is_none());
        bot.capabilities.visibility = "public".into();
        bot.actor_kind = ActorKind::Human;
        assert!(agent_card(&bot).is_none());

        let mut group = Group::new("group", "agent", vec![]);
        assert!(group_card(&group).is_none());
        group.visibility = "public".into();
        group.status = GroupStatus::Completed;
        assert_eq!(group_card(&group).unwrap().card["description"], "");
        group.record_status = "provisioning".into();
        assert!(group_card(&group).is_none());
        group.record_status = "active".into();
        group.group_kind = GroupKind::Dm;
        assert!(group_card(&group).is_none());
    }

    #[test]
    fn changes_coalesce_and_failed_batches_do_not_starve_later_ids() {
        let queue = CardChangeQueue::default();
        for id in 0..105 { queue.agent_changed(&format!("bot-{id}")); }
        queue.agent_changed("bot-0");
        let batch = queue.take_batch();
        assert_eq!(batch.len(), 100);
        for resource in batch { queue.retry(resource); }
        let next = queue.take_batch();
        assert_eq!(next[0], Resource::Agent("bot-100".into()));
        assert_eq!(next[4], Resource::Agent("bot-104".into()));
        assert_eq!(queue.take_batch().len(), 5);
    }
}
