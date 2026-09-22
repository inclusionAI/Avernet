//! Provider-scoped registration. Membership and HTTP delivery are separate.
use super::ids::{new_bot_uuid, new_session_token};
use super::provider_core::{DownlinkConfig, parse_downlink_config};
use async_trait::async_trait;
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::core::provider_registration::*;
use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;
use bcs_service_api::bot_provider::BotProviderRecord;
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, ProviderAuthMode,
    ProviderBotBindingRepoPort, ProviderCredentialRepoPort, ProviderRecord, ProviderRepoPort,
    RelationCoreService, ServiceError, ServiceResult,
};
use std::sync::Arc;

pub struct ProviderRegistrationCore {
    providers: Arc<dyn ProviderRepoPort>,
    credentials: Arc<dyn ProviderCredentialRepoPort>,
    bindings: Arc<dyn ProviderBotBindingRepoPort>,
    registrations: Arc<dyn BotProviderRepoPort>,
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
        registrations: Arc<dyn BotProviderRepoPort>,
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
        downlink: &DownlinkConfig,
        command: &RegisterProviderBot,
    ) -> ServiceResult<Option<String>> {
        if command.mode == ProviderRegistrationMode::Plugin {
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
        if !downlink.enabled {
            return Err(invalid("gateway requires enabled downlink"));
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
            ProviderRegistrationMode::Plugin,
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
        let downlink = parse_downlink_config(&provider.config)?;
        let effective_webhook_url = self.endpoint(&provider, &downlink, &command).await?;
        // No reservation journal and no credential replay. The database enforces
        // Provider/ref uniqueness across both modes, including deleted Bots.
        if self.bindings.get_binding_by_provider_ref(&command.provider_id, &command.provider_bot_ref).await?.is_some() {
            return Err(ServiceError::Conflict("provider_bot_ref is already registered".into()));
        }
        let record = ProviderRegistrationRecord {
            provider_id: command.provider_id, provider_bot_ref: command.provider_bot_ref,
            owner: command.owner, mode: command.mode, bot_name: command.bot_name,
            bot_uuid: new_bot_uuid(), bot_token: new_session_token(),
            webhook_url: command.webhook_url,
        };
        self.registrations.create_provider_bot(BotProviderRecord {
            bot_uuid: record.bot_uuid.clone(), provider_id: record.provider_id.clone(),
            provider_bot_ref: record.provider_bot_ref.clone(), connection_mode: record.mode,
            webhook_url: record.webhook_url.clone(), is_deleted: false,
        }, BotCapabilities {
            name: Some(record.bot_name.clone()), visibility: "protected".into(),
            // Match Provider-admin registration for both connection modes:
            // AgentPass identifies this Bot by its Provider's external ref.
            agent_code: (downlink.auth_mode == ProviderAuthMode::AgentPass)
                .then(|| record.provider_bot_ref.clone()),
            ..BotCapabilities::default()
        }, &record.owner, &record.bot_token).await?;
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
