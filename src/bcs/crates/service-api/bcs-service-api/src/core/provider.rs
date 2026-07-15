use async_trait::async_trait;
use bcs_domain::{
    ProviderAuthMode, ProviderBotBinding, ProviderCoordinationConfig, ProviderCredential,
    ProviderOrganizationManagementConfig, ProviderRecord, Skill,
};

use crate::{ServiceError, ServiceResult};

#[derive(Debug, Clone)]
pub struct RuntimeBotIdentity {
    pub bot_uuid: String,
    pub provider_id: String,
}

#[derive(Debug, Clone)]
pub struct RegisteredProvider {
    pub provider: ProviderRecord,
    pub provider_admin_token: String,
    pub bcs_to_provider_token: String,
}

/// Parameters for registering a provider-managed bot.
///
/// `domains`, `skills`, and `scopes` carry the same capability semantics as the
/// `POST /bots/onboard` flow.
#[derive(Debug, Clone, Default)]
pub struct RegisterProviderBotParams {
    pub bot_name: String,
    pub summary: Option<String>,
    pub owners: Vec<String>,
    pub provider_bot_ref: String,
    pub domains: Vec<String>,
    pub skills: Vec<Skill>,
    pub scopes: Vec<String>,
    pub bot_uuid: Option<String>,
}

#[async_trait]
pub trait ProviderCoreService: Send + Sync {
    async fn register_provider(
        &self,
        name: String,
        webhook_url: String,
        auth_mode: ProviderAuthMode,
        created_by: String,
        protocol_version: Option<String>,
        coordination: Option<ProviderCoordinationConfig>,
    ) -> ServiceResult<RegisteredProvider>;

    async fn authenticate_provider_admin(&self, token: &str) -> ServiceResult<ProviderRecord>;
    async fn get_downlink_credential(&self, provider_id: &str)
    -> ServiceResult<ProviderCredential>;
    async fn get_provider(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
    ) -> ServiceResult<ProviderRecord>;
    async fn update_provider(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
        authenticated_staff_id: &str,
        name: Option<String>,
        webhook_url: Option<String>,
        protocol_version: Option<String>,
        coordination: Option<ProviderCoordinationConfig>,
        organization_management: Option<ProviderOrganizationManagementConfig>,
    ) -> ServiceResult<ProviderRecord>;

    async fn update_provider_admin_callback_url(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
        authenticated_staff_id: &str,
        admin_callback_url: String,
    ) -> ServiceResult<ProviderRecord> {
        let _ = (
            provider_id,
            provider_admin_token,
            authenticated_staff_id,
            admin_callback_url,
        );
        Err(ServiceError::InvalidOperation {
            message: "provider admin callback updates are not configured".to_string(),
            request_id: None,
        })
    }
    async fn set_provider_disabled(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
        authenticated_staff_id: &str,
        disabled: bool,
    ) -> ServiceResult<ProviderRecord>;
}

#[async_trait]
pub trait ProviderBotCoreService: Send + Sync {
    async fn register_provider_bot_with_bot_uuid(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
        params: RegisterProviderBotParams,
    ) -> ServiceResult<(ProviderBotBinding, Option<String>)>;

    async fn list_provider_bots(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
    ) -> ServiceResult<Vec<ProviderBotBinding>>;

    async fn get_provider_bot_binding_by_ref(
        &self,
        provider_id: &str,
        provider_bot_ref: &str,
    ) -> ServiceResult<Option<ProviderBotBinding>> {
        let _ = (provider_id, provider_bot_ref);
        Err(ServiceError::InvalidOperation {
            message: "provider bot binding lookup is not configured".to_string(),
            request_id: None,
        })
    }

    async fn get_provider_bot_binding_by_bot_uuid(
        &self,
        bot_uuid: &str,
    ) -> ServiceResult<Option<ProviderBotBinding>> {
        let _ = bot_uuid;
        Err(ServiceError::InvalidOperation {
            message: "provider bot binding lookup is not configured".to_string(),
            request_id: None,
        })
    }

    async fn authenticate_static_bearer_event(
        &self,
        provider_id: &str,
        bot_runtime_token: &str,
    ) -> ServiceResult<RuntimeBotIdentity>;

    async fn authenticate_agentpass_event(
        &self,
        provider_id: &str,
        agent_code: &str,
    ) -> ServiceResult<RuntimeBotIdentity>;

    async fn authenticate_provider_admin_event(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
        provider_bot_ref: &str,
    ) -> ServiceResult<RuntimeBotIdentity>;

    async fn get_provider_coordination_config(
        &self,
        provider_id: &str,
    ) -> ServiceResult<ProviderCoordinationConfig> {
        let _ = provider_id;
        Err(ServiceError::InvalidOperation {
            message: "provider coordination config lookup is not configured".to_string(),
            request_id: None,
        })
    }

    async fn set_provider_bot_disabled(
        &self,
        provider_id: &str,
        bot_uuid: &str,
        provider_admin_token: &str,
        disabled: bool,
    ) -> ServiceResult<ProviderBotBinding>;
}
