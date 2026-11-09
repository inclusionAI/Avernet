use super::*;
use bcs_service_api::{ActorKind, ProviderBotConnectionMode, RegisterProviderBotCommand,
    RegisterProviderBotOutcome, RegisterProviderBotParams};
use bcs_service_api::types::a2a_bot::A2aRegistrationCard;
use bcs_service_api::types::bot_profile::{AvatarChange, ProfileWrite};

fn invalid(message: &str) -> ServiceError {
    ServiceError::InvalidOperation {message:message.into(),request_id:None}
}

impl A2aBotManagement {
    pub(super) async fn register_admin(&self, command: RegisterProviderBotCommand,
        source: A2aCardSource, now_ms: i64) -> ServiceResult<RegisterProviderBotOutcome> {
        self.authorize(&command.provider_id, &command.provider_admin_token).await?;
        bcs_service_api::types::bot_authority::normalize_provider_owners(&command.owners)?;
        if command.connection_mode != ProviderBotConnectionMode::Gateway || command.webhook_url.is_some()
            || (!command.name.is_empty() && (!(2..=64).contains(&command.name.chars().count()) || command.name!=command.name.trim()))
            || now_ms < 0 {
            return Err(invalid("A2A registration requires gateway, one owner and a valid Card source without webhook_url"));
        }
        let (agent_card_url,fetched)=self.load_card(&command.provider_id,source).await?;
        self.register_loaded(command,agent_card_url,fetched,now_ms).await
    }
    pub(super) async fn register_loaded(&self,command:RegisterProviderBotCommand,
        agent_card_url:Option<String>,fetched:bcs_service_api::port::a2a_card::ValidatedA2aCard,now_ms:i64)
        ->ServiceResult<RegisterProviderBotOutcome> {
        self.register_loaded_with_visibility(command,agent_card_url,fetched,now_ms,
            bcs_service_api::types::a2a_bot::A2aRegistrationVisibility::Protected).await
    }
    pub(super) async fn register_loaded_with_visibility(&self,mut command:RegisterProviderBotCommand,
        agent_card_url:Option<String>,fetched:bcs_service_api::port::a2a_card::ValidatedA2aCard,now_ms:i64,
        visibility:bcs_service_api::types::a2a_bot::A2aRegistrationVisibility)
        ->ServiceResult<RegisterProviderBotOutcome> {
        let registration = self.registration.as_ref().ok_or_else(|| invalid("A2A Provider registration is not configured"))?;
        command.name=bcs_service_api::types::a2a_bot::card_bot_name(&fetched.raw).map_err(invalid)?.into();
        if command.summary.is_none() {command.summary=fetched.raw.get("description").and_then(serde_json::Value::as_str).map(str::to_owned);}
        let card=A2aRegistrationCard {visibility,agent_card_url,agent_card:fetched.raw,interface:fetched.interface,registered_at_ms:now_ms};
        let params = RegisterProviderBotParams {webhook_url:None,bot_name:command.name,summary:command.summary,
            owners:command.owners,provider_bot_ref:command.provider_bot_ref,domains:command.domains,
            skills:command.skills,scopes:command.scopes,bot_uuid:command.bot_uuid,connection_mode:command.connection_mode,initial_caller:None};
        let result = registration.core.register(&command.provider_id,&command.provider_admin_token,params,card).await?;
        // Ownership and Card have committed. Match ordinary Provider registration:
        // explicit avatar failures are errors, without rollback or retry of admission.
        if !matches!(command.avatar, AvatarChange::Keep) {
            let written=registration.registry.update_avatar_existing(&result.binding.bot_uuid,command.avatar).await?;
            if written==ProfileWrite::Missing {
                return Err(ServiceError::BotNotFound(result.binding.bot_uuid));
            }
        }
        let bot_runtime_token = if matches!(result.auth_mode, bcs_service_api::ProviderAuthMode::AgentPass) {
            None
        } else { Some(result.bot_runtime_token) };
        Ok(RegisterProviderBotOutcome {auth_mode:result.auth_mode,identity:result.identity,webhook_url:None,
            bot_uuid:result.binding.bot_uuid,provider_id:result.binding.provider_id,provider_bot_ref:result.binding.provider_bot_ref,
            bot_runtime_token, message:None,created:true,
            capabilities:Some(result.capabilities),actor_kind:ActorKind::Bot})
    }
}
