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

#[derive(Clone)]
pub struct ProviderCore {
    providers: Arc<dyn ProviderRepoPort>,
    credentials: Arc<dyn ProviderCredentialRepoPort>,
    bindings: Arc<dyn ProviderBotBindingRepoPort>,
    registry: Arc<dyn BotRegistryCoreService>,
    webhook_url_guard: OutboundUrlGuard,
    bot_providers: Option<Arc<dyn BotProviderRepoPort>>,
}

impl ProviderCore {
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
        }
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
        } = params;
        let provider = self
            .authenticated_provider(provider_id, provider_admin_token)
            .await?;
        validate_external_id("provider_bot_ref", &provider_bot_ref)?;
        let downlink = parse_downlink_config(&provider.config)?;
        let identity = bcs_service_api::types::provider_identity::normalize(downlink.auth_mode, &provider_bot_ref).map_err(|message| ServiceError::InvalidOperation { message: message.into(), request_id: None })?;
        let provider_bot_ref = identity.provider_bot_ref;
        let bot_uuid = if downlink.auth_mode == ProviderAuthMode::AgentPassSubbots { None } else { bot_uuid };
        let owner = owners.first().map(|owner| owner.trim()).unwrap_or_default();
        if owners.len() != 1 || owner.is_empty() {
            return Err(ServiceError::InvalidOperation {
                message: "owners must contain exactly one non-empty staff_no".to_string(),
                request_id: None,
            });
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
    // Same validation as register: "1.0", "2.0" or A2A.
    let Some(normalized) = bcs_service_api::normalize_provider_protocol_version(Some(protocol_version)) else {
        return Err(ServiceError::InvalidOperation {
            message: format!("unsupported protocol_version '{}'", protocol_version.trim()),
            request_id: None,
        });
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

#[async_trait]
impl ProviderCoreService for ProviderCore {
    async fn get_provider_by_slug(&self, slug: &str) -> ServiceResult<Option<ProviderBasicInfo>> {
        validate_provider_slug(slug)?;
        let Some(provider) = self.providers.get_provider_by_slug(slug).await? else { return Ok(None); };
        let downlink = parse_downlink_config(&provider.config)
            .map_err(|_| ServiceError::InternalError("invalid provider configuration".into()))?;
        Ok(Some(ProviderBasicInfo {
            provider_id: provider.provider_id,
            slug: provider.slug.ok_or_else(|| ServiceError::InternalError("provider slug is missing".into()))?,
            name: provider.name,
            auth_mode: downlink.auth_mode,
            enabled: !provider.disabled,
            protocol_version: downlink.protocol_version,
            created_at: provider.created_at,
            updated_at: provider.updated_at,
        }))
    }

    async fn register_provider(
        &self,
        name: String,
        webhook_url: Option<String>,
        auth_mode: ProviderAuthMode,
        created_by: String,
        protocol_version: Option<String>,
        coordination: Option<ProviderCoordinationConfig>,
    ) -> ServiceResult<RegisteredProvider> {
        self.register_provider_with_slug(name, webhook_url, auth_mode, created_by,
            protocol_version, coordination, None, None).await
    }

    async fn register_provider_with_slug(
        &self, name: String, webhook_url: Option<String>, auth_mode: ProviderAuthMode,
        created_by: String, protocol_version: Option<String>,
        coordination: Option<ProviderCoordinationConfig>, slug: Option<String>,
        admin_callback_url: Option<String>,
    ) -> ServiceResult<RegisteredProvider> {
        self.register_provider_with_policy(name, webhook_url, auth_mode, created_by, protocol_version, coordination, slug, admin_callback_url, None).await
    }

    async fn register_provider_with_policy(
        &self, name: String, webhook_url: Option<String>, auth_mode: ProviderAuthMode,
        created_by: String, protocol_version: Option<String>,
        coordination: Option<ProviderCoordinationConfig>, slug: Option<String>,
        admin_callback_url: Option<String>, scope: Option<ProviderCollaborationScope>,
    ) -> ServiceResult<RegisteredProvider> {
        if let Some(slug) = slug.as_deref() { validate_provider_slug(slug)?; }
        let provider_id = new_provider_id();
        validate_external_id("provider_id", &provider_id)?;
        if let Some(url) = webhook_url.as_deref() {
            validate_outbound_url(&self.webhook_url_guard, "webhook_url", url)?;
        }
        if let Some(url) = admin_callback_url.as_deref() {
            validate_outbound_url(&self.webhook_url_guard, "admin_callback_url", url)?;
        }
        let Some(protocol_version) = bcs_service_api::normalize_provider_protocol_version(protocol_version.as_deref()) else {
            return Err(ServiceError::InvalidOperation {
                message: format!("unsupported protocol_version '{}'", protocol_version.as_deref().unwrap_or_default().trim()),
                request_id: None,
            });
        };
        let created_by = created_by.trim().to_string();
        if created_by.is_empty() {
            return Err(ServiceError::Unauthorized(
                "valid human identity is required".to_string(),
            ));
        }

        let now = now_ms();
        let provider_admin_token = generated_id("bcs_pa");
        let bcs_to_provider_token = generated_id("bcs_b2p");
        let owners = serde_json::to_string(&vec![created_by.clone()])?;
        let mut config = provider_config(webhook_url.as_deref(), auth_mode, protocol_version, coordination)?;
        if let Some(url) = admin_callback_url.as_deref() {
            config = replace_admin_callback_url(&config, url)?;
        }
        if let Some(scope) = scope {
            let mut value: serde_json::Map<String, Value> = serde_json::from_str(&config)?;
            value.insert("collaboration_scope".into(), serde_json::to_value(scope)?);
            config = serde_json::to_string(&value)?;
        }
        let provider = ProviderRecord {
            provider_id: provider_id.clone(),
            slug,
            name,
            config,
            created_by,
            owners,
            disabled: false,
            created_at: now,
            updated_at: now,
        };
        self.providers
            .insert_provider(provider.clone())
            .await
            .inspect_err(|err| {
                warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    provider_id = %provider_id,
                    error = %err,
                    "register_provider: insert_provider failed"
                );
            })?;
        info!(provider_id = %provider_id, "register_provider: provider record inserted");
        self.credentials
            .insert_credential(ProviderCredential {
                provider_id: provider_id.clone(),
                credential_kind: "provider_admin".to_string(),
                secret_value: provider_admin_token.clone(),
                disabled: false,
                created_at: now,
                updated_at: now,
            })
            .await
            .inspect_err(|err| {
                warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    provider_id = %provider_id,
                    kind = "provider_admin",
                    error = %err,
                    "register_provider: insert_credential failed; provider record orphaned"
                );
            })?;
        self.credentials
            .insert_credential(ProviderCredential {
                provider_id: provider_id.clone(),
                credential_kind: "downlink_bcs_to_provider".to_string(),
                secret_value: bcs_to_provider_token.clone(),
                disabled: false,
                created_at: now,
                updated_at: now,
            })
            .await
            .inspect_err(|err| {
                warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    provider_id = %provider_id,
                    kind = "downlink_bcs_to_provider",
                    error = %err,
                    "register_provider: insert_credential failed; provider+admin_credential orphaned"
                );
            })?;
        info!(
            provider_id = %provider_id,
            "register_provider: completed"
        );

        Ok(RegisteredProvider {
            provider,
            provider_admin_token,
            bcs_to_provider_token,
        })
    }

    async fn authenticate_provider_admin(&self, token: &str) -> ServiceResult<ProviderRecord> {
        let credential = self
            .credentials
            .get_credential_by_secret("provider_admin", token)
            .await?
            .ok_or_else(|| {
                ServiceError::Unauthorized("invalid provider admin token".to_string())
            })?;
        if credential.disabled {
            return Err(ServiceError::Unauthorized(
                "provider admin token is disabled".to_string(),
            ));
        }
        self.providers
            .get_provider(&credential.provider_id)
            .await?
            .ok_or_else(|| ServiceError::InvalidOperation {
                message: format!("provider '{}' not found", credential.provider_id),
                request_id: None,
            })
    }

    async fn get_downlink_credential(
        &self,
        provider_id: &str,
    ) -> ServiceResult<ProviderCredential> {
        let credential = self
            .credentials
            .get_credential_by_kind(provider_id, "downlink_bcs_to_provider")
            .await?
            .ok_or_else(|| ServiceError::InvalidOperation {
                message: format!("provider '{}' downlink credential not found", provider_id),
                request_id: None,
            })?;
        if credential.disabled {
            return Err(ServiceError::InvalidOperation {
                message: format!("provider '{}' downlink credential is disabled", provider_id),
                request_id: None,
            });
        }
        Ok(credential)
    }

    async fn get_provider(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
    ) -> ServiceResult<ProviderRecord> {
        self.provider_admin_for_path(provider_id, provider_admin_token)
            .await
    }

    async fn update_provider(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
        authenticated_staff_id: &str,
        name: Option<String>,
        webhook_url: Option<String>,
        protocol_version: Option<String>,
        coordination: Option<ProviderCoordinationConfig>,
        organization_management: Option<ProviderOrganizationManagementConfig>,
    ) -> ServiceResult<ProviderRecord> {
        self.update_provider_with_slug(provider_id, provider_admin_token, authenticated_staff_id,
            name, webhook_url, protocol_version, coordination, organization_management, None, None).await
    }

    async fn update_provider_with_slug(
        &self, provider_id: &str, provider_admin_token: &str, authenticated_staff_id: &str,
        name: Option<String>, webhook_url: Option<String>, protocol_version: Option<String>,
        coordination: Option<ProviderCoordinationConfig>,
        organization_management: Option<ProviderOrganizationManagementConfig>, slug: Option<String>,
        admin_callback_url: Option<String>,
    ) -> ServiceResult<ProviderRecord> {
        self.update_provider_with_policy(provider_id, provider_admin_token, authenticated_staff_id, name, webhook_url, protocol_version, coordination, organization_management, slug, admin_callback_url, None).await
    }

    async fn update_provider_with_policy(
        &self, provider_id: &str, provider_admin_token: &str, authenticated_staff_id: &str,
        name: Option<String>, webhook_url: Option<String>, protocol_version: Option<String>,
        coordination: Option<ProviderCoordinationConfig>,
        organization_management: Option<ProviderOrganizationManagementConfig>, slug: Option<String>,
        admin_callback_url: Option<String>, scope: Option<ProviderCollaborationScope>,
    ) -> ServiceResult<ProviderRecord> {
        let current = self
            .provider_admin_for_path(provider_id, provider_admin_token)
            .await?;
        ensure_provider_owner(&current, authenticated_staff_id)?;
        if let Some(slug) = slug.as_deref() { validate_provider_slug(slug)?; }
        let mut config = match webhook_url {
            Some(webhook_url) => {
                validate_outbound_url(&self.webhook_url_guard, "webhook_url", &webhook_url)?;
                Some(replace_webhook_url(&current.config, &webhook_url)?)
            }
            None => None,
        };
        if let Some(protocol_version) = protocol_version {
            let source = config.as_deref().unwrap_or(&current.config);
            config = Some(replace_protocol_version(source, &protocol_version)?);
        }
        if let Some(coordination) = coordination {
            let source = config.as_deref().unwrap_or(&current.config);
            config = Some(replace_coordination_config(source, coordination)?);
        }
        if let Some(url) = admin_callback_url.as_deref() {
            validate_outbound_url(&self.webhook_url_guard, "admin_callback_url", url)?;
            let source = config.as_deref().unwrap_or(&current.config);
            config = Some(replace_admin_callback_url(source, url)?);
        }
        if let Some(mut organization_management) = organization_management {
            for manager_provider_id in &organization_management.authorized_manager_provider_ids {
                validate_external_id("authorized_manager_provider_id", manager_provider_id)?;
            }
            organization_management
                .authorized_manager_provider_ids
                .retain(|manager_provider_id| manager_provider_id != provider_id);
            organization_management
                .authorized_manager_provider_ids
                .sort();
            organization_management
                .authorized_manager_provider_ids
                .dedup();
            let manager_providers = self
                .providers
                .list_providers_by_ids(&organization_management.authorized_manager_provider_ids)
                .await?;
            if let Some(unknown_provider_id) = organization_management
                .authorized_manager_provider_ids
                .iter()
                .find(|manager_provider_id| {
                    !manager_providers.iter().any(|provider| {
                        provider.provider_id.as_str() == manager_provider_id.as_str()
                    })
                })
            {
                return Err(ServiceError::InvalidOperation {
                    message: format!("provider '{}' not found", unknown_provider_id),
                    request_id: None,
                });
            }
            let source = config.as_deref().unwrap_or(&current.config);
            config = Some(replace_organization_management_config(
                source,
                organization_management,
            )?);
        }
        if let Some(scope) = scope {
            let current_scope = ProviderCollaborationScope::from_config(&current.config)?;
            if current_scope == ProviderCollaborationScope::SameOwner && scope == ProviderCollaborationScope::Standard {
                return Err(ServiceError::Forbidden("same_owner Provider policy cannot be relaxed through Provider administration".into()));
            }
            let mut value: serde_json::Map<String, Value> = serde_json::from_str(config.as_deref().unwrap_or(&current.config))?;
            value.insert("collaboration_scope".into(), serde_json::to_value(scope)?);
            config = Some(serde_json::to_string(&value)?);
        }
        self.providers
            .update_provider_metadata(provider_id, name.as_deref(), config.as_deref(), slug.as_deref(), now_ms())
            .await?
            .ok_or_else(|| ServiceError::InvalidOperation {
                message: format!("provider '{}' not found", provider_id),
                request_id: None,
            })
    }

    async fn update_provider_admin_callback_url(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
        authenticated_staff_id: &str,
        admin_callback_url: String,
    ) -> ServiceResult<ProviderRecord> {
        let current = self
            .provider_admin_for_path(provider_id, provider_admin_token)
            .await?;
        ensure_provider_owner(&current, authenticated_staff_id)?;
        validate_outbound_url(
            &self.webhook_url_guard,
            "admin_callback_url",
            &admin_callback_url,
        )?;
        let config = replace_admin_callback_url(&current.config, &admin_callback_url)?;
        self.providers
            .update_provider_metadata(provider_id, None, Some(&config), None, now_ms())
            .await?
            .ok_or_else(|| ServiceError::InvalidOperation {
                message: format!("provider '{}' not found", provider_id),
                request_id: None,
            })
    }

    async fn set_provider_disabled(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
        authenticated_staff_id: &str,
        disabled: bool,
    ) -> ServiceResult<ProviderRecord> {
        let provider = self
            .provider_admin_for_path(provider_id, provider_admin_token)
            .await?;
        ensure_provider_owner(&provider, authenticated_staff_id)?;
        self.providers
            .update_provider_disabled(&provider.provider_id, disabled, now_ms())
            .await?
            .ok_or_else(|| ServiceError::InvalidOperation {
                message: format!("provider '{}' not found", provider_id),
                request_id: None,
            })
    }
}

#[async_trait]
impl ProviderBotCoreService for ProviderCore {
    async fn retire_registered_bot(
        &self,
        params: bcs_service_api::core::RetireProviderBotParams,
    ) -> ServiceResult<ProviderRetirement> {
        use bcs_service_api::core::{
            ProviderRetire, ProviderRetireBranch,
        };
        let RetireProviderBotParams {
            provider_id,
            provider_admin_token,
            provider_bot_ref,
            allow_unbound_owner_suffixed_bot,
        } = params;
        // The credential verification stays in the Core
        // (the same flows the read/update paths run); the
        // token NEVER enters the mutation, audit rows or
        // the return value.
        let provider = self
            .authenticated_provider(&provider_id, &provider_admin_token)
            .await?;
        if provider.provider_id != provider_id {
            return Err(ServiceError::Forbidden(
                "provider_id_mismatch".to_string(),
            ));
        }
        validate_external_id("provider_bot_ref", &provider_bot_ref)?;
        let Some(metadata) = &self.bot_providers else {
            return Err(ServiceError::InvalidOperation {
                message: "provider bot retirement requires the \
                          Bot Provider metadata adapter"
                    .to_string(),
                request_id: None,
            });
        };

        // Resolve the Bot within the existing limits on the
        // three durable identity branches. The flagged
        // LegacyUnbound lookup NEVER grants delete power by
        // itself — the owner-suffix admission and the store's
        // re-verified rules stay intact.
        let (bot_id, expected_branch) = if let Some(record) =
            metadata
                .get_provider_bot_by_ref(
                    &provider.provider_id,
                    &provider_bot_ref,
                )
                .await?
        {
            // The accepted Provider membership routes the
            // retirement; the tombstone KEEPS the metadata,
            // so a cleanup retry re-derives the same branch.
            (record.bot_uuid, ProviderRetireBranch::Metadata)
        } else if let Some(binding) = self
            .bindings
            .get_binding_by_provider_ref(
                &provider.provider_id,
                &provider_bot_ref,
            )
            .await?
        {
            // Legacy gateway before metadata backfill: the
            // binding routes the retirement.
            (
                binding.bot_uuid,
                ProviderRetireBranch::BindingFallback,
            )
        } else if allow_unbound_owner_suffixed_bot
            && bcs_service_api::core::provider::
                is_owner_suffixed_bot_id(&provider_bot_ref)
        {
            // Existing LegacyUnbound admission (owner-suffixed
            // Bot that never gained metadata or a binding). A
            // cleanup retry finds the identity on the retained
            // tombstone — never a resurrected one.
            if self.registry.get(&provider_bot_ref).await.is_none()
                && self
                    .registry
                    .get_including_deleted(&provider_bot_ref)
                    .await
                    .is_none()
            {
                return Err(ServiceError::BotNotFound(
                    provider_bot_ref.to_string(),
                ));
            }
            (
                provider_bot_ref.clone(),
                ProviderRetireBranch::LegacyUnbound,
            )
        } else {
            return Err(ServiceError::BotNotFound(
                provider_bot_ref.to_string(),
            ));
        };

        // The verified Provider identity is the REAL actor
        // (spec §3.1); no Human impersonation, no token.
        let mutation =
            bcs_service_api::types::bot_authority::AuthorityMutation {
                mutation_id: format!(
                    "retire_{}",
                    uuid::Uuid::new_v4().simple()
                ),
                actor:
                    bcs_service_api::types::bot_authority::AuthorityActor::
                        Provider {
                            provider_id: provider.provider_id.clone(),
                        },
            };
        // ONE atomic retirement command — never chained into
        // an ordinary delete afterwards (spec §7.1).
        metadata
            .retire_with_authority(ProviderRetire {
                provider_id: provider.provider_id.clone(),
                bot_id,
                provider_bot_ref,
                mutation,
                expected_branch,
            })
            .await
    }

    async fn update_provider_bot_webhook(
        &self, provider_id: &str, provider_admin_token: &str,
        provider_bot_ref: &str, webhook_url: Option<String>,
    ) -> ServiceResult<UpdateProviderBotCoreResult> {
        let provider = self.authenticated_provider(provider_id, provider_admin_token).await?;
        validate_external_id("provider_bot_ref", provider_bot_ref)?;
        let binding = self.bindings.get_binding_by_provider_ref(provider_id, provider_bot_ref)
            .await?.ok_or_else(|| ServiceError::BotNotFound(provider_bot_ref.to_string()))?;
        if binding.provider_id != provider.provider_id {
            return Err(ServiceError::Forbidden("provider_id_mismatch".to_string()));
        }
        let bot = self.registry.try_get(&binding.bot_uuid).await?
            .ok_or_else(|| ServiceError::BotNotFound(binding.bot_uuid.clone()))?;
        if let Some(url) = webhook_url.as_deref() {
            validate_outbound_url(&self.webhook_url_guard, "webhook_url", url)?;
        } else if parse_downlink_config(&provider.config)?.webhook_url.is_none() {
            return Err(ServiceError::InvalidOperation {
                message: "missing_delivery_endpoint: cannot inherit an absent Provider webhook_url".to_string(),
                request_id: None,
            });
        }
        let updated = self.bindings.update_binding_webhook_url(provider_id, &binding.bot_uuid,
            webhook_url.as_deref(), now_ms()).await?
            .ok_or_else(|| ServiceError::BotNotFound(binding.bot_uuid.clone()))?;
        Ok(UpdateProviderBotCoreResult { binding: updated, capabilities: bot.capabilities })
    }

    async fn register_provider_bot_with_bot_uuid(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
        params: RegisterProviderBotParams,
    ) -> ServiceResult<(ProviderBotBinding, Option<String>)> {
        self.register_provider_bot_internal(provider_id, provider_admin_token, params)
            .await
    }

    async fn list_provider_bots(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
    ) -> ServiceResult<Vec<ProviderBotBinding>> {
        self.authenticated_provider(provider_id, provider_admin_token)
            .await?;
        // Return all bindings registered by this provider in the store, excluding
        // soft-deleted (disabled) ones, regardless of whether each bot is
        // currently active in the registry.
        let bindings = self.bindings.list_bindings_by_provider(provider_id).await?;
        Ok(bindings
            .into_iter()
            .filter(|binding| !binding.disabled)
            .collect())
    }

    async fn get_provider_bot_binding_by_ref(
        &self,
        provider_id: &str,
        provider_bot_ref: &str,
    ) -> ServiceResult<Option<ProviderBotBinding>> {
        self.bindings
            .get_binding_by_provider_ref(provider_id, provider_bot_ref)
            .await
    }

    async fn get_provider_bot_binding_by_bot_uuid(
        &self,
        bot_uuid: &str,
    ) -> ServiceResult<Option<ProviderBotBinding>> {
        self.bindings.get_binding_by_bot_uuid(bot_uuid).await
    }

    async fn authenticate_static_bearer_event(
        &self,
        provider_id: &str,
        bot_runtime_token: &str,
    ) -> ServiceResult<RuntimeBotIdentity> {
        let bot_uuid = self.registry.resolve_bot_token(bot_runtime_token, None).await?
            .ok_or_else(|| ServiceError::Unauthorized("invalid bot runtime token".to_string()))?.bot.bot_id;
        let binding = self
            .bindings
            .get_binding_by_bot_uuid(&bot_uuid)
            .await?
            .ok_or_else(|| ServiceError::Unauthorized("bot is not provider managed".to_string()))?;
        if binding.provider_id != provider_id {
            return Err(ServiceError::Forbidden("provider_id_mismatch".to_string()));
        }
        let provider = self.load_provider_for_binding(&binding).await?;
        let auth_mode = parse_downlink_config(&provider.config)?.auth_mode;
        if !matches!(auth_mode, ProviderAuthMode::StaticBearer | ProviderAuthMode::AgentPassSubbots) {
            return Err(ServiceError::Unauthorized("auth_mode_mismatch".to_string()));
        }
        Ok(RuntimeBotIdentity {
            bot_uuid,
            provider_id: provider_id.to_string(),
        })
    }

    async fn authenticate_agentpass_event(
        &self,
        provider_id: &str,
        agent_code: &str,
    ) -> ServiceResult<RuntimeBotIdentity> {
        let provider = self.providers.get_provider(provider_id).await?.ok_or_else(|| ServiceError::ProviderNotFound(provider_id.into()))?;
        if provider.disabled { return Err(ServiceError::Unauthorized("provider is disabled".into())); }
        if parse_downlink_config(&provider.config)?.auth_mode == ProviderAuthMode::AgentPassSubbots {
            return Err(ServiceError::Unauthorized("ActorRequired: parent AgentPass requires a Bot Actor".into()));
        }
        let binding = self
            .bindings
            .get_binding_by_provider_ref(provider_id, agent_code)
            .await?
            .ok_or_else(|| ServiceError::Unauthorized("unknown agent_code".to_string()))?;
        let provider = self.load_provider_for_binding(&binding).await?;
        let auth_mode = parse_downlink_config(&provider.config)?.auth_mode;
        if auth_mode != ProviderAuthMode::AgentPass {
            return Err(ServiceError::Unauthorized("auth_mode_mismatch".to_string()));
        }
        Ok(RuntimeBotIdentity {
            bot_uuid: binding.bot_uuid,
            provider_id: provider_id.to_string(),
        })
    }

    async fn authenticate_agentpass_actor_event(&self, provider_id: &str, agent_code: &str, actor: &str) -> ServiceResult<RuntimeBotIdentity> {
        let bot = self.registry.resolve_agent_bot(agent_code, Some(actor)).await?.ok_or_else(|| ServiceError::Unauthorized("AgentPass Bot is not registered".into()))?;
        if bot.bot.provider_id.as_deref() != Some(provider_id) { return Err(ServiceError::Forbidden("provider_id_mismatch".into())); }
        Ok(RuntimeBotIdentity { bot_uuid: bot.bot.bot_id, provider_id: provider_id.into() })
    }

    async fn authenticate_provider_admin_event(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
        provider_bot_ref: &str,
    ) -> ServiceResult<RuntimeBotIdentity> {
        let credential = self
            .credentials
            .get_credential_by_secret("provider_admin", provider_admin_token)
            .await?
            .ok_or_else(|| {
                ServiceError::Unauthorized("invalid provider admin token".to_string())
            })?;
        if credential.disabled {
            return Err(ServiceError::Unauthorized(
                "provider admin token is disabled".to_string(),
            ));
        }
        if credential.provider_id != provider_id {
            return Err(ServiceError::Forbidden("provider_id_mismatch".to_string()));
        }
        let provider = self
            .providers
            .get_provider(&credential.provider_id)
            .await?
            .ok_or_else(|| ServiceError::InvalidOperation {
                message: format!("provider '{}' not found", credential.provider_id),
                request_id: None,
            })?;
        if provider.disabled {
            return Err(ServiceError::InvalidOperation {
                message: format!("provider '{}' is disabled", provider.provider_id),
                request_id: None,
            });
        }
        let auth_mode = parse_downlink_config(&provider.config)?.auth_mode;
        if auth_mode != ProviderAuthMode::ProviderAdmin {
            return Err(ServiceError::Unauthorized("auth_mode_mismatch".to_string()));
        }
        let binding = self
            .bindings
            .get_binding_by_provider_ref(&provider.provider_id, provider_bot_ref)
            .await?
            .ok_or_else(|| {
                ServiceError::BotNotFound(format!("provider bot '{}' not found", provider_bot_ref))
            })?;
        if binding.disabled {
            return Err(ServiceError::InvalidOperation {
                message: format!("provider bot '{}' is disabled", binding.bot_uuid),
                request_id: None,
            });
        }
        if self.registry.get(&binding.bot_uuid).await.is_none() {
            return Err(ServiceError::BotNotFound(binding.bot_uuid));
        }
        Ok(RuntimeBotIdentity {
            bot_uuid: binding.bot_uuid,
            provider_id: provider.provider_id,
        })
    }

    async fn get_provider_coordination_config(
        &self,
        provider_id: &str,
    ) -> ServiceResult<ProviderCoordinationConfig> {
        let provider = self
            .providers
            .get_provider(provider_id)
            .await?
            .ok_or_else(|| ServiceError::InvalidOperation {
                message: format!("provider '{}' not found", provider_id),
                request_id: None,
            })?;
        if provider.disabled {
            return Err(ServiceError::InvalidOperation {
                message: format!("provider '{}' is disabled", provider.provider_id),
                request_id: None,
            });
        }
        parse_coordination_config(&provider.config)
    }

    async fn set_provider_bot_disabled(
        &self,
        provider_id: &str,
        bot_uuid: &str,
        provider_admin_token: &str,
        disabled: bool,
    ) -> ServiceResult<ProviderBotBinding> {
        self.authenticated_provider(provider_id, provider_admin_token)
            .await?;
        let binding = self
            .bindings
            .get_binding_by_bot_uuid(bot_uuid)
            .await?
            .ok_or_else(|| ServiceError::InvalidOperation {
                message: format!("provider bot '{}' not found", bot_uuid),
                request_id: None,
            })?;
        if binding.provider_id != provider_id {
            return Err(ServiceError::Forbidden("provider_id_mismatch".to_string()));
        }
        let updated = self.bindings
            .update_binding_disabled(bot_uuid, disabled, now_ms())
            .await?
            .ok_or_else(|| ServiceError::InvalidOperation {
                message: format!("provider bot '{}' not found", bot_uuid),
                request_id: None,
            })?;
        if disabled && self.bot_providers.is_some() {
            // The projection has committed the tombstone; evict runtime caches.
            self.registry.soft_delete(bot_uuid).await;
        }
        Ok(updated)
    }

    async fn update_provider_bot(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
        provider_bot_ref: &str,
        name: Option<String>,
        summary: Option<String>,
        domains: Option<Vec<String>>,
        skills: Option<Vec<Skill>>,
        scopes: Option<Vec<String>>,
        visibility: Option<String>,
    ) -> ServiceResult<UpdateProviderBotCoreResult> {
        let provider = self
            .authenticated_provider(provider_id, provider_admin_token)
            .await?;
        validate_external_id("provider_bot_ref", provider_bot_ref)?;
        let binding = match self.bindings.get_binding_by_provider_ref(&provider.provider_id, provider_bot_ref).await? {
            Some(binding) => binding,
            None => {
                let metadata = self.bot_providers.as_ref().ok_or_else(|| ServiceError::BotNotFound(provider_bot_ref.into()))?;
                let record = metadata.get_provider_bot_by_ref(provider_id, provider_bot_ref).await?
                    .filter(|record| record.connection_mode == BotConnectionMode::Plugin && !record.is_deleted)
                    .ok_or_else(|| ServiceError::BotNotFound(provider_bot_ref.into()))?;
                ProviderBotBinding { bot_uuid: record.bot_uuid, provider_id:record.provider_id,
                    provider_bot_ref:record.provider_bot_ref, webhook_url:None, disabled:false,
                    created_at:0, updated_at:0 }
            }
        };
        if binding.provider_id != provider.provider_id {
            return Err(ServiceError::Forbidden("provider_id_mismatch".to_string()));
        }
        if binding.disabled {
            return Err(ServiceError::InvalidOperation {
                message: format!("provider bot '{}' is disabled", binding.bot_uuid),
                request_id: None,
            });
        }
        let existing = self
            .registry
            .try_get(&binding.bot_uuid)
            .await?
            .ok_or_else(|| ServiceError::BotNotFound(binding.bot_uuid.clone()))?;

        // Stored identity is immutable during ordinary capability updates.
        let agent_code = existing.capabilities.agent_code.clone();

        // PATCH-merge: each `Some` replaces (empty Vec clears), `None` keeps.
        // `agent_token` is left `None` (managed in-memory only, never via caps).
        if let Some(visibility) = visibility.as_deref() {
            let trimmed = visibility.trim();
            if !matches!(trimmed, "public" | "protected" | "private") {
                return Err(ServiceError::InvalidOperation {
                    message: "visibility must be 'public', 'protected', or 'private'"
                        .to_string(),
                    request_id: None,
                });
            }
        }

        let mut merged = existing.capabilities.clone();
        if let Some(name) = name {
            merged.name = Some(name);
        }
        if let Some(summary) = summary {
            merged.summary = Some(summary);
        }
        if let Some(domains) = domains {
            merged.domains = domains;
        }
        if let Some(skills) = skills {
            merged.skills = skills;
        }
        if let Some(scopes) = scopes {
            merged.scopes = scopes;
        }
        if let Some(visibility) = visibility {
            merged.visibility = visibility.trim().to_string();
        }
        merged.agent_code = agent_code;

        // Use `update_capabilities` (wholesale replacement) rather than
        // `register`, whose existing-bot merge skips empty capability arrays
        // — a PATCH that clears domains/skills/scopes must take effect in the
        // live registry and runtime discovery, not only in the response.
        self.registry
            .update_capabilities(&binding.bot_uuid, merged.clone())
            .await?;
        info!(
            provider_id = %provider.provider_id,
            bot_uuid = %binding.bot_uuid,
            provider_bot_ref = %provider_bot_ref,
            "update_provider_bot: capabilities updated in place"
        );
        Ok(UpdateProviderBotCoreResult {
            binding,
            capabilities: merged,
        })
    }
}
