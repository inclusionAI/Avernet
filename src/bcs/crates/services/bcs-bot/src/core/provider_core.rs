use std::sync::Arc;

use async_trait::async_trait;
use bcs_route_security::OutboundUrlGuard;
use serde_json::Value;
use tracing::{info, warn};

use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, CoordinationMode, ProviderAuthMode, ProviderBotBinding,
    ProviderBotBindingRepoPort, ProviderCoordinationConfig, ProviderBotCoreService, ProviderCoreService,
    ProviderCredential, ProviderCredentialRepoPort, ProviderOrganizationManagementConfig,
    ProviderRecord, ProviderRepoPort, RegisterProviderBotParams, RegisteredProvider,
    RuntimeBotIdentity, ServiceError, ServiceResult,
};

use super::ids::{new_bot_uuid, new_provider_id, new_session_token};

#[derive(Clone)]
pub struct ProviderCore {
    providers: Arc<dyn ProviderRepoPort>,
    credentials: Arc<dyn ProviderCredentialRepoPort>,
    bindings: Arc<dyn ProviderBotBindingRepoPort>,
    registry: Arc<dyn BotRegistryCoreService>,
    webhook_url_guard: OutboundUrlGuard,
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
        }
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
            bot_name,
            summary,
            owners,
            provider_bot_ref,
            domains,
            skills,
            scopes,
            bot_uuid,
        } = params;
        let provider = self
            .authenticated_provider(provider_id, provider_admin_token)
            .await?;
        validate_external_id("provider_bot_ref", &provider_bot_ref)?;
        let owner = owners.first().map(|owner| owner.trim()).unwrap_or_default();
        if owners.len() != 1 || owner.is_empty() {
            return Err(ServiceError::InvalidOperation {
                message: "owners must contain exactly one non-empty staff_no".to_string(),
                request_id: None,
            });
        }
        if let Some(existing_binding) = self
            .bindings
            .get_binding_by_provider_ref(&provider.provider_id, &provider_bot_ref)
            .await?
        {
            info!(
                provider_id = %provider.provider_id,
                bot_uuid = %existing_binding.bot_uuid,
                provider_bot_ref = %provider_bot_ref,
                "register_provider_bot: provider bot ref already registered; returning existing binding"
            );
            return Ok((existing_binding, None));
        }

        let provider_auth_mode = parse_downlink_config(&provider.config)?.auth_mode;
        let capabilities = BotCapabilities {
            name: Some(bot_name),
            summary,
            domains,
            skills,
            scopes,
            binding_channels: None,
            hidden: false,
            visibility: "protected".to_string(),
            agent_code: (provider_auth_mode == ProviderAuthMode::AgentPass)
                .then_some(provider_bot_ref.clone()),
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
        let session_token = new_session_token();
        let bot_runtime_token = matches!(
            provider_auth_mode,
            ProviderAuthMode::StaticBearer | ProviderAuthMode::ProviderAdmin
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
                    provider_id = %provider.provider_id,
                    bot_uuid = %bot_uuid,
                    error = %err,
                    "register_provider_bot: registry.register failed"
                );
            })?;

        let now = now_ms();
        let binding = ProviderBotBinding {
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
}

#[derive(Debug, Clone)]
pub(crate) struct DownlinkConfig {
    pub enabled: bool,
    pub webhook_url: String,
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
    let webhook_url = downlink
        .get("webhook_url")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| ServiceError::InvalidOperation {
            message: "provider downlink webhook_url is missing".to_string(),
            request_id: None,
        })?
        .to_string();
    let auth_mode = match downlink.get("auth_mode").and_then(Value::as_str) {
        Some("static_bearer") => ProviderAuthMode::StaticBearer,
        Some("agentpass") => ProviderAuthMode::AgentPass,
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

pub(crate) fn parse_coordination_config(
    config: &str,
) -> ServiceResult<ProviderCoordinationConfig> {
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
    ProviderOrganizationManagementConfig::from_provider_config(config)
        .map_err(ServiceError::from)
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

fn validate_webhook_url(guard: &OutboundUrlGuard, webhook_url: &str) -> ServiceResult<()> {
    guard.validate_configured_http_url(webhook_url).map_err(|error| {
        ServiceError::InvalidOperation {
            message: format!("webhook_url is not allowed: {error}"),
            request_id: None,
        }
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

    match coordination.mode {
        CoordinationMode::McporterMcp => {
            if !has_mcp_server || !has_mcporter_command {
                return Err(ServiceError::InvalidOperation {
                    message: "mcporter_mcp coordination requires mcp_server and mcporter_command"
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
            if has_mcp_server || has_mcporter_command {
                return Err(ServiceError::InvalidOperation {
                    message: "native_tool coordination must not set mcp_server or mcporter_command"
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
    webhook_url: &str,
    auth_mode: ProviderAuthMode,
    protocol_version: &str,
    coordination: Option<ProviderCoordinationConfig>,
) -> ServiceResult<String> {
    let auth_mode = match auth_mode {
        ProviderAuthMode::StaticBearer => "static_bearer",
        ProviderAuthMode::AgentPass => "agentpass",
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

fn ensure_provider_owner(provider: &ProviderRecord, staff_no: &str) -> ServiceResult<()> {
    let staff_no = staff_no.trim();
    if staff_no.is_empty() {
        return Err(ServiceError::Unauthorized(
            "valid human identity is required".to_string(),
        ));
    }
    let owners: Vec<String> = serde_json::from_str(&provider.owners).map_err(|_| {
        ServiceError::Forbidden("provider_owner_required".to_string())
    })?;
    let is_owner = owners.iter().any(|owner| owner.trim() == staff_no);
    if is_owner {
        Ok(())
    } else {
        Err(ServiceError::Forbidden("provider_owner_required".to_string()))
    }
}

#[async_trait]
impl ProviderCoreService for ProviderCore {
    async fn register_provider(
        &self,
        name: String,
        webhook_url: String,
        auth_mode: ProviderAuthMode,
        created_by: String,
        protocol_version: Option<String>,
        coordination: Option<ProviderCoordinationConfig>,
    ) -> ServiceResult<RegisteredProvider> {
        let provider_id = new_provider_id();
        validate_external_id("provider_id", &provider_id)?;
        validate_webhook_url(&self.webhook_url_guard, &webhook_url)?;
        let protocol_version = match protocol_version.as_deref().map(str::trim) {
            None | Some("") | Some("1.0") => "1.0",
            Some("2.0") => "2.0",
            Some(other) => {
                return Err(ServiceError::InvalidOperation {
                    message: format!("unsupported protocol_version '{other}'"),
                    request_id: None,
                });
            }
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
        let provider = ProviderRecord {
            provider_id: provider_id.clone(),
            name,
            config: provider_config(&webhook_url, auth_mode, protocol_version, coordination)?,
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
            .ok_or_else(|| ServiceError::Unauthorized("invalid provider admin token".to_string()))?;
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

    async fn get_downlink_credential(&self, provider_id: &str) -> ServiceResult<ProviderCredential> {
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
        let current = self
            .provider_admin_for_path(provider_id, provider_admin_token)
            .await?;
        ensure_provider_owner(&current, authenticated_staff_id)?;
        let mut config = match webhook_url {
            Some(webhook_url) => {
                validate_webhook_url(&self.webhook_url_guard, &webhook_url)?;
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
        if let Some(mut organization_management) = organization_management {
            for manager_provider_id in &organization_management.authorized_manager_provider_ids {
                validate_external_id("authorized_manager_provider_id", manager_provider_id)?;
            }
            organization_management
                .authorized_manager_provider_ids
                .retain(|manager_provider_id| manager_provider_id != provider_id);
            organization_management.authorized_manager_provider_ids.sort();
            organization_management.authorized_manager_provider_ids.dedup();
            let manager_providers = self
                .providers
                .list_providers_by_ids(
                    &organization_management.authorized_manager_provider_ids,
                )
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
        self.providers
            .update_provider_metadata(
                provider_id,
                name.as_deref(),
                config.as_deref(),
                now_ms(),
            )
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
        let bot_uuid = self
            .registry
            .find_bot_by_token(bot_runtime_token)
            .await
            .ok_or_else(|| ServiceError::Unauthorized("invalid bot runtime token".to_string()))?;
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
        if auth_mode != ProviderAuthMode::StaticBearer {
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
            .ok_or_else(|| ServiceError::Unauthorized("invalid provider admin token".to_string()))?;
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
                ServiceError::BotNotFound(format!(
                    "provider bot '{}' not found",
                    provider_bot_ref
                ))
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
        self.bindings
            .update_binding_disabled(bot_uuid, disabled, now_ms())
            .await?
            .ok_or_else(|| ServiceError::InvalidOperation {
                message: format!("provider bot '{}' not found", bot_uuid),
                request_id: None,
            })
    }
}
