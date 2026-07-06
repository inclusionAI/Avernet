//! Rule 21 Noop auth doubles: always return None / placeholder identity.

use async_trait::async_trait;
use axum::http::HeaderMap;

use bcs_auth_api::{AuthError, AuthPlugin, AuthPrincipal, UserIdentityInfo, UserIdentityPort};

#[derive(Debug, Default)]
pub struct NoopAuthPlugin;

#[async_trait]
impl AuthPlugin for NoopAuthPlugin {
    fn can_authenticate(&self, _headers: &HeaderMap) -> bool {
        false
    }
    async fn authenticate(
        &self,
        _headers: &HeaderMap,
    ) -> Result<Option<AuthPrincipal>, AuthError> {
        Ok(None)
    }
    fn priority(&self) -> u8 {
        u8::MAX
    }
    fn name(&self) -> &'static str {
        "noop"
    }
}

/// Rule 21 Noop implementation of `UserIdentityPort`.
///
/// `ensure_identity` returns a deterministic placeholder; `lookup_by_user_id`
/// always returns `None`. Suitable for environments where no identity
/// persistence is needed (e.g. tests, local dev without DB).
#[derive(Debug, Default)]
pub struct NoopUserIdentityPort;

#[async_trait]
impl UserIdentityPort for NoopUserIdentityPort {
    async fn ensure_identity(
        &self,
        _auth_source: &str,
        _external_user_id: &str,
        _external_user_name: Option<&str>,
        _avatar: Option<&str>,
        _env: &str,
    ) -> Result<String, AuthError> {
        Ok("noop-identity".to_string())
    }

    async fn lookup_by_user_id(
        &self,
        _user_id: &str,
        _auth_source: &str,
    ) -> Result<Option<String>, AuthError> {
        Ok(None)
    }

    async fn get_identity_by_token(
        &self,
        _token: &str,
    ) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(None)
    }

    async fn get_identity_by_user_id(
        &self,
        _user_id: &str,
    ) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(None)
    }

    async fn update_token(
        &self,
        _user_id: &str,
        _token: &str,
        _expire_at: u64,
    ) -> Result<(), AuthError> {
        Ok(())
    }
}
#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn noop_always_none() {
        let p = NoopAuthPlugin;
        assert!(!p.can_authenticate(&HeaderMap::new()));
        assert!(p.authenticate(&HeaderMap::new()).await.unwrap().is_none());
    }

    #[tokio::test]
    async fn noop_user_identity_ensure() {
        let port = NoopUserIdentityPort;
        let id = port
            .ensure_identity("google", "user@example.com", Some("Test"), None, "dev")
            .await
            .expect("ensure_identity");
        assert_eq!(id, "noop-identity");
    }

    #[tokio::test]
    async fn noop_user_identity_lookup_none() {
        let port = NoopUserIdentityPort;
        let result = port
            .lookup_by_user_id("noop-identity", "google")
            .await
            .expect("lookup_by_user_id");
        assert!(result.is_none());
    }
}
