//! Integration test: full OAuth flow using MemoryUserIdentityRepo.

use std::sync::Arc;

use async_trait::async_trait;
use axum::http::HeaderMap;
use bcs_auth_api::{AuthError, AuthPlugin, OAuthProvider, UserIdentityInfo, UserIdentityPort};
use bcs_auth_google::{GoogleOAuthConfig, GoogleOAuthProvider};
use bcs_auth_oauth::{verify_oauth_session, OAuthSessionPlugin};
use bcs_jwt::JwtService;
use bcs_service_api::UserIdentityRepoPort;
use bcs_user_identity::MemoryUserIdentityRepo;

/// Mock UserIdentityPort wrapping MemoryUserIdentityRepo.
struct MockUserIdentityPort {
    inner: MemoryUserIdentityRepo,
}

#[async_trait]
impl UserIdentityPort for MockUserIdentityPort {
    async fn ensure_identity(
        &self,
        auth_source: &str,
        external_user_id: &str,
        external_user_name: Option<&str>,
        avatar: Option<&str>,
        env: &str,
    ) -> Result<String, AuthError> {
        self.inner
            .ensure_identity(auth_source, external_user_id, external_user_name, avatar, env)
            .await
            .map_err(AuthError::LookupFailed)
    }

    async fn lookup_by_user_id(
        &self,
        user_id: &str,
        auth_source: &str,
    ) -> Result<Option<String>, AuthError> {
        Ok(self.inner.lookup_by_user_id(user_id, auth_source).await)
    }

    async fn get_identity_by_token(
        &self,
        token: &str,
    ) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(self.inner.get_by_token(token).await.map(|r| UserIdentityInfo {
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
        Ok(self.inner.get_by_user_id_display(user_id).await.map(|r| UserIdentityInfo {
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
        self.inner
            .update_token(user_id, token, expire_at)
            .await
            .map_err(AuthError::LookupFailed)
    }
}

#[tokio::test]
async fn full_oauth_session_flow() {
    let jwt_secret = "test-secret-key-at-least-32-bytes!!";
    let port: Arc<dyn UserIdentityPort> = Arc::new(MockUserIdentityPort {
        inner: MemoryUserIdentityRepo::new(),
    });

    // 1. Simulate: user logs in via Google, backend ensures identity
    let user_id = port
        .ensure_identity("google", "google-sub-123", Some("Alice"), None, "default")
        .await
        .unwrap();

    // 2. Backend issues JWT
    let jwt_svc = JwtService::new(jwt_secret);
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let claims = bcs_jwt::Claims {
        sub: user_id.clone(),
        src: "google".to_string(),
        iat: now,
        exp: now + 1800,
        name: None,
    };
    let jwt = jwt_svc.sign(&claims).unwrap();
    // Callback persists the JWT fingerprint so the hot path can bind to it.
    port.update_token(&user_id, &bcs_jwt::token_hash(&jwt), claims.exp)
        .await
        .unwrap();

    // 3. Next request: the provider-agnostic session plugin verifies the JWT
    let plugin = OAuthSessionPlugin::new(jwt_secret.to_string(), port.clone());
    let mut headers = HeaderMap::new();
    headers.insert(
        "cookie",
        format!("bcs_session={}", jwt).parse().unwrap(),
    );

    assert!(plugin.can_authenticate(&headers));
    let result = plugin.authenticate(&headers).await.unwrap();
    assert!(result.is_some());
    let principal = result.unwrap();
    assert_eq!(principal.user_id.as_deref(), Some(user_id.as_str()));
    assert_eq!(principal.source_name.as_deref(), Some("google"));

    // 4. No cookie => cannot authenticate
    let empty_headers = HeaderMap::new();
    assert!(!plugin.can_authenticate(&empty_headers));

    // 5. Invalid JWT signature => returns None (not error)
    let mut bad_headers = HeaderMap::new();
    bad_headers.insert("cookie", "bcs_session=invalid.jwt.token".parse().unwrap());
    assert!(plugin.can_authenticate(&bad_headers));
    let result = plugin.authenticate(&bad_headers).await.unwrap();
    assert!(result.is_none());
}

#[test]
fn google_provider_auth_url_format() {
    let config = GoogleOAuthConfig {
        client_id: "123.apps.googleusercontent.com".to_string(),
        client_secret: "secret".to_string(),
    };
    let provider = GoogleOAuthProvider::new(config);
    let url = provider.auth_url("csrf-state-xyz", "http://localhost:21000/auth/callback/google");

    assert!(url.starts_with("https://accounts.google.com/o/oauth2/v2/auth"));
    assert!(url.contains("client_id=123.apps.googleusercontent.com"));
    assert!(url.contains("state=csrf-state-xyz"));
    assert!(url.contains("redirect_uri="));
    assert!(url.contains("scope="));
}

#[tokio::test]
async fn expired_jwt_returns_none() {
    let jwt_secret = "test-secret-key-at-least-32-bytes!!";
    let port: Arc<dyn UserIdentityPort> = Arc::new(MockUserIdentityPort {
        inner: MemoryUserIdentityRepo::new(),
    });

    let user_id = port
        .ensure_identity("google", "expired-user", None, None, "default")
        .await
        .unwrap();

    // Issue an already-expired JWT (exp = 100, well in the past)
    let jwt_svc = JwtService::new(jwt_secret);
    let claims = bcs_jwt::Claims {
        sub: user_id,
        src: "google".to_string(),
        iat: 50,
        exp: 100,
        name: None,
    };
    let jwt = jwt_svc.sign(&claims).unwrap();

    let plugin = OAuthSessionPlugin::new(jwt_secret.to_string(), port.clone());
    let mut headers = HeaderMap::new();
    headers.insert("cookie", format!("bcs_session={}", jwt).parse().unwrap());

    assert!(plugin.can_authenticate(&headers));
    let result = plugin.authenticate(&headers).await.unwrap();
    assert!(result.is_none(), "expired JWT should return None");
}

/// The hot path is read-only: even when a JWT is well past its 50% lifetime,
/// `verify_oauth_session` must NOT re-sign or set `principal.token`. Renewal is
/// the job of `POST /auth/refresh`, the only place that can return a fresh
/// cookie. (Regression guard for the dropped-cookie / DB-thrash bug.)
#[tokio::test]
async fn verify_oauth_session_hot_path_does_not_refresh() {
    let jwt_secret = "test-secret-key-at-least-32-bytes!!";
    let port: Arc<dyn UserIdentityPort> = Arc::new(MockUserIdentityPort {
        inner: MemoryUserIdentityRepo::new(),
    });

    let user_id = port
        .ensure_identity("google", "sliding-user", Some("Bob"), None, "default")
        .await
        .unwrap();

    // JWT issued 25 minutes ago, expires in 5 minutes => past the 50% threshold
    let jwt_svc = JwtService::new(jwt_secret);
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let claims = bcs_jwt::Claims {
        sub: user_id.clone(),
        src: "google".to_string(),
        iat: now - 1500,
        exp: now + 300,
        name: None,
    };
    let jwt = jwt_svc.sign(&claims).unwrap();
    // Bind the session (as the callback would) so the hash check passes.
    port.update_token(&user_id, &bcs_jwt::token_hash(&jwt), claims.exp)
        .await
        .unwrap();

    let mut headers = HeaderMap::new();
    headers.insert("cookie", format!("bcs_session={}", jwt).parse().unwrap());

    let result = verify_oauth_session(&headers, jwt_secret, port.as_ref()).await.unwrap();
    let principal = result.expect("bound session should authenticate");
    assert_eq!(principal.user_id.as_deref(), Some(user_id.as_str()));
    assert!(
        principal.token.is_none(),
        "hot path must not re-sign; renewal belongs to POST /auth/refresh"
    );
}

/// A validly-signed, unexpired JWT whose fingerprint is NOT the stored session
/// (superseded or never bound) must be rejected — single-session enforcement.
#[tokio::test]
async fn verify_oauth_session_rejects_unbound_jwt() {
    let jwt_secret = "test-secret-key-at-least-32-bytes!!";
    let port: Arc<dyn UserIdentityPort> = Arc::new(MockUserIdentityPort {
        inner: MemoryUserIdentityRepo::new(),
    });
    let user_id = port
        .ensure_identity("google", "unbound-user", None, None, "default")
        .await
        .unwrap();

    let jwt_svc = JwtService::new(jwt_secret);
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let claims = bcs_jwt::Claims {
        sub: user_id.clone(),
        src: "google".to_string(),
        iat: now,
        exp: now + 1800,
        name: None,
    };
    let jwt = jwt_svc.sign(&claims).unwrap();
    // Note: no update_token — this JWT was never bound as the current session.

    let mut headers = HeaderMap::new();
    headers.insert("cookie", format!("bcs_session={}", jwt).parse().unwrap());

    let result = verify_oauth_session(&headers, jwt_secret, port.as_ref()).await.unwrap();
    assert!(result.is_none(), "unbound JWT must not authenticate");
}

#[tokio::test]
async fn token_storage_and_retrieval_via_port() {
    let port: Arc<dyn UserIdentityPort> = Arc::new(MockUserIdentityPort {
        inner: MemoryUserIdentityRepo::new(),
    });

    // 1. Create identity with avatar
    let user_id = port
        .ensure_identity("google", "token-test-user", Some("Carol"), Some("https://img.url/carol"), "default")
        .await
        .unwrap();

    // 2. Before update_token, get_identity_by_token finds nothing
    assert!(
        port.get_identity_by_token("no-such-token").await.unwrap().is_none(),
        "should not find identity before token is stored"
    );

    // 3. Store a token
    port.update_token(&user_id, "jwt-carol-abc", 9999).await.unwrap();

    // 4. Now get_identity_by_token finds the user
    let found = port
        .get_identity_by_token("jwt-carol-abc")
        .await
        .unwrap()
        .expect("should find identity by stored token");
    assert_eq!(found.user_id, user_id);
    assert_eq!(found.auth_source, "google");
    assert_eq!(found.external_user_name.as_deref(), Some("Carol"));
    assert_eq!(found.avatar.as_deref(), Some("https://img.url/carol"));

    // 5. Overwrite token (single-session: old token invalidated)
    port.update_token(&user_id, "jwt-carol-v2", 10000).await.unwrap();
    assert!(
        port.get_identity_by_token("jwt-carol-abc").await.unwrap().is_none(),
        "old token should no longer match after overwrite"
    );
    let found2 = port
        .get_identity_by_token("jwt-carol-v2")
        .await
        .unwrap()
        .expect("new token should match");
    assert_eq!(found2.user_id, user_id);

    // 6. get_identity_by_user_id also works
    let by_id = port
        .get_identity_by_user_id(&user_id)
        .await
        .unwrap()
        .expect("should find identity by user_id");
    assert_eq!(by_id.user_id, user_id);
    assert_eq!(by_id.avatar.as_deref(), Some("https://img.url/carol"));
}