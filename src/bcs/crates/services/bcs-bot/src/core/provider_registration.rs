//! Provider-scoped registration. Membership and HTTP delivery are separate.
use super::ids::{new_bot_uuid, new_session_token};
use super::provider_core::parse_downlink_config;
use async_trait::async_trait;
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::core::provider_registration::*;
use bcs_service_api::port::repo::provider_registration::ProviderRegistrationRepoPort;
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, ProviderAuthMode, ProviderBotBinding,
    ProviderBotBindingRepoPort, ProviderCredentialRepoPort, ProviderRecord, ProviderRepoPort,
    RelationCoreService, ServiceError, ServiceResult,
};
use std::sync::Arc;

pub struct ProviderRegistrationCore {
    providers: Arc<dyn ProviderRepoPort>,
    credentials: Arc<dyn ProviderCredentialRepoPort>,
    bindings: Arc<dyn ProviderBotBindingRepoPort>,
    registrations: Arc<dyn ProviderRegistrationRepoPort>,
    registry: Arc<dyn BotRegistryCoreService>,
    relations: Arc<dyn RelationCoreService>,
    env: String,
    self_service_providers: Vec<String>,
    url_guard: OutboundUrlGuard,
}

impl ProviderRegistrationCore {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        providers: Arc<dyn ProviderRepoPort>,
        credentials: Arc<dyn ProviderCredentialRepoPort>,
        bindings: Arc<dyn ProviderBotBindingRepoPort>,
        registrations: Arc<dyn ProviderRegistrationRepoPort>,
        registry: Arc<dyn BotRegistryCoreService>,
        relations: Arc<dyn RelationCoreService>,
        env: String,
        self_service_providers: Vec<String>,
        url_guard: OutboundUrlGuard,
    ) -> Self {
        Self {
            providers,
            credentials,
            bindings,
            registrations,
            registry,
            relations,
            env,
            self_service_providers,
            url_guard,
        }
    }

    async fn authorized_provider(
        &self,
        provider_id: &str,
        owner: &str,
    ) -> ServiceResult<ProviderRecord> {
        validate_id("provider_id", provider_id)?;
        if owner.trim().is_empty() || owner != owner.trim() {
            return Err(invalid("registration owner must not be blank"));
        }
        let provider = self
            .providers
            .get_provider(provider_id)
            .await?
            .ok_or_else(|| ServiceError::ProviderNotFound(provider_id.into()))?;
        if provider.disabled {
            return Err(ServiceError::Forbidden("provider is disabled".into()));
        }
        if !is_provider_manager(&provider, owner)?
            && !self
                .self_service_providers
                .iter()
                .any(|id| id == provider_id)
        {
            return Err(ServiceError::Forbidden(
                "provider registration is not authorized".into(),
            ));
        }
        Ok(provider)
    }

    async fn endpoint(
        &self,
        provider: &ProviderRecord,
        command: &RegisterProviderBot,
    ) -> ServiceResult<Option<String>> {
        if command.mode == ProviderRegistrationMode::Upstream {
            if command.webhook_url.is_some() {
                return Err(invalid("upstream registration cannot specify webhook_url"));
            }
            return Ok(None);
        }
        // Delivery authenticates with the Provider-wide credential. Self-service
        // membership does not authorize sending that credential to a new host.
        if command.webhook_url.is_some() && !is_provider_manager(provider, &command.owner)? {
            return Err(ServiceError::Forbidden(
                "only Provider managers may set a Bot webhook override".into(),
            ));
        }
        let downlink = parse_downlink_config(&provider.config)?;
        if !downlink.enabled || downlink.auth_mode == ProviderAuthMode::AgentPass {
            return Err(invalid(
                "gateway requires enabled static_bearer or provider_admin downlink",
            ));
        }
        let url = command
            .webhook_url
            .as_ref()
            .or(downlink.webhook_url.as_ref())
            .ok_or_else(|| {
                invalid("missing_delivery_endpoint: configure a Bot or Provider webhook_url")
            })?;
        self.url_guard
            .validate_configured_http_url(url)
            .map_err(|_| invalid("webhook_url is not an allowed HTTP(S) endpoint"))?;
        let credential = self
            .credentials
            .get_credential_by_kind(&provider.provider_id, "downlink_bcs_to_provider")
            .await?;
        if !credential.is_some_and(|credential| {
            !credential.disabled && !credential.secret_value.trim().is_empty()
        }) {
            return Err(ServiceError::ProviderNotReadyForDownlink {
                provider_id: provider.provider_id.clone(),
                reason: "gateway requires an enabled, nonblank downlink credential".into(),
            });
        }
        Ok(Some(url.clone()))
    }

    async fn check_binding(&self, record: &ProviderRegistrationRecord) -> ServiceResult<bool> {
        let existing = self
            .bindings
            .get_binding_by_provider_ref(&record.provider_id, &record.provider_bot_ref)
            .await?;
        let by_bot = self
            .bindings
            .get_binding_by_bot_uuid(&record.bot_uuid)
            .await?;
        match (existing, by_bot) {
            (None, None) => Ok(false),
            (Some(binding), Some(by_bot))
                if record.mode == ProviderRegistrationMode::Gateway
                    && binding == by_bot
                    && binding.bot_uuid == record.bot_uuid
                    && !binding.disabled
                    && binding.webhook_url == record.webhook_url =>
            {
                Ok(true)
            }
            _ => Err(ServiceError::Conflict(
                "registration delivery binding has changed or ref is already used".into(),
            )),
        }
    }

    async fn check_bot(&self, record: &ProviderRegistrationRecord) -> ServiceResult<()> {
        let bot = self
            .registry
            .try_get(&record.bot_uuid)
            .await?
            .ok_or_else(|| {
                ServiceError::Conflict("registered Bot is missing or has been removed".into())
            })?;
        if bot.created_by.as_deref() != Some(record.owner.as_str())
            || self
                .registry
                .try_load_token(&record.bot_uuid)
                .await?
                .as_deref()
                != Some(record.bot_token.as_str())
        {
            return Err(ServiceError::Conflict(
                "registered Bot ownership or credential has changed".into(),
            ));
        }
        Ok(())
    }
}

#[async_trait]
impl ProviderRegistrationCoreService for ProviderRegistrationCore {
    async fn authorize(
        &self,
        provider_id: &str,
        owner: &str,
    ) -> ServiceResult<Vec<ProviderRegistrationMode>> {
        self.authorized_provider(provider_id, owner).await?;
        // Endpoint readiness is checked at registration, never at issuance.
        Ok(vec![
            ProviderRegistrationMode::Upstream,
            ProviderRegistrationMode::Gateway,
        ])
    }

    async fn register(
        &self,
        command: RegisterProviderBot,
    ) -> ServiceResult<ProviderRegistrationResult> {
        let provider = self
            .authorized_provider(&command.provider_id, &command.owner)
            .await?;
        validate_id("provider_bot_ref", &command.provider_bot_ref)?;
        if !(2..=64).contains(&command.bot_name.chars().count())
            || command.bot_name != command.bot_name.trim()
        {
            return Err(invalid(
                "bot-name must be 2-64 characters without surrounding whitespace",
            ));
        }
        let effective_webhook_url = self.endpoint(&provider, &command).await?;
        // Do not adopt a Bot that was registered through another registration API.
        if self
            .registrations
            .get(&command.provider_id, &command.provider_bot_ref)
            .await?
            .is_none()
            && self
                .bindings
                .get_binding_by_provider_ref(&command.provider_id, &command.provider_bot_ref)
                .await?
                .is_some()
        {
            return Err(ServiceError::Conflict(
                "provider_bot_ref is already registered through another API".into(),
            ));
        }
        let mut record = self
            .registrations
            .reserve(ProviderRegistrationRecord {
                provider_id: command.provider_id.clone(),
                provider_bot_ref: command.provider_bot_ref.clone(),
                owner: command.owner.clone(),
                mode: command.mode,
                bot_name: command.bot_name.clone(),
                bot_uuid: new_bot_uuid(),
                bot_token: new_session_token(),
                webhook_url: command.webhook_url.clone(),
                completed: false,
            })
            .await?;
        if record.owner != command.owner
            || record.mode != command.mode
            || record.bot_name != command.bot_name
            || record.webhook_url != command.webhook_url
        {
            return Err(ServiceError::Conflict(
                "provider_bot_ref has different immutable registration inputs".into(),
            ));
        }
        let binding_exists = self.check_binding(&record).await?;
        if record.completed {
            self.check_bot(&record).await?;
            if record.mode == ProviderRegistrationMode::Gateway && !binding_exists {
                return Err(ServiceError::Conflict(
                    "registered delivery binding has been removed".into(),
                ));
            }
            return Ok(ProviderRegistrationResult {
                record,
                effective_webhook_url,
            });
        }
        // Create-only is atomic at the store. A delayed concurrent retry must
        // not turn a stale "missing" read into an upsert of completed state.
        // Tombstones also count as existing; no lossy tombstone lookup is used.
        self.registry
            .create_registration_if_absent(
                record.bot_uuid.clone(),
                BotCapabilities {
                    name: Some(record.bot_name.clone()),
                    visibility: "protected".into(),
                    ..BotCapabilities::default()
                },
                &record.owner,
                &record.bot_token,
            )
            .await?;
        self.check_bot(&record).await?;
        self.registry
            .ensure_human_actor(&record.owner, &record.owner)
            .await?;
        self.relations
            .ensure_owner_edges(
                &format!("human_{}", record.owner),
                &record.bot_uuid,
                &self.env,
            )
            .await?;
        if record.mode == ProviderRegistrationMode::Gateway && !binding_exists {
            let now = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|value| value.as_millis() as u64)
                .map_err(|_| ServiceError::InternalError("system clock is before epoch".into()))?;
            let binding = ProviderBotBinding {
                bot_uuid: record.bot_uuid.clone(),
                provider_id: record.provider_id.clone(),
                provider_bot_ref: record.provider_bot_ref.clone(),
                webhook_url: record.webhook_url.clone(),
                disabled: false,
                created_at: now,
                updated_at: now,
            };
            if let Err(error) = self.bindings.insert_binding(binding).await {
                // A concurrent identical request may already have committed it.
                // Never treat an unrelated write failure as successful.
                if !self.check_binding(&record).await? {
                    return Err(error);
                }
            }
        }
        self.registrations
            .complete(&record.provider_id, &record.provider_bot_ref)
            .await?;
        record.completed = true;
        Ok(ProviderRegistrationResult {
            record,
            effective_webhook_url,
        })
    }
}

fn invalid(message: &str) -> ServiceError {
    ServiceError::InvalidOperation {
        message: message.into(),
        request_id: None,
    }
}

fn is_provider_manager(provider: &ProviderRecord, owner: &str) -> ServiceResult<bool> {
    let owners: Vec<String> = serde_json::from_str(&provider.owners)?;
    Ok(provider.created_by == owner || owners.iter().any(|id| id == owner))
}

fn validate_id(field: &str, id: &str) -> ServiceResult<()> {
    if id.is_empty()
        || id.len() > 128
        || !id
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'_' | b'-' | b'.' | b':'))
    {
        return Err(invalid(&format!(
            "{field} must be 1-128 ASCII letters, digits, _, -, . or :"
        )));
    }
    Ok(())
}
