//! Fresh multi-user registration has no owner-only legacy-migration window.
use super::*;
use bcs_service_api::types::bot_authority::OwnershipInitialization;

impl ProviderCore {
    pub(super) async fn require_initialized_snapshot_replay(&self, bot_id: &str) -> ServiceResult<()> {
        let state = self.registry.ownership_governance_state(bot_id).await?;
        if !state.is_some_and(|state| state.live && state.ownership_version > 0) {
            return Err(ServiceError::Conflict("existing uninitialized Bot requires authority reconciliation before multi-user registration".into()));
        }
        Ok(())
    }

    pub(super) async fn create_with_initial_authority(
        &self, record: BotProviderRecord, capabilities: BotCapabilities,
        auth_mode: ProviderAuthMode, initialization: OwnershipInitialization,
    ) -> ServiceResult<(ProviderBotBinding, Option<String>)> {
        let repo = self.bot_providers.as_ref().ok_or_else(|| ServiceError::InternalError(
            "atomic Provider authority registration is not configured".into()))?;
        // Preserve the existing Plugin MOCK and AgentPass-subbot credential rules.
        let plugin_mock = record.connection_mode == BotConnectionMode::Plugin
            && auth_mode != ProviderAuthMode::AgentPassSubbots;
        let token = if plugin_mock { mock_token() } else { new_session_token() };
        let returned_token = if plugin_mock || auth_mode == ProviderAuthMode::AgentPass {
            None
        } else {
            Some(token.clone())
        };
        let now = now_ms();
        let owner = initialization.owner_user_id.clone();
        repo.create_provider_admin_bot_with_ownership(record.clone(), capabilities,
            &owner, &token, initialization).await?;
        Ok((ProviderBotBinding { bot_uuid: record.bot_uuid, provider_id: record.provider_id,
            provider_bot_ref: record.provider_bot_ref, webhook_url: record.webhook_url,
            disabled: false, created_at: now, updated_at: now }, returned_token))
    }
}
