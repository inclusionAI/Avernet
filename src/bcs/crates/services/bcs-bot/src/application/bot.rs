use std::collections::BTreeMap;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::{
    ActorKind, ActorStatus, BotCapabilities, BotConnectCommand, BotConnectParams, BotConnectResult,
    BotConnectionControlPort, BotDeliveryTarget, BotDetailCommand, BotDetailResult,
    BotDiscoveryCommand, BotDiscoveryEntry, BotDiscoveryProviderInfo, BotDiscoveryResult,
    BotDiscoveryService, BotLeaveCommand, BotLeaveResult, BotListCommand, BotListEntry,
    BotListResult, BotManagementService, BotPagedListCommand, BotPagedListResult,
    BotQueryByIdsCommand, BotQueryByIdsResult, BotQueryEntry, BotQueryService,
    BotRegistryCoreService, OrganizationCoreService, OrganizationMemberSummary,
    BotRuntimeConnectCommand, BotRuntimeConnectOutcome, BotRuntimeConnectionService,
    BotRuntimeDisconnectCommand, BotRuntimeStatusCommand, BotRuntimeStatusOutcome,
    BotStatusUpdateCommand, BotStatusUpdateResult, BotUseCaseError, BotVisibilityCommand,
    BotVisibilityQueryCommand, BotVisibilityQueryResult, BotVisibilityResult, ConnectionKind,
    DynamicStatusResponse, FriendCoreService, KickReason, ProviderBotBinding,
    ProviderBotDiscoverySelector, RegisteredBot, RelationCoreService, ServiceError, ServiceResult,
    SwitchDeliveryToProviderCommand, SwitchDeliveryToProviderResult,
};
use bcs_user_directory_api::UserDirectoryPlugin;

use crate::core::BotCore;

/// Bot query and management application service backed by the registry port.
#[derive(Clone)]
pub struct Bot {
    registry: Arc<dyn BotRegistryCoreService>,
    friend: Arc<dyn FriendCoreService>,
    bot_core: Option<Arc<BotCore>>,
    relation: Option<Arc<dyn RelationCoreService>>,
    user_directory: Option<Arc<dyn UserDirectoryPlugin>>,
    connection_control: Option<Arc<dyn BotConnectionControlPort>>,
    organization: Option<Arc<dyn OrganizationCoreService>>,
}

impl Bot {
    pub fn new(registry: Arc<dyn BotRegistryCoreService>) -> Self {
        Self::new_with_friend(registry, Arc::new(EmptyFriendCoreService))
    }

    pub fn new_with_friend(
        registry: Arc<dyn BotRegistryCoreService>,
        friend: Arc<dyn FriendCoreService>,
    ) -> Self {
        Self {
            registry,
            friend,
            bot_core: None,
            relation: None,
            user_directory: None,
            connection_control: None,
            organization: None,
        }
    }

    /// Wire the concrete `BotCore` so use cases that need provider-bindings
    /// access or the readiness helper can reach them.
    pub fn with_bot_core(mut self, bot_core: Arc<BotCore>) -> Self {
        self.bot_core = Some(bot_core);
        self
    }

    /// Wire the relation graph used to maintain Human ↔ Bot owner edges.
    pub fn with_relation(mut self, relation: Arc<dyn RelationCoreService>) -> Self {
        self.relation = Some(relation);
        self
    }

    /// Wire a user directory used to resolve Human actor display names.
    pub fn with_user_directory(mut self, user_directory: Arc<dyn UserDirectoryPlugin>) -> Self {
        self.user_directory = Some(user_directory);
        self
    }

    /// Wire the outbound port used to kick the bot's WebSocket connection.
    pub fn with_connection_control(
        mut self,
        port: Arc<dyn BotConnectionControlPort>,
    ) -> Self {
        self.connection_control = Some(port);
        self
    }

    pub fn with_organization(
        mut self,
        organization: Arc<dyn OrganizationCoreService>,
    ) -> Self {
        self.organization = Some(organization);
        self
    }

    async fn ensure_provider_switch_bot_onboarded(
        &self,
        bot_id: &str,
        owner_staff_no: &str,
        name: Option<&str>,
        summary: Option<&str>,
    ) -> Result<(), BotUseCaseError> {
        if self.registry.has_been_onboarded(bot_id).await {
            return Ok(());
        }

        let capabilities = BotCapabilities {
            name: Some(non_empty_text(name).unwrap_or_else(|| bot_id.to_string())),
            summary: Some(non_empty_text(summary).unwrap_or_else(|| bot_id.to_string())),
            visibility: "protected".to_string(),
            ..Default::default()
        };
        let token = self
            .registry
            .load_token(bot_id)
            .await
            .filter(|token| !token.is_empty())
            .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
        self.registry
            .register_with_owner_and_token(
                bot_id.to_string(),
                capabilities,
                owner_staff_no,
                &token,
            )
            .await?;
        Ok(())
    }

    async fn ensure_owner_binding_for_switch(
        &self,
        bot_id: &str,
        owner_staff_no: &str,
    ) -> Result<(), BotUseCaseError> {
        let relation = self.relation.as_ref().ok_or_else(|| {
            BotUseCaseError::Service(ServiceError::InternalError(
                "Bot is missing RelationCoreService wiring; \
                 switch_delivery_to_provider requires .with_relation(...)"
                    .to_string(),
            ))
        })?;

        self.registry
            .save_created_by(bot_id, owner_staff_no, true)
            .await?;
        let nick_name = self.resolve_owner_nick_name(owner_staff_no).await;
        self.registry
            .ensure_human_actor(owner_staff_no, &nick_name)
            .await?;
        let human_id = format!("human_{}", owner_staff_no);
        let env = bcs_config::resolve_env_str();
        relation.ensure_owner_edges(&human_id, bot_id, &env).await?;
        Ok(())
    }

    async fn resolve_owner_nick_name(&self, staff_no: &str) -> String {
        let Some(user_directory) = self.user_directory.as_ref() else {
            return staff_no.to_string();
        };
        match user_directory.lookup_by_staff_no(staff_no).await {
            Ok(Some(profile)) => profile
                .nick_name
                .as_deref()
                .map(str::trim)
                .filter(|nick_name| !nick_name.is_empty())
                .map(str::to_string)
                .unwrap_or_else(|| staff_no.to_string()),
            Ok(None) => {
                tracing::warn!(
                    staff_no = %staff_no,
                    "user directory returned no profile; falling back to staff_no for human actor name"
                );
                staff_no.to_string()
            }
            Err(error) => {
                tracing::warn!(
                    staff_no = %staff_no,
                    error = %error,
                    "user directory lookup failed; falling back to staff_no for human actor name"
                );
                staff_no.to_string()
            }
        }
    }
}

#[derive(Debug)]
struct EmptyFriendCoreService;

#[async_trait]
impl FriendCoreService for EmptyFriendCoreService {
    async fn list_friends(&self, _bot_id: &str) -> Vec<String> {
        Vec::new()
    }

    async fn are_friends(&self, _bot_a: &str, _bot_b: &str) -> bool {
        false
    }

    async fn are_all_friends(
        &self,
        _bot_id: &str,
        others: &[String],
    ) -> bcs_service_api::ServiceResult<()> {
        if others.is_empty() {
            Ok(())
        } else {
            Err(ServiceError::NotFriends(others.to_vec()))
        }
    }

    async fn add_friendship(
        &self,
        _bot_a: &str,
        _bot_b: &str,
    ) -> bcs_service_api::ServiceResult<()> {
        Ok(())
    }

    async fn remove_all_friendships(&self, _bot_id: &str) -> bcs_service_api::ServiceResult<usize> {
        Ok(0)
    }
}

#[async_trait]
impl BotQueryService for Bot {
    async fn list_bots(&self, command: BotListCommand) -> Result<BotListResult, BotUseCaseError> {
        let filtered: Vec<RegisteredBot> = self
            .registry
            .list_active()
            .await
            .into_iter()
            .filter(|bot| is_in_list_bots_scope(bot, command.onboarded))
            .collect();
        let total = filtered.len() as u64;
        let offset = to_usize(command.offset);
        let limit = to_usize(command.limit);
        let bots = filtered
            .into_iter()
            .skip(offset)
            .take(limit)
            .map(bot_to_list_entry)
            .collect();

        Ok(BotListResult {
            bots,
            offset: command.offset,
            limit: command.limit,
            total,
        })
    }

    async fn get_bot(&self, command: BotDetailCommand) -> Result<BotDetailResult, BotUseCaseError> {
        let bot = self
            .registry
            .get(&command.bot_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(command.bot_id.clone()))?;
        authorize_visibility_read(command.caller_actor_id.as_deref(), &bot)?;

        Ok(self.bot_to_detail(bot).await)
    }

    async fn list_bots_by_creator(
        &self,
        staff_no: &str,
    ) -> Result<Vec<BotListEntry>, BotUseCaseError> {
        let bots: Vec<BotListEntry> = self
            .registry
            .list_bots_by_creator(staff_no)
            .await
            .into_iter()
            .map(bot_to_list_entry)
            .collect();
        Ok(bots)
    }

    async fn get_visibility(
        &self,
        command: BotVisibilityQueryCommand,
    ) -> Result<BotVisibilityQueryResult, BotUseCaseError> {
        let bot = self
            .registry
            .get(&command.bot_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(command.bot_id.clone()))?;

        authorize_visibility_read(command.caller_actor_id.as_deref(), &bot)?;

        Ok(BotVisibilityQueryResult {
            bot_uuid: command.bot_id,
            visibility: normalized_visibility(&bot.capabilities.visibility).to_string(),
        })
    }

    async fn list_bots_paged(
        &self,
        command: BotPagedListCommand,
    ) -> Result<BotPagedListResult, BotUseCaseError> {
        let filtered: Vec<RegisteredBot> = self
            .registry
            .list_active()
            .await
            .into_iter()
            .filter(|bot| match &command.user_id {
                Some(user_id) => bot
                    .bot_uuid
                    .rsplit_once(':')
                    .map(|(_, suffix)| suffix == user_id.as_str())
                    .unwrap_or(false),
                None => true,
            })
            .collect();
        self.bot_page_from_registered(filtered, command.offset, command.limit)
            .await
    }

    async fn list_my_bots(
        &self,
        command: bcs_service_api::MyBotsCommand,
    ) -> Result<BotPagedListResult, BotUseCaseError> {
        let bots = self.registry.list_bots_by_creator(&command.staff_no).await;
        self.my_bot_page_from_registered(
            bots,
            command.offset,
            command.limit,
            command.active_only,
        )
            .await
    }

    async fn query_bots_by_ids(
        &self,
        command: BotQueryByIdsCommand,
    ) -> Result<BotQueryByIdsResult, BotUseCaseError> {
        let bots = self
            .registry
            .get_by_ids(&command.bot_ids)
            .await
            .into_iter()
            .filter(|bot| bot.capabilities.name.is_some())
            .collect::<Vec<_>>();
        let mut entries = Vec::with_capacity(bots.len());
        for bot in bots {
            entries.push(self.bot_to_query_entry(bot).await);
        }
        Ok(BotQueryByIdsResult { bots: entries })
    }
}

#[async_trait]
impl BotDiscoveryService for Bot {
    async fn discover_bots(
        &self,
        command: BotDiscoveryCommand,
    ) -> Result<BotDiscoveryResult, BotUseCaseError> {
        if command.role.is_some() && command.organization_code.is_none() {
            return Err(ServiceError::InvalidOperation {
                message: "role_requires_organization_code".to_string(),
                request_id: None,
            }
            .into());
        }
        if command.organization_code.is_some() {
            let code = command.organization_code.clone().unwrap_or_default();
            return self.discover_organization_bots(&code, command).await;
        }

        let bots = self.discover_candidates(&command).await;

        if let Some(collaborate_bot) = command.collaborate_bot.as_deref() {
            let collaborate_bot_is_private = self
                .registry
                .get(collaborate_bot)
                .await
                .map(|bot| !is_discover_visible(&bot.capabilities.visibility))
                .unwrap_or(false);
            if collaborate_bot_is_private {
                return Ok(BotDiscoveryResult {
                    bots: Vec::new(),
                    count: 0,
                });
            }
        }

        let friend_uuids = if let Some(collaborate_bot) = command.collaborate_bot.as_deref() {
            Some(self.friend.list_friends(collaborate_bot).await)
        } else {
            None
        };

        let entries = bots
            .into_iter()
            .filter(|candidate| matches_discovery_selector(&candidate.bot, &command))
            .filter_map(|candidate| discover_entry(candidate, &command, friend_uuids.as_ref()))
            .collect::<Vec<_>>();
        let mut entries_with_agent_code = Vec::with_capacity(entries.len());
        for mut entry in entries {
            entry.agent_code = self
                .registry
                .get_agent_credentials(&entry.bot_uuid)
                .await
                .and_then(|credentials| credentials.agent_code);
            entries_with_agent_code.push(entry);
        }
        Ok(BotDiscoveryResult {
            count: entries_with_agent_code.len(),
            bots: entries_with_agent_code,
        })
    }
}

#[async_trait]
impl BotManagementService for Bot {
    async fn connect_bot(
        &self,
        command: BotConnectCommand,
    ) -> Result<BotConnectResult, BotUseCaseError> {
        if let Some(bot_id) = command.bot_id.as_deref() {
            self.validate_connect_bot_id(bot_id).await?;
        }

        let params = BotConnectParams {
            token: command.token,
            bot_id: command.bot_id,
            protocol_version: command.protocol_version,
            client_kind: None,
        };

        self.registry
            .connect_bot(params, ConnectionKind::Http)
            .await
            .map_err(BotUseCaseError::Connect)
    }

    async fn update_status(
        &self,
        command: BotStatusUpdateCommand,
    ) -> Result<BotStatusUpdateResult, BotUseCaseError> {
        let BotStatusUpdateCommand {
            caller_actor_id,
            bot_id,
            status,
        } = command;
        match self.registry.get(&bot_id).await {
            Some(bot) => authorize_bot_management(caller_actor_id.as_deref(), &bot)?,
            None if caller_actor_id.as_deref() == Some(bot_id.as_str()) => {}
            None => return Err(ServiceError::BotNotFound(bot_id).into()),
        }

        let updated = self.registry.update_status(&bot_id, status.clone()).await;

        Ok(BotStatusUpdateResult {
            updated,
            bot_uuid: bot_id,
            status,
        })
    }

    async fn set_visibility(
        &self,
        command: BotVisibilityCommand,
    ) -> Result<BotVisibilityResult, BotUseCaseError> {
        let BotVisibilityCommand {
            caller_actor_id,
            bot_id,
            visibility,
        } = command;

        if !is_valid_visibility(&visibility) {
            return Err(BotUseCaseError::InvalidVisibility(visibility));
        }

        let bot = self
            .registry
            .get(&bot_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(bot_id.clone()))?;
        authorize_bot_management(caller_actor_id.as_deref(), &bot)?;

        self.registry
            .update_visibility(&bot_id, &visibility)
            .await?;

        Ok(BotVisibilityResult {
            bot_uuid: bot_id,
            visibility,
        })
    }

    async fn leave_bot(&self, command: BotLeaveCommand) -> Result<BotLeaveResult, BotUseCaseError> {
        let caller = command.caller_actor_id.as_deref().ok_or_else(|| {
            BotUseCaseError::Unauthorized("valid human identity is required".to_string())
        })?;
        let staff_no = caller.strip_prefix("human_").ok_or_else(|| {
            BotUseCaseError::Forbidden(
                "owner delete requires a human owner identity".to_string(),
            )
        })?;
        if command.human_actor_id.as_deref() != Some(caller) {
            return Err(BotUseCaseError::Forbidden(
                "owner delete requires matching human identity".to_string(),
            ));
        }

        let bot = self
            .registry
            .get(&command.bot_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(command.bot_id.clone()))?;
        authorize_human_creator_required(staff_no, &bot)?;

        if is_owner_suffixed_bot_id_for_staff(&command.bot_id, staff_no) {
            return Err(BotUseCaseError::Forbidden(
                "TC bot must be deleted from TC".to_string(),
            ));
        }

        if self.is_provider_managed_bot(&command.bot_id).await? {
            return Err(BotUseCaseError::Forbidden(
                "provider-managed bot must be deleted from provider side".to_string(),
            ));
        }

        let left = self.registry.soft_delete(&command.bot_id).await;
        Ok(BotLeaveResult {
            left,
            bot_uuid: command.bot_id,
        })
    }

    async fn switch_delivery_to_provider(
        &self,
        command: SwitchDeliveryToProviderCommand,
    ) -> Result<SwitchDeliveryToProviderResult, BotUseCaseError> {
        let SwitchDeliveryToProviderCommand {
            bot_id,
            provider_id,
            provider_bot_ref,
            name,
            summary,
        } = command;

        if bot_id.trim().is_empty() {
            return Err(BotUseCaseError::InvalidBotId(
                "bot_id must not be empty".to_string(),
            ));
        }

        if provider_bot_ref.trim().is_empty() {
            return Err(BotUseCaseError::InvalidProviderBotRef(
                "provider_bot_ref must not be empty".to_string(),
            ));
        }
        let owner_staff_no = owner_from_provider_bot_ref(&provider_bot_ref)?;

        let bot_core = self.bot_core.as_ref().ok_or_else(|| {
            BotUseCaseError::Service(ServiceError::InternalError(
                "Bot is missing BotCore wiring; \
                 switch_delivery_to_provider requires .with_bot_core(...)"
                    .to_string(),
            ))
        })?;

        match bot_core.assert_provider_ready_for_downlink(&provider_id).await {
            Ok(()) => {}
            Err(ServiceError::ProviderNotFound(p)) => {
                return Err(BotUseCaseError::ProviderNotFound(p));
            }
            Err(ServiceError::ProviderNotReadyForDownlink { provider_id, reason }) => {
                return Err(BotUseCaseError::ProviderNotReadyForDownlink {
                    provider_id,
                    reason,
                });
            }
            Err(other) => return Err(BotUseCaseError::Service(other)),
        }

        let bindings = bot_core.provider_bindings_repo().ok_or_else(|| {
            BotUseCaseError::Service(ServiceError::InternalError(
                "provider_bindings repo not configured".to_string(),
            ))
        })?;
        let existing = bindings.get_binding_by_bot_uuid(&bot_id).await?;
        if existing.is_none() {
            if let Some(binding) = bindings
                .get_binding_by_provider_ref(&provider_id, &provider_bot_ref)
                .await?
            {
                if binding.bot_uuid != bot_id {
                    return Err(BotUseCaseError::BotAlreadyBound {
                        bot_id,
                        existing_provider_id: binding.provider_id,
                        existing_provider_bot_ref: binding.provider_bot_ref,
                    });
                }
            }
        }
        let (binding, idempotent_replay) = match existing {
            Some(b)
                if !b.disabled
                    && b.provider_id == provider_id
                    && b.provider_bot_ref == provider_bot_ref =>
            {
                self.ensure_provider_switch_bot_onboarded(
                    &bot_id,
                    &owner_staff_no,
                    name.as_deref(),
                    summary.as_deref(),
                )
                .await?;
                self.ensure_owner_binding_for_switch(&bot_id, &owner_staff_no)
                    .await?;
                (b, true)
            }
            Some(b) => {
                return Err(BotUseCaseError::BotAlreadyBound {
                    bot_id,
                    existing_provider_id: b.provider_id,
                    existing_provider_bot_ref: b.provider_bot_ref,
                });
            }
            None => {
                self.ensure_provider_switch_bot_onboarded(
                    &bot_id,
                    &owner_staff_no,
                    name.as_deref(),
                    summary.as_deref(),
                )
                .await?;
                self.ensure_owner_binding_for_switch(&bot_id, &owner_staff_no)
                    .await?;
                let now = now_ms();
                let binding = ProviderBotBinding {
                    bot_uuid: bot_id.clone(),
                    provider_id: provider_id.clone(),
                    provider_bot_ref: provider_bot_ref.clone(),
                    disabled: false,
                    created_at: now,
                    updated_at: now,
                };
                bindings.insert_binding(binding.clone()).await?;
                (binding, false)
            }
        };

        let websocket_kicked = match self.connection_control.as_ref() {
            Some(port) => port.kick(&bot_id, KickReason::DeliverySwitchedToProvider).await,
            None => false,
        };

        tracing::info!(
            bot_id = %bot_id,
            provider_id = %binding.provider_id,
            provider_bot_ref = %binding.provider_bot_ref,
            idempotent_replay,
            websocket_kicked,
            "switch_delivery_to_provider"
        );

        Ok(SwitchDeliveryToProviderResult {
            bot_id,
            provider_id: binding.provider_id,
            provider_bot_ref: binding.provider_bot_ref,
            binding_created_at: binding.created_at,
            idempotent_replay,
            websocket_kicked,
        })
    }
}

#[async_trait]
impl BotRuntimeConnectionService for Bot {
    async fn connect_streaming(
        &self,
        command: BotRuntimeConnectCommand,
    ) -> Result<BotRuntimeConnectOutcome, BotUseCaseError> {
        let BotRuntimeConnectCommand {
            caller_actor_id: _,
            token,
            bot_id,
            protocol_version,
            client_kind,
        } = command;

        if let Some(bot_id) = bot_id.as_deref() {
            self.validate_connect_bot_id(bot_id).await?;
        }

        let params = BotConnectParams {
            token,
            bot_id,
            protocol_version,
            client_kind: client_kind.clone(),
        };
        let result = self
            .registry
            .connect_bot(params, ConnectionKind::Streaming)
            .await
            .map_err(BotUseCaseError::Connect)?;

        if let Some(version) = protocol_version {
            self.registry
                .set_protocol_version(&result.bot_uuid, version)
                .await;
        }
        if let Some(client_kind) = client_kind
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
        {
            self.registry
                .add_bot_info(&result.bot_uuid, "client_kind", client_kind.to_string())
                .await;
        }

        Ok(BotRuntimeConnectOutcome::from_connect_result(result))
    }

    async fn update_runtime_status(
        &self,
        command: BotRuntimeStatusCommand,
    ) -> Result<BotRuntimeStatusOutcome, BotUseCaseError> {
        let BotRuntimeStatusCommand {
            caller_actor_id,
            bot_id,
            status,
        } = command;

        match self.registry.get(&bot_id).await {
            Some(bot) => authorize_bot_management(caller_actor_id.as_deref(), &bot)?,
            None if caller_actor_id.as_deref() == Some(bot_id.as_str()) => {}
            None => return Err(ServiceError::BotNotFound(bot_id).into()),
        }

        let updated = self.registry.update_status(&bot_id, status.clone()).await;

        Ok(BotRuntimeStatusOutcome {
            updated,
            bot_uuid: bot_id,
            status,
        })
    }

    async fn disconnect_streaming(
        &self,
        command: BotRuntimeDisconnectCommand,
    ) -> Result<(), BotUseCaseError> {
        self.registry.disconnect_streaming(&command.bot_id).await;
        Ok(())
    }

    async fn is_provider_downlink_bot(&self, bot_id: &str) -> ServiceResult<bool> {
        let Some(bot_core) = self.bot_core.as_ref() else {
            return Ok(false);
        };
        let Some(bindings) = bot_core.provider_bindings_repo() else {
            return Ok(false);
        };
        let binding = bindings.get_binding_by_bot_uuid(bot_id).await?;
        Ok(binding.is_some_and(|binding| !binding.disabled))
    }

    async fn resolve_delivery_target(&self, bot_id: &str) -> ServiceResult<BotDeliveryTarget> {
        self.registry.resolve_delivery_target(bot_id).await
    }
}

impl Bot {
    async fn discover_organization_bots(
        &self,
        organization_code: &str,
        command: BotDiscoveryCommand,
    ) -> Result<BotDiscoveryResult, BotUseCaseError> {
        let requester = command.requester_bot_id.as_deref().ok_or_else(|| {
            BotUseCaseError::Forbidden("organization discovery requires a bot caller".to_string())
        })?;
        let organization = self.organization.as_ref().ok_or_else(|| {
            ServiceError::InvalidOperation {
                message: "organization service is not configured".to_string(),
                request_id: None,
            }
        })?;
        organization
            .require_effective_member(organization_code, requester)
            .await?;
        let members = organization
            .list_effective_members(organization_code, command.role.as_deref())
            .await?;
        let member_by_bot = members
            .iter()
            .map(|member| (member.bot_uuid.clone(), member.clone()))
            .collect::<BTreeMap<_, _>>();
        let bot_ids = members
            .iter()
            .map(|member| member.bot_uuid.clone())
            .collect::<Vec<_>>();
        let bots = self.registry.get_by_ids(&bot_ids).await;
        let mut entries = Vec::new();
        for bot in bots {
            if bot.actor_kind != ActorKind::Bot || bot.capabilities.name.is_none() {
                continue;
            }
            if !matches_discovery_selector(&bot, &command) {
                continue;
            }
            let visibility = bot.capabilities.visibility.clone();
            if let Some(visibility_filter) = command.visibility.as_deref() {
                if visibility != visibility_filter {
                    continue;
                }
            }
            let is_friend = self.friend.are_friends(requester, &bot.bot_uuid).await;
            if !is_organization_discover_visible(&visibility) && !is_friend {
                continue;
            }
            let Some(member) = member_by_bot.get(&bot.bot_uuid) else {
                continue;
            };
            entries.push(BotDiscoveryEntry {
                bot_uuid: bot.bot_uuid,
                capabilities: bot.capabilities,
                visibility,
                is_friend: Some(is_friend),
                agent_code: None,
                provider_info: None,
                organization_member: Some(OrganizationMemberSummary {
                    organization_code: organization_code.to_string(),
                    role: member.role.clone(),
                }),
            });
        }
        let mut entries_with_agent_code = Vec::with_capacity(entries.len());
        for mut entry in entries {
            entry.agent_code = self
                .registry
                .get_agent_credentials(&entry.bot_uuid)
                .await
                .and_then(|credentials| credentials.agent_code);
            entries_with_agent_code.push(entry);
        }
        Ok(BotDiscoveryResult {
            count: entries_with_agent_code.len(),
            bots: entries_with_agent_code,
        })
    }

    async fn validate_connect_bot_id(&self, bot_id: &str) -> Result<(), BotUseCaseError> {
        if !bot_id.starts_with("human_") {
            return Ok(());
        }

        match self.registry.get(bot_id).await {
            Some(existing) if existing.actor_kind == ActorKind::Human => Ok(()),
            _ => Err(BotUseCaseError::InvalidBotId(
                "human_ 前缀仅用于 Human Actor".to_string(),
            )),
        }
    }

    async fn is_provider_managed_bot(&self, bot_id: &str) -> Result<bool, BotUseCaseError> {
        let Some(bot_core) = self.bot_core.as_ref() else {
            return Ok(false);
        };
        let Some(bindings) = bot_core.provider_bindings_repo() else {
            return Ok(false);
        };
        Ok(bindings.get_binding_by_bot_uuid(bot_id).await?.is_some())
    }

    async fn bot_to_detail(&self, bot: RegisteredBot) -> BotDetailResult {
        let visibility = bot.capabilities.visibility.clone();
        let created_by = bot.created_by.clone();
        let dynamic_status = effective_dynamic_status(self.registry.as_ref(), &bot).await;

        BotDetailResult {
            bot_uuid: bot.bot_uuid,
            capabilities: bot.capabilities,
            status: bot.status,
            visibility,
            owner_actor_id: owner_actor_id(created_by.clone()),
            created_by,
            actor_kind: bot.actor_kind,
            env: bot.env,
            dynamic_status,
        }
    }

    async fn bot_page_from_registered(
        &self,
        bots: Vec<RegisteredBot>,
        offset: u64,
        limit: u64,
    ) -> Result<BotPagedListResult, BotUseCaseError> {
        let total = bots.len() as u64;
        let page = bots
            .into_iter()
            .skip(to_usize(offset))
            .take(to_usize(limit))
            .collect::<Vec<_>>();
        let mut items = Vec::with_capacity(page.len());
        for bot in page {
            items.push(self.bot_to_query_entry(bot).await);
        }
        Ok(BotPagedListResult {
            items,
            total,
            offset,
            limit,
        })
    }

    async fn my_bot_page_from_registered(
        &self,
        bots: Vec<RegisteredBot>,
        offset: u64,
        limit: u64,
        active_only: bool,
    ) -> Result<BotPagedListResult, BotUseCaseError> {
        let mut entries = Vec::with_capacity(bots.len());
        let bot_uuids = bots
            .iter()
            .map(|bot| bot.bot_uuid.clone())
            .collect::<Vec<_>>();
        let active_bot_ids = self
            .registry
            .list_runtime_active_bot_ids(&bot_uuids)
            .await
            .into_iter()
            .collect::<std::collections::HashSet<_>>();
        for bot in bots {
            let is_active = active_bot_ids.contains(&bot.bot_uuid);
            if active_only && !is_active {
                continue;
            }
            let bot_uuid = bot.bot_uuid.clone();
            entries.push((is_active, bot_uuid, Self::bot_to_my_query_entry(bot, is_active)));
        }
        entries.sort_by(|a, b| b.0.cmp(&a.0).then_with(|| a.1.cmp(&b.1)));
        let total = entries.len() as u64;
        let items = entries
            .into_iter()
            .skip(to_usize(offset))
            .take(to_usize(limit))
            .map(|(_, _, entry)| entry)
            .collect();

        Ok(BotPagedListResult {
            items,
            total,
            offset,
            limit,
        })
    }

    fn bot_to_my_query_entry(bot: RegisteredBot, is_active: bool) -> BotQueryEntry {
        let visibility = bot.capabilities.visibility.clone();
        BotQueryEntry {
            bot_uuid: bot.bot_uuid,
            capabilities: bot.capabilities,
            visibility,
            status: bot.status,
            actor_kind: bot.actor_kind,
            env: bot.env,
            dynamic_status: DynamicStatusResponse {
                status: if is_active { "active" } else { "offline" }.to_string(),
            },
            created_by: bot.created_by,
        }
    }

    async fn bot_to_query_entry(&self, bot: RegisteredBot) -> BotQueryEntry {
        let visibility = bot.capabilities.visibility.clone();
        let dynamic_status = effective_dynamic_status(self.registry.as_ref(), &bot).await;
        BotQueryEntry {
            bot_uuid: bot.bot_uuid,
            capabilities: bot.capabilities,
            visibility,
            status: bot.status,
            actor_kind: bot.actor_kind,
            env: bot.env,
            dynamic_status,
            created_by: bot.created_by,
        }
    }

    async fn discover_candidates(&self, command: &BotDiscoveryCommand) -> Vec<DiscoveryCandidate> {
        let mut merged = BTreeMap::new();
        for bot in self.registry.list_active().await {
            merged.insert(
                bot.bot_uuid.clone(),
                DiscoveryCandidate {
                    bot,
                    provider_info: None,
                },
            );
        }

        for candidate in self.discover_provider_bots(command).await {
            merged.insert(candidate.bot.bot_uuid.clone(), candidate);
        }

        merged.into_values().collect()
    }

    async fn discover_provider_bots(&self, command: &BotDiscoveryCommand) -> Vec<DiscoveryCandidate> {
        let Some(bot_core) = self.bot_core.as_ref() else {
            return Vec::new();
        };
        let Some(provider_bindings) = bot_core.provider_bindings_repo() else {
            return Vec::new();
        };
        let selector = provider_discovery_selector(command);
        let query_started_at = std::time::Instant::now();
        let records_result = provider_bindings
            .list_discoverable_provider_bot_records(&selector)
            .await;
        let elapsed_ms = query_started_at.elapsed().as_millis();
        let records = match records_result {
            Ok(records) => {
                tracing::info!(
                    elapsed_ms = %elapsed_ms,
                    record_count = records.len(),
                    selector = ?selector,
                    "discover_provider_bots: listed provider bot records"
                );
                records
            }
            Err(error) => {
                tracing::warn!(
                    elapsed_ms = %elapsed_ms,
                    selector = ?selector,
                    error = %error,
                    "discover_provider_bots: failed to list provider bot records"
                );
                return Vec::new();
            }
        };
        if records.is_empty() {
            return Vec::new();
        }

        let bot_ids = records
            .iter()
            .map(|record| record.bot_uuid.clone())
            .collect::<Vec<_>>();
        let mut provider_info_by_bot = records
            .into_iter()
            .map(|record| {
                (
                    record.bot_uuid,
                    BotDiscoveryProviderInfo {
                        provider_id: record.provider_id,
                        provider_name: record.provider_name,
                    },
                )
            })
            .collect::<BTreeMap<_, _>>();

        self.registry
            .get_by_ids(&bot_ids)
            .await
            .into_iter()
            .filter(|bot| bot.actor_kind == ActorKind::Bot)
            .filter_map(|bot| {
                provider_info_by_bot
                    .remove(&bot.bot_uuid)
                    .map(|provider_info| DiscoveryCandidate {
                        bot,
                        provider_info: Some(provider_info),
                    })
            })
            .collect()
    }
}

fn is_in_list_bots_scope(bot: &RegisteredBot, onboarded: Option<bool>) -> bool {
    match onboarded {
        Some(false) => bot.capabilities.name.is_none(),
        _ => {
            if bot.bot_uuid.contains("default") {
                bot.capabilities
                    .summary
                    .as_ref()
                    .is_some_and(|summary| !summary.is_empty())
            } else {
                bot.capabilities.name.is_some()
            }
        }
    }
}

fn bot_to_list_entry(bot: RegisteredBot) -> BotListEntry {
    let name = bot.capabilities.name.clone();
    let summary = bot.capabilities.summary.clone();
    let visibility = bot.capabilities.visibility.clone();
    let created_by = bot.created_by.clone();

    BotListEntry {
        bot_uuid: bot.bot_uuid,
        name,
        summary,
        capabilities: bot.capabilities,
        status: bot.status,
        visibility,
        owner_actor_id: owner_actor_id(created_by.clone()),
        created_by,
    }
}

struct DiscoveryCandidate {
    bot: RegisteredBot,
    provider_info: Option<BotDiscoveryProviderInfo>,
}

fn discover_entry(
    candidate: DiscoveryCandidate,
    command: &BotDiscoveryCommand,
    friend_uuids: Option<&Vec<String>>,
) -> Option<BotDiscoveryEntry> {
    let DiscoveryCandidate { bot, provider_info } = candidate;
    let visibility = bot.capabilities.visibility.clone();
    if !is_discover_visible(&visibility) {
        return None;
    }

    if let Some(visibility_filter) = command.visibility.as_deref() {
        if visibility != visibility_filter {
            return None;
        }
    }

    let is_friend = if let Some(friends) = friend_uuids {
        let is_friend = friends.contains(&bot.bot_uuid);
        if command.visibility.is_none() && visibility != "public" && !is_friend {
            return None;
        }
        Some(is_friend)
    } else {
        None
    };

    Some(BotDiscoveryEntry {
        bot_uuid: bot.bot_uuid,
        capabilities: bot.capabilities,
        visibility,
        is_friend,
        agent_code: None,
        provider_info,
        organization_member: None,
    })
}

fn is_discover_visible(visibility: &str) -> bool {
    matches!(visibility, "public" | "protected")
}

fn is_organization_discover_visible(visibility: &str) -> bool {
    matches!(visibility, "public" | "protected")
}

fn provider_discovery_selector(command: &BotDiscoveryCommand) -> ProviderBotDiscoverySelector {
    if let Some(q) = command.q.as_deref() {
        ProviderBotDiscoverySelector::Query(q.to_string())
    } else if let Some(name) = command.name.as_deref() {
        ProviderBotDiscoverySelector::Name(name.to_string())
    } else if let Some(skills_str) = command.skills.as_deref() {
        ProviderBotDiscoverySelector::Skills(split_csv_strings(skills_str))
    } else if let Some(domains_str) = command.domains.as_deref() {
        ProviderBotDiscoverySelector::Domains(split_csv_strings(domains_str))
    } else if let Some(scopes_str) = command.scopes.as_deref() {
        ProviderBotDiscoverySelector::Scopes(split_csv_strings(scopes_str))
    } else {
        ProviderBotDiscoverySelector::All
    }
}

fn matches_discovery_selector(bot: &RegisteredBot, command: &BotDiscoveryCommand) -> bool {
    if let Some(q) = command.q.as_deref() {
        matches_query(bot, q)
    } else if let Some(name) = command.name.as_deref() {
        bot.capabilities
            .name
            .as_deref()
            .is_some_and(|value| contains_ignore_case(value, name))
    } else if let Some(skills_str) = command.skills.as_deref() {
        split_csv(skills_str).iter().all(|skill| {
            bot.capabilities
                .skills
                .iter()
                .any(|candidate| contains_ignore_case(&candidate.name, skill))
        })
    } else if let Some(domains_str) = command.domains.as_deref() {
        split_csv(domains_str).iter().all(|domain| {
            bot.capabilities
                .domains
                .iter()
                .any(|candidate| contains_ignore_case(candidate, domain))
        })
    } else if let Some(scopes_str) = command.scopes.as_deref() {
        split_csv(scopes_str).iter().all(|scope| {
            bot.capabilities
                .scopes
                .iter()
                .any(|candidate| contains_ignore_case(candidate, scope))
        })
    } else {
        true
    }
}

fn matches_query(bot: &RegisteredBot, query: &str) -> bool {
    bot.capabilities
        .name
        .as_deref()
        .is_some_and(|value| contains_ignore_case(value, query))
        || bot
            .capabilities
            .summary
            .as_deref()
            .is_some_and(|value| contains_ignore_case(value, query))
        || bot
            .dynamic_status
            .dynamic_summary
            .as_deref()
            .is_some_and(|value| contains_ignore_case(value, query))
        || bot
            .capabilities
            .domains
            .iter()
            .any(|value| contains_ignore_case(value, query))
        || bot
            .capabilities
            .skills
            .iter()
            .any(|skill| contains_ignore_case(&skill.name, query))
        || bot
            .capabilities
            .scopes
            .iter()
            .any(|value| contains_ignore_case(value, query))
        || contains_ignore_case(&bot.bot_uuid, query)
}

fn contains_ignore_case(value: &str, query: &str) -> bool {
    value.to_lowercase().contains(&query.to_lowercase())
}

fn split_csv(value: &str) -> Vec<&str> {
    value.split(',').map(str::trim).collect()
}

fn split_csv_strings(value: &str) -> Vec<String> {
    split_csv(value).into_iter().map(str::to_string).collect()
}

fn non_empty_text(value: Option<&str>) -> Option<String> {
    value
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
}

fn created_by_from_provider_bot_ref(provider_bot_ref: &str) -> Option<String> {
    provider_bot_ref
        .rsplit_once(':')
        .map(|(_, owner)| owner.trim())
        .filter(|owner| !owner.is_empty())
        .map(str::to_string)
}

fn owner_from_provider_bot_ref(provider_bot_ref: &str) -> Result<String, BotUseCaseError> {
    created_by_from_provider_bot_ref(provider_bot_ref).ok_or_else(|| {
        BotUseCaseError::InvalidProviderBotRef(
            "provider_bot_ref must include owner staff_no".to_string(),
        )
    })
}

async fn effective_dynamic_status(
    registry: &dyn BotRegistryCoreService,
    bot: &RegisteredBot,
) -> DynamicStatusResponse {
    let is_active =
        bot.status == ActorStatus::Online && registry.is_effectively_online(&bot.bot_uuid).await;
    DynamicStatusResponse {
        status: if is_active { "active" } else { "offline" }.to_string(),
    }
}

fn owner_actor_id(created_by: Option<String>) -> Option<String> {
    created_by.map(|owner| {
        if owner.starts_with("human_") {
            owner
        } else {
            format!("human_{owner}")
        }
    })
}

fn is_valid_visibility(visibility: &str) -> bool {
    matches!(visibility, "public" | "protected" | "private")
}

fn normalized_visibility(visibility: &str) -> &'static str {
    match visibility {
        "public" => "public",
        "protected" => "protected",
        _ => "private",
    }
}

fn authorize_visibility_read(
    caller_actor_id: Option<&str>,
    bot: &RegisteredBot,
) -> Result<(), BotUseCaseError> {
    let Some(caller_actor_id) = caller_actor_id else {
        return Ok(());
    };

    if caller_actor_id == bot.bot_uuid {
        return Ok(());
    }

    if let Some(staff_no) = caller_actor_id.strip_prefix("human_") {
        if bot
            .created_by
            .as_deref()
            .is_some_and(|owner| owner != staff_no)
        {
            return Err(BotUseCaseError::Forbidden(format!(
                "Not authorized to access bot '{}'",
                bot.bot_uuid
            )));
        }
        return Ok(());
    }

    match bot.capabilities.visibility.as_str() {
        "public" | "protected" => Ok(()),
        _ => Err(ServiceError::BotNotFound(bot.bot_uuid.clone()).into()),
    }
}

fn authorize_human_creator_required(
    staff_no: &str,
    bot: &RegisteredBot,
) -> Result<(), BotUseCaseError> {
    match bot.created_by.as_deref() {
        Some(owner) if owner == staff_no => Ok(()),
        _ => Err(BotUseCaseError::Forbidden(format!(
            "User {} is not the creator of bot {}",
            staff_no, bot.bot_uuid
        ))),
    }
}

fn is_owner_suffixed_bot_id_for_staff(bot_uuid: &str, staff_no: &str) -> bool {
    bot_uuid
        .rsplit_once(':')
        .is_some_and(|(_, suffix)| suffix == staff_no)
}

fn authorize_bot_management(
    caller_actor_id: Option<&str>,
    bot: &RegisteredBot,
) -> Result<(), BotUseCaseError> {
    let Some(caller_actor_id) = caller_actor_id else {
        return Err(BotUseCaseError::Unauthorized(format!(
            "caller identity is required to modify bot '{}'",
            bot.bot_uuid
        )));
    };

    if caller_actor_id == bot.bot_uuid {
        return Ok(());
    }

    let Some(owner_staff_no) = bot.created_by.as_deref() else {
        return Err(BotUseCaseError::Forbidden(format!(
            "caller '{}' is not the owner of bot '{}'",
            caller_actor_id, bot.bot_uuid
        )));
    };

    if caller_actor_id == owner_staff_no
        || caller_actor_id.strip_prefix("human_") == Some(owner_staff_no)
    {
        return Ok(());
    }

    Err(BotUseCaseError::Forbidden(format!(
        "caller '{}' is not the owner of bot '{}'",
        caller_actor_id, bot.bot_uuid
    )))
}

fn to_usize(value: u64) -> usize {
    usize::try_from(value).unwrap_or(usize::MAX)
}

fn now_ms() -> u64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}
