//! Public Provider names use the existing unique slug column internally.
use super::*;
use bcs_service_api::application::a2a_bot::{NamedA2aCard,NamedA2aRegistration,RegisterNamedA2aBotCommand};
use bcs_service_api::types::a2a_bot::{valid_provider_name,card_bot_name};

fn invalid(message:&str)->ServiceError {ServiceError::InvalidOperation {message:message.into(),request_id:None}}
impl A2aBotManagement {
    async fn named_provider(&self,name:&str,token:&str)->ServiceResult<bcs_service_api::ProviderBasicInfo> {
        if !valid_provider_name(name) {return Err(invalid("provider_name must match [a-z][a-z0-9_-]{0,63}"));}
        let identity=self.providers.get_provider_by_slug(name).await?.ok_or_else(||ServiceError::ProviderNotFound(name.into()))?;
        self.authorize(&identity.provider_id,token).await?;
        Ok(identity)
    }
    pub(super) fn named_projection(&self,provider:&str,record:A2aBotRecord)->ServiceResult<NamedA2aCard> {
        Ok(NamedA2aCard {catalog_labels:record.catalog_labels,provider_bot_ref:record.provider_bot_ref,bot_id:record.bot_uuid,provider_name:provider.into(),bot_name:card_bot_name(&record.agent_card).map_err(invalid)?.into(),
            agent_card_url:record.agent_card_url,agent_card:record.agent_card})
    }
    pub(super) async fn register_by_name(&self,command:RegisterNamedA2aBotCommand)->ServiceResult<NamedA2aRegistration> {
        let provider=self.named_provider(&command.provider_name,&command.provider_admin_token).await?;
        let labels=self.policy.normalize(command.catalog_labels)?;
        if provider.auth_mode!=bcs_service_api::ProviderAuthMode::StaticBearer {
            return Err(invalid("Name-only registration requires a configured trusted Agent identity for this Provider authentication mode; use the identity-aware registration entry"));
        }
        bcs_service_api::types::bot_authority::normalize_provider_owners(&command.owners)?;
        let identity=bcs_service_api::types::provider_identity::normalize(provider.auth_mode,&command.provider_bot_ref).map_err(invalid)?;
        let reference=&identity.provider_bot_ref;
        if reference.is_empty() || reference.len()>256 || !reference.bytes().all(|c|c.is_ascii_alphanumeric() || matches!(c,b'_'|b'-'|b'.'|b':'))
            || command.now_ms<0 || self.registration.is_none() {
            return Err(invalid("invalid A2A registration"));
        }
        let (url,card)=self.load_card(&provider.provider_id,command.source).await?;
        self.authorize(&provider.provider_id,&command.provider_admin_token).await?;
        self.providers.pin_a2a_provider_name(&provider.provider_id,&command.provider_name).await?;
        let outcome=self.register_loaded_with_visibility(bcs_service_api::RegisterProviderBotCommand {
                    avatar: bcs_service_api::types::bot_profile::AvatarChange::Keep,
            provider_id:provider.provider_id,provider_admin_token:command.provider_admin_token,
            name:String::new(),provider_bot_ref:command.provider_bot_ref,owners:command.owners,
            summary:command.summary,webhook_url:None,domains:vec![],skills:vec![],scopes:vec![],bot_uuid:None,
            reject_existing_bot_uuid:true,connection_mode:bcs_service_api::ProviderBotConnectionMode::Gateway,
        },url,card,command.now_ms,bcs_service_api::types::a2a_bot::A2aRegistrationVisibility::Protected,bcs_service_api::types::a2a_bot::A2aAgentIdentity::Omitted,labels).await?;
        let record=self.core.get_card(&outcome.bot_uuid).await?.ok_or_else(||ServiceError::InternalError("registered Card missing".into()))?;
        Ok(NamedA2aRegistration {card:self.named_projection(&command.provider_name,record)?,bot_runtime_token:outcome.bot_runtime_token})
    }
}
