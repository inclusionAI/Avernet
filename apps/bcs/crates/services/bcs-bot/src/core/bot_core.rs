#[path = "bot_delivery.rs"]
mod delivery;
#[path = "bot_coordination.rs"]
mod coordination;

use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_bot_store::MemoryBotRepo;
use bcs_service_api::lifecycle::{LifecycleError, ServiceLifecycle};
use bcs_service_api::port::repo::{
    BotRepoPort, ProviderBotBindingRepoPort, ProviderCredentialRepoPort, ProviderRepoPort,
};
use bcs_service_api::core::registry::CommittedBotRegistration;
use bcs_service_api::{
    ActorStatus, AgentCredentials, BotCapabilities, BotConnectParams, BotConnectResult,
    BotDeliveryTarget, BotRegistryCoreService, ConnectError,
    ConnectStreamError, ConnectionKind, CoordinationSurface, EnsureHumanResult, RedactedToken,
    RegisteredBot, ServiceError, ServiceResult,
};
use bcs_service_api::types::bot_authority::BotOwnershipView;
use tracing::{debug, info, warn};

use super::ids::{new_bot_uuid, new_session_token};
use super::provider_core::{parse_coordination_config, parse_downlink_config};
use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;
use bcs_service_api::core::registry::RuntimeActivitySnapshot;

/// Unix milliseconds of the current instant — the
/// observation-instant clock of [`BotCore::
/// try_runtime_activity_for`].
fn unix_millis_now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|value| value.as_millis() as u64)
        .unwrap_or(0)
}

/// Core bot registry service implementation.
///
/// `BotCore` owns the core-service boundary and delegates persistence to a
/// repository implementation.
#[derive(Clone)]
pub struct BotCore {
    pub(super) repo: Arc<dyn BotRepoPort>,
    pub(super) provider_repo: Option<Arc<dyn ProviderRepoPort>>,
    provider_credentials: Option<Arc<dyn ProviderCredentialRepoPort>>,
    pub(super) provider_bindings: Option<Arc<dyn ProviderBotBindingRepoPort>>,
    pub(super) bot_providers: Option<Arc<dyn BotProviderRepoPort>>,
    a2a_cards: Option<Arc<dyn bcs_service_api::port::repo::a2a_bot::A2aBotRepoPort>>,
    a2a_url_guard:bcs_route_security::OutboundUrlGuard,
    a2a_grpc_urls:Vec<String>,
}

impl BotCore {
    pub fn new() -> Self {
        Self::memory()
    }

    pub fn memory() -> Self {
        Self::with_repo(Arc::new(MemoryBotRepo::new()))
    }

    pub fn with_base_dir(bots_base_dir: PathBuf) -> Self {
        Self::with_repo(Arc::new(MemoryBotRepo::with_base_dir(bots_base_dir)))
    }

    pub fn with_repo(repo: Arc<dyn BotRepoPort>) -> Self {
        Self {
            repo,
            provider_repo: None,
            provider_credentials: None,
            provider_bindings: None,
            bot_providers: None,
            a2a_cards: None,
            a2a_url_guard:bcs_route_security::OutboundUrlGuard::strict(),
            a2a_grpc_urls:vec![],
        }
    }

    pub fn with_provider_repos(
        repo: Arc<dyn BotRepoPort>,
        provider_repo: Arc<dyn ProviderRepoPort>,
        provider_credentials: Arc<dyn ProviderCredentialRepoPort>,
        provider_bindings: Arc<dyn ProviderBotBindingRepoPort>,
    ) -> Self {
        Self {
            repo,
            provider_repo: Some(provider_repo),
            provider_credentials: Some(provider_credentials),
            provider_bindings: Some(provider_bindings),
            bot_providers: None,
            a2a_cards: None,
            a2a_url_guard:bcs_route_security::OutboundUrlGuard::strict(),
            a2a_grpc_urls:vec![],
        }
    }

    pub fn with_a2a_route_policy(mut self, guard:bcs_route_security::OutboundUrlGuard, grpc_urls:Vec<String>)->Self {
        self.a2a_url_guard=guard;self.a2a_grpc_urls=grpc_urls;self
    }

    pub fn with_a2a_cards(mut self, cards: Arc<dyn bcs_service_api::port::repo::a2a_bot::A2aBotRepoPort>) -> Self {
        self.a2a_cards = Some(cards);
        self
    }

    pub fn with_bot_provider_repo(mut self, repo: Arc<dyn BotProviderRepoPort>) -> Self {
        self.bot_providers = Some(repo);
        self
    }

    /// Borrow the provider-bindings repo, if wired. Used by the application
    /// layer to insert / read bindings without going through the registry
    /// trait.
    pub(crate) fn provider_bindings_repo(
        &self,
    ) -> Option<&Arc<dyn ProviderBotBindingRepoPort>> {
        self.provider_bindings.as_ref()
    }

    /// Project the observed ids onto the requested bot ids
    /// and stamp the observation instant. The snapshot
    /// NEVER reports activity for ids the caller did not
    /// ask about.
    fn activity_snapshot(
        &self,
        observed_at_ms: u64,
        bot_ids: &[String],
        active: HashSet<String>,
    ) -> RuntimeActivitySnapshot {
        let requested = bot_ids.iter().cloned().collect::<HashSet<_>>();
        RuntimeActivitySnapshot {
            observed_at_ms,
            active_bot_ids: active
                .into_iter()
                .filter(|bot_id| requested.contains(bot_id))
                .collect(),
        }
    }

    /// Check affiliation before a delivery switch can create owner edges.
    pub(crate) async fn validate_provider_switch_membership(
        &self, bot_id: &str, provider_id: &str, provider_bot_ref: &str, owner: &str,
    ) -> ServiceResult<()> {
        let Some(metadata) = &self.bot_providers else { return Ok(()); };
        if let Some(record) = metadata.get_provider_bot(bot_id).await? {
            if record.is_deleted || record.provider_id != provider_id || record.provider_bot_ref != provider_bot_ref {
                return Err(ServiceError::Conflict("Bot Provider membership cannot be replaced".into()));
            }
            if self.repo.try_get(bot_id).await?.is_some_and(|bot| bot.created_by.as_deref().is_some_and(|existing| existing != owner)) {
                return Err(ServiceError::Forbidden("Bot owner does not match Provider reference owner".into()));
            }
        }
        if metadata.get_provider_bot_by_ref(provider_id, provider_bot_ref).await?
            .is_some_and(|record| record.is_deleted || record.bot_uuid != bot_id)
        {
            return Err(ServiceError::Conflict("Provider/ref is already registered".into()));
        }
        Ok(())
    }

    /// Returns Ok iff the provider is fully ready to receive downlink
    /// deliveries: provider exists, not disabled, downlink config enabled,
    /// downlink credential present and not disabled.
    ///
    /// Errors:
    /// - `ServiceError::ProviderNotFound` if the provider does not exist
    /// - `ServiceError::ProviderNotReadyForDownlink` for every other failure
    /// - `ServiceError::InternalError` if any required repo is not wired
    pub(crate) async fn assert_provider_ready_for_downlink(
        &self,
        provider_id: &str,
        webhook_url: Option<&str>,
    ) -> ServiceResult<()> {
        let providers = self.provider_repo.as_ref().ok_or_else(|| {
            ServiceError::InternalError("provider repo not configured".to_string())
        })?;
        let provider = providers
            .get_provider(provider_id)
            .await?
            .ok_or_else(|| ServiceError::ProviderNotFound(provider_id.to_string()))?;
        if provider.disabled {
            return Err(ServiceError::ProviderNotReadyForDownlink {
                provider_id: provider.provider_id,
                reason: "provider disabled".to_string(),
            });
        }
        let downlink = parse_downlink_config(&provider.config)?;
        if webhook_url.or(downlink.webhook_url.as_deref()).is_none() {
            return Err(ServiceError::ProviderNotReadyForDownlink {
                provider_id: provider.provider_id,
                reason: "missing_delivery_endpoint".to_string(),
            });
        }
        if !downlink.enabled {
            return Err(ServiceError::ProviderNotReadyForDownlink {
                provider_id: provider.provider_id,
                reason: "downlink disabled".to_string(),
            });
        }
        let credentials = self.provider_credentials.as_ref().ok_or_else(|| {
            ServiceError::InternalError(
                "provider credential repo not configured".to_string(),
            )
        })?;
        let credential = credentials
            .get_credential_by_kind(&provider.provider_id, "downlink_bcs_to_provider")
            .await?
            .ok_or_else(|| ServiceError::ProviderNotReadyForDownlink {
                provider_id: provider.provider_id.clone(),
                reason: "downlink credential not found".to_string(),
            })?;
        if credential.disabled {
            return Err(ServiceError::ProviderNotReadyForDownlink {
                provider_id: provider.provider_id,
                reason: "downlink credential disabled".to_string(),
            });
        }
        Ok(())
    }
}

impl Default for BotCore {
    fn default() -> Self {
        Self::memory()
    }
}

#[async_trait]
impl ServiceLifecycle for BotCore {
    async fn initialize(&self) -> Result<(), LifecycleError> {
        Ok(())
    }

    async fn shutdown(&self) -> Result<(), LifecycleError> {
        Ok(())
    }
}

#[async_trait]
impl BotRegistryCoreService for BotCore {
    async fn collaboration_owner(&self, actor_ids: &[String]) -> ServiceResult<Option<String>> {
        let Some(metadata) = &self.bot_providers else { return Ok(None); };
        let mut ids = actor_ids.to_vec();
        ids.sort(); ids.dedup();
        let actors = metadata.collaboration_actors(&ids).await?;
        super::collaboration_policy::enforce_owner_scope(&actors, &ids)
    }
    async fn create_registration_if_absent(
        &self, bot_id: String, capabilities: BotCapabilities, created_by: &str, token: &str,
    ) -> ServiceResult<bool> {
        self.repo.create_registration_if_absent(bot_id, capabilities, created_by, token).await
    }

    /// Trusted human registration (spec §10.2): the core
    /// assigns the Bot ID and runtime token and hands the
    /// whole committed state to the repo's composite create.
    /// Failure means NOTHING committed, so no credential ever
    /// reaches the caller.
    async fn register_with_ownership(
        &self,
        registration: bcs_service_api::core::registry::TrustedBotRegistration,
    ) -> ServiceResult<CommittedBotRegistration> {
        let bcs_service_api::core::registry::TrustedBotRegistration {
            capabilities,
            initialization,
        } = registration;
        let owner = initialization.owner_user_id.trim().to_string();
        if owner.is_empty() {
            return Err(ServiceError::InvalidOperation {
                message: "trusted registration requires an owner".into(),
                request_id: None,
            });
        }
        if capabilities.name.as_deref().unwrap_or("").trim().is_empty() {
            return Err(ServiceError::InvalidOperation {
                message: "trusted registration requires a bot name".into(),
                request_id: None,
            });
        }
        let bot_id = new_bot_uuid();
        let token = new_session_token();
        let created = self
            .repo
            .create_registration_if_absent_with_ownership(
                bot_id.clone(),
                capabilities,
                owner.trim(),
                &token,
                initialization,
            )
            .await?;
        if !created {
            return Err(ServiceError::Conflict(
                "Bot registration identity already exists".into(),
            ));
        }
        Ok(CommittedBotRegistration {
            bot_name: self
                .repo
                .get(&bot_id)
                .await
                .and_then(|bot| bot.capabilities.name)
                .unwrap_or_default(),
            bot_uuid: bot_id,
            bot_token: token,
        })
    }

    async fn initialize_ownership(
        &self,
        bot_id: &str,
        owner_user_id: &str,
        mutation: bcs_service_api::types::bot_authority::AuthorityMutation,
    ) -> ServiceResult<BotOwnershipView> {
        self.repo.initialize_ownership(bot_id, owner_user_id, mutation).await
    }

    async fn ownership_governance_state(
        &self,
        bot_id: &str,
    ) -> ServiceResult<
        Option<bcs_service_api::types::bot_authority::BotOwnershipGovernance>,
    > {
        self.repo.ownership_governance_state(bot_id).await
    }

    async fn retire_with_authority(
        &self,
        bot_id: &str,
        mutation: bcs_service_api::types::bot_authority::AuthorityMutation,
    ) -> ServiceResult<bool> {
        // The ordinary `retire` is its own command — never
        // chained into a Provider retirement (spec §7.1).
        // The real mutation actor is the audit initiator.
        self.repo.retire_with_authority(bot_id, mutation).await
    }

    async fn register(&self, bot_id: String, capabilities: BotCapabilities) -> ServiceResult<()> {
        let capabilities = self.protect_provider_identity(&bot_id, capabilities).await?;
        self.repo.register(bot_id, capabilities).await
    }

    async fn register_with_owner_and_token(
        &self,
        bot_id: String,
        capabilities: BotCapabilities,
        created_by: &str,
        token: &str,
    ) -> ServiceResult<()> {
        let capabilities = self.protect_provider_identity(&bot_id, capabilities).await?;
        self.repo
            .register_with_owner_and_token(bot_id, capabilities, created_by, token)
            .await
    }

    async fn update_capabilities(
        &self,
        bot_id: &str,
        capabilities: BotCapabilities,
    ) -> ServiceResult<()> {
        let capabilities = self.protect_provider_identity(bot_id, capabilities).await?;
        self.repo.update_capabilities(bot_id, capabilities).await
    }

    async fn update_status(&self, bot_id: &str) -> bool {
        self.repo.update_status(bot_id).await
    }

    async fn get(&self, bot_id: &str) -> Option<RegisteredBot> {
        self.repo.get(bot_id).await
    }

    async fn try_get(&self, bot_id: &str) -> ServiceResult<Option<RegisteredBot>> {
        self.repo.try_get(bot_id).await
    }

    async fn try_get_many(&self, bot_ids: &[String]) -> ServiceResult<Vec<RegisteredBot>> {
        self.repo.try_get_many(bot_ids).await
    }

    async fn get_including_deleted(&self, bot_id: &str) -> Option<RegisteredBot> {
        self.repo.get_including_deleted(bot_id).await
    }

    async fn get_agent_credentials(&self, bot_id: &str) -> Option<AgentCredentials> {
        self.repo.get_agent_credentials(bot_id).await
    }

    async fn add_bot_info(&self, bot_id: &str, key: &str, value: String) {
        self.repo.add_bot_info(bot_id, key, value).await;
    }

    async fn set_bot_info(&self, bot_id: &str, key: &str, value: Option<String>) {
        self.repo.set_bot_info(bot_id, key, value).await;
    }

    async fn get_bot_info(&self, bot_id: &str, key: &str) -> Option<String> {
        self.repo.get_bot_info(bot_id, key).await
    }

    async fn resolve_delivery_target(&self, bot_id: &str) -> ServiceResult<BotDeliveryTarget> {
        self.resolve_delivery_target_impl(bot_id,false).await
    }

    async fn resolve_abort_target(&self,bot_id:&str)->ServiceResult<BotDeliveryTarget> {
        self.resolve_delivery_target_impl(bot_id,true).await
    }

    async fn resolve_coordination_surface(
        &self,
        bot_id: &str,
    ) -> ServiceResult<CoordinationSurface> {
        let _bot = self
            .repo
            .get(bot_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(bot_id.to_string()))?;

        if let Some(surface) = self.provider_coordination_surface(bot_id).await? {
            return Ok(surface);
        }

        let client_kind = self
            .repo
            .get_bot_info(bot_id, "client_kind")
            .await
            .map(|value| value.trim().to_ascii_lowercase());
        match client_kind.as_deref() {
            Some("native_mcp") => return Ok(CoordinationSurface::native_mcp_bcs()),
            Some("mcporter_mcp") => return Ok(CoordinationSurface::mcporter_mcp_bcs()),
            Some("native_tool" | "plugin" | "openclaw-channel-bcn" | "deepseek-harness-channel-bcn") => {
                return Ok(CoordinationSurface::native_tool());
            }
            _ => {}
        }

        Ok(CoordinationSurface::legacy_upstream())
    }

    async fn get_by_ids(&self, bot_ids: &[String]) -> Vec<RegisteredBot> {
        self.repo.get_by_ids(bot_ids).await
    }

    async fn list_active(&self) -> Vec<RegisteredBot> {
        self.repo.list_active().await
    }

    async fn list_bots_by_creator(&self, created_by: &str) -> Vec<RegisteredBot> {
        self.repo.list_bots_by_creator(created_by).await
    }

    async fn try_list_bots_by_creator(
        &self,
        created_by: &str,
    ) -> ServiceResult<Vec<RegisteredBot>> {
        self.repo.try_list_bots_by_creator(created_by).await
    }

    async fn list_uninitialized_bots_by_creator(
        &self,
        created_by: &str,
        limit: usize,
    ) -> ServiceResult<Vec<RegisteredBot>> {
        self.repo
            .list_uninitialized_bots_by_creator(created_by, limit)
            .await
    }

    async fn discover(&self, query: &str) -> Vec<RegisteredBot> {
        self.repo.discover(query).await
    }

    async fn find_by_skills(&self, skills: &[&str]) -> Vec<RegisteredBot> {
        self.repo.find_by_skills(skills).await
    }

    async fn find_by_domains(&self, domains: &[&str]) -> Vec<RegisteredBot> {
        self.repo.find_by_domains(domains).await
    }

    async fn find_by_scopes(&self, scopes: &[&str]) -> Vec<RegisteredBot> {
        self.repo.find_by_scopes(scopes).await
    }

    async fn find_by_name(&self, name: &str) -> Vec<RegisteredBot> {
        self.repo.find_by_name(name).await
    }

    async fn list_all_bots(&self) -> Vec<RegisteredBot> {
        self.repo.list_all_bots().await
    }

    async fn list_bots_by_name_and_cooperatable_with(
        &self,
        name: &str,
        bot_uuid: &str,
        cooperatable_only: bool,
        friend_uuids: &HashSet<String>,
        offset: usize,
        limit: usize,
    ) -> (Vec<(RegisteredBot, bool)>, usize) {
        self.repo
            .list_bots_by_name_and_cooperatable_with(
                name,
                bot_uuid,
                cooperatable_only,
                friend_uuids,
                offset,
                limit,
            )
            .await
    }

    async fn unregister(&self, bot_id: &str) -> bool {
        self.soft_delete(bot_id).await
    }

    async fn soft_delete(&self, bot_id: &str) -> bool {
        let mut deleted = false;
        if let Some(metadata) = &self.bot_providers {
            let result = async {
                if let Some(record) = metadata.get_provider_bot(bot_id).await? {
                    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
                        .map(|value| value.as_millis() as u64)
                        .map_err(|_| ServiceError::InternalError("system clock is before epoch".into()))?;
                    return metadata.delete_provider_bot(&record.provider_id, bot_id, now).await;
                }
                // Legacy-read rollout can encounter a gateway before backfill.
                // Let the dual-write projection attach metadata and delete both
                // representations before clearing the registry's runtime state.
                if let Some(bindings) = &self.provider_bindings {
                    if let Some(binding) = bindings.get_binding_by_bot_uuid(bot_id).await? {
                        if binding.disabled {
                            return Err(ServiceError::Conflict("disabled legacy binding requires audited migration".into()));
                        }
                        return bindings.update_binding_disabled(bot_id, true, binding.updated_at.saturating_add(1)).await
                            .map(|updated| updated.is_some());
                    }
                }
                Ok(false)
            }.await;
            match result {
                Ok(value) => deleted = value,
                Err(error) => {
                    warn!(bot_id, error = %error, "Bot Provider soft deletion failed");
                    return false;
                }
            }
        }
        // Also clear the registry's runtime/token caches after the durable write.
        self.repo.soft_delete(bot_id).await || deleted
    }

    async fn cleanup_expired(&self) {
        self.repo.cleanup_expired().await
    }

    async fn load_from_storage(&self, bot_id: &str) -> Option<BotCapabilities> {
        self.repo.load_from_storage(bot_id).await
    }

    async fn save_to_storage(&self, bot_id: &str, caps: &BotCapabilities) -> ServiceResult<()> {
        let caps = self.protect_provider_identity(bot_id, caps.clone()).await?;
        self.repo.save_to_storage(bot_id, &caps).await
    }

    async fn update_visibility(&self, bot_id: &str, visibility: &str) -> ServiceResult<()> {
        self.repo.update_visibility(bot_id, visibility).await
    }

    #[allow(deprecated)]
    async fn set_hidden(&self, bot_id: &str, hidden: bool) -> ServiceResult<()> {
        self.repo.set_hidden(bot_id, hidden).await
    }

    async fn update_actor_status(&self, bot_id: &str, status: ActorStatus) -> ServiceResult<()> {
        self.repo.update_actor_status(bot_id, status).await
    }

    async fn ensure_human_actor(
        &self,
        staff_no: &str,
        nick_name: &str,
    ) -> ServiceResult<EnsureHumanResult> {
        self.repo.ensure_human_actor(staff_no, nick_name).await
    }

    async fn list_legacy_bots_for_owner(
        &self,
        staff_no: &str,
        env: &str,
    ) -> ServiceResult<Vec<RegisteredBot>> {
        self.repo.list_legacy_bots_for_owner(staff_no, env).await
    }

    async fn update_human_name(&self, staff_no: &str, new_name: &str) -> ServiceResult<()> {
        self.repo.update_human_name(staff_no, new_name).await
    }

    async fn has_been_onboarded(&self, bot_id: &str) -> bool {
        self.repo.has_been_onboarded(bot_id).await
    }

    async fn save_created_by(
        &self,
        bot_id: &str,
        created_by: &str,
        overwrite: bool,
    ) -> ServiceResult<()> {
        self.repo
            .save_created_by(bot_id, created_by, overwrite)
            .await
    }

    async fn save_token(&self, bot_id: &str, token: &str) -> ServiceResult<()> {
        self.repo.save_token(bot_id, token).await
    }

    async fn load_token(&self, bot_id: &str) -> Option<String> {
        self.repo.load_token(bot_id).await
    }

    async fn try_load_token(&self, bot_id: &str) -> ServiceResult<Option<String>> {
        self.repo.try_load_token(bot_id).await
    }

    async fn find_bot_by_token(&self, token: &str) -> Option<String> {
        self.repo.find_bot_by_token(token).await
    }

    async fn query_agent_bots(&self, query: &bcs_service_api::types::agent_registration::AgentBotQuery) -> ServiceResult<Vec<bcs_service_api::types::agent_registration::AgentBotRecord>> { self.agent_bots(query).await }
    async fn validate_signed_bot(&self, id: &str) -> ServiceResult<()> { self.signed_bot_identity(id).await }
    async fn resolve_agent_bot(&self, agent_code: &str, actor: Option<&str>) -> ServiceResult<Option<bcs_service_api::types::agent_registration::AgentBotRecord>> { self.agent_bot_identity(agent_code, actor).await }
    async fn resolve_bot_token(&self, token: &str, actor: Option<&str>) -> ServiceResult<Option<bcs_service_api::types::agent_registration::AgentBotRecord>> { self.token_bot_identity(token, actor).await }

    async fn find_bot_by_agent_code(&self, agent_code: &str) -> Option<String> {
        self.agent_bot_identity(agent_code, None).await.ok().flatten().map(|bot| bot.bot.bot_id)
    }

    async fn find_agent_registration(
        &self, agent_code: &str,
    ) -> ServiceResult<Option<bcs_service_api::types::AgentBotRegistration>> {
        self.repo.find_agent_registration(agent_code).await
    }

    async fn find_bot_by_binding_channel(
        &self,
        channel: &str,
        binding_key: &str,
    ) -> Option<String> {
        self.repo
            .find_bot_by_binding_channel(channel, binding_key)
            .await
    }

    async fn register_streaming_connection(&self, bot_id: String) -> Result<String, ()> {
        self.repo.register_streaming_connection(bot_id).await
    }

    async fn connect_or_promote_streaming(
        &self,
        bot_id: String,
    ) -> Result<String, ConnectStreamError> {
        self.repo.connect_or_promote_streaming(bot_id).await
    }

    async fn reconnect_streaming(&self, existing_token: String) -> Result<(String, String), ()> {
        self.repo.reconnect_streaming(existing_token).await
    }

    async fn disconnect_streaming(&self, bot_id: &str) {
        self.repo.disconnect_streaming(bot_id).await
    }

    async fn is_connected(&self, bot_id: &str) -> bool {
        self.repo.is_connected(bot_id).await
    }

    /// ONE runtime-activity observation (spec §8.0
    /// point 2): ONE WS connection-set read of the
    /// current instance, then the Provider
    /// binding/provider/credential resolution over the
    /// NOT-WS-active ids in bounded batches (≤
    /// `AUTHORITY_QUERY_CHUNK` ids per bindings query;
    /// the one provider and credential batch). The
    /// `WS active || HTTP Provider routable` semantics of
    /// `list_runtime_active_bot_ids` are preserved
    /// exactly — but read failures PROPAGATE instead of
    /// the legacy warn-and-degrade, so a store outage can
    /// never masquerade as "unreachable".
    async fn try_runtime_activity_for(&self, bot_ids: &[String]) -> ServiceResult<RuntimeActivitySnapshot> {
        self.try_runtime_activity_for_impl(bot_ids).await
    }

    /// Batch check runtime-active actors for owner-facing
    /// lists (the legacy per-page helper).
    ///
    /// Runtime active intentionally ignores actor lifecycle
    /// visibility (`Hidden`) and means either an active WS
    /// connection or a routable HTTP provider target. On
    /// provider read failures this kept the historical
    /// degraded-success shape — the mine path does NOT use
    /// it any more; [`Self::try_runtime_activity_for`] is
    /// the strict §8.0 observation.
    async fn list_runtime_active_bot_ids(&self, bot_ids: &[String]) -> Vec<String> {
        self.list_runtime_active_bot_ids_impl(bot_ids).await
    }

    async fn is_effectively_online(&self, bot_id: &str) -> bool {
        let Some(bot) = bcs_observability::observe_value("bot.online.load", self.repo.get(bot_id)).await else {
            return false;
        };
        if bot.status != ActorStatus::Online {
            return false;
        }
        if bcs_observability::observe_value("bot.online.connection", self.repo.is_connected(bot_id)).await {
            return true;
        }
        matches!(
            bcs_observability::observe_result("bot.online.delivery_target", self.resolve_delivery_target(bot_id)).await,
            Ok(BotDeliveryTarget::HttpProvider { .. } | BotDeliveryTarget::A2a { .. })
        )
    }

    async fn send_frame(&self, bot_id: &str, frame: String) -> Result<(), ()> {
        self.repo.send_frame(bot_id, frame).await
    }

    async fn send_request(
        &self,
        bot_id: &str,
        method: &str,
        params: serde_json::Value,
        timeout_ms: u64,
    ) -> Result<serde_json::Value, String> {
        self.repo
            .send_request(bot_id, method, params, timeout_ms)
            .await
    }

    async fn resolve_pending_request(&self, request_id: &str, response: serde_json::Value) {
        self.repo
            .resolve_pending_request(request_id, response)
            .await
    }

    async fn list_connected(&self) -> Vec<String> {
        self.repo.list_connected().await
    }

    async fn store_token_mapping(&self, token: String, bot_id: String) {
        self.repo.store_token_mapping(token, bot_id).await
    }

    async fn get_protocol_version(&self, bot_id: &str) -> u32 {
        self.repo.get_protocol_version(bot_id).await
    }

    async fn set_protocol_version(&self, bot_id: &str, version: u32) {
        self.repo.set_protocol_version(bot_id, version).await
    }

    async fn register_http_connection(&self, bot_id: String, token: String) -> String {
        self.repo.register_http_connection(bot_id, token).await
    }

    async fn connect_bot(
        &self,
        params: BotConnectParams,
        kind: ConnectionKind,
    ) -> Result<BotConnectResult, ConnectError> {
        let token_preview = params
            .token
            .as_ref()
            .map(|t| format!("{}...", &t[..t.len().min(4)]))
            .unwrap_or_else(|| "None".to_string());
        info!(token_present = params.token.is_some(), token_preview = %token_preview, "Processing connect_bot");

        let (is_new, bot_id, token): (bool, String, String) = if let Some(ref token_str) =
            params.token
        {
            if let Some(existing_bot_id) = self.repo.find_bot_by_token(token_str).await {
                info!(bot_id = %existing_bot_id, token_preview = %token_preview, "Valid token, reconnection");
                self.repo
                    .store_token_mapping(token_str.clone(), existing_bot_id.clone())
                    .await;
                (false, existing_bot_id, token_str.clone())
            } else {
                let new_token = new_session_token();
                info!(token_preview = %token_preview, new_token_preview = %format!("{}...", &new_token[..4]), "Invalid/not found token, new connection");
                (true, String::new(), new_token)
            }
        } else {
            let new_token = new_session_token();
            info!(new_token_preview = %format!("{}...", &new_token[..4]), "No token, new connection");
            (true, String::new(), new_token)
        };

        let (final_bot_id, final_token) = if is_new {
            let connect_bot_id = if let Some(ref provided_bot_id) = params.bot_id {
                if provided_bot_id.is_empty() {
                    return Err(ConnectError::InvalidBotId);
                }
                // Reject a provided bot_id that already exists, EXCEPT for the
                // plugin connection_mode promote path: if the existing bot's
                // stored token is the MOCK sentinel, a streaming connect is
                // allowed to promote it to a real token (§3.5). Everything else
                // (Http connections; real-token bots) keeps the historical
                // `AlreadyRegistered` refusal so duplicate ids cannot hijack.
                if self.repo.get(provided_bot_id).await.is_some() {
                    let allow_promote = matches!(kind, ConnectionKind::Streaming)
                        && self
                            .repo
                            .load_token(provided_bot_id)
                            .await
                            .map(|t| bcs_service_api::is_mock_token(&t))
                            .unwrap_or(false);
                    if !allow_promote {
                        return Err(ConnectError::AlreadyRegistered(
                            provided_bot_id.clone(),
                        ));
                    }
                }
                info!(bot_id = %provided_bot_id, "Using preconfigured bot_id");
                provided_bot_id.clone()
            } else {
                let temp_id = new_bot_uuid();
                info!(temp_id = %temp_id, "Generated bot_id");
                temp_id
            };

            let registered_token = match kind {
                ConnectionKind::Streaming => {
                    // Delegate create / MOCK-promote / already registered+connected
                    // to the store, which decides atomically under its write lock
                    // (the lock-free `repo.get` check that used to fire
                    // `AlreadyRegistered` here is moved into that lock).
                    self.repo
                        .connect_or_promote_streaming(connect_bot_id.clone())
                        .await
                        .map_err(|e| match e {
                            ConnectStreamError::AlreadyConnected(id) => {
                                ConnectError::AlreadyConnected(id)
                            }
                            ConnectStreamError::AlreadyRegistered(id) => {
                                ConnectError::AlreadyRegistered(id)
                            }
                            ConnectStreamError::InternalError(m) => ConnectError::InternalError(m),
                        })?
                }
                ConnectionKind::Http => {
                    self.repo
                        .register_http_connection(connect_bot_id.clone(), token.clone())
                        .await
                }
            };

            (connect_bot_id, registered_token)
        } else {
            match kind {
                ConnectionKind::Streaming => {
                    // The token was resolved above. An occupied streaming slot
                    // must reject this socket; never fall back to registration
                    // and report success without acquiring the slot.
                    let (reconnected_bot_id, tok) = self.repo
                        .reconnect_streaming(token.clone())
                        .await
                        .map_err(|()| ConnectError::AlreadyConnected(bot_id.clone()))?;
                    info!(bot_id = %reconnected_bot_id, "Reconnected via streaming transport");
                    (reconnected_bot_id, tok)
                }
                ConnectionKind::Http => {
                    self.repo
                        .store_token_mapping(token.clone(), bot_id.clone())
                        .await;
                    (bot_id, token)
                }
            }
        };

        if is_new {
            if self.repo.has_been_onboarded(&final_bot_id).await {
                if let Some(caps) = self.repo.load_from_storage(&final_bot_id).await {
                    let _ = self.repo.register(final_bot_id.clone(), caps).await;
                    info!(bot_id = %final_bot_id, "Loaded capabilities from storage for previously onboarded bot");
                }
            } else {
                info!(bot_id = %final_bot_id, "New bot connected, waiting for onboard");
            }
        } else if let Some(caps) = self.repo.load_from_storage(&final_bot_id).await {
            let _ = self.repo.register(final_bot_id.clone(), caps).await;
            info!(bot_id = %final_bot_id, "Loaded capabilities from storage for reconnecting bot");
        }

        Ok(BotConnectResult {
            is_new,
            bot_uuid: final_bot_id,
            token: final_token,
        })
    }
}
