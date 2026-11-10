use bcs_service_api::ProviderCollaborationScope;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_route_security::OutboundUrlGuard;
use serde_json::Value;
use tracing::{info, warn};

use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, CoordinationMode, ProviderAuthMode,
    ProviderBotBinding, ProviderBotBindingRepoPort, ProviderBotConnectionMode,
    ProviderBasicInfo, ProviderBotCoreService, ProviderCoordinationConfig, ProviderCoreService, ProviderCredential,
    ProviderCredentialRepoPort, ProviderOrganizationManagementConfig, ProviderRecord,
    ProviderRepoPort, ProviderRetirement, RegisterProviderBotParams, RegisteredProvider, RuntimeBotIdentity,
    ServiceError, ServiceResult, Skill, UpdateProviderBotCoreResult, mock_token,
};

use bcs_service_api::core::RetireProviderBotParams;

use super::ids::{new_bot_uuid, new_provider_id, new_session_token};
use bcs_service_api::bot_provider::{BotConnectionMode, BotProviderRecord};
use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;
use bcs_service_api::port::repo::BotControlPlaneRepoPort;

#[derive(Clone)]
pub struct ProviderCore {
    providers: Arc<dyn ProviderRepoPort>,
    credentials: Arc<dyn ProviderCredentialRepoPort>,
    bindings: Arc<dyn ProviderBotBindingRepoPort>,
    registry: Arc<dyn BotRegistryCoreService>,
    webhook_url_guard: OutboundUrlGuard,
    bot_providers: Option<Arc<dyn BotProviderRepoPort>>,
    /// P1.3: caller-config CAS channel for the registration's initial
    /// snapshot. `None` (memory composition) means registration cannot
    /// carry `initial_caller` — such requests fail closed with
    /// reconciliation-required rather than silently skipping the snapshot.
    caller_config: Option<Arc<dyn BotControlPlaneRepoPort>>,
}

impl ProviderCore {
    /// Compatibility builder; admitted anonymous Cards no longer need a runtime opt-in.
    pub fn with_a2a_anonymous(self,_allow:bool)->Self {self}

    pub fn new(
        providers: Arc<dyn ProviderRepoPort>,
        credentials: Arc<dyn ProviderCredentialRepoPort>,
        bindings: Arc<dyn ProviderBotBindingRepoPort>,
        registry: Arc<dyn BotRegistryCoreService>,
    ) -> Self {
        Self::new_with_webhook_url_guard(
            providers,
            credentials,
            bindings,
            registry,
            OutboundUrlGuard::strict(),
        )
    }

    pub fn new_with_webhook_url_guard(
        providers: Arc<dyn ProviderRepoPort>,
        credentials: Arc<dyn ProviderCredentialRepoPort>,
        bindings: Arc<dyn ProviderBotBindingRepoPort>,
        registry: Arc<dyn BotRegistryCoreService>,
        webhook_url_guard: OutboundUrlGuard,
    ) -> Self {
        Self {
            providers,
            credentials,
            bindings,
            registry,
            webhook_url_guard,
            bot_providers: None,
            caller_config: None,
        }
    }

    pub fn with_caller_config_store(
        mut self,
        store: Arc<dyn BotControlPlaneRepoPort>,
    ) -> Self {
        self.caller_config = Some(store);
        self
    }

    pub fn with_bot_provider_repo(mut self, repo: Arc<dyn BotProviderRepoPort>) -> Self {
        self.bot_providers = Some(repo);
        self
    }

    async fn authenticated_provider(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
    ) -> ServiceResult<ProviderRecord> {
        let provider = self
            .provider_admin_for_path(provider_id, provider_admin_token)
            .await?;
        if provider.disabled {
            return Err(ServiceError::InvalidOperation {
                message: format!("provider '{}' is disabled", provider.provider_id),
                request_id: None,
            });
        }
        Ok(provider)
    }

    async fn provider_admin_for_path(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
    ) -> ServiceResult<ProviderRecord> {
        let provider = self
            .authenticate_provider_admin(provider_admin_token)
            .await?;
        if provider.provider_id != provider_id {
            return Err(ServiceError::Forbidden("provider_id_mismatch".to_string()));
        }
        Ok(provider)
    }

    async fn load_provider_for_binding(
        &self,
        binding: &ProviderBotBinding,
    ) -> ServiceResult<ProviderRecord> {
        if self.registry.get(&binding.bot_uuid).await.is_none() {
            return Err(ServiceError::BotNotFound(binding.bot_uuid.clone()));
        }
        let provider = self
            .providers
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
        if binding.disabled {
            return Err(ServiceError::InvalidOperation {
                message: format!("provider bot '{}' is disabled", binding.bot_uuid),
                request_id: None,
            });
        }
        Ok(provider)
    }

    async fn register_provider_bot_internal(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
        params: RegisterProviderBotParams,
        initial_managers: Vec<String>,
    ) -> ServiceResult<(ProviderBotBinding, Option<String>)> {
        let RegisterProviderBotParams {
            webhook_url,
            bot_name,
            summary,
            owners,
            provider_bot_ref,
            domains,
            skills,
            scopes,
            bot_uuid,
            connection_mode,
            initial_caller,
        } = params;
        let provider = self
            .authenticated_provider(provider_id, provider_admin_token)
            .await?;
        validate_external_id("provider_bot_ref", &provider_bot_ref)?;
        let downlink = parse_downlink_config(&provider.config)?;
        let identity = bcs_service_api::types::provider_identity::normalize(downlink.auth_mode, &provider_bot_ref).map_err(|message| ServiceError::InvalidOperation { message: message.into(), request_id: None })?;
        let provider_bot_ref = identity.provider_bot_ref;
        let bot_uuid = if downlink.auth_mode == ProviderAuthMode::AgentPassSubbots { None } else { bot_uuid };
        // This Core creates identity/delivery metadata, not authority. The
        // application must split its owner/manager snapshot before calling it.
        if owners.len() != 1 {
            return Err(ServiceError::InvalidOperation {
                message: "identity registration requires exactly one owner; use ProviderManagement for initial managers".into(),
                request_id: None,
            });
        }
        let (owner, _) = bcs_service_api::types::bot_authority::normalize_provider_owners(&owners)?;
        let owner = owner.as_str();
        let initialization = bcs_service_api::types::bot_authority::OwnershipInitialization {
            owner_user_id: owner.into(), initial_manager_user_ids: initial_managers,
            mutation: bcs_service_api::types::bot_authority::AuthorityMutation {
                mutation_id: uuid::Uuid::new_v4().to_string(),
                actor: bcs_service_api::types::bot_authority::AuthorityActor::Provider { provider_id: provider.provider_id.clone() },
            },
        };
        bcs_service_api::types::bot_authority::validate_initial_managers(&initialization)?;
        let atomic_authority = !initialization.initial_manager_user_ids.is_empty();
        if atomic_authority && initial_caller.is_some() {
            return Err(ServiceError::InvalidOperation { message: "initial caller configuration requires a separate configuration write".into(), request_id: None });
        }
        if let Some(url) = webhook_url.as_deref() {
            if matches!(connection_mode, ProviderBotConnectionMode::Plugin) {
                return Err(ServiceError::InvalidOperation {
                    message: "plugin bots cannot configure a webhook_url".to_string(),
                    request_id: None,
                });
            }
            validate_outbound_url(&self.webhook_url_guard, "webhook_url", url)?;
        }
        // Gateway mode short-circuits on an existing binding (idempotent replay).
        // Plugin mode never writes a binding, so this short-circuit never applies.
        if !matches!(connection_mode, ProviderBotConnectionMode::Plugin) {
            if let Some(existing_binding) = self
                .bindings
                .get_binding_by_provider_ref(&provider.provider_id, &provider_bot_ref)
                .await?
            {
                if downlink.auth_mode == ProviderAuthMode::AgentPassSubbots {
                    return Err(ServiceError::Conflict("Provider/ref is already registered".into()));
                }
                if webhook_url.is_some() && webhook_url != existing_binding.webhook_url {
                    return Err(ServiceError::Conflict(
                        "webhook_url differs from the registered Bot; use PATCH to update it".to_string(),
                    ));
                }
                if atomic_authority { self.require_initialized_snapshot_replay(&existing_binding.bot_uuid).await?; }
                info!(
                    provider_id = %provider.provider_id,
                    bot_uuid = %existing_binding.bot_uuid,
                    provider_bot_ref = %provider_bot_ref,
                    "register_provider_bot: provider bot ref already registered; returning existing binding"
                );
                return Ok((existing_binding, None));
            }
        }

        if let Some(metadata) = &self.bot_providers {
            if let Some(existing) = metadata.get_provider_bot_by_ref(provider_id, &provider_bot_ref).await? {
                if existing.is_deleted || bot_uuid.as_deref() != Some(existing.bot_uuid.as_str())
                    || existing.connection_mode != BotConnectionMode::Plugin
                    || connection_mode != ProviderBotConnectionMode::Plugin
                { return Err(ServiceError::Conflict("Provider/ref is already registered".into())); }
            }
            if let Some(bot_id) = &bot_uuid {
                if let Some(existing) = metadata.get_provider_bot(bot_id).await? {
                    if existing.is_deleted || existing.provider_id != provider_id || existing.provider_bot_ref != provider_bot_ref {
                        return Err(ServiceError::Conflict("Bot Provider membership cannot be replaced".into()));
                    }
                }
                if let Some(existing) = self.registry.try_get(bot_id).await? {
                    if existing.created_by.as_deref().is_some_and(|existing_owner| existing_owner != owner) {
                        return Err(ServiceError::Forbidden("Bot owner does not match registration owner".into()));
                    }
                }
            }
        }
        if matches!(connection_mode, ProviderBotConnectionMode::Gateway)
            && webhook_url.is_none() && downlink.webhook_url.is_none()
        {
            return Err(ServiceError::InvalidOperation {
                message: "missing_delivery_endpoint: configure a Bot or Provider webhook_url".to_string(),
                request_id: None,
            });
        }
        let provider_auth_mode = downlink.auth_mode;
        let capabilities = BotCapabilities {
            name: Some(bot_name),
            summary,
            domains,
            skills,
            scopes,
            binding_channels: None,
            hidden: false,
            visibility: "protected".to_string(),
            agent_code: identity.agent_code,
            agent_token: None,
            ..BotCapabilities::default()
        };
        let bot_uuid = match bot_uuid {
            Some(bot_uuid) => {
                validate_external_id("bot_uuid", &bot_uuid)?;
                bot_uuid
            }
            None => new_bot_uuid(),
        };

        if atomic_authority {
            if self.registry.try_get(&bot_uuid).await?.is_none() {
                return self.create_with_initial_authority(BotProviderRecord {
                    bot_uuid, provider_id: provider.provider_id, provider_bot_ref,
                    connection_mode, webhook_url, is_deleted: false,
                }, capabilities, provider_auth_mode, initialization).await;
            }
            self.require_initialized_snapshot_replay(&bot_uuid).await?;
        }

        if provider_auth_mode == ProviderAuthMode::AgentPassSubbots {
            let metadata = self.bot_providers.as_ref().ok_or_else(|| ServiceError::InternalError("atomic Provider Bot registration is not configured".into()))?;
            let session_token = new_session_token();
            metadata.create_provider_bot(BotProviderRecord {
                bot_uuid: bot_uuid.clone(), provider_id: provider.provider_id.clone(), provider_bot_ref: provider_bot_ref.clone(),
                connection_mode, webhook_url: webhook_url.clone(), is_deleted: false,
            }, capabilities, owner, &session_token).await?;
            let now = now_ms();
            return Ok((ProviderBotBinding { bot_uuid, provider_id: provider.provider_id,
                provider_bot_ref, webhook_url, disabled: false, created_at: now, updated_at: now }, Some(session_token)));
        }

        if let Some(metadata) = &self.bot_providers {
            metadata.validate_provider_affiliation(&provider.provider_id, &bot_uuid).await?;
            metadata.validate_provider_bot_name(&provider.provider_id,
                capabilities.name.as_deref().unwrap_or(&bot_uuid),Some(&bot_uuid)).await?;
        }
        match connection_mode {
            ProviderBotConnectionMode::Gateway => {
                let session_token = new_session_token();
                let bot_runtime_token = matches!(
                    provider_auth_mode,
                    ProviderAuthMode::StaticBearer | ProviderAuthMode::ProviderAdmin | ProviderAuthMode::AgentPassSubbots
                )
                .then(|| session_token.clone());
                self.registry
                    .register_with_owner_and_token(
                        bot_uuid.clone(),
                        capabilities,
                        owner,
                        &session_token,
                    )
                    .await
                    .inspect_err(|err| {
                        warn!(
                            request_id = %bcs_observability::CurrentRequestId,
                            provider_id = %provider.provider_id,
                            bot_uuid = %bot_uuid,
                            error = %err,
                            "register_provider_bot: registry.register failed"
                        );
                    })?;

                let now = now_ms();
                let binding = ProviderBotBinding {
                    webhook_url,
                    bot_uuid: bot_uuid.clone(),
                    provider_id: provider.provider_id.clone(),
                    provider_bot_ref: provider_bot_ref.clone(),
                    disabled: false,
                    created_at: now,
                    updated_at: now,
                };
                self.bindings
                    .insert_binding(binding.clone())
                    .await
                    .inspect_err(|err| {
                        warn!(
                            request_id = %bcs_observability::CurrentRequestId,
                            provider_id = %provider.provider_id,
                            bot_uuid = %bot_uuid,
                            provider_bot_ref = %provider_bot_ref,
                            error = %err,
                            "register_provider_bot: insert_binding failed; bot record orphaned"
                        );
                    })?;
                // P1.3 initial caller snapshot: the registration stays
                // committed; a failed post-commit sync reports
                // reconciliation-required — never auto-retried, never
                // deleted, never credential-replayed, and a disabled
                // caller config can never unblock it (the caller config is
                // simply absent until the same command is resubmitted).
                if let Some(snapshot) = &initial_caller {
                    let synced = match self.caller_config.as_ref() {
                        Some(store) => {
                            store
                                .sync_caller_configuration(
                                    &bot_uuid,
                                    &bcs_config::resolve_env_str(),
                                    snapshot.clone(),
                                )
                                .await
                        }
                        None => Ok(None),
                    };
                    match synced {
                        Ok(Some(_)) => {}
                        Ok(None) => {
                            warn!(
                                request_id = %bcs_observability::CurrentRequestId,
                                bot_uuid = %bot_uuid,
                                provider_bot_ref = %provider_bot_ref,
                                "register_provider_bot: caller snapshot target missing; reconciliation required"
                            );
                            return Err(ServiceError::RegistrationReconciliationRequired);
                        }
                        Err(error) => {
                            warn!(
                                request_id = %bcs_observability::CurrentRequestId,
                                bot_uuid = %bot_uuid,
                                provider_bot_ref = %provider_bot_ref,
                                error = %error,
                                "register_provider_bot: initial caller snapshot failed; reconciliation required"
                            );
                            return Err(ServiceError::RegistrationReconciliationRequired);
                        }
                    }
                }
                info!(
                    provider_id = %provider.provider_id,
                    bot_uuid = %bot_uuid,
                    provider_bot_ref = %provider_bot_ref,
                    auth_mode = ?provider_auth_mode,
                    "register_provider_bot: completed"
                );
                Ok((binding, bot_runtime_token))
            }
            ProviderBotConnectionMode::Plugin => {
                // Token rule (token-agnostic): preserve whatever the registry
                // currently holds (memory → DB fallback), else mint a MOCK
                // placeholder for later WS-connect promotion. Registration never
                // rewrites/overwrites a real token.
                let existing_token = self.registry.load_token(&bot_uuid).await;
                let session_token = existing_token.clone().unwrap_or_else(mock_token);
                let branch_label = if existing_token.is_some() {
                    "merge_preserve_token"
                } else {
                    "create_mock"
                };
                // Plugin bots do NOT write a provider_binding → the bot reaches
                // BCS over WebSocket; `resolve_delivery_target` falls back to
                // WebSocket and `is_provider_downlink_bot` returns false.
                self.registry
                    .register_with_owner_and_token(
                        bot_uuid.clone(),
                        capabilities,
                        owner,
                        &session_token,
                    )
                    .await
                    .inspect_err(|err| {
                        warn!(
                            request_id = %bcs_observability::CurrentRequestId,
                            provider_id = %provider.provider_id,
                            bot_uuid = %bot_uuid,
                            error = %err,
                            "register_provider_bot: registry.register failed"
                        );
                    })?;

                // Unpersisted phantom returned for response population only;
                // `list_provider_bots` queries the binding repo directly so the
                // phantom is never listed.
                let now = now_ms();
                let binding = ProviderBotBinding {
                    webhook_url: None,
                    bot_uuid: bot_uuid.clone(),
                    provider_id: provider.provider_id.clone(),
                    provider_bot_ref: provider_bot_ref.clone(),
                    disabled: false,
                    created_at: now,
                    updated_at: now,
                };
                if let Some(metadata) = &self.bot_providers {
                    metadata.attach_provider_bot(BotProviderRecord {
                        bot_uuid: bot_uuid.clone(), provider_id: provider.provider_id.clone(), provider_bot_ref: provider_bot_ref.clone(),
                        connection_mode: BotConnectionMode::Plugin, webhook_url: None, is_deleted: false,
                    }).await?;
                }
                info!(
                    provider_id = %provider.provider_id,
                    bot_id = %bot_uuid,
                    provider_bot_ref = %provider_bot_ref,
                    connection_mode = "plugin",
                    branch = branch_label,
                    token_preview = %format!("{}...", &session_token[..session_token.len().min(4)]),
                    "register_provider_bot: plugin bot registered (no binding)"
                );
                // bot_runtime_token is None for plugin: the real runtime token
                // comes from the WS handshake; the MOCK sentinel never leaves BCS.
                Ok((binding, None))
            }
        }
    }
}

#[derive(Debug, Clone)]
pub(crate) struct DownlinkConfig {
    pub enabled: bool,
    pub webhook_url: Option<String>,
    pub auth_mode: ProviderAuthMode,
    pub protocol_version: String,
}

pub(crate) fn parse_downlink_config(config: &str) -> ServiceResult<DownlinkConfig> {
    let value: Value = serde_json::from_str(config)?;
    let downlink = value
        .get("downlink")
        .and_then(Value::as_object)
        .ok_or_else(|| ServiceError::InvalidOperation {
            message: "provider downlink config is missing".to_string(),
            request_id: None,
        })?;
    let webhook_url = match downlink.get("webhook_url") {
        None | Some(Value::Null) => None,
        Some(Value::String(url)) if !url.trim().is_empty() => Some(url.clone()),
        _ => return Err(ServiceError::InvalidOperation {
            message: "provider downlink webhook_url must be a non-empty URL or null".to_string(),
            request_id: None,
        }),
    };
    let auth_mode = match downlink.get("auth_mode").and_then(Value::as_str) {
        Some("static_bearer") => ProviderAuthMode::StaticBearer,
        Some("agentpass") => ProviderAuthMode::AgentPass,
        Some("agentpass-subbots") => ProviderAuthMode::AgentPassSubbots,
        Some("provider_admin") => ProviderAuthMode::ProviderAdmin,
        Some(other) => {
            return Err(ServiceError::InvalidOperation {
                message: format!("unsupported provider auth_mode '{}'", other),
                request_id: None,
            });
        }
        None => {
            return Err(ServiceError::InvalidOperation {
                message: "provider downlink auth_mode is missing".to_string(),
                request_id: None,
            });
        }
    };
    Ok(DownlinkConfig {
        enabled: downlink
            .get("enabled")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        webhook_url,
        auth_mode,
        protocol_version: downlink
            .get("protocol_version")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .unwrap_or("1.0")
            .to_string(),
    })
}

pub(crate) fn parse_coordination_config(config: &str) -> ServiceResult<ProviderCoordinationConfig> {
    let value: Value = serde_json::from_str(config)?;
    let Some(coordination) = value.get("coordination") else {
        return Ok(ProviderCoordinationConfig::disabled());
    };
    let parsed: ProviderCoordinationConfig = serde_json::from_value(coordination.clone())?;
    validate_provider_coordination_config(&parsed)?;
    Ok(parsed)
}

pub(crate) fn parse_organization_management_config(
    config: &str,
) -> ServiceResult<ProviderOrganizationManagementConfig> {
    ProviderOrganizationManagementConfig::from_provider_config(config).map_err(ServiceError::from)
}

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}

fn generated_id(prefix: &str) -> String {
    format!("{}_{}", prefix, uuid::Uuid::new_v4().simple())
}

fn validate_external_id(kind: &str, value: &str) -> ServiceResult<()> {
    let valid = !value.is_empty()
        && value.len() <= 256
        && value
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | '.' | ':'));
    if valid {
        Ok(())
    } else {
        Err(ServiceError::InvalidOperation {
            message: format!("invalid {kind}: '{value}'"),
            request_id: None,
        })
    }
}

fn validate_provider_slug(slug: &str) -> ServiceResult<()> {
    // Retain legacy slugs while accepting the stricter public A2A name grammar.
    if bcs_service_api::types::a2a_names::valid_provider_name(slug) {return Ok(());}
    let alphanumeric = |byte: u8| byte.is_ascii_lowercase() || byte.is_ascii_digit();
    if slug.is_empty() || slug.len() > 64
        || !slug.bytes().all(|byte| alphanumeric(byte) || byte == b'-')
        || !slug.as_bytes().first().copied().is_some_and(alphanumeric)
        || !slug.as_bytes().last().copied().is_some_and(alphanumeric)
    {
        return Err(ServiceError::InvalidOperation {
            message: "slug must be 1-64 lowercase ASCII letters, digits or hyphens, beginning and ending with a letter or digit".into(),
            request_id: None,
        });
    }
    Ok(())
}

fn validate_outbound_url(guard: &OutboundUrlGuard, field: &str, url: &str) -> ServiceResult<()> {
    guard
        .validate_configured_http_url(url)
        .map_err(|error| ServiceError::InvalidOperation {
            message: format!("{field} is not allowed: {error}"),
            request_id: None,
        })
}

fn validate_provider_coordination_config(
    coordination: &ProviderCoordinationConfig,
) -> ServiceResult<()> {
    let has_mcp_server = coordination
        .mcp_server
        .as_deref()
        .map(str::trim)
        .is_some_and(|value| !value.is_empty());
    let has_mcporter_command = coordination
        .mcporter_command
        .as_deref()
        .map(str::trim)
        .is_some_and(|value| !value.is_empty());
    let has_tool_name_mapping = !coordination.tool_name_mapping.is_empty();

    if coordination.tool_name_mapping.len() > 32 {
        return Err(ServiceError::InvalidOperation {
            message: "coordination tool_name_mapping must not exceed 32 entries".to_string(),
            request_id: None,
        });
    }
    for (provider_tool_name, canonical_tool_name) in &coordination.tool_name_mapping {
        let provider_tool_name_is_valid = !provider_tool_name.is_empty()
            && provider_tool_name.len() <= 512
            && provider_tool_name.trim() == provider_tool_name;
        if !provider_tool_name_is_valid {
            return Err(ServiceError::InvalidOperation {
                message: "coordination tool_name_mapping contains an invalid provider tool name"
                    .to_string(),
                request_id: None,
            });
        }
        if !matches!(
            canonical_tool_name.as_str(),
            "bcs_assign_task" | "bcs_send_task_message" | "bcs_task_complete"
        ) {
            return Err(ServiceError::InvalidOperation {
                message: format!(
                    "coordination tool_name_mapping has unsupported canonical tool '{canonical_tool_name}'"
                ),
                request_id: None,
            });
        }
    }

    match coordination.mode {
        CoordinationMode::McporterMcp => {
            if !has_mcp_server || !has_mcporter_command {
                return Err(ServiceError::InvalidOperation {
                    message: "mcporter_mcp coordination requires mcp_server and mcporter_command"
                        .to_string(),
                    request_id: None,
                });
            }
            if has_tool_name_mapping {
                return Err(ServiceError::InvalidOperation {
                    message: "mcporter_mcp coordination must not set tool_name_mapping"
                        .to_string(),
                    request_id: None,
                });
            }
        }
        CoordinationMode::NativeMcp => {
            if !has_mcp_server {
                return Err(ServiceError::InvalidOperation {
                    message: "native_mcp coordination requires mcp_server".to_string(),
                    request_id: None,
                });
            }
            if has_mcporter_command {
                return Err(ServiceError::InvalidOperation {
                    message: "native_mcp coordination must not set mcporter_command".to_string(),
                    request_id: None,
                });
            }
        }
        CoordinationMode::NativeTool => {
            if has_mcp_server || has_mcporter_command || has_tool_name_mapping {
                return Err(ServiceError::InvalidOperation {
                    message: "native_tool coordination must not set mcp_server, mcporter_command, or tool_name_mapping"
                        .to_string(),
                    request_id: None,
                });
            }
        }
        CoordinationMode::Disabled => {}
        CoordinationMode::LegacyUpstream => {
            return Err(ServiceError::InvalidOperation {
                message: "legacy_upstream is not a provider coordination mode".to_string(),
                request_id: None,
            });
        }
    }
    Ok(())
}

fn provider_config(
    webhook_url: Option<&str>,
    auth_mode: ProviderAuthMode,
    protocol_version: &str,
    coordination: Option<ProviderCoordinationConfig>,
) -> ServiceResult<String> {
    let auth_mode = match auth_mode {
        ProviderAuthMode::StaticBearer => "static_bearer",
        ProviderAuthMode::AgentPass => "agentpass",
        ProviderAuthMode::AgentPassSubbots => "agentpass-subbots",
        ProviderAuthMode::ProviderAdmin => "provider_admin",
    };
    let mut config = serde_json::json!({
        "downlink": {
            "enabled": true,
            "webhook_url": webhook_url,
            "auth_mode": auth_mode,
            "protocol_version": protocol_version
        }
    });
    if let Some(coordination) = coordination {
        validate_provider_coordination_config(&coordination)?;
        config["coordination"] = serde_json::to_value(coordination)?;
    }
    Ok(config.to_string())
}

fn replace_webhook_url(config: &str, webhook_url: &str) -> ServiceResult<String> {
    let mut value: Value = serde_json::from_str(config)?;
    let Some(downlink) = value.get_mut("downlink").and_then(Value::as_object_mut) else {
        return Err(ServiceError::InvalidOperation {
            message: "provider downlink config is missing".to_string(),
            request_id: None,
        });
    };
    downlink.insert(
        "webhook_url".to_string(),
        Value::String(webhook_url.to_string()),
    );
    Ok(value.to_string())
}

fn replace_admin_callback_url(config: &str, admin_callback_url: &str) -> ServiceResult<String> {
    let mut value: Value = serde_json::from_str(config)?;
    let Some(config) = value.as_object_mut() else {
        return Err(ServiceError::InvalidOperation {
            message: "provider config is not an object".to_string(),
            request_id: None,
        });
    };
    config.insert(
        "admin_callback_url".to_string(),
        Value::String(admin_callback_url.to_string()),
    );
    Ok(value.to_string())
}

fn replace_protocol_version(config: &str, protocol_version: &str) -> ServiceResult<String> {
    // Same validation as register: only "1.0" / "2.0" are supported.
    let normalized = match protocol_version.trim() {
        "" | "1.0" => "1.0",
        "2.0" => "2.0",
        other => {
            return Err(ServiceError::InvalidOperation {
                message: format!("unsupported protocol_version '{other}'"),
                request_id: None,
            });
        }
    };
    let mut value: Value = serde_json::from_str(config)?;
    let Some(downlink) = value.get_mut("downlink").and_then(Value::as_object_mut) else {
        return Err(ServiceError::InvalidOperation {
            message: "provider downlink config is missing".to_string(),
            request_id: None,
        });
    };
    downlink.insert(
        "protocol_version".to_string(),
        Value::String(normalized.to_string()),
    );
    Ok(value.to_string())
}

fn replace_coordination_config(
    config: &str,
    coordination: ProviderCoordinationConfig,
) -> ServiceResult<String> {
    validate_provider_coordination_config(&coordination)?;
    let mut value: Value = serde_json::from_str(config)?;
    value["coordination"] = serde_json::to_value(coordination)?;
    Ok(value.to_string())
}

fn replace_organization_management_config(
    config: &str,
    organization_management: ProviderOrganizationManagementConfig,
) -> ServiceResult<String> {
    parse_organization_management_config(config)?;
    let mut value: Value = serde_json::from_str(config)?;
    value["organization_management"] = serde_json::to_value(organization_management)?;
    Ok(value.to_string())
}

/// Merge a caller-capability declaration into the provider config
/// (`$.caller_capabilities`, spec §10.7.3). Round-trips through the
/// domain type so a partially-written or hand-edited JSON stays caught by
/// the strict `ProviderCallerCapabilities` shape on every later read.
fn replace_caller_capabilities(
    config: &str,
    caller_capabilities: &bcs_service_api::ProviderCallerCapabilities,
) -> ServiceResult<String> {
    let mut value: Value = serde_json::from_str(config)?;
    // Round-trip validates the declared shape end-to-end, exactly the way
    // `ProviderRepoPort::get_caller_capabilities` parses it later.
    value["caller_capabilities"] = serde_json::to_value(caller_capabilities)?;
    let encoded = value.to_string();
    let root: Value = serde_json::from_str(&encoded)?;
    match root.get("caller_capabilities") {
        Some(value) => serde_json::from_value::<bcs_service_api::ProviderCallerCapabilities>(value.clone())?,
        None => return Err(ServiceError::InternalError("caller capabilities merge failed".into())),
    };
    Ok(encoded)
}

fn ensure_provider_owner(provider: &ProviderRecord, staff_no: &str) -> ServiceResult<()> {
    let staff_no = staff_no.trim();
    if staff_no.is_empty() {
        return Err(ServiceError::Unauthorized(
            "valid human identity is required".to_string(),
        ));
    }
    let owners: Vec<String> = serde_json::from_str(&provider.owners)
        .map_err(|_| ServiceError::Forbidden("provider_owner_required".to_string()))?;
    let is_owner = owners.iter().any(|owner| owner.trim() == staff_no);
    if is_owner {
        Ok(())
    } else {
        Err(ServiceError::Forbidden(
            "provider_owner_required".to_string(),
        ))
    }
}

mod management;
mod bots;

#[path = "provider_core/initial_authority.rs"]
mod initial_authority;
