//! Current-user projection for the pre-[api.auth] OAuth facade only.
//! Bootstrap selects this verifier before dispatch; it is never a fallback
//! after a Gateway failure and never authenticates protected V1 APIs.
use std::sync::Arc;

use async_trait::async_trait;
use axum::http::HeaderMap;
use bcs_api_http::{CredentialKind, PrincipalVerificationError, PrincipalVerifier, VerifiedRequestIdentity};
use bcs_auth_api::{AuthError, AuthPluginChain};
use bcs_service_api::application::v1::{AuthenticatedCaller, AuthenticatedUserIdentity};

pub(crate) struct CompatAuthPrincipalVerifier(pub Arc<AuthPluginChain>);

#[async_trait]
impl PrincipalVerifier for CompatAuthPrincipalVerifier {
    async fn verify(&self, headers: &HeaderMap) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
        let principal = self.0.authenticate(headers).await.map_err(|error| match error {
            AuthError::LookupFailed(_) | AuthError::SdkError(_) => PrincipalVerificationError::DependencyUnavailable,
            AuthError::Forbidden(_) => PrincipalVerificationError::Forbidden("Authentication refused the principal".into()),
            AuthError::InvalidToken(_) | AuthError::ExpiredToken(_) => PrincipalVerificationError::Invalid("Invalid authentication credential".into()),
        })?.principal.ok_or(PrincipalVerificationError::Missing)?;
        let id = principal.user_id.filter(|id| !id.is_empty())
            .ok_or(PrincipalVerificationError::Missing)?;
        let caller = AuthenticatedCaller {
            tenant: None,
            user: Some(AuthenticatedUserIdentity {
                username: id.clone(), id, display_name: principal.user_name, full_name: None,
            }),
            bot: None, app: None, access_key: None,
        };
        Ok(VerifiedRequestIdentity::pin(
            caller,
            principal.source_name.unwrap_or_else(|| "chain".into()),
            CredentialKind::LegacyBrowserBoundary,
        ).with_avatar(principal.avatar))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_auth_api::{AuthPlugin, AuthPrincipal, AuthSource};
    use std::sync::atomic::{AtomicUsize, Ordering};

    struct Source {
        calls: Arc<AtomicUsize>,
        unavailable: bool,
    }

    #[async_trait]
    impl AuthPlugin for Source {
        fn can_authenticate(&self, _: &HeaderMap) -> bool { true }
        fn priority(&self) -> u8 { 25 }
        fn name(&self) -> &'static str { "test" }
        async fn authenticate(&self, _: &HeaderMap) -> Result<Option<AuthPrincipal>, AuthError> {
            self.calls.fetch_add(1, Ordering::SeqCst);
            if self.unavailable {
                return Err(AuthError::LookupFailed("private backend error".into()));
            }
            let mut principal = AuthPrincipal::new(AuthSource::OAuth("github".into()));
            principal.user_id = Some("user".into());
            principal.user_name = Some("Alice".into());
            principal.avatar = Some("/avatar.png".into());
            Ok(Some(principal))
        }
    }

    #[tokio::test]
    async fn one_chain_pass_preserves_display_data_and_never_falls_back_on_outage() {
        for unavailable in [false, true] {
            let calls = Arc::new(AtomicUsize::new(0));
            let later = Arc::new(AtomicUsize::new(0));
            let verifier = CompatAuthPrincipalVerifier(Arc::new(AuthPluginChain::new(vec![
                Box::new(Source { calls: calls.clone(), unavailable }),
                Box::new(Source { calls: later.clone(), unavailable: false }),
            ])));
            let result = verifier.verify(&HeaderMap::new()).await;
            if unavailable {
                assert!(matches!(result, Err(PrincipalVerificationError::DependencyUnavailable)));
            } else {
                let identity = result.unwrap();
                assert_eq!(identity.authentication_context.source, "github");
                assert_eq!(identity.avatar.as_deref(), Some("/avatar.png"));
                assert_eq!(identity.caller.user.unwrap().display_name.as_deref(), Some("Alice"));
            }
            assert_eq!(calls.load(Ordering::SeqCst), 1);
            assert_eq!(later.load(Ordering::SeqCst), 0);
        }
    }
}
