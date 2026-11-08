#[path = "bot_runtime.rs"]
mod runtime;
#[path = "bot_helpers.rs"]
mod helpers;
use helpers::*;

use std::collections::{BTreeMap, HashSet};
use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::port::bot_authority::BotAuthorityPort;
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
    registry: Arc<dyn BotRegistryCoreService>,
    friend: Arc<dyn FriendCoreService>,
    edge_grants: Option<Arc<dyn bcs_service_api::port::repo::EdgeGrantRepoPort>>,
    bot_core: Option<Arc<BotCore>>,
    control_plane: Option<Arc<dyn BotControlPlaneCoreService>>,
    relation: Option<Arc<dyn RelationCoreService>>,
    user_directory: Option<Arc<dyn UserDirectoryPlugin>>,
    connection_control: Option<Arc<dyn BotConnectionControlPort>>,
    organization: Option<Arc<dyn OrganizationCoreService>>,
    /// The narrow authority hook (bot-authority plan
    /// Task 6.3): the Human 删除/配置/状态 control
    /// judgments consult it live; a missing wiring
    /// fails closed.
    bot_authority: Option<Arc<dyn BotAuthorityPort>>,
    /// Migration-window compat (spec §10.2a): `/bots/my`
    /// also surfaces live physical Bots whose stored
    /// `created_by` byte-matches the caller while their
    /// ownership is still uninitialized. Strict posture
    /// pins it off (default).
    legacy_created_by_fallback: bool,
    uplink: bcs_config_api::UplinkConfig,
}

impl Bot {
    async fn validate_connect_identity(&self, caller: Option<&str>, token: Option<&str>, target: Option<&str>) -> Result<(), BotUseCaseError> {
        let expected = caller.filter(|id| !id.starts_with("human_"));
        if expected.is_some_and(|id| target.is_some_and(|target| target != id)) {
            return Err(BotUseCaseError::Unauthorized("Connect Bot does not match authenticated Actor".into()));
        }
        let resolved = if let Some(token) = token {
            self.registry.resolve_bot_token(token, expected).await?
        } else { None };
        if let Some(bot) = resolved {
            if target.is_some_and(|target| target != bot.bot.bot_id) {
                return Err(BotUseCaseError::Unauthorized("Connect token and Bot UUID do not match".into()));
            }
        } else if expected.is_some() {
            return Err(BotUseCaseError::Unauthorized("Connect requires this Bot's static token".into()));
        }
        Ok(())
    }
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
            bot_authority: None,
            legacy_created_by_fallback: false,
            uplink: Default::default(),
        }
    }

    /// Wire the §10.2a compat posture the composition
    /// root resolved for this process: ON only inside
    /// the declared migration window, OFF (default)
    /// everywhere else. `/bots/my` consults it per call.
    pub fn with_legacy_created_by_fallback(
        mut self,
        enabled: bool,
    ) -> Self {
        self.legacy_created_by_fallback = enabled;
        self
    }

    /// Wire the authority hook (bot-authority plan
    /// Task 6.3); 删除/配置/状态 judgments consult it.
    pub fn with_bot_authority(
        mut self,
        bot_authority: Arc<dyn BotAuthorityPort>,
    ) -> Self {
        self.bot_authority = Some(bot_authority);
        self
    }

    /// The wired authority hook; failing to wire one is
    /// a composition error and must fail closed (never
    /// deny-as-Ok).
    fn authority(
        &self,
    ) -> Result<&dyn BotAuthorityPort, BotUseCaseError> {
        self.bot_authority.as_deref().ok_or_else(|| {
            BotUseCaseError::Service(ServiceError::InternalError(
                "bot authority hook is not wired".to_string(),
            ))
        })
    }

    /// 删除/配置/状态 control judgment (bot-authority plan
    /// Task 6.3, spec §8): these are Bot BUSINESS operations
    /// ("Bot 配置、状态、删除 … 使用当前角色"), so the gate
    /// admits the CURRENT ROLE — owner OR manager
    /// (`require_manage`, spec §1 goal 1: manager 获得
    /// 相同的 Bot 业务操作权限); the historical
    /// `created_by` (and the unowned-bot legacy) no longer
    /// authorize (spec §3.3: 旧创建者在失去 manager 后不再
    /// 有特权). Only the ownership-transfer commands stay
    /// owner-exclusive. `caller_actor_id` may be the Bot
    /// itself (its own runtime contract) or a Human actor id.
    async fn authorize_current_role(
        &self,
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
        let staff_no = caller_actor_id
            .strip_prefix("human_")
            .unwrap_or(caller_actor_id);
        match self
            .authority()?
            .require_manage(staff_no, &bot.bot_uuid)
            .await
        {
            Ok(()) => Ok(()),
            Err(ServiceError::Forbidden(message)) => {
                Err(BotUseCaseError::Forbidden(message))
            }
            Err(other) => Err(BotUseCaseError::Service(
                ServiceError::InternalError(format!(
                    "bot authority lookup failed: {other}"
                )),
            )),
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
        authorize_visibility_read(
            command.caller_actor_id.as_deref(),
            &bot,
            self.bot_authority.as_deref(),
        )
        .await?;

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

        authorize_visibility_read(
            command.caller_actor_id.as_deref(),
            &bot,
            self.bot_authority.as_deref(),
        )
        .await?;

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
        // mine / legacy `/bots/my` (spec §8
        // 首行): the owner/manager UNION from the
        // control plane's controllable read —
        // the literal `list_bots_by_creator` is
        // history and no longer feeds this
        // entry (bot-authority plan Task 6.3,
        // ruling T6c-1). Ruling T6d-3: the
        // authenticated Human's SELF entry keeps
        // its self semantics on this page (the
        // trusted onboarded Human actor row is
        // appended below).
        if let Some(control_plane) = self.control_plane.as_ref() {
            let candidates = control_plane
                .list_controllable(
                    bcs_service_api::ControllableCandidateQuery {
                        user_id: command.staff_no.clone(),
                        env: bcs_config::resolve_env_str(),
                        kind: None,
                        name: None,
                        status: None,
                        include_legacy_creator: self
                            .legacy_created_by_fallback,
                    },
                )
                .await
                .map_err(BotUseCaseError::Service)?;
            // Per-item relation sampling (spec §8
            // mine row: 逐项
            // `access_relation=owner|manager`; the
            // Human self entry keeps `self`).
            let mut relations: std::collections::HashMap<
                String,
                String,
            > = std::collections::HashMap::new();
            // Hydrate the Bot-kind candidates with ONE strict
            // batch read (legacy-mine ruling): a store failure
            // is an Err to the caller — never a page quietly
            // missing entries.
            let mut hydrate_ids: Vec<String> = Vec::new();
            for candidate in &candidates {
                if candidate.kind != ActorKind::Bot {
                    continue;
                }
                relations.insert(
                    candidate.id.clone(),
                    match candidate.access_relation {
                        bcs_service_api::types::bot_authority::BotAccessRelation::Owner => {
                            "owner"
                        }
                        bcs_service_api::types::bot_authority::BotAccessRelation::Manager => {
                            "manager"
                        }
                    }
                    .to_string(),
                );
                hydrate_ids.push(candidate.id.clone());
            }
            let mut bots = self
                .registry
                .try_get_many(&hydrate_ids)
                .await
                .map_err(BotUseCaseError::Service)?;
            // Human SELF entry (spec §8 first row):
            // the trusted onboarded Human actor row
            // lives in the SAME registry; the legacy
            // page kept it alongside the union. It is
            // self-visible by construction — the page
            // only lists this caller's union and this
            // caller's own row; no Human→Human edges
            // exist.
            let human_actor_id =
                format!("human_{}", command.staff_no);
            if !bots
                .iter()
                .any(|bot| bot.bot_uuid == human_actor_id)
            {
                if let Some(human) =
                    self.registry.get(&human_actor_id).await
                {
                    if human.actor_kind == ActorKind::Human
                        && human
                            .created_by
                            .as_deref()
                            == Some(command.staff_no.as_str())
                    {
                        bots.push(human);
                        // Spec §8 mine row: "Human
                        // 自身条目保持 self 语义".
                        relations.insert(
                            human_actor_id.clone(),
                            "self".to_string(),
                        );
                    }
                }
            }
            return self
                .my_bot_page_from_registered(
                    bots,
                    command.offset,
                    command.limit,
                    command.active_only,
                    relations,
                )
                .await;
        }
        // Legacy fallback for unwired test
        // harnesses: without the control plane
        // the union read is impossible, so the
        // historical behavior stands (no
        // sampled relations exist to expose).
        let bots = self.registry.list_bots_by_creator(&command.staff_no).await;
        self.my_bot_page_from_registered(
            bots,
            command.offset,
            command.limit,
            command.active_only,
            std::collections::HashMap::new(),
        )
            .await
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
    async fn search_bots_via_control_plane(
        &self,
        control_plane: &dyn BotControlPlaneCoreService,
        command: SearchBotsCommand,
    ) -> Result<BotSearchResult, BotUseCaseError> {
        let viewer_actor_id = command.viewer_actor_id.as_deref();
        let friend_ids = if let Some(viewer_actor_id) = viewer_actor_id {
            if let Some(edge_grants) = self.edge_grants.as_ref() {
                edge_grants
                    .list_friends(viewer_actor_id, &bcs_config::resolve_env_str())
                    .await
                    .into_iter()
                    .collect::<HashSet<_>>()
            } else {
                self.friend
                    .list_friends(viewer_actor_id)
                    .await
                    .into_iter()
                    .collect::<HashSet<_>>()
            }
        } else {
            HashSet::new()
        };
        let friendship = command.friendship.unwrap_or(BotSearchFriendshipFilter::All);
        if !matches!(friendship, BotSearchFriendshipFilter::All) && viewer_actor_id.is_none() {
            return Err(ServiceError::InvalidOperation {
                message: "friendship filter requires viewer_actor_id".to_string(),
                request_id: None,
            }
            .into());
        }

        let query = BotSearchCandidateQuery {
            acting_bot_id: command.requester_actor_id.clone().unwrap_or_default(),
            env: bcs_config::resolve_env_str(),
            visibility: bcs_service_api::BotCandidateVisibility::Discovery,
            friend_ids,
            bot_uuids: command.bot_uuids.clone(),
            name: command.q.clone(),
            q: command.q.clone(),
            visibility_filter: command.visibility.clone(),
            user_visibility: command.user_visibility.clone(),
            status: command.status,
            friendship: Some(friendship),
            tc_bot: command.tc_bot,
            offset: command.offset,
            limit: command.limit,
        };
        let (candidates, total) = control_plane.search_candidates(query).await?;
        let candidate_bot_ids = candidates
            .iter()
            .map(|candidate| candidate.bot.record.bot_id.clone())
            .collect::<Vec<_>>();
        let active_bot_ids = self
            .registry
            .list_runtime_active_bot_ids(&candidate_bot_ids)
            .await
            .into_iter()
            .collect::<HashSet<_>>();

        let mut items = Vec::with_capacity(candidates.len());
        for candidate in candidates {
            let record = candidate.bot.record;
            let capabilities = bot_capabilities_from_record(&record);
            let bot_uuid = record.bot_id;
            let dynamic_status = if record.status == ActorStatus::Online
                && active_bot_ids.contains(&bot_uuid)
            {
                DynamicStatusResponse {
                    status: "active".to_string(),
                }
            } else {
                DynamicStatusResponse {
                    status: "offline".to_string(),
                }
            };
            items.push(BotQueryEntry {
                bot_uuid,
                capabilities,
                visibility: record.visibility,
                status: record.status,
                actor_kind: record.kind,
                env: Some(record.env),
                dynamic_status,
                created_by: record.created_by,
                user_visibility: user_visibility_to_wire(record.user_visibility).to_string(),
                friend_ext: record.friend_ext,
                friend_check_in_strategy: friend_check_in_strategy_to_wire(
                    record.friend_check_in_strategy,
                )
                .to_string(),
                is_friend: viewer_actor_id.map(|_| candidate.is_friend),
                access_relation: None,
            });
        }

        Ok(BotSearchResult { items, total })
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
            .filter(|candidate| {
                command.requester_bot_id.as_deref() != Some(candidate.bot.bot_uuid.as_str())
            })
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
        self.validate_connect_identity(command.caller_actor_id.as_deref(), command.token.as_deref(), command.bot_id.as_deref()).await?;
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
            Some(bot) => {
                self.authorize_current_role(caller_actor_id.as_deref(), &bot)
                    .await?
            }
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
        self.authorize_current_role(caller_actor_id.as_deref(), &bot)
            .await?;

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
                "bot delete requires a human identity".to_string(),
            )
        })?;
        if command.human_actor_id.as_deref() != Some(caller) {
            return Err(BotUseCaseError::Forbidden(
                "bot delete requires matching human identity".to_string(),
            ));
        }

        let bot = self
            .registry
            .get(&command.bot_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(command.bot_id.clone()))?;
        // 删除 (spec §8: "Bot 配置、状态、删除、onboard |
        // 使用当前角色"): a BUSINESS operation, so the gate
        // admits the current OWNER OR MANAGER (`require_manage`
        // — spec §1 goal 1); the historical `created_by`
        // (spec §3.3) and the ownership-transfer commands
        // change nothing here. `bot` also feeds the INTRINSIC
        // TC-origin classification below.
        match self
            .authority()?
            .require_manage(staff_no, &command.bot_id)
            .await
        {
            Ok(()) => {}
            Err(ServiceError::Forbidden(message)) => {
                return Err(BotUseCaseError::Forbidden(message));
            }
            Err(other) => {
                return Err(BotUseCaseError::Service(ServiceError::InternalError(
                    format!("bot authority lookup failed: {other}"),
                )));
            }
        }

        // F5: the TC-origin guard classifies from the BOT's OWN
        // persisted `created_by` — never the requester's staff id.
        // A manager (or a new owner after transfer) must not be
        // able to retire a TC-origin Bot through this endpoint.
        if is_legacy_tc_origin_bot(&bot) {
            return Err(BotUseCaseError::Forbidden(
                "TC bot must be deleted from TC".to_string(),
            ));
        }

        if self.is_provider_managed_bot(&command.bot_id).await? {
            return Err(BotUseCaseError::Forbidden(
                "provider-managed bot must be deleted from provider side".to_string(),
            ));
        }

        // Ordinary Bot retirement (bot-authority spec
        // §7.1): ONE atomic store command — the Bot
        // tombstone, the owner/manager edge revocation,
        // the pending-transfer closure (fixed system
        // marker) and the `retire` audit carrying the
        // REAL initiating Human. Never an ordinary
        // delete chained behind anything.
        let mutation = bcs_service_api::types::bot_authority::AuthorityMutation {
            mutation_id: format!("retire_{}", uuid::Uuid::new_v4().simple()),
            actor: bcs_service_api::types::bot_authority::AuthorityActor::Human {
                user_id: staff_no.to_string(),
            },
        };
        let left = self
            .registry
            .retire_with_authority(&command.bot_id, mutation)
            .await?;
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
