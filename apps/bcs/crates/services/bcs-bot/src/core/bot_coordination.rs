use super::*;

impl BotCore {
    /// Provider ownership is persisted independently of HTTP delivery bindings.
    /// None means genuinely unbound; invalid/deleted ownership must never fall back
    /// to deployment-wide uplink permissions.
    pub async fn provider_coordination_surface(
        &self,
        bot_id: &str,
    ) -> ServiceResult<Option<CoordinationSurface>> {
        let provider_id = if let Some(record) = match &self.bot_providers {
            Some(repo) => repo.get_provider_bot(bot_id).await?,
            None => None,
        } {
            if record.is_deleted || record.bot_uuid != bot_id {
                return Err(ServiceError::Forbidden("provider bot ownership is inactive".into()));
            }
            if record.connection_mode == bcs_service_api::bot_provider::BotConnectionMode::Gateway {
                if let Some(bindings) = &self.provider_bindings {
                    if bindings.get_binding_by_bot_uuid(bot_id).await?
                        .is_some_and(|binding| binding.disabled) {
                        return Err(ServiceError::Forbidden("provider bot binding is disabled".into()));
                    }
                }
            }
            Some(record.provider_id)
        } else if let Some(bindings) = &self.provider_bindings {
            // Compatibility for installations that still store only Gateway bindings.
            match bindings.get_binding_by_bot_uuid(bot_id).await? {
                Some(binding) if binding.disabled => {
                    return Err(ServiceError::Forbidden("provider bot binding is disabled".into()));
                }
                Some(binding) => Some(binding.provider_id),
                None => None,
            }
        } else {
            None
        };
        let Some(provider_id) = provider_id else { return Ok(None); };
        let providers = self.provider_repo.as_ref().ok_or_else(|| {
            ServiceError::InternalError("provider repo not configured".into())
        })?;
        let provider = providers.get_provider(&provider_id).await?.ok_or_else(|| {
            ServiceError::ProviderNotFound(provider_id.clone())
        })?;
        if provider.disabled {
            return Err(ServiceError::Forbidden("provider is disabled".into()));
        }
        Ok(Some(parse_coordination_config(&provider.config)?.into()))
    }
}
