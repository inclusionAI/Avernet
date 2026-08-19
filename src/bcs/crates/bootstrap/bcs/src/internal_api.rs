use std::sync::Arc;

use async_trait::async_trait;
use bcs_api_http::v1::internal::{InternalProviderAuthError, InternalProviderAuthenticator};
use bcs_service_api::ProviderCoreService;
use tracing::{info, warn};

pub(crate) struct ProviderAdminInternalAuthenticator {
    provider_core: Arc<dyn ProviderCoreService>,
    trusted_provider_id: Option<String>,
}

impl ProviderAdminInternalAuthenticator {
    pub(crate) fn new(
        provider_core: Arc<dyn ProviderCoreService>,
        trusted_provider_id: Option<String>,
    ) -> Self {
        if let Some(provider_id) = trusted_provider_id.as_deref() {
            info!(
                trusted_provider_id = provider_id,
                "Internal API Provider trust configured"
            );
        } else {
            warn!(
                "Internal API trusted backend Provider is not configured; private routes fail closed"
            );
        }
        Self {
            provider_core,
            trusted_provider_id,
        }
    }
}

#[async_trait]
impl InternalProviderAuthenticator for ProviderAdminInternalAuthenticator {
    async fn authenticate(
        &self,
        token: &str,
        provider_id: &str,
    ) -> Result<(), InternalProviderAuthError> {
        let Some(trusted_provider_id) = self.trusted_provider_id.as_deref() else {
            warn!(
                failure = "trusted_provider_not_configured",
                "Internal API Provider verification failed"
            );
            return Err(InternalProviderAuthError::Forbidden);
        };
        let provider = self
            .provider_core
            .authenticate_provider_admin(token)
            .await
            .map_err(|_| {
                warn!(
                    provider_id,
                    failure = "invalid_provider_admin_token",
                    "Internal API Provider verification failed"
                );
                InternalProviderAuthError::Unauthorized
            })?;

        // COSEC: Bind the caller-supplied Provider header to the identity
        // proven by the Provider-admin token to prevent confused-deputy access.
        if provider.provider_id != provider_id {
            warn!(
                header_provider_id = provider_id,
                authenticated_provider_id = %provider.provider_id,
                failure = "provider_id_mismatch",
                "Internal API Provider verification failed"
            );
            return Err(InternalProviderAuthError::Forbidden);
        }
        if provider.disabled {
            warn!(
                provider_id = %provider.provider_id,
                failure = "provider_disabled",
                "Internal API Provider verification failed"
            );
            return Err(InternalProviderAuthError::Forbidden);
        }
        // COSEC: Private backend access is restricted to one explicitly
        // configured Provider identity and defaults to deny when absent.
        if provider.provider_id != trusted_provider_id {
            warn!(
                provider_id = %provider.provider_id,
                failure = "untrusted_provider",
                "Internal API Provider verification failed"
            );
            return Err(InternalProviderAuthError::Forbidden);
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use async_trait::async_trait;
    use bcs_api_http::v1::internal::{InternalProviderAuthError, InternalProviderAuthenticator};
    use bcs_domain::{
        ProviderAuthMode, ProviderCoordinationConfig, ProviderOrganizationManagementConfig,
        ProviderRecord,
    };
    use bcs_service_api::{ProviderCoreService, RegisteredProvider, ServiceError, ServiceResult};

    use super::ProviderAdminInternalAuthenticator;

    struct FakeProviderCore {
        provider_id: String,
        disabled: bool,
        reject_authentication_lookup: bool,
    }

    #[async_trait]
    impl ProviderCoreService for FakeProviderCore {
        async fn register_provider(
            &self,
            _name: String,
            _webhook_url: String,
            _auth_mode: ProviderAuthMode,
            _created_by: String,
            _protocol_version: Option<String>,
            _coordination: Option<ProviderCoordinationConfig>,
        ) -> ServiceResult<RegisteredProvider> {
            unreachable!("internal auth test does not register Providers")
        }

        async fn authenticate_provider_admin(&self, token: &str) -> ServiceResult<ProviderRecord> {
            assert!(
                !self.reject_authentication_lookup,
                "Provider credential lookup must not run without trusted Provider configuration"
            );
            if token != "valid-provider-token" {
                return Err(ServiceError::Unauthorized(
                    "invalid provider admin token".to_string(),
                ));
            }
            Ok(ProviderRecord {
                provider_id: self.provider_id.clone(),
                name: "Backend".to_string(),
                config: "{}".to_string(),
                created_by: "staff-1".to_string(),
                owners: "[]".to_string(),
                disabled: self.disabled,
                created_at: 1,
                updated_at: 1,
            })
        }

        async fn get_downlink_credential(
            &self,
            _provider_id: &str,
        ) -> ServiceResult<bcs_domain::ProviderCredential> {
            unreachable!("internal auth test does not read downlink credentials")
        }

        async fn get_provider(
            &self,
            _provider_id: &str,
            _provider_admin_token: &str,
        ) -> ServiceResult<ProviderRecord> {
            unreachable!("internal auth test does not get Providers by id")
        }

        async fn update_provider(
            &self,
            _provider_id: &str,
            _provider_admin_token: &str,
            _authenticated_staff_id: &str,
            _name: Option<String>,
            _webhook_url: Option<String>,
            _protocol_version: Option<String>,
            _coordination: Option<ProviderCoordinationConfig>,
            _organization_management: Option<ProviderOrganizationManagementConfig>,
        ) -> ServiceResult<ProviderRecord> {
            unreachable!("internal auth test does not update Providers")
        }

        async fn set_provider_disabled(
            &self,
            _provider_id: &str,
            _provider_admin_token: &str,
            _authenticated_staff_id: &str,
            _disabled: bool,
        ) -> ServiceResult<ProviderRecord> {
            unreachable!("internal auth test does not update Provider status")
        }
    }

    fn authenticator(
        authenticated_provider_id: &str,
        disabled: bool,
        trusted_provider_id: Option<&str>,
    ) -> ProviderAdminInternalAuthenticator {
        ProviderAdminInternalAuthenticator::new(
            Arc::new(FakeProviderCore {
                provider_id: authenticated_provider_id.to_string(),
                disabled,
                reject_authentication_lookup: false,
            }),
            trusted_provider_id.map(str::to_string),
        )
    }

    #[tokio::test]
    async fn provider_admin_internal_authenticator_enforces_every_trust_gate() {
        assert_eq!(
            authenticator("backend-provider", false, Some("backend-provider"))
                .authenticate("valid-provider-token", "backend-provider")
                .await,
            Ok(())
        );
        assert_eq!(
            authenticator("backend-provider", false, Some("backend-provider"))
                .authenticate("invalid-provider-token", "backend-provider")
                .await,
            Err(InternalProviderAuthError::Unauthorized)
        );
        assert_eq!(
            authenticator("backend-provider", false, Some("backend-provider"))
                .authenticate("valid-provider-token", "other-provider")
                .await,
            Err(InternalProviderAuthError::Forbidden)
        );
        assert_eq!(
            authenticator("backend-provider", true, Some("backend-provider"))
                .authenticate("valid-provider-token", "backend-provider")
                .await,
            Err(InternalProviderAuthError::Forbidden)
        );
        assert_eq!(
            authenticator("backend-provider", false, Some("other-provider"))
                .authenticate("valid-provider-token", "backend-provider")
                .await,
            Err(InternalProviderAuthError::Forbidden)
        );
        assert_eq!(
            authenticator("backend-provider", false, None)
                .authenticate("valid-provider-token", "backend-provider")
                .await,
            Err(InternalProviderAuthError::Forbidden)
        );
    }

    #[tokio::test]
    async fn missing_trusted_provider_rejects_before_provider_credential_lookup() {
        let authenticator = ProviderAdminInternalAuthenticator::new(
            Arc::new(FakeProviderCore {
                provider_id: "backend-provider".to_string(),
                disabled: false,
                reject_authentication_lookup: true,
            }),
            None,
        );

        assert_eq!(
            authenticator
                .authenticate("invalid-provider-token", "backend-provider")
                .await,
            Err(InternalProviderAuthError::Forbidden)
        );
    }
}
