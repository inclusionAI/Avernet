//! Username/password register + login application service.
//!
//! Implements `bcs_service_api::PasswordAuthService`. Register: validate →
//! `ensure_identity("password", username, ...)` → argon2 hash →
//! `create_credential` → sign JWT → bind hash. Login: `find_for_login` →
//! argon2 verify → sign JWT → bind hash. The issued JWT has `src = "password"`
//! and is verified by the existing `OAuthSessionPlugin` (cookie or Bearer).

use std::sync::Arc;

use async_trait::async_trait;
use bcs_auth_api::{AuthError, UserIdentityPort};
use bcs_jwt::{token_hash, Claims, JwtService};
use bcs_service_api::{
    PasswordAuthError, PasswordLoginResult, PasswordAuthService, UserCredentialRepoPort,
};

use argon2::password_hash::SaltString;
use argon2::{Argon2, PasswordHash, PasswordHasher, PasswordVerifier};

/// Argon2id hash of a password, returned as the PHC string (self-describing:
/// salt + params + hash). Uses `rand::rngs::OsRng` for salt generation.
fn hash_password(password: &str) -> Result<String, PasswordAuthError> {
    let salt = SaltString::generate(&mut rand::rngs::OsRng);
    let phc = Argon2::default()
        .hash_password(password.as_bytes(), &salt)
        .map_err(|e| PasswordAuthError::Storage(format!("hash: {e}")))?;
    Ok(phc.to_string())
}

fn verify_password(password: &str, phc: &str) -> Result<bool, PasswordAuthError> {
    let parsed = PasswordHash::new(phc)
        .map_err(|e| PasswordAuthError::Storage(format!("parse hash: {e}")))?;
    Ok(Argon2::default()
        .verify_password(password.as_bytes(), &parsed)
        .is_ok())
}

fn validate_credentials(username: &str, password: &str) -> Result<(), PasswordAuthError> {
    let len = username.chars().count();
    if !(3..=32).contains(&len) {
        return Err(PasswordAuthError::ValidationFailed(
            "username must be 3-32 characters".to_string(),
        ));
    }
    if !username
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
    {
        return Err(PasswordAuthError::ValidationFailed(
            "username may only contain A-Za-z0-9 _ -".to_string(),
        ));
    }
    if password.chars().count() < 8 {
        return Err(PasswordAuthError::ValidationFailed(
            "password must be at least 8 characters".to_string(),
        ));
    }
    Ok(())
}

fn now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

pub struct PasswordAuthServiceImpl {
    user_port: Arc<dyn UserIdentityPort>,
    credential_repo: Arc<dyn UserCredentialRepoPort>,
    jwt: JwtService,
    env: String,
    idle_timeout_secs: u64,
}

impl PasswordAuthServiceImpl {
    pub fn new(
        user_port: Arc<dyn UserIdentityPort>,
        credential_repo: Arc<dyn UserCredentialRepoPort>,
        jwt_secret: &str,
        env: String,
        idle_timeout_secs: u64,
    ) -> Self {
        Self {
            user_port,
            credential_repo,
            jwt: JwtService::new(jwt_secret),
            env,
            idle_timeout_secs,
        }
    }

    fn issue_session(
        &self,
        user_id: String,
        username: String,
    ) -> Result<PasswordLoginResult, PasswordAuthError> {
        let now = now_secs();
        let claims = Claims {
            sub: user_id.clone(),
            src: "password".to_string(),
            iat: now,
            exp: now + self.idle_timeout_secs,
        };
        let jwt = self
            .jwt
            .sign(&claims)
            .map_err(|e| PasswordAuthError::Storage(format!("jwt sign: {e}")))?;
        Ok(PasswordLoginResult {
            user_id,
            username,
            token: jwt,
            expires_at: claims.exp,
        })
    }
}

#[async_trait]
impl PasswordAuthService for PasswordAuthServiceImpl {
    async fn register(
        &self,
        username: &str,
        password: &str,
    ) -> Result<PasswordLoginResult, PasswordAuthError> {
        validate_credentials(username, password)?;

        // Pre-check: username taken? (credential table is the source of truth)
        if self
            .credential_repo
            .find_for_login(username, &self.env)
            .await
            .map_err(PasswordAuthError::Storage)?
            .is_some()
        {
            return Err(PasswordAuthError::UsernameTaken);
        }

        let user_id = self
            .user_port
            .ensure_identity("password", username, Some(username), None, &self.env)
            .await
            .map_err(map_identity_err)?;

        let phc = hash_password(password)?;
        if let Err(e) = self
            .credential_repo
            .create_credential(&user_id, username, &phc, &self.env)
            .await
        {
            if e == "duplicate" {
                return Err(PasswordAuthError::UsernameTaken);
            }
            return Err(PasswordAuthError::Storage(e));
        }

        let result = self.issue_session(user_id, username.to_string())?;
        if let Err(e) = self
            .user_port
            .update_token(&result.user_id, &token_hash(&result.token), result.expires_at)
            .await
        {
            return Err(map_identity_err(e));
        }
        Ok(result)
    }

    async fn login(
        &self,
        username: &str,
        password: &str,
    ) -> Result<PasswordLoginResult, PasswordAuthError> {
        let cred = self
            .credential_repo
            .find_for_login(username, &self.env)
            .await
            .map_err(PasswordAuthError::Storage)?
            .ok_or(PasswordAuthError::InvalidCredentials)?;

        if !verify_password(password, &cred.password_hash)? {
            return Err(PasswordAuthError::InvalidCredentials);
        }

        let result = self.issue_session(cred.user_id.clone(), cred.username.clone())?;
        if let Err(e) = self
            .user_port
            .update_token(&result.user_id, &token_hash(&result.token), result.expires_at)
            .await
        {
            return Err(map_identity_err(e));
        }
        Ok(result)
    }
}

fn map_identity_err(e: AuthError) -> PasswordAuthError {
    PasswordAuthError::Storage(format!("identity: {e}"))
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;
    use bcs_auth_api::{AuthError, UserIdentityInfo};
    use bcs_service_api::{UserCredentialRepoPort, UserIdentityRepoPort};
    use bcs_user_identity::{MemoryUserCredentialRepo, MemoryUserIdentityRepo};

    /// In-memory `UserIdentityPort` wrapping the memory identity repo.
    struct InMemoryIdentityPort {
        repo: Arc<MemoryUserIdentityRepo>,
    }
    impl InMemoryIdentityPort {
        fn new() -> Self {
            Self {
                repo: Arc::new(MemoryUserIdentityRepo::new()),
            }
        }
    }
    #[async_trait]
    impl UserIdentityPort for InMemoryIdentityPort {
        async fn ensure_identity(
            &self,
            auth_source: &str,
            external_user_id: &str,
            external_user_name: Option<&str>,
            avatar: Option<&str>,
            env: &str,
        ) -> Result<String, AuthError> {
            self.repo
                .ensure_identity(auth_source, external_user_id, external_user_name, avatar, env)
                .await
                .map_err(AuthError::LookupFailed)
        }
        async fn lookup_by_user_id(
            &self,
            user_id: &str,
            auth_source: &str,
        ) -> Result<Option<String>, AuthError> {
            Ok(self.repo.lookup_by_user_id(user_id, auth_source).await)
        }
        async fn get_identity_by_token(
            &self,
            token: &str,
        ) -> Result<Option<UserIdentityInfo>, AuthError> {
            Ok(self.repo.get_by_token(token).await.map(|r| UserIdentityInfo {
                user_id: r.user_id,
                auth_source: r.auth_source,
                user_name: r.user_name,
                external_user_name: r.external_user_name,
                avatar: r.avatar,
            }))
        }
        async fn get_identity_by_user_id(
            &self,
            user_id: &str,
        ) -> Result<Option<UserIdentityInfo>, AuthError> {
            Ok(self
                .repo
                .get_by_user_id_display(user_id)
                .await
                .map(|r| UserIdentityInfo {
                    user_id: r.user_id,
                    auth_source: r.auth_source,
                    user_name: r.user_name,
                    external_user_name: r.external_user_name,
                    avatar: r.avatar,
                }))
        }
        async fn update_token(
            &self,
            user_id: &str,
            token: &str,
            expire_at: u64,
        ) -> Result<(), AuthError> {
            self.repo
                .update_token(user_id, token, expire_at)
                .await
                .map_err(AuthError::LookupFailed)
        }
    }

    fn service() -> (PasswordAuthServiceImpl, Arc<MemoryUserCredentialRepo>) {
        let creds = Arc::new(MemoryUserCredentialRepo::new());
        let svc = PasswordAuthServiceImpl::new(
            Arc::new(InMemoryIdentityPort::new()),
            creds.clone() as Arc<dyn UserCredentialRepoPort>,
            "test-secret",
            "dev".to_string(),
            1800,
        );
        (svc, creds)
    }

    #[tokio::test]
    async fn register_then_login_round_trip() {
        let (svc, _creds) = service();
        let r = svc.register("alice", "password1").await.unwrap();
        assert_eq!(r.username, "alice");
        assert!(!r.token.is_empty());
        let l = svc.login("alice", "password1").await.unwrap();
        assert_eq!(l.user_id, r.user_id);
    }

    #[tokio::test]
    async fn register_rejects_duplicate_username() {
        let (svc, _creds) = service();
        svc.register("alice", "password1").await.unwrap();
        match svc.register("alice", "password2").await {
            Err(PasswordAuthError::UsernameTaken) => {}
            other => panic!("expected UsernameTaken, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn register_rejects_weak_password_and_bad_username() {
        let (svc, _creds) = service();
        assert!(matches!(
            svc.register("ab", "password1").await,
            Err(PasswordAuthError::ValidationFailed(_))
        ));
        assert!(matches!(
            svc.register("alice", "short").await,
            Err(PasswordAuthError::ValidationFailed(_))
        ));
    }

    #[tokio::test]
    async fn login_wrong_password_or_unknown_user() {
        let (svc, _creds) = service();
        svc.register("alice", "password1").await.unwrap();
        assert!(matches!(
            svc.login("alice", "wrong").await,
            Err(PasswordAuthError::InvalidCredentials)
        ));
        assert!(matches!(
            svc.login("bob", "whatever1").await,
            Err(PasswordAuthError::InvalidCredentials)
        ));
    }

    #[tokio::test]
    async fn issued_jwt_verifies_with_same_secret() {
        let (svc, _creds) = service();
        let r = svc.register("alice", "password1").await.unwrap();
        let claims = JwtService::new("test-secret").verify(&r.token);
        assert!(claims.is_ok());
        let claims = claims.unwrap();
        assert_eq!(claims.sub, r.user_id);
        assert_eq!(claims.src, "password");
    }
}
