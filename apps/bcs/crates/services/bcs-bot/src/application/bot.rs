use std::collections::{BTreeMap, HashSet};
use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::{
    application::v1::{FriendCheckInStrategy, UserVisibility},
    ActorKind, ActorStatus, BotCapabilities, BotConnectCommand, BotConnectParams, BotConnectResult,
    BotConnectionControlPort, BotControlPlaneCoreService,
    BotControlPlaneRecord, BotDeliveryTarget, BotDetailCommand, BotDetailResult,
    BotDiscoveryCommand, BotDiscoveryEntry, BotDiscoveryProviderInfo, BotDiscoveryResult,
    BotDiscoveryService, BotLeaveCommand, BotLeaveResult, BotListCommand, BotListEntry,
    BotListResult, BotManagementService, BotPagedListCommand, BotPagedListResult,
    BotQueryByIdsCommand, BotQueryByIdsResult, BotQueryEntry, BotQueryService,
    BotRegistryCoreService, BotSearchCandidateQuery, BotSearchFriendshipFilter, BotSearchResult,
    OrganizationCoreService, OrganizationMemberSummary, SearchBotsCommand, BotRuntimeConnectCommand,
    BotRuntimeConnectOutcome, BotRuntimeConnectionService, BotRuntimeDisconnectCommand,
    BotRuntimeStatusCommand, BotRuntimeStatusOutcome, BotStatusUpdateCommand,
    BotStatusUpdateResult, BotUseCaseError, BotVisibilityCommand, BotVisibilityQueryCommand,
    BotVisibilityQueryResult, BotVisibilityResult, ConnectionKind,
    DynamicStatusResponse, FriendCoreService, KickReason, ProviderBotBinding,
    ProviderBotDiscoverySelector, RegisteredBot, RelationCoreService, ServiceError, ServiceResult,
    SwitchDeliveryToProviderCommand, SwitchDeliveryToProviderResult,
};
use bcs_user_directory_api::UserDirectoryPlugin;

use crate::core::BotCore;

/// Bot query and management application service backed by the registry port.
#[derive(Clone)]
pub struct Bot {
    pub(crate) registry: Arc<dyn BotRegistryCoreService>,
    pub(crate) friend: Arc<dyn FriendCoreService>,
    pub(crate) edge_grants: Option<Arc<dyn bcs_service_api::port::repo::EdgeGrantRepoPort>>,
    pub(crate) bot_core: Option<Arc<BotCore>>,
    pub(crate) control_plane: Option<Arc<dyn BotControlPlaneCoreService>>,
    pub(crate) relation: Option<Arc<dyn RelationCoreService>>,
    pub(crate) user_directory: Option<Arc<dyn UserDirectoryPlugin>>,
    pub(crate) connection_control: Option<Arc<dyn BotConnectionControlPort>>,
    pub(crate) organization: Option<Arc<dyn OrganizationCoreService>>,
    pub(crate) uplink: bcs_config_api::UplinkConfig,
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
            edge_grants: None,
            bot_core: None,
            control_plane: None,
            relation: None,
            user_directory: None,
            connection_control: None,
            organization: None,
            uplink: Default::default(),
        }
    }

    /// Inject deployment authorization; profile selection is never persisted per Bot.
    pub fn with_uplink_config(mut self, uplink: bcs_config_api::UplinkConfig) -> Self {
        self.uplink = uplink;
        self
    }

    /// Wire the concrete `BotCore` so use cases that need provider-bindings
    /// access or the readiness helper can reach them.
    pub fn with_bot_core(mut self, bot_core: Arc<BotCore>) -> Self {
        self.bot_core = Some(bot_core);
        self
    }

    /// Wire the control-plane core so the optimized bot search path can push
    /// filtering and paging down into the repository instead of loading every row.
    pub fn with_control_plane(
        mut self,
        control_plane: Arc<dyn BotControlPlaneCoreService>,
    ) -> Self {
        self.control_plane = Some(control_plane);
        self
    }

    pub fn with_edge_grants(
        mut self,
        edge_grants: Arc<dyn bcs_service_api::port::repo::EdgeGrantRepoPort>,
    ) -> Self {
        self.edge_grants = Some(edge_grants);
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

        // Plan Task 6 (spec 13.3): a delivery switch is NOT a trusted
        // first-registration context — it never claims ownership and never
        // rewrites owner/manager/version/`created_by` of an initialized Bot.
        // `created_by` is only filled when empty (first-writer-wins); the
        // former unconditional overwrite reset the creator on every switch.
        self.registry
            .save_created_by(bot_id, owner_staff_no, false)
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
                    request_id = %bcs_observability::CurrentRequestId,
                    staff_no = %staff_no,
                    "user directory returned no profile; falling back to staff_no for human actor name"
                );
                staff_no.to_string()
            }
            Err(error) => {
                tracing::warn!(
                    request_id = %bcs_observability::CurrentRequestId,
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
        // Plan Task 12 (spec §7 mine projection + §12.4 cutover): the legacy
        // `/bots/my` lane serves the SAME mine union the v1 facade serves —
        // physical owner ∪ manager edges (deduplicated, `owner` label wins)
        // plus the caller's own Human self row — while keeping ITS legacy
        // client contract: the `active_only` filter and the active-first,
        // id-ascending sort and the exact `/bots/my` item shape (now with
        // the `access_relation` label). `list_bots_by_creator` stays a
        // literal creation-source query and is no longer the permission
        // source: a bot the user merely CREATED (and later lost) does not
        // appear; a bot they currently MANAGE does.
        let control_plane = self.control_plane.as_ref().ok_or_else(|| {
            BotUseCaseError::Service(ServiceError::InvalidOperation {
                message: "Bot is missing BotControlPlaneCoreService wiring; \
                 list_my_bots requires .with_control_plane(...)"
                    .to_string(),
                request_id: None,
            })
        })?;
        let views = control_plane
            .list_controllable(bcs_service_api::BotControllableQuery {
                user_id: command.staff_no.clone(),
                env: bcs_config::resolve_env_str(),
                kind: None,
                name: None,
                status: None,
            })
            .await
            .map_err(|error| BotUseCaseError::Service(error))?;

        // Same legacy reachability semantics: one batched runtime-activity
        // read, then filter/sort/paginate like the old lane.
        let bot_ids = views.iter().map(|view| view.bot.record.bot_id.clone()).collect::<Vec<_>>();
        let active_bot_ids = self
            .registry
            .list_runtime_active_bot_ids(&bot_ids)
            .await
            .into_iter()
            .collect::<HashSet<String>>();
        let mut entries: Vec<(bool, String, BotQueryEntry)> = Vec::with_capacity(views.len());
        for view in views {
            let access_relation = match view.access_relation {
                bcs_service_api::types::BotAccessRelation::Owner => "owner".to_string(),
                bcs_service_api::types::BotAccessRelation::Manager => "manager".to_string(),
            };
            let record = &view.bot.record;
            let bot_uuid = record.bot_id.clone();
            let is_active = active_bot_ids.contains(&record.bot_id);
            if command.active_only && !is_active {
                continue;
            }
            entries.push((
                is_active,
                bot_uuid,
                Self::record_to_my_query_entry(record.clone(), is_active, access_relation),
            ));
        }
        // Preserve the legacy ordering exactly: active entries first, then
        // ascending bot uuid.
        entries.sort_by(|a, b| b.0.cmp(&a.0).then_with(|| a.1.cmp(&b.1)));
        let total = entries.len() as u64;
        let items = entries
            .into_iter()
            .skip(to_usize(command.offset))
            .take(to_usize(command.limit))
            .map(|(_, _, entry)| entry)
            .collect();
        Ok(BotPagedListResult {
            items,
            total,
            offset: command.offset,
            limit: command.limit,
        })
    }

    async fn query_bots_by_ids(
        &self,
        command: BotQueryByIdsCommand,
    ) -> Result<BotQueryByIdsResult, BotUseCaseError> {
        let started = std::time::Instant::now();
        let input_count = command.bot_ids.len();
        let unique_count = command.bot_ids.iter().collect::<std::collections::HashSet<_>>().len();
        let bots = bcs_observability::observe_value("bots.query.batch_load", self
            .registry
            .get_by_ids(&command.bot_ids)).await
            .into_iter()
            .filter(|bot| bot.capabilities.name.is_some())
            .collect::<Vec<_>>();
        let load_ms = started.elapsed().as_secs_f64() * 1000.0;
        let enrich_started = std::time::Instant::now();
        let mut entries = Vec::with_capacity(bots.len());
        for bot in bots {
            entries.push(bcs_observability::observe_value("bots.query.status_enrich", self.bot_to_query_entry(bot)).await);
        }
        tracing::info!(target: "bcs_observation", request_id = %bcs_observability::current_request_id(), input_count, unique_count, returned_count = entries.len(), load_ms, enrich_ms = enrich_started.elapsed().as_secs_f64() * 1000.0, duration_ms = started.elapsed().as_secs_f64() * 1000.0, "bots.query.summary");
        Ok(BotQueryByIdsResult { bots: entries })
    }

    async fn search_bots(
        &self,
        command: SearchBotsCommand,
    ) -> Result<BotSearchResult, BotUseCaseError> {
        let control_plane = self.control_plane.as_ref().ok_or_else(|| {
            BotUseCaseError::Service(ServiceError::InvalidOperation {
                message: "Bot is missing BotControlPlaneCoreService wiring; search_bots requires .with_control_plane(...)".to_string(),
                request_id: None,
            })
        })?;
        self.search_bots_via_control_plane(control_plane.as_ref(), command).await
    }
}

impl Bot {


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

        let updated = self.registry.update_status(&bot_id).await;

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

        // The owner-delete lane is an authority lifecycle act, not a plain
        // cache tombstone: the Bot retires through the governed single-
        // transaction deletion boundary (plan Task 5), which withdraws the
        // Bot's approved owner/manager edges, terminates its PENDING
        // transfers and appends the lifecycle audit in the SAME commit. The
        // former plain soft delete left dangling role edges behind the
        // tombstone, and the strict Task 3 authority reads fail the WHOLE
        // mine union on them — one deleted agent poisoned its former
        // owner's mine (500) and every fail-closed guard lane (403s).
        let left = self
            .registry
            .retire_bot_lifecycle(
                &command.bot_id,
                bcs_service_api::types::BotOperationContext {
                    operation_id: uuid::Uuid::new_v4().to_string(),
                    actor: bcs_service_api::types::BotOperationActor::Human {
                        user_id: staff_no.to_string(),
                        effective_actor_id: caller.to_string(),
                    },
                },
            )
            .await
            .map_err(BotUseCaseError::Service)?;
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

        let bindings = bot_core.provider_bindings_repo().ok_or_else(|| {
            BotUseCaseError::Service(ServiceError::InternalError(
                "provider_bindings repo not configured".to_string(),
            ))
        })?;
        bot_core.validate_provider_switch_membership(&bot_id, &provider_id, &provider_bot_ref, &owner_staff_no).await?;
        let existing = bindings.get_binding_by_bot_uuid(&bot_id).await?;
        let webhook_url = existing.as_ref()
            .filter(|b| b.provider_id == provider_id && b.provider_bot_ref == provider_bot_ref)
            .and_then(|b| b.webhook_url.as_deref());
        match bot_core.assert_provider_ready_for_downlink(&provider_id, webhook_url).await {
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
                    webhook_url: None,
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

impl Bot {

}

pub(crate) fn is_in_list_bots_scope(bot: &RegisteredBot, onboarded: Option<bool>) -> bool {
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

pub(crate) fn contains_ignore_case(value: &str, query: &str) -> bool {
    value.to_lowercase().contains(&query.to_lowercase())
}

pub(crate) fn bot_capabilities_from_record(record: &BotControlPlaneRecord) -> BotCapabilities {
    BotCapabilities {
        name: non_empty_text(Some(record.name.as_str())),
        summary: non_empty_text(Some(record.descriptor.summary.as_str())),
        domains: record.descriptor.domains.clone(),
        skills: record.descriptor.skills.clone(),
        scopes: record.descriptor.scopes.clone(),
        visibility: record.visibility.clone(),
        ..Default::default()
    }
}

pub(crate) fn user_visibility_to_wire(value: UserVisibility) -> &'static str {
    match value {
        UserVisibility::Public => "public",
        UserVisibility::Protected => "protected",
        UserVisibility::Private => "private",
    }
}

pub(crate) fn friend_check_in_strategy_to_wire(value: FriendCheckInStrategy) -> &'static str {
    match value {
        FriendCheckInStrategy::Open => "OPEN",
        FriendCheckInStrategy::Approval => "APPROVAL",
        FriendCheckInStrategy::DeptFree => "DEPT_FREE",
    }
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

pub(crate) async fn effective_dynamic_status(
    registry: &dyn BotRegistryCoreService,
    bot: &RegisteredBot,
) -> DynamicStatusResponse {
    let is_active =
        bot.status == ActorStatus::Online && registry.is_effectively_online(&bot.bot_uuid).await;
    DynamicStatusResponse {
        status: if is_active { "active" } else { "offline" }.to_string(),
    }
}

pub(crate) fn owner_actor_id(created_by: Option<String>) -> Option<String> {
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

pub(crate) fn authorize_bot_management(
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

pub(crate) fn to_usize(value: u64) -> usize {
    usize::try_from(value).unwrap_or(usize::MAX)
}

fn now_ms() -> u64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}
