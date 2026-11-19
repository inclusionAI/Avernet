use crate::*;
use bcs_service_api::application::caller_execution::CallerExecution;

impl BcsChannelService {
    pub(crate) fn caller_execution_for(&self, binding: &ChannelBinding, msg: &InboundMessage)
        -> Result<Option<CallerExecution>, ChannelInboundError> {
        let invalid = || ChannelInboundError::new(ChannelInboundFailureKind::CallerExecutionUnavailable, false,
            "caller execution requires an authenticated callback credential and a single Bot binding; resend after checking configuration");
        let provider = self.provider_for(&binding.channel_type).map_err(|_| invalid())?;
        let legacy_enabled = provider.requires_caller_execution(&binding.config).map_err(|_| invalid())?;
        let native_protected = provider.uses_native_caller_execution();
        if !legacy_enabled && !native_protected { return Ok(None); }
        let target_bot_id = match &binding.target {
            BindingTarget::Bot { bot_id } => bot_id.clone(),
            BindingTarget::Group { .. } if !legacy_enabled => String::new(),
            BindingTarget::Group { .. } => return Err(invalid()),
        };
        let token = msg.execution_token.as_ref().filter(|t| !t.expose().trim().is_empty()).cloned();
        if legacy_enabled {
            provider.validate_binding_config(&binding.config, &binding.target).map_err(|_| invalid())?;
            if token.is_none() { return Err(invalid()); }
        }
        if msg.im_user_id.trim().is_empty() { return Err(invalid()); }
        let token_type = token.as_ref().map(|token| token.token_type().to_string()).unwrap_or_default();
        Ok(Some(CallerExecution {
            reference: (self.new_id)(),
            token, token_type, legacy_enabled, native_protected, user_id: msg.im_user_id.clone(),
            binding_id: binding.id.clone(), source_message_id: msg.msg_id.clone(), target_bot_id,
        }))
    }
}
