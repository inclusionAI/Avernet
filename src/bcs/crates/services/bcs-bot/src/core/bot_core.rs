use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_bot_store::MemoryBotRepo;
use bcs_service_api::lifecycle::{LifecycleError, ServiceLifecycle};
use bcs_service_api::port::repo::{
    BotRepoPort, ProviderBotBindingRepoPort, ProviderCredentialRepoPort, ProviderRepoPort,
};
use bcs_service_api::{
    ActorStatus, AgentCredentials, BotCapabilities, BotConnectParams, BotConnectResult,
    BotDeliveryTarget, BotDynamicStatus, BotRegistryCoreService, ConnectError, ConnectionKind,
    CoordinationSurface, EnsureHumanResult, RedactedToken, RegisteredBot, ServiceError,
    ServiceResult,
};
use tracing::{debug, info, warn};

use super::ids::{new_bot_uuid, new_session_token};
use super::provider_core::{parse_coordination_config, parse_downlink_config};

/// Core bot registry service implementation.
///
/// `BotCore` owns the core-service boundary and delegates persistence to a
/// repository implementation.
#[derive(Clone)]
pub struct BotCore {
    repo: Arc<dyn BotRepoPort>,
    provider_repo: Option<Arc<dyn ProviderRepoPort>>,
    provider_credentials: Option<Arc<dyn ProviderCredentialRepoPort>>,
    provider_bindings: Option<Arc<dyn ProviderBotBindingRepoPort>>,
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
        }
    }

    /// Borrow the provider-bindings repo, if wired. Used by the application
    /// layer to insert / read bindings without going through the registry
    /// trait.
    pub(crate) fn provider_bindings_repo(
        &self,
    ) -> Option<&Arc<dyn ProviderBotBindingRepoPort>> {
        self.provider_bindings.as_ref()
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
    async fn register(&self, bot_id: String, capabilities: BotCapabilities) -> ServiceResult<()> {
        self.repo.register(bot_id, capabilities).await
    }

    async fn register_with_owner_and_token(
        &self,
        bot_id: String,
        capabilities: BotCapabilities,
        created_by: &str,
        token: &str,
    ) -> ServiceResult<()> {
        self.repo
            .register_with_owner_and_token(bot_id, capabilities, created_by, token)
            .await
    }

    async fn update_status(&self, bot_id: &str, status: BotDynamicStatus) -> bool {
        self.repo.update_status(bot_id, status).await
    }

    async fn get(&self, bot_id: &str) -> Option<RegisteredBot> {
        self.repo.get(bot_id).await
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

    async fn get_bot_info(&self, bot_id: &str, key: &str) -> Option<String> {
        self.repo.get_bot_info(bot_id, key).await
    }

    async fn resolve_delivery_target(&self, bot_id: &str) -> ServiceResult<BotDeliveryTarget> {
        let bot = self
            .repo
            .get(bot_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(bot_id.to_string()))?;

        let Some(bindings) = &self.provider_bindings else {
            debug!(bot_id = %bot_id, "resolve_delivery_target: provider_bindings repo not configured, fallback to websocket");
            return Ok(BotDeliveryTarget::WebSocket { bot_id: bot.bot_uuid });
        };
        let Some(binding) = bindings.get_binding_by_bot_uuid(bot_id).await? else {
            debug!(bot_id = %bot_id, "resolve_delivery_target: no provider binding, fallback to websocket");
            return Ok(BotDeliveryTarget::WebSocket { bot_id: bot.bot_uuid });
        };
        if binding.disabled {
            warn!(bot_id = %bot_id, provider_id = %binding.provider_id, "resolve_delivery_target: provider bot binding disabled");
            return Err(ServiceError::InvalidOperation {
                message: format!("provider bot '{}' is disabled", bot_id),
                request_id: None,
            });
        }

        let providers = self.provider_repo.as_ref().ok_or_else(|| {
            ServiceError::InternalError("provider repo not configured".to_string())
        })?;
        let provider = providers
            .get_provider(&binding.provider_id)
            .await?
            .ok_or_else(|| {
                warn!(bot_id = %bot_id, provider_id = %binding.provider_id, "resolve_delivery_target: provider not found");
                ServiceError::InvalidOperation {
                    message: format!("provider '{}' not found", binding.provider_id),
                    request_id: None,
                }
            })?;
        if provider.disabled {
            warn!(bot_id = %bot_id, provider_id = %provider.provider_id, "resolve_delivery_target: provider disabled");
            return Err(ServiceError::InvalidOperation {
                message: format!("provider '{}' is disabled", provider.provider_id),
                request_id: None,
            });
        }
        let downlink = parse_downlink_config(&provider.config)?;
        if !downlink.enabled {
            warn!(bot_id = %bot_id, provider_id = %provider.provider_id, "resolve_delivery_target: provider downlink disabled");
            return Err(ServiceError::InvalidOperation {
                message: format!("provider '{}' downlink is disabled", provider.provider_id),
                request_id: None,
            });
        }

        let credentials = self.provider_credentials.as_ref().ok_or_else(|| {
            ServiceError::InternalError("provider credential repo not configured".to_string())
        })?;
        let credential = credentials
            .get_credential_by_kind(&provider.provider_id, "downlink_bcs_to_provider")
            .await?
            .ok_or_else(|| {
                warn!(bot_id = %bot_id, provider_id = %provider.provider_id, "resolve_delivery_target: downlink credential not found");
                ServiceError::InvalidOperation {
                    message: format!(
                        "provider '{}' downlink credential not found",
                        provider.provider_id
                    ),
                    request_id: None,
                }
            })?;
        if credential.disabled {
            warn!(bot_id = %bot_id, provider_id = %provider.provider_id, "resolve_delivery_target: downlink credential disabled");
            return Err(ServiceError::InvalidOperation {
                message: format!(
                    "provider '{}' downlink credential is disabled",
                    provider.provider_id
                ),
                request_id: None,
            });
        }

        info!(
            bot_id = %bot_id,
            provider_id = %provider.provider_id,
            provider_bot_ref = %binding.provider_bot_ref,
            webhook_url = %downlink.webhook_url,
            protocol_version = %downlink.protocol_version,
            "resolve_delivery_target: resolved http provider target"
        );
        Ok(BotDeliveryTarget::HttpProvider {
            bot_id: bot.bot_uuid,
            provider_id: provider.provider_id,
            provider_bot_ref: binding.provider_bot_ref,
            webhook_url: downlink.webhook_url,
            bcs_to_provider_token: RedactedToken::new(credential.secret_value),
            protocol_version: downlink.protocol_version,
        })
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

        if let Some(bindings) = &self.provider_bindings {
            if let Some(binding) = bindings.get_binding_by_bot_uuid(bot_id).await? {
                if binding.disabled {
                    return Err(ServiceError::InvalidOperation {
                        message: format!("provider bot '{}' is disabled", bot_id),
                        request_id: None,
                    });
                }
                let providers = self.provider_repo.as_ref().ok_or_else(|| {
                    ServiceError::InternalError("provider repo not configured".to_string())
                })?;
                let provider = providers
                    .get_provider(&binding.provider_id)
                    .await?
                    .ok_or_else(|| ServiceError::InvalidOperation {
                        message: format!("provider '{}' not found", binding.provider_id),
                        request_id: None,
                    })?;
                if provider.disabled {
                    return Err(ServiceError::InvalidOperation {
                        message: format!("provider '{}' is disabled", provider.provider_id),
                        request_id: None,
                    });
                }
                return Ok(parse_coordination_config(&provider.config)?.into());
            }
        }

        let client_kind = self
            .repo
            .get_bot_info(bot_id, "client_kind")
            .await
            .map(|value| value.trim().to_ascii_lowercase());
        if client_kind.as_deref() == Some("plugin") {
            return Ok(CoordinationSurface::native_tool());
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
        self.repo.unregister(bot_id).await
    }

    async fn soft_delete(&self, bot_id: &str) -> bool {
        self.repo.soft_delete(bot_id).await
    }

    async fn cleanup_expired(&self) {
        self.repo.cleanup_expired().await
    }

    async fn load_from_storage(&self, bot_id: &str) -> Option<BotCapabilities> {
        self.repo.load_from_storage(bot_id).await
    }

    async fn save_to_storage(&self, bot_id: &str, caps: &BotCapabilities) -> ServiceResult<()> {
        self.repo.save_to_storage(bot_id, caps).await
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

    async fn find_bot_by_token(&self, token: &str) -> Option<String> {
        self.repo.find_bot_by_token(token).await
    }

    async fn find_bot_by_agent_code(&self, agent_code: &str) -> Option<String> {
        self.repo.find_bot_by_agent_code(agent_code).await
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

    async fn reconnect_streaming(&self, existing_token: String) -> Result<(String, String), ()> {
        self.repo.reconnect_streaming(existing_token).await
    }

    async fn disconnect_streaming(&self, bot_id: &str) {
        self.repo.disconnect_streaming(bot_id).await
    }

    async fn is_connected(&self, bot_id: &str) -> bool {
        self.repo.is_connected(bot_id).await
    }

    async fn list_runtime_active_bot_ids(&self, bot_ids: &[String]) -> Vec<String> {
        let mut active = HashSet::new();
        let mut provider_candidates = Vec::new();
        for bot_id in bot_ids {
            if self.repo.is_connected(bot_id).await {
                active.insert(bot_id.clone());
            } else {
                provider_candidates.push(bot_id.clone());
            }
        }

        let Some(provider_bindings) = &self.provider_bindings else {
            return active.into_iter().collect();
        };
        let Some(provider_repo) = &self.provider_repo else {
            return active.into_iter().collect();
        };
        let Some(provider_credentials) = &self.provider_credentials else {
            return active.into_iter().collect();
        };

        let bindings = match provider_bindings
            .list_bindings_by_bot_uuids(&provider_candidates)
            .await
        {
            Ok(bindings) => bindings,
            Err(error) => {
                warn!(error = %error, "list_runtime_active_bot_ids: failed to list provider bindings");
                return active.into_iter().collect();
            }
        };
        let provider_ids = bindings
            .iter()
            .filter(|binding| !binding.disabled)
            .map(|binding| binding.provider_id.clone())
            .collect::<HashSet<_>>()
            .into_iter()
            .collect::<Vec<_>>();
        if provider_ids.is_empty() {
            return active.into_iter().collect();
        }

        let providers = match provider_repo.list_providers_by_ids(&provider_ids).await {
            Ok(providers) => providers,
            Err(error) => {
                warn!(error = %error, "list_runtime_active_bot_ids: failed to list providers");
                return active.into_iter().collect();
            }
        };
        let credentials = match provider_credentials
            .list_credentials_by_kind_for_providers(
                &provider_ids,
                "downlink_bcs_to_provider",
            )
            .await
        {
            Ok(credentials) => credentials,
            Err(error) => {
                warn!(error = %error, "list_runtime_active_bot_ids: failed to list provider credentials");
                return active.into_iter().collect();
            }
        };

        let providers = providers
            .into_iter()
            .map(|provider| (provider.provider_id.clone(), provider))
            .collect::<HashMap<_, _>>();
        let credentials = credentials
            .into_iter()
            .map(|credential| (credential.provider_id.clone(), credential))
            .collect::<HashMap<_, _>>();

        for binding in bindings {
            if binding.disabled {
                continue;
            }
            let Some(provider) = providers.get(&binding.provider_id) else {
                continue;
            };
            if provider.disabled {
                continue;
            }
            let Ok(downlink) = parse_downlink_config(&provider.config) else {
                continue;
            };
            if !downlink.enabled {
                continue;
            }
            let Some(credential) = credentials.get(&binding.provider_id) else {
                continue;
            };
            if credential.disabled {
                continue;
            }
            active.insert(binding.bot_uuid);
        }

        active.into_iter().collect()
    }

    async fn is_effectively_online(&self, bot_id: &str) -> bool {
        let Some(bot) = self.repo.get(bot_id).await else {
            return false;
        };
        if bot.status != ActorStatus::Online {
            return false;
        }
        if self.repo.is_connected(bot_id).await {
            return true;
        }
        matches!(
            self.resolve_delivery_target(bot_id).await,
            Ok(BotDeliveryTarget::HttpProvider { .. })
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
                if self.repo.get(provided_bot_id).await.is_some() {
                    return Err(ConnectError::AlreadyRegistered(provided_bot_id.clone()));
                }
                info!(bot_id = %provided_bot_id, "Using preconfigured bot_id");
                provided_bot_id.clone()
            } else {
                let temp_id = new_bot_uuid();
                info!(temp_id = %temp_id, "Generated bot_id");
                temp_id
            };

            let registered_token = match kind {
                ConnectionKind::Streaming => self
                    .repo
                    .register_streaming_connection(connect_bot_id.clone())
                    .await
                    .map_err(|_| ConnectError::AlreadyConnected(connect_bot_id.clone()))?,
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
                    match self.repo.reconnect_streaming(token.clone()).await {
                        Ok((reconnected_bot_id, tok)) => {
                            info!(bot_id = %reconnected_bot_id, "Reconnected via streaming transport");
                            (reconnected_bot_id, tok)
                        }
                        Err(()) => {
                            warn!(bot_id = %bot_id, "Reconnect failed, trying direct register");
                            let registered_token = self
                                .repo
                                .register_streaming_connection(bot_id.clone())
                                .await
                                .unwrap_or_else(|_| token.clone());
                            (bot_id, registered_token)
                        }
                    }
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
