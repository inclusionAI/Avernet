#![allow(dead_code)]

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_protocol::BcsFrame;
use bcs_service_api::{
    ActorKind, ActorStatus, AgentCredentials, BotCapabilities, BotDeliveryCommand, BotDeliveryKind,
    BotDeliveryPort, BotDeliveryResult, BotDeliveryTarget, BotDynamicStatus,
    BotRegistryCoreService, CoordinationSurface, FrontendDeliveryCommand, FrontendDeliveryPort,
    FrontendDeliveryResult, Group, GroupCoreService, GroupMessage, GroupStatus, Participant,
    ParticipantMode, ParticipantRole, ProviderTransportPreference, RedactedToken, RegisteredBot,
    RouteAndSendResult, RoutingCoreService, RoutingDecision, RoutingTarget, ServiceError,
    ServiceResult, StructuredRoutingError, Workspace,
    core::{GroupMutationCommand, GroupMutationKind},
};
use tokio::sync::RwLock;

pub struct FlowTestSupport {
    pub group: Arc<FakeGroupCoreService>,
    pub routing: Arc<FakeRoutingCoreService>,
    pub registry: Arc<FakeRegistryService>,
    pub bot_delivery: Arc<RecordingBotDelivery>,
    pub frontend_delivery: Arc<RecordingFrontendDelivery>,
}

impl FlowTestSupport {
    pub async fn new_group_with_driver_and_observer() -> Self {
        let group = Arc::new(FakeGroupCoreService::default());
        let routing = Arc::new(FakeRoutingCoreService::default());
        let registry = Arc::new(FakeRegistryService::default());
        let bot_delivery = Arc::new(RecordingBotDelivery::default());
        let frontend_delivery = Arc::new(RecordingFrontendDelivery::default());

        registry.insert_named_actor("human_1", "Human One").await;
        registry.insert_named_actor("bot-driver", "Driver").await;
        registry
            .insert_named_actor("bot-observer", "Observer")
            .await;

        let session = Group::new(
            "group-1",
            "bot-driver",
            vec![
                bot_participant("bot-driver", "Driver", ParticipantRole::Driver),
                bot_participant("bot-observer", "Observer", ParticipantRole::Observer),
                Participant {
                    bot_uuid: "human_1".to_string(),
                    bot_name: Some("Human One".to_string()),
                    kind: None,
                    role: ParticipantRole::Observer,
                    actor_kind: ActorKind::Human,
                    mode: None,
                    tags: Vec::new(),
                },
            ],
        );
        group.upsert(session).await.unwrap();
        group.increment_message_count("group-1").await.unwrap();

        Self {
            group,
            routing,
            registry,
            bot_delivery,
            frontend_delivery,
        }
    }
}

fn bot_participant(id: &str, name: &str, role: ParticipantRole) -> Participant {
    let mut participant = Participant::bot(id, role);
    participant.bot_name = Some(name.to_string());
    participant
}

#[derive(Default)]
pub struct FakeGroupCoreService {
    groups: RwLock<HashMap<String, Group>>,
    get_counts: RwLock<HashMap<String, usize>>,
    message_counts: RwLock<HashMap<String, usize>>,
    fail_add_message: RwLock<bool>,
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

}

#[derive(Default)]
pub struct FakeRoutingCoreService {
    route_calls: RwLock<Vec<(String, String, Option<String>)>>,
    dm_route_calls: RwLock<Vec<(String, String, String)>>,
    send_calls: RwLock<Vec<(String, String, Option<String>, Option<String>)>>,
}

impl FakeRoutingCoreService {
    pub async fn route_calls(&self) -> Vec<(String, String, Option<String>)> {
        self.route_calls.read().await.clone()
    }

    pub async fn dm_route_calls(&self) -> Vec<(String, String, String)> {
        self.dm_route_calls.read().await.clone()
    }

    pub async fn send_calls(&self) -> Vec<(String, String, Option<String>, Option<String>)> {
        self.send_calls.read().await.clone()
    }
}

#[async_trait]
impl RoutingCoreService for FakeRoutingCoreService {
    /// Mirrors the production legacy `route()`: text @-mentions are resolved
    /// against participants (including Humans, which `route()` does not
    /// classify), so this delegates to the overlay variant with an empty
    /// overlay — actor kind/mode/status then fall back to the participant
    /// rows, matching the production router's defensive default.
    async fn route(
        &self,
        group: &Group,
        message: &str,
        sender_bot_id: Option<&str>,
    ) -> RoutingDecision {
        self.route_with_overlay(group, message, sender_bot_id, &[]).await
    }

    /// Mirrors the production router contract for text @-mentions so message
    /// flow contract tests can exercise the human mention notify hook:
    /// display-name/uuid resolution, Absent-Human drop (Human default mode is
    /// Absent), Hidden drop, `@all`/`@所有人` all-Bot Send, and mention
    /// stripping. Actor kind/mode/status are read from the overlay first
    /// (authoritative, like production `route_with_overlay`), falling back to
    /// the participant row when the overlay has no entry. Simplifications vs
    /// the production router: no display-name mention boundary check, no
    /// ManagerWorker worker exclusion, and no muted/hidden forced-Inject
    /// downgrade (Hidden actors are dropped from mentions entirely instead of
    /// being carried as hidden mentions).
    async fn route_with_overlay(
        &self,
        group: &Group,
        message: &str,
        sender_bot_id: Option<&str>,
        overlay: &[bcs_service_api::RouteParticipantOverlay],
    ) -> RoutingDecision {
        self.route_calls.write().await.push((
            group.id.clone(),
            message.to_string(),
            sender_bot_id.map(str::to_string),
        ));
        let lower = message.to_lowercase();
        let has_all = lower.contains("@all") || message.contains("@所有人");

        let mut resolved: Vec<String> = Vec::new();
        let mut strip_at: Vec<usize> = Vec::new();
        for (at_index, _) in message.match_indices('@') {
            let after_at = &message[at_index + '@'.len_utf8()..];
            let by_name = group
                .participants
                .iter()
                .filter(|participant| {
                    participant
                        .bot_name
                        .as_deref()
                        .map_or(false, |name| !name.is_empty() && after_at.starts_with(name))
                })
                .max_by_key(|participant| participant.bot_name.as_deref().map(str::len));
            let uuid = if let Some(participant) = by_name {
                Some(participant.bot_uuid.clone())
            } else {
                let token: String = after_at
                    .chars()
                    .take_while(|ch| ch.is_alphanumeric() || *ch == '-' || *ch == '_' || *ch == ':')
                    .collect();
                if token.is_empty() {
                    None
                } else {
                    group
                        .participants
                        .iter()
                        .find(|participant| participant.bot_uuid == token)
                        .map(|participant| participant.bot_uuid.clone())
                }
            };
            if let Some(uuid) = uuid {
                strip_at.push(at_index);
                if !resolved.contains(&uuid) {
                    resolved.push(uuid);
                }
            }
        }

        let mut mentions: Vec<String> = Vec::new();
        let mut bot_mentions: Vec<String> = Vec::new();
        for uuid in &resolved {
            let Some(participant) = group.get_participant(uuid) else {
                continue;
            };
            let (actor_kind, mode, status) = match overlay
                .iter()
                .find(|entry| entry.bot_uuid == *uuid)
            {
                Some(entry) => (
                    entry.actor_kind,
                    entry
                        .mode
                        .unwrap_or_else(|| ParticipantMode::default_for(entry.actor_kind)),
                    entry.status,
                ),
                None => (
                    participant.actor_kind,
                    participant
                        .mode
                        .unwrap_or_else(|| ParticipantMode::default_for(participant.actor_kind)),
                    ActorStatus::Online,
                ),
            };
            if status == ActorStatus::Hidden {
                continue;
            }
            if actor_kind == ActorKind::Human && mode == ParticipantMode::Absent {
                continue;
            }
            if actor_kind == ActorKind::Bot {
                bot_mentions.push(uuid.clone());
            }
            if !mentions.contains(uuid) {
                mentions.push(uuid.clone());
            }
        }

        let targets = group
            .participants
            .iter()
            .filter(|participant| participant.is_bot())
            .map(|participant| {
                let delivery_type = if has_all {
                    bcs_service_api::DeliveryType::Send
                } else if !bot_mentions.is_empty() {
                    if bot_mentions.contains(&participant.bot_uuid) {
                        bcs_service_api::DeliveryType::Send
                    } else {
                        bcs_service_api::DeliveryType::Inject
                    }
                } else if participant.bot_uuid == group.driver_bot {
                    bcs_service_api::DeliveryType::Send
                } else {
                    bcs_service_api::DeliveryType::Inject
                };
                RoutingTarget {
                    bot_uuid: participant.bot_uuid.clone(),
                    url: String::new(),
                    is_driver: participant.bot_uuid == group.driver_bot,
                    delivery_type,
                }
            })
            .collect();

        let cleaned_message = message
            .char_indices()
            .filter(|(index, _)| !strip_at.contains(index))
            .map(|(_, ch)| ch)
            .collect();

        RoutingDecision {
            targets,
            mentions,
            cleaned_message,
            hidden_mentions: vec![],
        }
    }

    async fn route_dm_with_overlay(
        &self,
        group: &Group,
        message: &str,
        sender_actor_id: &str,
        _overlay: &[bcs_service_api::RouteParticipantOverlay],
    ) -> RoutingDecision {
        self.dm_route_calls.write().await.push((
            group.id.clone(),
            message.to_string(),
            sender_actor_id.to_string(),
        ));
        let targets = group
            .participants
            .iter()
            .find(|participant| participant.bot_uuid != sender_actor_id && participant.is_bot())
            .map(|participant| RoutingTarget {
                bot_uuid: participant.bot_uuid.clone(),
                url: String::new(),
                is_driver: participant.bot_uuid == group.driver_bot,
                delivery_type: bcs_service_api::DeliveryType::Send,
            })
            .into_iter()
            .collect();
        RoutingDecision {
            targets,
            mentions: Vec::new(),
            cleaned_message: message.to_string(),
            hidden_mentions: vec![],
        }
    }

    async fn send_to_bot(
        &self,
        target: &RoutingTarget,
        message: &str,
        from: Option<&str>,
        group_id: Option<&str>,
    ) -> bcs_service_api::BotSendResult {
        self.send_calls.write().await.push((
            target.bot_uuid.clone(),
            message.to_string(),
            from.map(str::to_string),
            group_id.map(str::to_string),
        ));
        bcs_service_api::BotSendResult {
            bot_uuid: target.bot_uuid.clone(),
            content: String::new(),
            success: true,
            error: None,
        }
    }

    async fn route_and_send(
        &self,
        _group: &Group,
        _message: &str,
        _from: Option<&str>,
    ) -> RouteAndSendResult {
        RouteAndSendResult {
            results: Vec::new(),
            mentions: Vec::new(),
        }
    }

    /// Minimal faithful stand-in for the production structured router:
    /// resolves `bot`/`name` selectors by participant uuid or display name and
    /// mirrors the production compatibility behavior of copying the resolved
    /// responder ids into `decision.mentions` (which may name Humans — the
    /// field is a routing transcript, not a text-mention signal).
    async fn route_structured(
        &self,
        group: &Group,
        routing: &bcs_service_api::ChatEventRouting,
        sender_bot_id: &str,
        _registry: &dyn BotRegistryCoreService,
    ) -> Result<RoutingDecision, StructuredRoutingError> {
        let mut resolved: Vec<String> = Vec::new();
        for selector in &routing.responders {
            let Some(value) = selector.value.as_deref() else {
                continue;
            };
            if selector.selector_type != "bot" && selector.selector_type != "name" {
                continue;
            }
            if let Some(participant) = group
                .participants
                .iter()
                .find(|p| p.bot_uuid == value || p.bot_name.as_deref() == Some(value))
            {
                if !resolved.contains(&participant.bot_uuid) {
                    resolved.push(participant.bot_uuid.clone());
                }
            }
        }
        if resolved.is_empty() {
            return Err(StructuredRoutingError::NoTargetMatched);
        }
        let include_self = routing.include_self.unwrap_or(false);
        let targets = group
            .participants
            .iter()
            .filter(|p| p.is_bot())
            .filter(|p| p.bot_uuid != sender_bot_id || (include_self && resolved.contains(&p.bot_uuid)))
            .map(|p| RoutingTarget {
                bot_uuid: p.bot_uuid.clone(),
                url: String::new(),
                is_driver: p.bot_uuid == group.driver_bot,
                delivery_type: if resolved.contains(&p.bot_uuid) {
                    bcs_service_api::DeliveryType::Send
                } else {
                    bcs_service_api::DeliveryType::Inject
                },
            })
            .collect();
        Ok(RoutingDecision {
            targets,
            mentions: resolved,
            cleaned_message: String::new(),
            hidden_mentions: vec![],
        })
    }
}

#[derive(Default)]
pub struct FakeRegistryService {
    bots: RwLock<HashMap<String, RegisteredBot>>,
    protocol_versions: RwLock<HashMap<String, u32>>,
    delivery_targets: RwLock<HashMap<String, BotDeliveryTarget>>,
    coordination_surfaces: RwLock<HashMap<String, CoordinationSurface>>,
    coordination_surface_resolutions: RwLock<HashMap<String, usize>>,
    including_deleted_gets: RwLock<HashMap<String, usize>>,
}

impl FakeRegistryService {
    pub async fn insert_named_actor(&self, id: &str, name: &str) {
        let capabilities = BotCapabilities {
            name: Some(name.to_string()),
            visibility: "protected".to_string(),
            ..BotCapabilities::default()
        };
        self.bots.write().await.insert(
            id.to_string(),
            RegisteredBot {
                bot_uuid: id.to_string(),
                capabilities,
                dynamic_status: BotDynamicStatus::default(),
                env: None,
                created_by: None,
                actor_kind: if id.starts_with("human_") {
                    ActorKind::Human
                } else {
                    ActorKind::Bot
                },
                status: ActorStatus::Online,
            },
        );
    }

    pub async fn including_deleted_get_count(&self, id: &str) -> usize {
        self.including_deleted_gets
            .read()
            .await
            .get(id)
            .copied()
            .unwrap_or_default()
    }

    pub async fn set_visibility(&self, id: &str, visibility: &str) {
        if let Some(bot) = self.bots.write().await.get_mut(id) {
            bot.capabilities.visibility = visibility.to_string();
        }
    }

    pub async fn set_protocol_version(&self, bot_id: &str, version: u32) {
        self.protocol_versions
            .write()
            .await
            .insert(bot_id.to_string(), version);
    }

    pub async fn set_delivery_target(&self, bot_id: &str, target: BotDeliveryTarget) {
        self.delivery_targets
            .write()
            .await
            .insert(bot_id.to_string(), target);
    }

    pub async fn set_coordination_surface(&self, bot_id: &str, surface: CoordinationSurface) {
        self.coordination_surfaces
            .write()
            .await
            .insert(bot_id.to_string(), surface);
    }

    pub async fn coordination_surface_resolution_count(&self, bot_id: &str) -> usize {
        self.coordination_surface_resolutions
            .read()
            .await
            .get(bot_id)
            .copied()
            .unwrap_or_default()
    }

    pub fn provider_target(bot_id: &str) -> BotDeliveryTarget {
        BotDeliveryTarget::HttpProvider {
            bot_id: bot_id.to_string(),
            provider_id: "provider-1".to_string(),
            provider_bot_ref: bot_id.to_string(),
            webhook_url: "https://provider.example.com/bcs/webhook".to_string(),
            bcs_to_provider_token: RedactedToken::new("secret-b2p"),
            protocol_version: "1.0".to_string(),
        }
    }
}

#[async_trait]
impl BotRegistryCoreService for FakeRegistryService {
    async fn register(&self, bot_id: String, capabilities: BotCapabilities) -> ServiceResult<()> {
        self.bots.write().await.insert(
            bot_id.clone(),
            RegisteredBot {
                bot_uuid: bot_id,
                capabilities,
                dynamic_status: BotDynamicStatus::default(),
                env: None,
                created_by: None,
                actor_kind: ActorKind::Bot,
                status: ActorStatus::Online,
            },
        );
        Ok(())
    }

    async fn update_status(&self, _bot_id: &str, _status: BotDynamicStatus) -> bool {
        false
    }

    async fn get(&self, bot_id: &str) -> Option<RegisteredBot> {
        self.bots.read().await.get(bot_id).cloned()
    }

    async fn get_including_deleted(&self, bot_id: &str) -> Option<RegisteredBot> {
        let mut gets = self.including_deleted_gets.write().await;
        *gets.entry(bot_id.to_string()).or_default() += 1;
        drop(gets);
        self.get(bot_id).await
    }

    async fn get_agent_credentials(&self, bot_id: &str) -> Option<AgentCredentials> {
        // Test bots get synthetic agent credentials so the outbound interceptor
        // chain runs through to BlockingInterceptor / SecurityInterceptor in
        // tests. Production code skips the chain when agent_code is missing
        // (see group_flow::apply_outbound_interceptors).
        if self.bots.read().await.contains_key(bot_id) {
            Some(AgentCredentials {
                agent_code: Some(format!("test-agent-{bot_id}")),
                agent_token: Some(format!("test-token-{bot_id}")),
            })
        } else {
            None
        }
    }

    async fn list_active(&self) -> Vec<RegisteredBot> {
        self.bots.read().await.values().cloned().collect()
    }

    async fn list_bots_by_creator(&self, _created_by: &str) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn discover(&self, _query: &str) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn find_by_skills(&self, _skills: &[&str]) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn find_by_domains(&self, _domains: &[&str]) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn find_by_scopes(&self, _scopes: &[&str]) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn unregister(&self, bot_id: &str) -> bool {
        self.bots.write().await.remove(bot_id).is_some()
    }

    async fn cleanup_expired(&self) {}

    async fn load_from_storage(&self, _bot_id: &str) -> Option<BotCapabilities> {
        None
    }

    async fn save_to_storage(&self, _bot_id: &str, _caps: &BotCapabilities) -> ServiceResult<()> {
        Ok(())
    }

    async fn update_visibility(&self, _bot_id: &str, _visibility: &str) -> ServiceResult<()> {
        Ok(())
    }

    #[allow(deprecated)]
    async fn set_hidden(&self, _bot_id: &str, _hidden: bool) -> ServiceResult<()> {
        Ok(())
    }

    async fn has_been_onboarded(&self, _bot_id: &str) -> bool {
        false
    }

    async fn save_created_by(
        &self,
        bot_id: &str,
        created_by: &str,
        overwrite: bool,
    ) -> ServiceResult<()> {
        if let Some(bot) = self.bots.write().await.get_mut(bot_id) {
            if overwrite || bot.created_by.is_none() {
                bot.created_by = Some(created_by.to_string());
            }
        }
        Ok(())
    }

    async fn save_token(&self, _bot_id: &str, _token: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn load_token(&self, _bot_id: &str) -> Option<String> {
        None
    }

    async fn find_bot_by_token(&self, _token: &str) -> Option<String> {
        None
    }

    async fn register_streaming_connection(&self, _bot_id: String) -> Result<String, ()> {
        Err(())
    }

    async fn reconnect_streaming(&self, _existing_token: String) -> Result<(String, String), ()> {
        Err(())
    }

    async fn disconnect_streaming(&self, _bot_id: &str) {}

    async fn is_connected(&self, bot_id: &str) -> bool {
        self.bots.read().await.contains_key(bot_id)
    }

    async fn send_frame(&self, _bot_id: &str, _frame: String) -> Result<(), ()> {
        Ok(())
    }

    async fn list_connected(&self) -> Vec<String> {
        self.bots.read().await.keys().cloned().collect()
    }

    async fn store_token_mapping(&self, _token: String, _bot_id: String) {}

    async fn get_protocol_version(&self, bot_id: &str) -> u32 {
        self.protocol_versions
            .read()
            .await
            .get(bot_id)
            .copied()
            .unwrap_or(2)
    }

    async fn register_http_connection(&self, _bot_id: String, token: String) -> String {
        token
    }

    async fn resolve_delivery_target(&self, bot_id: &str) -> ServiceResult<BotDeliveryTarget> {
        if let Some(target) = self.delivery_targets.read().await.get(bot_id).cloned() {
            return Ok(target);
        }
        Ok(BotDeliveryTarget::WebSocket {
            bot_id: bot_id.to_string(),
        })
    }

    async fn resolve_coordination_surface(
        &self,
        bot_id: &str,
    ) -> ServiceResult<CoordinationSurface> {
        let mut resolutions = self.coordination_surface_resolutions.write().await;
        *resolutions.entry(bot_id.to_string()).or_default() += 1;
        drop(resolutions);
        if let Some(surface) = self.coordination_surfaces.read().await.get(bot_id).cloned() {
            return Ok(surface);
        }
        if !self.bots.read().await.contains_key(bot_id) {
            return Err(ServiceError::BotNotFound(bot_id.to_string()));
        }
        Ok(CoordinationSurface::legacy_upstream())
    }
}

#[derive(Default)]
pub struct RecordingBotDelivery {
    kinds: RwLock<Vec<BotDeliveryKind>>,
    frames: RwLock<Vec<BcsFrame>>,
    targets: RwLock<Vec<BotDeliveryTarget>>,
    provider_transports: RwLock<Vec<ProviderTransportPreference>>,
    fail_for: RwLock<Vec<String>>,
    not_delivered_for: RwLock<Vec<String>>,
}

impl RecordingBotDelivery {
    pub async fn kinds(&self) -> Vec<BotDeliveryKind> {
        self.kinds.read().await.clone()
    }

    pub async fn frames(&self) -> Vec<BcsFrame> {
        self.frames.read().await.clone()
    }

    pub async fn targets(&self) -> Vec<BotDeliveryTarget> {
        self.targets.read().await.clone()
    }

    pub async fn provider_transports(&self) -> Vec<ProviderTransportPreference> {
        self.provider_transports.read().await.clone()
    }

    pub async fn fail_for(&self, bot_id: &str) {
        self.fail_for.write().await.push(bot_id.to_string());
    }

    pub async fn not_delivered_for(&self, bot_id: &str) {
        self.not_delivered_for.write().await.push(bot_id.to_string());
    }
}

#[async_trait]
impl BotDeliveryPort for RecordingBotDelivery {
    async fn is_available(&self, _target: &BotDeliveryTarget) -> bool {
        true
    }

    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        let target_bot_id = cmd.target_bot_id().to_string();
        self.targets.write().await.push(cmd.target.clone());
        self.kinds.write().await.push(cmd.delivery_kind);
        self.provider_transports
            .write()
            .await
            .push(cmd.provider_transport);
        self.frames.write().await.push(cmd.frame);
        if self.fail_for.read().await.contains(&target_bot_id) {
            return Err(ServiceError::BotNotConnected(target_bot_id));
        }
        if self.not_delivered_for.read().await.contains(&target_bot_id) {
            return Ok(BotDeliveryResult {
                target_bot_id: target_bot_id.clone(),
                delivered: false,
                error: Some(ServiceError::BotNotConnected(target_bot_id)),
            });
        }
        Ok(BotDeliveryResult {
            target_bot_id,
            delivered: true,
            error: None,
        })
    }
}

#[derive(Default)]
pub struct RecordingFrontendDelivery {
    events: RwLock<Vec<String>>,
    commands: RwLock<Vec<FrontendDeliveryCommand>>,
    fail_publish: RwLock<bool>,
}

impl RecordingFrontendDelivery {
    pub async fn events(&self) -> Vec<String> {
        self.events.read().await.clone()
    }

    pub async fn commands(&self) -> Vec<FrontendDeliveryCommand> {
        self.commands.read().await.clone()
    }

    pub async fn fail_publish(&self) {
        *self.fail_publish.write().await = true;
    }
}

#[async_trait]
impl FrontendDeliveryPort for RecordingFrontendDelivery {
    async fn publish(&self, cmd: FrontendDeliveryCommand) -> ServiceResult<FrontendDeliveryResult> {
        if *self.fail_publish.read().await {
            return Err(ServiceError::InternalError("publish failed".to_string()));
        }
        self.events.write().await.push(cmd.event_json.clone());
        self.commands.write().await.push(cmd.clone());
        Ok(FrontendDeliveryResult {
            target: cmd.target,
            delivered: 1,
        })
    }

    async fn unregister_run(&self, _run_id: &str) -> ServiceResult<()> {
        Ok(())
    }
}

/// Recording fake for the human mention notify port (contract tests).
#[derive(Default)]
pub struct RecordingHumanMentionNotify {
    available: std::sync::atomic::AtomicBool,
    notifications: tokio::sync::Mutex<Vec<bcs_service_api::port::human_notify::MentionNotification>>,
}

impl RecordingHumanMentionNotify {
    pub fn available(available: bool) -> Self {
        Self {
            available: std::sync::atomic::AtomicBool::new(available),
            notifications: tokio::sync::Mutex::new(Vec::new()),
        }
    }

    pub async fn notifications(
        &self,
    ) -> Vec<bcs_service_api::port::human_notify::MentionNotification> {
        self.notifications.lock().await.clone()
    }

    /// Poll until `expected` notifications arrive or a 2s deadline passes.
    pub async fn wait_for(
        &self,
        expected: usize,
    ) -> Vec<bcs_service_api::port::human_notify::MentionNotification> {
        let deadline = tokio::time::Instant::now() + tokio::time::Duration::from_secs(2);
        loop {
            let count = self.notifications.lock().await.len();
            if count >= expected || tokio::time::Instant::now() >= deadline {
                return self.notifications.lock().await.clone();
            }
            tokio::time::sleep(tokio::time::Duration::from_millis(10)).await;
        }
    }

    /// Negative assertion helper: waits a fixed window during which a spawned
    /// notification would have arrived, then returns what was recorded
    /// (callers assert emptiness). `wait_for(0)` must NOT be used for
    /// negative assertions — it returns immediately.
    pub async fn wait_for_none(
        &self,
    ) -> Vec<bcs_service_api::port::human_notify::MentionNotification> {
        tokio::time::sleep(tokio::time::Duration::from_millis(200)).await;
        self.notifications.lock().await.clone()
    }
}

#[async_trait]
impl bcs_service_api::port::human_notify::HumanMentionNotifyPort for RecordingHumanMentionNotify {
    fn is_available(&self) -> bool {
        self.available.load(std::sync::atomic::Ordering::SeqCst)
    }

    async fn notify_mentioned_humans(
        &self,
        notification: bcs_service_api::port::human_notify::MentionNotification,
    ) -> ServiceResult<()> {
        self.notifications.lock().await.push(notification);
        Ok(())
    }
}
