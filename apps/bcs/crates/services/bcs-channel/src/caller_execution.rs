use crate::*;
use bcs_service_api::application::caller_execution::CallerExecution;

impl BcsChannelService {
    pub(crate) fn caller_execution_for(&self, binding: &ChannelBinding, msg: &InboundMessage)
        -> Result<Option<CallerExecution>, ChannelInboundError> {
        let invalid = || ChannelInboundError::new(ChannelInboundFailureKind::CallerExecutionUnavailable, false,
            "caller execution requires an authenticated callback credential and a single Bot binding; resend after checking configuration");
        let provider = self.provider_for(&binding.channel_type).map_err(|_| invalid())?;
        if !provider.requires_caller_execution(&binding.config).map_err(|_| invalid())? { return Ok(None); }
        provider.validate_binding_config(&binding.config, &binding.target).map_err(|_| invalid())?;
        let BindingTarget::Bot { bot_id } = &binding.target else { return Err(invalid()); };
        let token = msg.execution_token.as_ref().filter(|t| !t.expose().trim().is_empty()).ok_or_else(invalid)?;
        if msg.im_user_id.trim().is_empty() { return Err(invalid()); }
        Ok(Some(CallerExecution {
            reference: (self.new_id)(),
            token: token.clone(), token_type: token.token_type().to_string(), user_id: msg.im_user_id.clone(),
            binding_id: binding.id.clone(), source_message_id: msg.msg_id.clone(), target_bot_id: bot_id.clone(),
        }))
    }
}
