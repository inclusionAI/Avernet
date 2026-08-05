use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use bcs_jwt::{Claims, GroupSessionJwtService, JwtService};
use bcs_service_api::port::{
    GroupSessionTokenError, GroupSessionTokenScope, GROUP_SESSION_TOKEN_MAX_COMPACT_LEN,
};

const NOW: u64 = 1_800_000_000;
const TTL_SECONDS: u64 = 300;
const TEST_KEY: &str = "test-only-group-session-jwt-key-at-least-32-bytes";

fn service() -> GroupSessionJwtService {
    match GroupSessionJwtService::new(TEST_KEY) {
        Ok(value) => value,
        Err(_) => panic!("test key must build the group-session JWT service"),
    }
}

fn scope() -> GroupSessionTokenScope {
    GroupSessionTokenScope {
        tenant: "tenant-a".into(),
        user_id: "user-a".into(),
        group_id: "group-a".into(),
        session_id: "session-a".into(),
    }
}

#[test]
fn issues_exact_five_minute_session_scoped_claims() {
    let issued = match service().issue_at(scope(), TTL_SECONDS, NOW) {
        Ok(value) => value,
        Err(_) => panic!("valid scope must issue"),
    };
    let claims = match service().verify_at(&issued.token, NOW + 1) {
        Ok(value) => value,
        Err(_) => panic!("fresh token must verify"),
    };

    assert_eq!(claims.scope, scope());
    assert_eq!(claims.issued_at, NOW);
    assert_eq!(claims.expires_at, NOW + TTL_SECONDS);
    assert_eq!(issued.expires_at.unix_timestamp(), (NOW + TTL_SECONDS) as i64);

    let payload = issued.token.split('.').nth(1).unwrap_or_default();
    let decoded = match URL_SAFE_NO_PAD.decode(payload) {
        Ok(value) => value,
        Err(_) => panic!("issued payload must be base64url"),
    };
    let wire: serde_json::Value = match serde_json::from_slice(&decoded) {
        Ok(value) => value,
        Err(_) => panic!("issued payload must be JSON"),
    };
    assert_eq!(wire["iss"], "bcn");
    assert_eq!(wire["aud"], "bcn-group-session-ws");
    assert_eq!(wire["purpose"], "group_session_ws");
    assert_eq!(wire["sub"], "user-a");
    assert_eq!(wire["uid"], "user-a");
    assert_eq!(wire["gid"], "group-a");
    assert_eq!(wire["sid"], "session-a");
}

#[test]
fn rejects_expired_and_future_issued_tokens() {
    let issued = match service().issue_at(scope(), TTL_SECONDS, NOW) {
        Ok(value) => value,
        Err(_) => panic!("valid scope must issue"),
    };

    assert!(matches!(
        service().verify_at(&issued.token, NOW + TTL_SECONDS),
        Err(GroupSessionTokenError::Expired)
    ));
    assert!(matches!(
        service().verify_at(&issued.token, NOW - 1),
        Err(GroupSessionTokenError::Invalid)
    ));
}

#[test]
fn rejects_invalid_lifetime_blank_scope_and_oversized_inputs() {
    assert!(matches!(
        service().issue_at(scope(), TTL_SECONDS - 1, NOW),
        Err(GroupSessionTokenError::Invalid)
    ));

    let mut blank = scope();
    blank.session_id = "   ".into();
    assert!(matches!(
        service().issue_at(blank, TTL_SECONDS, NOW),
        Err(GroupSessionTokenError::Invalid)
    ));

    let mut oversized = scope();
    oversized.group_id = "g".repeat(257);
    assert!(matches!(
        service().issue_at(oversized, TTL_SECONDS, NOW),
        Err(GroupSessionTokenError::Invalid)
    ));

    let compact = "x".repeat(GROUP_SESSION_TOKEN_MAX_COMPACT_LEN + 1);
    assert!(matches!(
        service().verify_at(&compact, NOW),
        Err(GroupSessionTokenError::Invalid)
    ));
}

#[test]
fn rejects_wrong_key_and_login_jwt_token_confusion() {
    let issued = match service().issue_at(scope(), TTL_SECONDS, NOW) {
        Ok(value) => value,
        Err(_) => panic!("valid scope must issue"),
    };
    let other = match GroupSessionJwtService::new(
        "different-test-only-group-session-key-at-least-32-bytes",
    ) {
        Ok(value) => value,
        Err(_) => panic!("second test key must build"),
    };
    assert!(matches!(
        other.verify_at(&issued.token, NOW + 1),
        Err(GroupSessionTokenError::Invalid)
    ));

    let login = JwtService::new(TEST_KEY);
    let login_token = match login.sign(&Claims {
        sub: "user-a".into(),
        src: "oauth".into(),
        iat: NOW,
        exp: NOW + TTL_SECONDS,
    }) {
        Ok(value) => value,
        Err(_) => panic!("login JWT fixture must sign"),
    };
    assert!(matches!(
        service().verify_at(&login_token, NOW + 1),
        Err(GroupSessionTokenError::Invalid)
    ));
}

#[test]
fn rejects_empty_signing_key() {
    assert!(GroupSessionJwtService::new("").is_err());
    assert!(GroupSessionJwtService::new("   ").is_err());
}

#[test]
fn one_token_can_be_verified_more_than_once_during_its_lifetime() {
    let issued = match service().issue_at(scope(), TTL_SECONDS, NOW) {
        Ok(value) => value,
        Err(_) => panic!("valid scope must issue"),
    };

    assert!(service().verify_at(&issued.token, NOW + 1).is_ok());
    assert!(service().verify_at(&issued.token, NOW + 2).is_ok());
}
