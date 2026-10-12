//! Bot runtime-connection lane and shared DTO projections (plan Task 12
//! fix round, behavior-preserving move out of the former over-limit
//! `bot.rs`): streaming connect, runtime status/disconnect, provider
//! downlink resolution, plus the registry→DTO projection helpers the query
//! and management lanes call through `pub(crate)` methods.

use async_trait::async_trait;

use bcs_service_api::{
    ActorKind, ActorStatus, BotCapabilities, BotControlPlaneRecord, BotConnectParams, BotDeliveryTarget,
    BotDetailCommand, BotDetailResult, BotPagedListResult, BotQueryEntry, BotRuntimeConnectCommand,
    BotRuntimeConnectOutcome, BotRuntimeConnectionService, BotRuntimeDisconnectCommand,
 BotRuntimeStatusCommand, BotRuntimeStatusOutcome,
    BotUseCaseError, ConnectionKind, DynamicStatusResponse, FriendCheckInStrategy, RegisteredBot,
    ServiceError, ServiceResult, UserVisibility,
};

use crate::application::bot::{
    bot_capabilities_from_record, effective_dynamic_status,
 friend_check_in_strategy_to_wire, owner_actor_id,
    to_usize, user_visibility_to_wire, Bot,
};

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

        let requested_client_kind = client_kind
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(|value| value.to_ascii_lowercase());

        let params = BotConnectParams {
            token,
            bot_id,
            protocol_version,
            client_kind: None,
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
        let negotiated_client_kind = self.uplink.negotiate(protocol_version, requested_client_kind);
        self.registry
            .set_bot_info(
                &result.bot_uuid,
                "client_kind",
                negotiated_client_kind.clone(),
            )
            .await;

        let mut outcome = BotRuntimeConnectOutcome::from_connect_result(result);
        outcome.negotiated_client_kind = negotiated_client_kind;
        Ok(outcome)
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
            Some(bot) => {
                let bot_id = bot.bot_uuid.clone();
                self.authorize_management_caller(caller_actor_id.as_deref(), &bot_id, &bot)
                    .await?
            }
            None if caller_actor_id.as_deref() == Some(bot_id.as_str()) => {}
            None => return Err(ServiceError::BotNotFound(bot_id).into()),
        }

        let updated = self.registry.update_status(&bot_id).await;

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
        // Clear the active profile before releasing the streaming slot. Once
        // the slot is released a reconnect may negotiate a new profile, which
        // an older connection's cleanup must never erase.
        self.registry
            .set_bot_info(&command.bot_id, "client_kind", None)
            .await;
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
    pub(crate) async fn validate_connect_bot_id(&self, bot_id: &str) -> Result<(), BotUseCaseError> {
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

    pub(crate) async fn is_provider_managed_bot(&self, bot_id: &str) -> Result<bool, BotUseCaseError> {
        let Some(bot_core) = self.bot_core.as_ref() else {
            return Ok(false);
        };
        let Some(bindings) = bot_core.provider_bindings_repo() else {
            return Ok(false);
        };
        Ok(bindings.get_binding_by_bot_uuid(bot_id).await?.is_some())
    }

    pub(crate) async fn bot_to_detail(&self, bot: RegisteredBot) -> BotDetailResult {
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

    pub(crate) async fn bot_page_from_registered(
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

    /// Project one controllable control-plane record into the legacy
    /// `/bots/my` item shape, carrying the mine access-relation label.
    pub(crate) fn record_to_my_query_entry(
        record: BotControlPlaneRecord,
        is_active: bool,
        access_relation: String,
    ) -> BotQueryEntry {
        let capabilities = BotCapabilities {
            name: (!record.name.is_empty()).then(|| record.name.clone()),
            summary: (!record.descriptor.summary.is_empty())
                .then(|| record.descriptor.summary.clone()),
            domains: record.descriptor.domains.clone(),
            skills: record.descriptor.skills.clone(),
            scopes: record.descriptor.scopes.clone(),
            ..BotCapabilities::default()
        };
        let visibility = record.visibility.clone();
        BotQueryEntry {
            bot_uuid: record.bot_id,
            capabilities,
            visibility,
            status: record.status,
            actor_kind: record.kind,
            env: Some(record.env),
            dynamic_status: DynamicStatusResponse {
                status: if is_active { "active" } else { "offline" }.to_string(),
            },
            created_by: record.created_by,
            user_visibility: user_visibility_to_wire(UserVisibility::Protected).to_string(),
            friend_ext: record.friend_ext,
            friend_check_in_strategy: friend_check_in_strategy_to_wire(
                FriendCheckInStrategy::Approval,
            )
            .to_string(),
            is_friend: None,
            access_relation: Some(access_relation),
        }
    }

    pub(crate) async fn bot_to_query_entry(&self, bot: RegisteredBot) -> BotQueryEntry {
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
            user_visibility: user_visibility_to_wire(UserVisibility::Protected).to_string(),
            friend_ext: serde_json::Map::new(),
            friend_check_in_strategy: friend_check_in_strategy_to_wire(
                FriendCheckInStrategy::Approval,
            )
            .to_string(),
            is_friend: None,
            access_relation: None,
        }
    }
}