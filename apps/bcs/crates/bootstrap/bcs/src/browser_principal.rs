//! Projects the existing BCS browser login chain into the Human V1 contract.
//! Used only by /collaboration/user, never as a fallback for Gateway verification.
use std::sync::Arc;
use async_trait::async_trait;
use axum::http::HeaderMap;
use bcs_api_http::{PrincipalVerifier, PrincipalVerificationError};
use bcs_auth_api::AuthPluginChain;
use bcs_service_api::application::v1::{AuthenticatedCaller, AuthenticatedUserIdentity};

pub(crate) struct BrowserPrincipalVerifier(pub Arc<AuthPluginChain>);

#[async_trait]
impl PrincipalVerifier for BrowserPrincipalVerifier {
    async fn verify(&self, headers: &HeaderMap) -> Result<AuthenticatedCaller, PrincipalVerificationError> {
        // This cookie-only surface must not turn Bot/session credentials into their owner.
        for header in ["authorization", "actor", "x-bcs-bot-token", "x-avernet-bot-token",
            "x-avernet-principal", "x-bcs-draft-read-token"] {
            if headers.contains_key(header) {
                return Err(PrincipalVerificationError::Invalid("Human browser login required".into()));
            }
        }
        let principal = self.0.authenticate(headers).await
            .map_err(|_| PrincipalVerificationError::Invalid("Browser authentication failed".into()))?
            .principal.ok_or(PrincipalVerificationError::Missing)?;
        if principal.bot_uuid.is_some() || principal.external_bot_id.is_some() || principal.owner_id.is_some() {
            return Err(PrincipalVerificationError::Invalid("Human browser login required".into()));
        }
        let id = principal.user_id.filter(|id| !id.trim().is_empty())
            .ok_or(PrincipalVerificationError::Missing)?;
        Ok(AuthenticatedCaller { tenant: None, user: Some(AuthenticatedUserIdentity {
            username: id.clone(), id, display_name: principal.user_name, full_name: None,
        }), bot: None, app: None, access_key: None })
    }
}
