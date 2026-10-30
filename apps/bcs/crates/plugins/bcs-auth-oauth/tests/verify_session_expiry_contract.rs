//! P2-A slice 2 conformance: the production OAuth session verification
//! carries its *actually verified* JWT expiry into
//! `AuthPrincipal.human_expires_at_ms` — the only production source of the
//! trusted window the strict caller-identity extraction consumes. Real
//! `JwtService` signing/verification; only the identity table is a stub.

use async_trait::async_trait;
use axum::http::{HeaderMap, HeaderValue};
use bcs_auth_api::{AuthError, UserIdentityInfo, UserIdentityPort};
use bcs_auth_oauth::verify_oauth_session;

const SECRET: &str = "contract-secret";
const NOW: u64 = 1_780_000_000;

struct StubIdentity {
    user_id: String,
}

#[async_trait]
impl UserIdentityPort for StubIdentity {
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
        _token_hash: &str,
    ) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(Some(UserIdentityInfo {
            user_id: self.user_id.clone(),
            auth_source: "google".to_string(),
            user_name: Some("Alice".to_string()),
            external_user_name: None,
            avatar: None,
        }))
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
        _token_hash: &str,
        _expire_at: u64,
    ) -> Result<(), AuthError> {
        Ok(())
    }
}

fn session_headers(token: &str) -> HeaderMap {
    let mut headers = HeaderMap::new();
    headers.insert(
        axum::http::header::COOKIE,
        HeaderValue::from_str(&format!("bcs_session={token}")).unwrap(),
    );
    headers
}

fn signed(exp: u64) -> String {
    let svc = bcs_jwt::JwtService::new(SECRET);
    let claims = bcs_jwt::Claims {
        sub: "user-1".to_string(),
        src: "google".to_string(),
        iat: NOW,
        exp,
        name: None,
    };
    svc.sign(&claims).expect("sign")
}

#[tokio::test]
async fn verified_session_carries_its_real_expiry_in_milliseconds() {
    let port = StubIdentity { user_id: "user-1".to_string() };
    let exp_secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs()
        + 1800;
    let token = signed(exp_secs);
    let principal = verify_oauth_session(
        &session_headers(&token),
        SECRET,
        &port,
    )
    .await
    .expect("no IO failure")
    .expect("verified session yields a principal");
    // The trusted window is the verified JWT exp converted seconds -> ms.
    assert_eq!(principal.human_expires_at_ms, Some(exp_secs.saturating_mul(1000)));
}

#[tokio::test]
async fn no_cookie_yields_no_principal_and_no_expiry_material() {
    let port = StubIdentity { user_id: "user-1".to_string() };
    let principal = verify_oauth_session(&HeaderMap::new(), SECRET, &port)
        .await
        .expect("no cookie is a miss, not an error");
    assert!(principal.is_none());
}

#[tokio::test]
async fn an_expired_session_is_rejected_before_any_expiry_material_exists() {
    let port = StubIdentity { user_id: "user-1".to_string() };
    // Already expired: verify must reject (uuid-adjacent fixed past instant).
    let token = signed(1);
    let principal = verify_oauth_session(&session_headers(&token), SECRET, &port)
        .await
        .expect("expired is a miss, not an error");
    assert!(principal.is_none(), "expired session must not yield a principal");
}

#[tokio::test]
async fn wrong_secret_signature_never_yields_a_principal() {
    let port = StubIdentity { user_id: "user-1".to_string() };
    let exp_secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs()
        + 1800;
    // Signed with a different secret: signature verification fails first.
    let svc = bcs_jwt::JwtService::new("other-secret");
    let claims = bcs_jwt::Claims {
        sub: "user-1".to_string(),
        src: "google".to_string(),
        iat: NOW,
        exp: exp_secs,
        name: None,
    };
    let forged = svc.sign(&claims).expect("sign");
    let principal = verify_oauth_session(&session_headers(&forged), SECRET, &port)
        .await
        .expect("bad signature is a miss, not an error");
    assert!(principal.is_none());
}