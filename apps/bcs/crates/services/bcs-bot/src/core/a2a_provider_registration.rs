//! Atomic A2A admission under the Provider's own management principal.
use std::sync::Arc;
use async_trait::async_trait;
use bcs_service_api::{BotCapabilities, ProviderBotBinding, ProviderCoreService,
    ProviderBotConnectionMode, RegisterProviderBotParams, ServiceError, ServiceResult};
use bcs_service_api::bot_provider::BotProviderRecord;
use bcs_service_api::core::a2a_bot::{A2aProviderRegistrationCoreService, A2aProviderRegistrationResult};
use bcs_service_api::port::repo::a2a_bot::A2aBotRecord;
use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;
use bcs_service_api::types::a2a_bot::A2aRegistrationCard;
use bcs_service_api::types::bot_authority::{AuthorityActor, AuthorityMutation, OwnershipInitialization};
use super::{ids::{new_bot_uuid, new_session_token}, provider_core::parse_downlink_config};

pub struct A2aProviderRegistrationCore {
    providers: Arc<dyn ProviderCoreService>,
    bots: Arc<dyn BotProviderRepoPort>,
}

impl A2aProviderRegistrationCore {
    pub fn new(providers: Arc<dyn ProviderCoreService>, bots: Arc<dyn BotProviderRepoPort>) -> Self {
        Self { providers, bots }
    }
}

fn invalid(message: &str) -> ServiceError {
    ServiceError::InvalidOperation { message: message.into(), request_id: None }
}

#[async_trait]
impl A2aProviderRegistrationCoreService for A2aProviderRegistrationCore {
    async fn register(&self, provider_id: &str, admin_token: &str, params: RegisterProviderBotParams,
        card: A2aRegistrationCard) -> ServiceResult<A2aProviderRegistrationResult> {
        // Repeat credential/enabled checks after the outbound Card fetch.
        let provider = self.providers.get_provider(provider_id, admin_token).await?;
        if provider.disabled { return Err(ServiceError::Forbidden("provider_disabled".into())); }
        let downlink = parse_downlink_config(&provider.config)?;
        let credential = self.providers.get_downlink_credential(provider_id).await?;
        if !downlink.enabled || credential.disabled || credential.secret_value.trim().is_empty()
            || credential.credential_kind != "downlink_bcs_to_provider" {
            return Err(invalid("A2A requires an enabled downlink credential"));
        }
        if params.connection_mode != ProviderBotConnectionMode::Gateway || params.webhook_url.is_some()
            || params.owners.len() != 1 || params.owners[0].trim().is_empty()
            || !(2..=64).contains(&params.bot_name.chars().count()) || params.bot_name != params.bot_name.trim()
            || card.interface.protocol_version != "1.0" || !card.agent_card.is_object()
            || card.registered_at_ms < 0 {
            return Err(invalid("invalid A2A Provider registration"));
        }
        let identity = bcs_service_api::types::provider_identity::normalize(downlink.auth_mode, &params.provider_bot_ref)
            .map_err(invalid)?;
        let reference = &identity.provider_bot_ref;
        if reference.is_empty() || reference.len() > 256 || !reference.bytes().all(|c|
            c.is_ascii_alphanumeric() || matches!(c, b'_' | b'-' | b'.' | b':')) {
            return Err(invalid("invalid provider_bot_ref"));
        }
        if self.bots.get_provider_bot_by_ref(provider_id, reference).await?.is_some() {
            return Err(ServiceError::Conflict("Provider/ref already registered; configure or refresh its Card explicitly".into()));
        }
        let bot_uuid = if downlink.auth_mode == bcs_service_api::ProviderAuthMode::AgentPassSubbots {
            new_bot_uuid()
        } else { params.bot_uuid.unwrap_or_else(new_bot_uuid) };
        let owner = params.owners[0].trim();
        let capabilities = BotCapabilities { name: Some(params.bot_name), summary: params.summary,
            domains: params.domains, skills: params.skills, scopes: params.scopes,
            visibility: "protected".into(), agent_code: identity.agent_code.clone(), ..Default::default() };
        let bot = BotProviderRecord { bot_uuid: bot_uuid.clone(), provider_id: provider_id.into(),
            provider_bot_ref: reference.clone(), connection_mode: ProviderBotConnectionMode::Gateway,
            webhook_url: None, is_deleted: false };
        let token = new_session_token();
        let initialization = OwnershipInitialization { owner_user_id: owner.into(), mutation: AuthorityMutation {
            mutation_id: uuid::Uuid::new_v4().to_string(), actor: AuthorityActor::Provider {provider_id:provider_id.into()},
        }};
        let snapshot = A2aBotRecord { bot_uuid: bot_uuid.clone(), provider_id: provider_id.into(),
            provider_bot_ref: reference.clone(), agent_card_url: card.agent_card_url, agent_card: card.agent_card,
            revision:1 };
        // No network calls or locks span this transaction. The existing composite
        // fences concurrent duplicates and includes the real Provider audit actor.
        self.bots.create_provider_admin_a2a_bot_with_ownership(bot, capabilities.clone(), owner, &token, initialization, snapshot).await?;
        let now = u64::try_from(card.registered_at_ms).map_err(|_|invalid("invalid registration time"))?;
        Ok(A2aProviderRegistrationResult { binding: ProviderBotBinding {bot_uuid, provider_id:provider_id.into(),
            provider_bot_ref:reference.clone(), webhook_url:None, disabled:false, created_at:now, updated_at:now},
            bot_runtime_token:token, capabilities, identity, auth_mode:downlink.auth_mode })
    }
}
