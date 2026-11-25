//! Only credential verification is doubled; callback admission is real.
use bcs_service_api::*;
pub struct CallbackIdentity;
#[async_trait::async_trait]
impl ProviderBotCoreService for CallbackIdentity {
    async fn register_provider_bot_with_bot_uuid(
        &self,
        _provider_id: &str,
        _provider_admin_token: &str,
        _params: RegisterProviderBotParams,
    ) -> ServiceResult<(ProviderBotBinding, Option<String>)> {
        Err(ServiceError::InternalError("unused test method".into()))
    }

    async fn list_provider_bots(
        &self,
        _provider_id: &str,
        _provider_admin_token: &str,
    ) -> ServiceResult<Vec<ProviderBotBinding>> {
        Ok(Vec::new())
    }

    async fn authenticate_static_bearer_event(
        &self, provider_id: &str, bot_runtime_token: &str,
    ) -> ServiceResult<RuntimeBotIdentity> {
        Ok(RuntimeBotIdentity { bot_uuid: bot_runtime_token.into(), provider_id: provider_id.into() })
    }

    async fn authenticate_agentpass_event(
        &self,
        _provider_id: &str,
        _agent_code: &str,
    ) -> ServiceResult<RuntimeBotIdentity> {
        Err(ServiceError::InternalError("unused test method".into()))
    }

    async fn authenticate_provider_admin_event(
        &self,
        _provider_id: &str,
        _provider_admin_token: &str,
        _provider_bot_ref: &str,
    ) -> ServiceResult<RuntimeBotIdentity> {
        Err(ServiceError::InternalError("unused test method".into()))
    }

    async fn get_provider_coordination_config(
        &self,
        _provider_id: &str,
    ) -> ServiceResult<ProviderCoordinationConfig> {
        Err(ServiceError::InternalError("unused test method".into()))
    }

    async fn set_provider_bot_disabled(
        &self,
        _provider_id: &str,
        _bot_uuid: &str,
        _provider_admin_token: &str,
        _disabled: bool,
    ) -> ServiceResult<ProviderBotBinding> {
        Err(ServiceError::InternalError("unused test method".into()))
    }
}

