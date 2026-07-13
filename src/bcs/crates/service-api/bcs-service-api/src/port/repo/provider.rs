use async_trait::async_trait;
use bcs_domain::{BotCapabilities, ProviderBotBinding, ProviderCredential, ProviderRecord};

use crate::ServiceResult;

#[derive(Debug, Clone)]
pub struct ProviderBotDiscoveryRecord {
    pub bot_uuid: String,
    pub provider_id: String,
    pub provider_name: String,
    /// Present when the store can join the registry table in the same query.
    pub capabilities: Option<BotCapabilities>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProviderBotDiscoverySelector {
    All,
    ProviderIds(Vec<String>),
    Query(String),
    Name(String),
    Skills(Vec<String>),
    Domains(Vec<String>),
    Scopes(Vec<String>),
}

impl Default for ProviderBotDiscoverySelector {
    fn default() -> Self {
        Self::All
    }
}

#[async_trait]
pub trait ProviderRepoPort: Send + Sync {
    async fn insert_provider(&self, provider: ProviderRecord) -> ServiceResult<()>;
    async fn get_provider(&self, provider_id: &str) -> ServiceResult<Option<ProviderRecord>>;
    async fn list_providers_by_ids(
        &self,
        provider_ids: &[String],
    ) -> ServiceResult<Vec<ProviderRecord>> {
        let mut providers = Vec::new();
        for provider_id in provider_ids {
            if let Some(provider) = self.get_provider(provider_id).await? {
                providers.push(provider);
            }
        }
        Ok(providers)
    }
    async fn list_providers(&self) -> ServiceResult<Vec<ProviderRecord>>;
    async fn update_provider_metadata(
        &self,
        provider_id: &str,
        name: Option<&str>,
        config: Option<&str>,
        updated_at: u64,
    ) -> ServiceResult<Option<ProviderRecord>>;
    async fn update_provider_disabled(
        &self,
        provider_id: &str,
        disabled: bool,
        updated_at: u64,
    ) -> ServiceResult<Option<ProviderRecord>>;
}

#[async_trait]
pub trait ProviderCredentialRepoPort: Send + Sync {
    async fn insert_credential(&self, credential: ProviderCredential) -> ServiceResult<()>;
    async fn get_credential_by_kind(
        &self,
        provider_id: &str,
        credential_kind: &str,
    ) -> ServiceResult<Option<ProviderCredential>>;
    async fn list_credentials_by_kind_for_providers(
        &self,
        provider_ids: &[String],
        credential_kind: &str,
    ) -> ServiceResult<Vec<ProviderCredential>> {
        let mut credentials = Vec::new();
        for provider_id in provider_ids {
            if let Some(credential) = self
                .get_credential_by_kind(provider_id, credential_kind)
                .await?
            {
                credentials.push(credential);
            }
        }
        Ok(credentials)
    }
    async fn get_credential_by_secret(
        &self,
        credential_kind: &str,
        secret_value: &str,
    ) -> ServiceResult<Option<ProviderCredential>>;
    async fn list_credentials_by_provider(
        &self,
        provider_id: &str,
    ) -> ServiceResult<Vec<ProviderCredential>>;
    async fn update_credential_disabled(
        &self,
        provider_id: &str,
        credential_kind: &str,
        disabled: bool,
        updated_at: u64,
    ) -> ServiceResult<Option<ProviderCredential>>;
}

#[async_trait]
pub trait ProviderBotBindingRepoPort: Send + Sync {
    async fn insert_binding(&self, binding: ProviderBotBinding) -> ServiceResult<()>;
    async fn get_binding_by_bot_uuid(
        &self,
        bot_uuid: &str,
    ) -> ServiceResult<Option<ProviderBotBinding>>;
    async fn list_bindings_by_bot_uuids(
        &self,
        bot_uuids: &[String],
    ) -> ServiceResult<Vec<ProviderBotBinding>> {
        let mut bindings = Vec::new();
        for bot_uuid in bot_uuids {
            if let Some(binding) = self.get_binding_by_bot_uuid(bot_uuid).await? {
                bindings.push(binding);
            }
        }
        Ok(bindings)
    }
    async fn get_binding_by_provider_ref(
        &self,
        provider_id: &str,
        provider_bot_ref: &str,
    ) -> ServiceResult<Option<ProviderBotBinding>>;
    async fn list_bindings_by_provider(
        &self,
        provider_id: &str,
    ) -> ServiceResult<Vec<ProviderBotBinding>>;
    async fn list_discoverable_provider_bot_records(
        &self,
        selector: &ProviderBotDiscoverySelector,
    ) -> ServiceResult<Vec<ProviderBotDiscoveryRecord>> {
        let _ = selector;
        Ok(Vec::new())
    }
    async fn update_binding_disabled(
        &self,
        bot_uuid: &str,
        disabled: bool,
        updated_at: u64,
    ) -> ServiceResult<Option<ProviderBotBinding>>;
}
