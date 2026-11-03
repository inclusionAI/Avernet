use super::*;
use bcs_service_api::{ActorKind, ProviderBotConnectionMode, RegisterProviderBotCommand,
    RegisterProviderBotOutcome, RegisterProviderBotParams};
use bcs_service_api::types::a2a_bot::A2aRegistrationCard;

fn invalid(message: &str) -> ServiceError {
    ServiceError::InvalidOperation {message:message.into(),request_id:None}
}

impl A2aBotManagement {
    pub(super) async fn register_admin(&self, mut command: RegisterProviderBotCommand,
        source: A2aCardSource, now_ms: i64) -> ServiceResult<RegisterProviderBotOutcome> {
        self.authorize(&command.provider_id, &command.provider_admin_token).await?;
        if command.connection_mode != ProviderBotConnectionMode::Gateway || command.webhook_url.is_some()
            || command.owners.len() != 1 || command.owners[0].trim().is_empty()
            || (!command.name.is_empty() && (!(2..=64).contains(&command.name.chars().count()) || command.name!=command.name.trim()))
            || now_ms < 0 {
            return Err(invalid("A2A registration requires gateway, one owner and a valid Card source without webhook_url"));
        }
        let registration = self.registration.as_ref().ok_or_else(|| invalid("A2A Provider registration is not configured"))?;
        let (agent_card_url,fetched)=self.load_card(&command.provider_id,source).await?;
        if command.name.is_empty() {
            command.name=fetched.raw.get("name").and_then(serde_json::Value::as_str).unwrap_or("").into();
        }
        if !(2..=64).contains(&command.name.chars().count()) || command.name!=command.name.trim() {
            return Err(invalid("Card name must fit the 2..64 character BCS display name; provide a name alias otherwise"));
        }
        if command.summary.is_none() {command.summary=fetched.raw.get("description").and_then(serde_json::Value::as_str).map(str::to_owned);}
        let card=A2aRegistrationCard {agent_card_url,agent_card:fetched.raw,interface:fetched.interface,registered_at_ms:now_ms};
        let params = RegisterProviderBotParams {webhook_url:None,bot_name:command.name,summary:command.summary,
            owners:command.owners,provider_bot_ref:command.provider_bot_ref,domains:command.domains,
            skills:command.skills,scopes:command.scopes,bot_uuid:command.bot_uuid,connection_mode:command.connection_mode,initial_caller:None};
        let result = registration.register(&command.provider_id,&command.provider_admin_token,params,card).await?;
        let bot_runtime_token = if matches!(result.auth_mode, bcs_service_api::ProviderAuthMode::AgentPass) {
            None
        } else { Some(result.bot_runtime_token) };
        Ok(RegisterProviderBotOutcome {auth_mode:result.auth_mode,identity:result.identity,webhook_url:None,
            bot_uuid:result.binding.bot_uuid,provider_id:result.binding.provider_id,provider_bot_ref:result.binding.provider_bot_ref,
            bot_runtime_token, message:None,created:true,
            capabilities:Some(result.capabilities),actor_kind:ActorKind::Bot})
    }
}
