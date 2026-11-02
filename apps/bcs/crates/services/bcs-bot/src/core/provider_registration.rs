//! Provider-scoped registration. Membership and HTTP delivery are separate.
use super::ids::{new_bot_uuid, new_session_token};
use super::provider_core::{DownlinkConfig, parse_downlink_config};
use async_trait::async_trait;
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::core::provider_registration::*;
use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;
use bcs_service_api::bot_provider::BotProviderRecord;
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService,
    ProviderBotBindingRepoPort, ProviderCredentialRepoPort, ProviderRecord, ProviderRepoPort,
    RelationCoreService, ServiceError, ServiceResult,
};
use std::sync::Arc;

pub struct ProviderRegistrationCore {
    providers: Arc<dyn ProviderRepoPort>,
    credentials: Arc<dyn ProviderCredentialRepoPort>,
    bindings: Arc<dyn ProviderBotBindingRepoPort>,
    registrations: Arc<dyn BotProviderRepoPort>,
    // Held for the retirement dispatch (next Task-4 step):
    // Human-registry lookups and legacy relation projection
    // cleanup after retirement; trusted registration itself
    // now runs through the repo composites.
    #[allow(dead_code)]
    registry: Arc<dyn BotRegistryCoreService>,
    #[allow(dead_code)]
    relations: Arc<dyn RelationCoreService>,
    #[allow(dead_code)]
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
    ) -> ServiceResult<ProviderRegistrationAuthorization> {
        let provider = self.authorized_provider(provider_id, owner).await?;
        let auth_mode = parse_downlink_config(&provider.config)?.auth_mode;
        // Endpoint readiness is checked at registration, never at issuance.
        Ok(ProviderRegistrationAuthorization {
            allowed_modes: vec![ProviderRegistrationMode::Plugin, ProviderRegistrationMode::Gateway],
            auth_mode,
        })
    }

    async fn register(
        &self,
        command: RegisterProviderBot,
    ) -> ServiceResult<ProviderRegistrationResult> {
        // Backward-compatible surface: the legacy path derives
        // its initialization from the verified token owner.
        let mutation_id = uuid::Uuid::new_v4().to_string();
        let owner = command.owner.clone();
        self.register_with_ownership(
            command,
            bcs_service_api::types::bot_authority::OwnershipInitialization {
                // The Provider token's verified Human IS the
                // initiator of the legacy surface too.
                owner_user_id: owner.clone(),
                mutation: bcs_service_api::types::bot_authority::AuthorityMutation {
                    mutation_id,
                    actor: bcs_service_api::types::bot_authority::AuthorityActor::Human {
                        user_id: owner,
                    },
                },
            },
        )
        .await
    }

    async fn register_with_ownership(
        &self,
        command: RegisterProviderBot,
        initialization: bcs_service_api::types::bot_authority::OwnershipInitialization,
    ) -> ServiceResult<ProviderRegistrationResult> {
        self.register_validated(command, initialization, None).await
    }

    async fn authorize_a2a_registration(&self, provider_id: &str, owner: &str) -> ServiceResult<()> {
        let provider = self.authorized_provider(provider_id, owner).await?;
        if !is_provider_manager(&provider, owner)? {
            return Err(ServiceError::Forbidden("A2A remote binding requires Provider management authority".into()));
        }
        let downlink = parse_downlink_config(&provider.config)?;
        let credential = self.credentials
                .get_credential_by_kind(provider_id, "downlink_bcs_to_provider").await?;
        if !downlink.enabled || !credential.is_some_and(|value| !value.disabled && !value.secret_value.trim().is_empty()) {
            return Err(invalid("A2A requires enabled downlink and credential"));
        }
        Ok(())
    }

    async fn register_a2a_with_ownership(
        &self, command: RegisterProviderBot,
        initialization: bcs_service_api::types::bot_authority::OwnershipInitialization,
        card: bcs_service_api::types::a2a_bot::A2aRegistrationCard,
    ) -> ServiceResult<ProviderRegistrationResult> {
        self.register_validated(command, initialization, Some(card)).await
    }
}

impl ProviderRegistrationCore {
    async fn register_validated(
        &self, command: RegisterProviderBot,
        initialization: bcs_service_api::types::bot_authority::OwnershipInitialization,
        card: Option<bcs_service_api::types::a2a_bot::A2aRegistrationCard>,
    ) -> ServiceResult<ProviderRegistrationResult> {
        let provider = self
            .authorized_provider(&command.provider_id, &command.owner)
            .await?;
        if card.is_some() && !is_provider_manager(&provider, &command.owner)? {
            return Err(ServiceError::Forbidden("A2A remote binding requires Provider management authority".into()));
        }
        validate_id("provider_bot_ref", &command.provider_bot_ref)?;
        if !(2..=64).contains(&command.bot_name.chars().count())
            || command.bot_name != command.bot_name.trim()
        {
            return Err(invalid(
                "bot-name must be 2-64 characters without surrounding whitespace",
            ));
        }
        let downlink = parse_downlink_config(&provider.config)?;
        if command.token_auth_mode.is_some_and(|mode| mode != downlink.auth_mode) {
            return Err(invalid("registration token auth_mode no longer matches Provider; obtain a new token"));
        }
        let identity = bcs_service_api::types::provider_identity::normalize(downlink.auth_mode, &command.provider_bot_ref).map_err(invalid)?;
        let effective_webhook_url = if let Some(card) = &card {
            if command.mode != ProviderRegistrationMode::Gateway || command.webhook_url.is_some()
                || !card.agent_card.is_object()
                || card.interface.protocol_version != "1.0" || !downlink.enabled {
                return Err(invalid("invalid A2A registration declaration or scope"));
            }
            self.url_guard.validate_configured_http_url(&card.interface.url)
                .map_err(|_|invalid("A2A interface is not an allowed HTTP(S) endpoint"))?;
            let credential = self.credentials
                .get_credential_by_kind(&provider.provider_id, "downlink_bcs_to_provider").await?;
            if !credential.is_some_and(|value| !value.disabled && !value.secret_value.trim().is_empty()) {
                return Err(invalid("A2A requires an enabled downlink credential"));
            }
            None
        } else { self.endpoint(&provider, &downlink, &command).await? };
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
        // The registered Bot gets its ownership initialization
        // in the SAME store transaction it is created by
        // (spec §10.2); nothing is written when any step
        // fails, so no token is returned on failure.
        let provider_bot = BotProviderRecord {
                bot_uuid: record.bot_uuid.clone(), provider_id: record.provider_id.clone(),
                provider_bot_ref: record.provider_bot_ref.clone(), connection_mode: record.mode,
                webhook_url: record.webhook_url.clone(), is_deleted: false,
            };
        let capabilities = BotCapabilities {
                name: Some(record.bot_name.clone()), visibility: "protected".into(),
                // Match Provider-admin registration for both connection modes:
                // AgentPass identifies this Bot by its Provider's external ref.
                agent_code: identity.agent_code.clone(),
                ..BotCapabilities::default()
            };
        if let Some(card) = card {
            let card = bcs_service_api::port::repo::a2a_bot::A2aBotRecord {
                bot_uuid:record.bot_uuid.clone(),provider_id:record.provider_id.clone(),
                provider_bot_ref:record.provider_bot_ref.clone(),agent_card_url:card.agent_card_url,
                agent_card:card.agent_card,revision:1,
            };
            self.registrations.create_a2a_provider_bot_with_ownership(provider_bot, capabilities,
                &record.owner, &record.bot_token, initialization, card).await?;
        } else {
            self.registrations.create_provider_bot_with_ownership(provider_bot, capabilities,
                &record.owner, &record.bot_token, initialization).await?;
        }
        Ok(ProviderRegistrationResult {
            record,
            effective_webhook_url,
            identity,
            auth_mode: downlink.auth_mode,
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
