#![allow(dead_code)]

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_protocol::BcsFrame;
use bcs_service_api::{
    ActorKind, ActorStatus, AgentCredentials, BotDeliveryCommand, BotDeliveryKind, BotDeliveryPort,
    BotDeliveryResult, BotDeliveryTarget, BotCapabilities, BotDynamicStatus, BotRegistryCoreService,
    FrontendDeliveryCommand, FrontendDeliveryPort, FrontendDeliveryResult, Group, GroupMessage,
    GroupCoreService, GroupStatus, Participant, ParticipantMode, ParticipantRole, RegisteredBot,
    ProviderTransportPreference, RedactedToken, RouteAndSendResult, RoutingDecision,
    RoutingCoreService, RoutingTarget, ServiceError, ServiceResult, StructuredRoutingError,
    Workspace,
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
    message_counts: RwLock<HashMap<String, usize>>,
    fail_add_message: RwLock<bool>,
}

impl FakeGroupCoreService {
    pub async fn fail_add_message(&self) {
        *self.fail_add_message.write().await = true;
    }
}

#[async_trait]
impl GroupCoreService for FakeGroupCoreService {
    async fn upsert(&self, group: Group) -> ServiceResult<()> {
        self.groups.write().await.insert(group.id.clone(), group);
        Ok(())
    }

    async fn get(&self, id: &str) -> Option<Group> {
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
    async fn route(
        &self,
        group: &Group,
        message: &str,
        sender_bot_id: Option<&str>,
    ) -> RoutingDecision {
        self.route_calls.write().await.push((
            group.id.clone(),
            message.to_string(),
            sender_bot_id.map(str::to_string),
        ));
        let targets = group
            .participants
            .iter()
            .filter(|participant| participant.is_bot())
            .map(|participant| RoutingTarget {
                bot_uuid: participant.bot_uuid.clone(),
                url: String::new(),
                is_driver: participant.bot_uuid == group.driver_bot,
                delivery_type: if participant.bot_uuid == group.driver_bot {
                    bcs_service_api::DeliveryType::Send
                } else {
                    bcs_service_api::DeliveryType::Inject
                },
            })
            .collect();
        RoutingDecision {
            targets,
            mentions: Vec::new(),
            cleaned_message: message.to_string(),
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

    async fn route_structured(
        &self,
        _group: &Group,
        _routing: &bcs_service_api::ChatEventRouting,
        _sender_bot_id: &str,
        _registry: &dyn BotRegistryCoreService,
    ) -> Result<RoutingDecision, StructuredRoutingError> {
        Err(StructuredRoutingError::NoTargetMatched)
    }
}

#[derive(Default)]
pub struct FakeRegistryService {
    bots: RwLock<HashMap<String, RegisteredBot>>,
    protocol_versions: RwLock<HashMap<String, u32>>,
    delivery_targets: RwLock<HashMap<String, BotDeliveryTarget>>,
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
