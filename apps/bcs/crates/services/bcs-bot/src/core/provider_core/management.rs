use super::*;

#[async_trait]
impl ProviderCoreService for ProviderCore {
    async fn pin_a2a_provider_name(&self, provider_id: &str, name: &str) -> ServiceResult<()> {
        self.providers.pin_a2a_slug(provider_id, name).await
    }

    async fn a2a_authentication(&self,provider_id:&str,card:&serde_json::Value)
        ->ServiceResult<bcs_service_api::types::a2a_auth::A2aAuthentication> {
        use bcs_service_api::types::a2a_auth::{select_authentication,A2aAuthentication,PROVIDER_BEARER};
        let provider=self.providers.get_provider(provider_id).await?.ok_or_else(||ServiceError::Forbidden("Provider unavailable".into()))?;
        if provider.disabled {return Err(ServiceError::Forbidden("Provider unavailable".into()));}
        if select_authentication(card,true,false)==Ok(A2aAuthentication::Anonymous) {
            return Ok(A2aAuthentication::Anonymous);
        }
        let credential=self.credentials.get_credential_by_kind(provider_id,PROVIDER_BEARER).await?;
        let ready=credential.is_some_and(|value|!value.disabled && !value.secret_value.trim().is_empty());
        select_authentication(card,true,ready)
            .map_err(|message|ServiceError::InvalidOperation {message:message.into(),request_id:None})
    }

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

    async fn get_caller_capabilities(
        &self,
        provider_id: &str,
    ) -> ServiceResult<Option<bcs_service_api::port::repo::ProviderCallerCapabilities>> {
        self.providers.get_caller_capabilities(provider_id).await
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
            protocol_version, coordination, None, None, None).await
    }

    async fn register_provider_with_slug(
        &self, name: String, webhook_url: Option<String>, auth_mode: ProviderAuthMode,
        created_by: String, protocol_version: Option<String>,
        coordination: Option<ProviderCoordinationConfig>, slug: Option<String>,
        admin_callback_url: Option<String>,
        caller_capabilities: Option<bcs_service_api::ProviderCallerCapabilities>,
    ) -> ServiceResult<RegisteredProvider> {
        self.register_provider_with_policy(name, webhook_url, auth_mode, created_by, protocol_version, coordination, slug, admin_callback_url, None, caller_capabilities).await
    }

    async fn register_provider_with_policy(
        &self, name: String, webhook_url: Option<String>, auth_mode: ProviderAuthMode,
        created_by: String, protocol_version: Option<String>,
        coordination: Option<ProviderCoordinationConfig>, slug: Option<String>,
        admin_callback_url: Option<String>, scope: Option<ProviderCollaborationScope>,
        caller_capabilities: Option<bcs_service_api::ProviderCallerCapabilities>,
    ) -> ServiceResult<RegisteredProvider> {
        self.register_provider_with_a2a(name, webhook_url, auth_mode, created_by, protocol_version, coordination, slug, admin_callback_url, scope, caller_capabilities, None).await
    }

    async fn register_provider_with_a2a(
        &self, name: String, webhook_url: Option<String>, auth_mode: ProviderAuthMode,
        created_by: String, protocol_version: Option<String>,
        coordination: Option<ProviderCoordinationConfig>, slug: Option<String>,
        admin_callback_url: Option<String>, scope: Option<ProviderCollaborationScope>,
        caller_capabilities: Option<bcs_service_api::ProviderCallerCapabilities>,
        a2a: Option<bcs_service_api::ProviderA2aPatch>,
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
        let mut config = provider_config(webhook_url.as_deref(), auth_mode, protocol_version, coordination)?;
        if let Some(url) = admin_callback_url.as_deref() {
            config = replace_admin_callback_url(&config, url)?;
        }
if let Some(scope) = scope {
            let mut value: serde_json::Map<String, Value> = serde_json::from_str(&config)?;
            value.insert("collaboration_scope".into(), serde_json::to_value(scope)?);
            config = serde_json::to_string(&value)?;
        }
        if let Some(caller_capabilities) = caller_capabilities.as_ref() {
            config = replace_caller_capabilities(&config, caller_capabilities)?;
        }
        if let Some(a2a) = a2a {
            config = a2a.apply(&config).map_err(|message| ServiceError::InvalidOperation {message,request_id:None})?;
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
            name, webhook_url, protocol_version, coordination, organization_management, None, None, None).await
    }

    async fn update_provider_with_slug(
        &self, provider_id: &str, provider_admin_token: &str, authenticated_staff_id: &str,
        name: Option<String>, webhook_url: Option<String>, protocol_version: Option<String>,
        coordination: Option<ProviderCoordinationConfig>,
        organization_management: Option<ProviderOrganizationManagementConfig>, slug: Option<String>,
        admin_callback_url: Option<String>,
        caller_capabilities: Option<bcs_service_api::ProviderCallerCapabilities>,
    ) -> ServiceResult<ProviderRecord> {
        self.update_provider_with_policy(provider_id, provider_admin_token, authenticated_staff_id, name, webhook_url, protocol_version, coordination, organization_management, slug, admin_callback_url, None, caller_capabilities).await
    }

    async fn update_provider_with_policy(
        &self, provider_id: &str, provider_admin_token: &str, authenticated_staff_id: &str,
        name: Option<String>, webhook_url: Option<String>, protocol_version: Option<String>,
        coordination: Option<ProviderCoordinationConfig>,
        organization_management: Option<ProviderOrganizationManagementConfig>, slug: Option<String>,
        admin_callback_url: Option<String>, scope: Option<ProviderCollaborationScope>,
        caller_capabilities: Option<bcs_service_api::ProviderCallerCapabilities>,
    ) -> ServiceResult<ProviderRecord> {
        self.update_provider_with_a2a(provider_id, provider_admin_token, authenticated_staff_id, name, webhook_url, protocol_version, coordination, organization_management, slug, admin_callback_url, scope, caller_capabilities, None).await
    }

    async fn update_provider_with_a2a(
        &self, provider_id: &str, provider_admin_token: &str, authenticated_staff_id: &str,
        name: Option<String>, webhook_url: Option<String>, protocol_version: Option<String>,
        coordination: Option<ProviderCoordinationConfig>,
        organization_management: Option<ProviderOrganizationManagementConfig>, slug: Option<String>,
        admin_callback_url: Option<String>, scope: Option<ProviderCollaborationScope>,
        caller_capabilities: Option<bcs_service_api::ProviderCallerCapabilities>,
        a2a: Option<bcs_service_api::ProviderA2aPatch>,
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
        if let Some(caller_capabilities) = caller_capabilities.as_ref() {
            let source = config.as_deref().unwrap_or(&current.config);
            config = Some(replace_caller_capabilities(source, caller_capabilities)?);
        }
        if let Some(a2a) = a2a.filter(|patch| patch.default_owner.is_some()) {
            config = Some(a2a.apply(config.as_deref().unwrap_or(&current.config))
                .map_err(|message| ServiceError::InvalidOperation {message,request_id:None})?);
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
