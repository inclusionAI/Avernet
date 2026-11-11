//! Coalesced, ordered publication of BCS discovery Cards.

use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use async_trait::async_trait;
use bcs_fuse_client::{CardResourceType, CardWriteRequest, FuseClient};
use bcs_service_api::lifecycle::{LifecycleError, ServiceLifecycle};
use bcs_service_api::port::{BotCatalogCleanupPort, CatalogChangePort};
use bcs_service_api::{ActorKind, BotControlPlaneRecord, BotControlPlaneRepoPort, Group, GroupCoreService, GroupKind, UserVisibility};
use serde_json::json;
use tokio::sync::Notify;
use tokio::task::JoinHandle;

use super::fuse_backed_sync::{MAX_SYNC_BACKOFF, MAX_SYNC_RETRIES};

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
enum Resource { Agent(String), Group(String) }

/// Holds only identities, so retries and concurrent mutations never reuse an
/// old Card payload. One worker serializes all requests to the remote catalog.
#[derive(Default)]
pub struct CardChangeQueue {
    pending: Mutex<Pending>,
    changed: Notify,
}

#[derive(Default)]
struct Pending {
    attempts: HashMap<Resource, u32>,
    order: VecDeque<Resource>,
}

impl CatalogChangePort for CardChangeQueue {
    fn agent_changed(&self, id: &str) {
        self.changed(Resource::Agent(id.into()));
    }
    fn group_changed(&self, id: &str) {
        self.changed(Resource::Group(id.into()));
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
    fn take_batch(&self) -> Vec<(Resource, u32)> {
        let mut pending = self.pending.lock().expect("Card change queue poisoned");
        (0..100).filter_map(|_| {
            let resource = pending.order.pop_front()?;
            let attempt = pending.attempts.remove(&resource).expect("queued Card attempt");
            Some((resource, attempt))
        }).collect()
    }

    fn changed(&self, resource: Resource) {
        let mut pending = self.pending.lock().expect("Card change queue poisoned");
        if pending.attempts.insert(resource.clone(), 0).is_none() {
            pending.order.push_back(resource);
        }
        self.changed.notify_one();
    }

    fn retry(&self, resource: Resource, attempt: u32) {
        let mut pending = self.pending.lock().expect("Card change queue poisoned");
        // A newer committed change already has its own fresh attempt budget.
        if let std::collections::hash_map::Entry::Vacant(entry) = pending.attempts.entry(resource.clone()) {
            entry.insert(attempt);
            pending.order.push_back(resource);
        }
    }
}

pub fn agent_card(bot: &BotControlPlaneRecord) -> Option<CardWriteRequest> {
    if bot.kind == ActorKind::Human {
        return None;
    }
    let caps = &bot.descriptor;
    // Fuse's default converter indexes description and skill text, not root domains.
    // Include domains here as well so domain-only Agents remain searchable.
    let description = std::iter::once(caps.summary.as_str())
        .chain(caps.domains.iter().map(String::as_str))
        .filter(|part| !part.is_empty()).collect::<Vec<_>>().join("\n");
    Some(CardWriteRequest {
        resource_type: CardResourceType::Agent,
        labels: [
            ("visibility".into(), vec![bot.visibility.clone()]),
            ("user_visibility".into(), vec![match bot.user_visibility {
                UserVisibility::Public => "public",
                UserVisibility::Protected => "protected",
                UserVisibility::Private => "private",
            }.into()]),
        ].into(),
        card: json!({
            "name": if bot.name.trim().is_empty() { &bot.bot_id } else { &bot.name },
            "description": description,
            "skills": caps.skills.iter().map(|skill| json!({
                "id": skill.name, "name": skill.name,
                "description": skill.description.as_deref().unwrap_or_default(),
                "tags": caps.domains,
            })).collect::<Vec<_>>(),
            "domains": caps.domains,
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
        labels: [("visibility".into(), vec![group.visibility.clone()])].into(),
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
    bots: Arc<dyn BotControlPlaneRepoPort>,
    groups: Arc<dyn GroupCoreService>,
    env: String,
    retry_delay: Duration,
    task: Mutex<Option<JoinHandle<()>>>,
}

impl FuseCardSyncLifecycle {
    pub fn new(
        queue: Arc<CardChangeQueue>, client: Arc<FuseClient>,
        bots: Arc<dyn BotControlPlaneRepoPort>, groups: Arc<dyn GroupCoreService>,
        env: String, retry_base_ms: u64,
    ) -> Self {
        Self { queue, client, bots, groups, env,
            retry_delay: Duration::from_millis(retry_base_ms).min(MAX_SYNC_BACKOFF),
            task: Mutex::new(None) }
    }
}

async fn publish(
    client: &FuseClient, bots: &dyn BotControlPlaneRepoPort,
    groups: &dyn GroupCoreService, env: &str, resource: &Resource,
) -> Result<(), String> {
    let (id, card) = match resource {
        Resource::Agent(id) => (id, bots.get_control_plane(id, env).await.map_err(|e| e.to_string())?
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
        let (queue, client, bots, groups, env, retry_delay) = (
            self.queue.clone(), self.client.clone(), self.bots.clone(),
            self.groups.clone(), self.env.clone(), self.retry_delay,
        );
        *task = Some(tokio::spawn(async move {
            loop {
                let batch = queue.take_batch();
                if batch.is_empty() {
                    queue.changed.notified().await;
                    continue;
                }
                let mut backoff: Option<Duration> = None;
                for (resource, attempt) in batch {
                    if let Err(error) = publish(client.as_ref(), bots.as_ref(), groups.as_ref(), &env, &resource).await {
                        if attempt + 1 < MAX_SYNC_RETRIES {
                            tracing::warn!(?resource, attempt = attempt + 1, %error, "Card sync failed; queued for retry");
                            queue.retry(resource, attempt + 1);
                            let delay = retry_delay.saturating_mul(2u32.pow(attempt.min(5))).min(MAX_SYNC_BACKOFF);
                            backoff = Some(backoff.unwrap_or_default().max(delay));
                        } else {
                            tracing::error!(?resource, attempts = MAX_SYNC_RETRIES, %error, "Card sync exhausted retries; waiting for the next change");
                        }
                    }
                }
                if let Some(delay) = backoff { tokio::time::sleep(delay).await; }
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
    use bcs_service_api::{ActorStatus, BotControlPlaneDescriptor, GroupStatus};

    #[test]
    fn projections_preserve_discovery_visibility_and_clearable_fields() {
        let mut bot = BotControlPlaneRecord {
            bot_id: "agent".into(), name: "Billing".into(), visibility: "protected".into(),
            descriptor: BotControlPlaneDescriptor {
                summary: String::new(), domains: vec![], skills: vec![], scopes: vec![],
            }, env: "local".into(), created_by: None, kind: ActorKind::Bot, status: ActorStatus::Hidden,
            agent_code: None, task_claim_mode: false, task_dream_mode: false,
            created_at: 0, updated_at: 0, user_visibility: UserVisibility::Protected,
            friend_ext: Default::default(), friend_check_in_strategy: Default::default(),
        };
        let card = agent_card(&bot).unwrap();
        assert_eq!(card.card["description"], "");
        assert_eq!(card.card["skills"], json!([]));
        assert!(card.card.get("visibility").is_none());
        bot.descriptor.domains = vec!["invoicing".into()];
        assert_eq!(agent_card(&bot).unwrap().card["description"], "invoicing");
        for visibility in ["public", "protected", "private"] {
            for user_visibility in [UserVisibility::Public, UserVisibility::Protected, UserVisibility::Private] {
                bot.visibility = visibility.into();
                bot.user_visibility = user_visibility;
                let card = serde_json::to_value(agent_card(&bot).unwrap()).unwrap();
                assert_eq!(card["labels"]["visibility"], json!([visibility]));
                assert_eq!(card["labels"]["user_visibility"], json!([user_visibility]));
                assert_eq!(card["labels"].as_object().unwrap().len(), 2);
            }
        }
        bot.kind = ActorKind::Human;
        assert!(agent_card(&bot).is_none());

        let mut group = Group::new("group", "agent", vec![]);
        assert!(group_card(&group).is_none());
        group.visibility = "public".into();
        group.status = GroupStatus::Completed;
        assert_eq!(group_card(&group).unwrap().card["description"], "");
        assert_eq!(serde_json::to_value(group_card(&group).unwrap()).unwrap()["labels"], json!({"visibility":["public"]}));
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
        for (resource, attempt) in batch { queue.retry(resource, attempt + 1); }
        let next = queue.take_batch();
        assert_eq!(next[0], (Resource::Agent("bot-100".into()), 0));
        assert_eq!(next[4], (Resource::Agent("bot-104".into()), 0));
        assert_eq!(next[5], (Resource::Agent("bot-0".into()), 1));
        assert_eq!(queue.take_batch().len(), 5);
    }

    #[test]
    fn new_changes_refresh_retry_budget_without_being_overwritten_by_old_failures() {
        let queue = CardChangeQueue::default();
        queue.retry(Resource::Agent("agent".into()), 9);
        queue.agent_changed("agent");
        queue.retry(Resource::Agent("agent".into()), 9);
        assert_eq!(queue.take_batch(), vec![(Resource::Agent("agent".into()), 0)]);
    }
}
