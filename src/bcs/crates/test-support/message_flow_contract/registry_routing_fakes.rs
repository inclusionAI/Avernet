//! Routing double mirroring the production legacy router contract for message-flow
//! contract tests, including overlay resolution and structured routing.
#![allow(dead_code)]

use std::collections::HashMap;

use async_trait::async_trait;
use bcs_service_api::{
    ActorKind, ActorStatus, AgentCredentials, BotCapabilities, BotDeliveryTarget, BotRegistryCoreService,
    CoordinationSurface,
    Group, ParticipantMode, RedactedToken, RegisteredBot, RouteAndSendResult, RoutingCoreService,
    RoutingDecision, RoutingTarget, ServiceError, ServiceResult, StructuredRoutingError,
};
use tokio::sync::RwLock;

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
                env: None,
                created_by: None,
                actor_kind: ActorKind::Bot,
                status: ActorStatus::Online,
            },
        );
        Ok(())
    }

    async fn update_status(&self, _bot_id: &str) -> bool {
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
