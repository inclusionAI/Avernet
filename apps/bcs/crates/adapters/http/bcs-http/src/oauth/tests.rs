//! OAuth unit tests, extracted verbatim from `mod.rs`. Pure move.
use super::*;
use async_trait::async_trait;
use axum::body::to_bytes;
use bcs_auth_api::{
    AuthError, AuthPlugin, AuthPluginChain, AuthPrincipal, AuthSource, UserIdentityInfo,
    UserIdentityPort,
};
use bcs_test_support::{MockOAuthProvider, NoopAuthPlugin, NoopUserIdentityPort};

/// A chain plugin that always yields a principal with the given `user_id`
/// (which may be `None`), so the empty/missing-user_id branch of
/// `current_user_handler` is reachable without depending on any specific
/// config-driven plugin's input validation.
struct FixedUserPlugin {
    user_id: Option<String>,
}

#[async_trait]
impl AuthPlugin for FixedUserPlugin {
    fn can_authenticate(&self, _headers: &HeaderMap) -> bool {
        true
    }
    async fn authenticate(
        &self,
        _headers: &HeaderMap,
    ) -> Result<Option<AuthPrincipal>, AuthError> {
        let mut p = AuthPrincipal::new(AuthSource::Local);
        p.user_id = self.user_id.clone();
        Ok(Some(p))
    }
    fn priority(&self) -> u8 {
        10
    }
    fn name(&self) -> &'static str {
        "fixed"
    }
}

fn chain_with(user_id: Option<String>) -> Arc<AuthPluginChain> {
    Arc::new(AuthPluginChain::new(vec![
        Box::new(FixedUserPlugin { user_id }),
        Box::new(NoopAuthPlugin),
    ]))
}

async fn run_current_user(chain: Arc<AuthPluginChain>) -> (StatusCode, serde_json::Value) {
    let state = Arc::new(OAuthRouteState::new_chain_only(
        Arc::new(NoopUserIdentityPort),
        chain,
    ));
    let resp = current_user_handler(State(state), HeaderMap::new())
        .await
        .into_response();
    let status = resp.status();
    let bytes = to_bytes(resp.into_body(), usize::MAX).await.expect("body");
    let body = serde_json::from_slice(&bytes).unwrap_or(serde_json::Value::Null);
    (status, body)
}

#[tokio::test]
async fn auth_service_complete_login_issues_redirect_cookie_without_network() {
    let mut providers: HashMap<String, Arc<dyn OAuthProvider>> = HashMap::new();
    providers.insert(
        "google".to_string(),
        Arc::new(MockOAuthProvider::new("google", "external-123")),
    );
    let state = OAuthRouteState::new(
        "test-secret-key-at-least-32-bytes!!",
        Arc::new(NoopUserIdentityPort),
        providers,
        OAuthConfig {
            jwt_secret: "test-secret-key-at-least-32-bytes!!".to_string(),
            idle_timeout_minutes: 30,
            base_url: "https://bcs.example.com".to_string(),
            cookie_secure: false,
            env: "test".to_string(),
            success_redirect_path: "/".to_string(),
        },
        None,
    );

    let urls = state
        .login_urls(BuildLoginUrls {
            callback_base_url: "https://bcs.example.com/openapi/v1/auth/callback".to_string(),
        })
        .await
        .expect("login urls");
    let provider_url = &urls.providers[0].url;
    let state_param = provider_url
        .split('&')
        .find_map(|part| part.strip_prefix("state="))
        .expect("state query param")
        .to_string();

    let result = state
        .complete_login(CompleteOAuthLogin {
            provider: "google".to_string(),
            code: Some("auth-code".to_string()),
            auth_code: None,
            state: state_param,
            callback_base_url: "https://bcs.example.com/openapi/v1/auth/callback".to_string(),
        })
        .await
        .expect("complete login");

    assert_eq!(result.location, "/");
    assert!(result.set_cookie.starts_with("bcs_session="));
    assert!(result.set_cookie.contains("HttpOnly"));
    assert!(result.set_cookie.contains("SameSite=Lax"));

    // The issued `bcs_session` JWT must carry the user's display name so an
    // external JWT-only verifier (e.g. a gateway) can populate user_name.
    let jwt = result
        .set_cookie
        .strip_prefix("bcs_session=")
        .and_then(|rest| rest.split(';').next())
        .expect("bcs_session cookie value");
    let claims = JwtService::new("test-secret-key-at-least-32-bytes!!")
        .verify_no_exp(jwt)
        .expect("verify issued login jwt");
    assert_eq!(
        claims.name.as_deref(),
        Some("Mock User"),
        "login JWT must carry the provider's display name"
    );
}


#[tokio::test]
async fn login_urls_rejects_empty_callback_base_url() {
    let mut providers: HashMap<String, Arc<dyn OAuthProvider>> = HashMap::new();
    providers.insert(
        "google".to_string(),
        Arc::new(MockOAuthProvider::new("google", "external-123")),
    );
    let state = OAuthRouteState::new(
        "test-secret-key-at-least-32-bytes!!",
        Arc::new(NoopUserIdentityPort),
        providers,
        OAuthConfig {
            jwt_secret: "test-secret-key-at-least-32-bytes!!".to_string(),
            idle_timeout_minutes: 30,
            base_url: "https://bcs.example.com".to_string(),
            cookie_secure: false,
            env: "test".to_string(),
            success_redirect_path: "/".to_string(),
        },
        None,
    );

    let err = state
        .login_urls(BuildLoginUrls {
            callback_base_url: "   ".to_string(),
        })
        .await
        .expect_err("empty callback base url should fail");

    assert_eq!(err.code(), "invalid_callback_base_url");
}

#[tokio::test]
async fn complete_login_rejects_missing_code() {
    let mut providers: HashMap<String, Arc<dyn OAuthProvider>> = HashMap::new();
    providers.insert(
        "google".to_string(),
        Arc::new(MockOAuthProvider::new("google", "external-123")),
    );
    let state = OAuthRouteState::new(
        "test-secret-key-at-least-32-bytes!!",
        Arc::new(NoopUserIdentityPort),
        providers,
        OAuthConfig {
            jwt_secret: "test-secret-key-at-least-32-bytes!!".to_string(),
            idle_timeout_minutes: 30,
            base_url: "https://bcs.example.com".to_string(),
            cookie_secure: false,
            env: "test".to_string(),
            success_redirect_path: "/".to_string(),
        },
        None,
    );

    let urls = state
        .login_urls(BuildLoginUrls {
            callback_base_url: "https://bcs.example.com/openapi/v1/auth/callback".to_string(),
        })
        .await
        .expect("login urls");
    let provider_url = &urls.providers[0].url;
    let state_param = provider_url
        .split('&')
        .find_map(|part| part.strip_prefix("state="))
        .expect("state query param")
        .to_string();

    let err = state
        .complete_login(CompleteOAuthLogin {
            provider: "google".to_string(),
            code: None,
            auth_code: None,
            state: state_param,
            callback_base_url: "https://bcs.example.com/openapi/v1/auth/callback".to_string(),
        })
        .await
        .expect_err("missing code should fail");

    assert_eq!(err.code(), "missing_code");
}

/// A principal whose `user_id` is `None` is not a human login — `/auth/user`
/// must NOT return 200 with an empty user_id.
#[tokio::test]
async fn current_user_rejects_principal_without_user_id() {
    let (status, body) = run_current_user(chain_with(None)).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED, "no user_id => 401, got {body}");
}

/// A principal whose `user_id` is an empty string must likewise be treated
/// as not authenticated, not as a logged-in user with id "".
#[tokio::test]
async fn current_user_rejects_principal_with_empty_user_id() {
    let (status, body) = run_current_user(chain_with(Some(String::new()))).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED, "empty user_id => 401, got {body}");
}

/// Regression guard: a populated user_id still returns 200.
#[tokio::test]
async fn current_user_accepts_principal_with_user_id() {
    let (status, body) = run_current_user(chain_with(Some("u-123".to_string()))).await;
    assert_eq!(status, StatusCode::OK, "real user_id => 200, got {body}");
    assert_eq!(body["user_id"], "u-123");
}

/// In-memory `UserIdentityPort` for refresh tests: binds one session token
/// hash to a single user and serves its display name, so `refresh_session`
/// (which reads `get_identity_by_token` and re-signs) can be exercised
/// without `NoopUserIdentityPort` short-circuiting to `None`.
struct BoundTokenIdentityPort {
    user_id: String,
    name: Option<String>,
    bound: tokio::sync::Mutex<Option<String>>,
}

impl BoundTokenIdentityPort {
    fn new(user_id: &str, name: Option<&str>) -> Self {
        Self {
            user_id: user_id.to_string(),
            name: name.map(str::to_string),
            bound: tokio::sync::Mutex::new(None),
        }
    }
}

#[async_trait]
impl UserIdentityPort for BoundTokenIdentityPort {
    async fn ensure_identity(
        &self,
        _auth_source: &str,
        _external_user_id: &str,
        _external_user_name: Option<&str>,
        _avatar: Option<&str>,
        _env: &str,
    ) -> Result<String, AuthError> {
        Ok(self.user_id.clone())
    }

    async fn lookup_by_user_id(
        &self,
        _user_id: &str,
        _auth_source: &str,
    ) -> Result<Option<String>, AuthError> {
        Ok(Some(self.user_id.clone()))
    }

    async fn get_identity_by_token(
        &self,
        token_hash: &str,
    ) -> Result<Option<UserIdentityInfo>, AuthError> {
        let bound = self.bound.lock().await;
        if bound.as_deref() == Some(token_hash) {
            Ok(Some(UserIdentityInfo {
                user_id: self.user_id.clone(),
                auth_source: "google".to_string(),
                user_name: self.name.clone(),
                external_user_name: self.name.clone(),
                avatar: None,
            }))
        } else {
            Ok(None)
        }
    }

    async fn get_identity_by_user_id(
        &self,
        _user_id: &str,
    ) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(Some(UserIdentityInfo {
            user_id: self.user_id.clone(),
            auth_source: "google".to_string(),
            user_name: self.name.clone(),
            external_user_name: self.name.clone(),
            avatar: None,
        }))
    }

    async fn update_token(
        &self,
        _user_id: &str,
        token_hash: &str,
        _expire_at: u64,
    ) -> Result<(), AuthError> {
        *self.bound.lock().await = if token_hash.is_empty() {
            None
        } else {
            Some(token_hash.to_string())
        };
        Ok(())
    }
}

/// `refresh_session` must re-sign the JWT carrying the identity row's CURRENT
/// display name (so an edited `user_name` re-syncs into the gateway-parsed
/// cookie on sliding renewal), regardless of the presented JWT's own `name`.
#[tokio::test]
async fn refresh_session_emits_current_name_in_jwt() {
    let jwt_secret = "test-secret-key-at-least-32-bytes!!";
    let port = Arc::new(BoundTokenIdentityPort::new("u1", Some("The Name")));
    let state = OAuthRouteState::new(
        jwt_secret,
        port.clone(),
        HashMap::new(),
        OAuthConfig {
            jwt_secret: jwt_secret.to_string(),
            idle_timeout_minutes: 30,
            base_url: "https://bcs.example.com".to_string(),
            cookie_secure: false,
            env: "test".to_string(),
            success_redirect_path: "/".to_string(),
        },
        None,
    );

    // Issue and bind a login-style JWT; its `name` is irrelevant — refresh
    // re-derives the name from the identity row via `get_identity_by_token`.
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let svc = JwtService::new(jwt_secret);
    let login_jwt = svc
        .sign(&Claims {
            sub: "u1".to_string(),
            src: "google".to_string(),
            iat: now,
            exp: now + 60,
            name: None,
        })
        .expect("sign login jwt");
    port.update_token("u1", &bcs_jwt::token_hash(&login_jwt), now + 60)
        .await
        .expect("bind login token");

    let result = state
        .refresh_session(RefreshSession {
            headers: RequestAuthHeaders {
                authorization: None,
                cookie: Some(format!("bcs_session={login_jwt}")),
                forwarded_headers: Vec::new(),
            },
        })
        .await
        .expect("refresh");
    assert!(result.set_cookie.starts_with("bcs_session="));

    let new_jwt = result
        .set_cookie
        .strip_prefix("bcs_session=")
        .and_then(|rest| rest.split(';').next())
        .expect("new bcs_session value");
    let claims = JwtService::new(jwt_secret)
        .verify_no_exp(new_jwt)
        .expect("verify renewed jwt");
    assert_eq!(claims.sub, "u1");
    assert_eq!(
        claims.name.as_deref(),
        Some("The Name"),
        "refresh must re-sign the JWT with the identity row's current name"
    );
}
