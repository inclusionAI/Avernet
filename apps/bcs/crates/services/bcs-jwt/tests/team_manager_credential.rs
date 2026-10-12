//! Task 13 RED/GREEN suite: the trusted team-manager service credential
//! (spec §6.1, plan Task 13).
//!
//! The credential is an HS256 JWT over the same pure signature boundary as
//! group-session tokens, with a DEDICATED purpose (`team_manager_sync`) —
//! a register or group-session token signed with the same key must never
//! verify as a team-manager credential (purpose isolation). Verification
//! yields the `VerifiedTeamManagerService` (service_id + env + allow
//! scopes) that business commands carry; the credential value itself never
//! enters audit, logs, or persisted commands.

use bcs_jwt::GroupSessionJwtService;
use bcs_jwt::team_manager_credential::{
    TEAM_MANAGER_CREDENTIAL_PURPOSE, TeamManagerJwtVerifier, TeamManagerServiceScopes,
};
use bcs_service_api::port::TeamManagerCredentialVerifierPort;
use bcs_service_api::types::team_manager_sync::VerifiedTeamManagerService;

const SIGNING_KEY: &str = "team-manager-credential-test-key-0123456789";
const NOW: u64 = 1_800_000_000;
const TTL_SECONDS: u64 = 3_600;

fn verifier() -> TeamManagerJwtVerifier {
    TeamManagerJwtVerifier::new(SIGNING_KEY).expect("verifier builds")
}

fn scopes() -> TeamManagerServiceScopes {
    TeamManagerServiceScopes {
        service_id: "team-sync-1".to_string(),
        env: "test-env".to_string(),
        allowed_bots: Some(vec!["bot-a".to_string()]),
        allowed_teams: Some(vec!["team-a".to_string(), "team-new".to_string()]),
        allowed_operations: Some(vec!["sync".to_string(), "move".to_string()]),
    }
}

fn sign(scopes: &TeamManagerServiceScopes, now: u64, ttl: u64) -> String {
    verifier()
        .sign_service_credential(scopes, now, ttl)
        .expect("credential signs")
}

#[test]
fn roundtrip_yields_the_verified_service_with_scopes() {
    let credential = sign(&scopes(), NOW, TTL_SECONDS);
    let verified = verifier()
        .verify_service_credential_at(&credential, NOW + 1)
        .expect("credential verifies");
    assert_eq!(verified.service_id, "team-sync-1");
    assert_eq!(verified.env, "test-env");
    assert_eq!(verified.allowed_bots, Some(vec!["bot-a".to_string()]));
    assert_eq!(
        verified.allowed_teams,
        Some(vec!["team-a".to_string(), "team-new".to_string()])
    );
    assert_eq!(allowed_operation_kinds(&verified), vec!["sync", "move"]);
}

#[test]
fn port_verify_uses_the_current_clock() {
    let system_now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_secs();
    let credential = sign(&scopes(), system_now.saturating_sub(60), TTL_SECONDS);
    let port = verifier();
    TeamManagerCredentialVerifierPort::verify(&port, &credential).expect("port verifies");
}

#[test]
fn unrestricted_scopes_decode_to_none() {
    let scopes = TeamManagerServiceScopes {
        service_id: "team-sync-1".to_string(),
        env: "test-env".to_string(),
        allowed_bots: None,
        allowed_teams: None,
        allowed_operations: None,
    };
    let credential = sign(&scopes, NOW, TTL_SECONDS);
    let verified = verifier()
        .verify_service_credential_at(&credential, NOW + 1)
        .expect("credential verifies");
    assert_eq!(verified, VerifiedTeamManagerService {
        service_id: "team-sync-1".to_string(),
        env: "test-env".to_string(),
        allowed_bots: None,
        allowed_teams: None,
        allowed_operations: None,
    });
}

#[test]
fn wrong_secret_is_rejected() {
    let credential = sign(&scopes(), NOW, TTL_SECONDS);
    let other = TeamManagerJwtVerifier::new("a-completely-different-signing-key").expect("verifier");
    let err = other
        .verify_service_credential_at(&credential, NOW + 1)
        .expect_err("cross-key credential must fail");
    // Rejection never leaks the signing key nor the credential value.
    let text = err.to_string();
    assert!(!text.contains(SIGNING_KEY));
    assert!(!text.contains(&credential));
}

#[test]
fn tampered_payload_is_rejected() {
    let credential = sign(&scopes(), NOW, TTL_SECONDS);
    let parts = credential.split('.').collect::<Vec<&str>>();
    // Deterministically flip the payload's leading base64 character and
    // keep the old signature: the signature check must fail closed no
    // matter how the flipped bits decode.
    let first = parts[1].chars().next().expect("payload chars");
    let flipped = if first == 'e' { 'f' } else { 'e' };
    let tampered = parts[1].replacen(first, &flipped.to_string(), 1);
    let tampered_credential = format!("{}.{}.{}", parts[0], tampered, parts[2]);
    assert_ne!(tampered_credential, credential);
    verifier()
        .verify_service_credential_at(&tampered_credential, NOW + 1)
        .expect_err("tampered credential must fail");
}

#[test]
fn expired_credential_is_rejected() {
    let credential = sign(&scopes(), NOW, TTL_SECONDS);
    let err =
        verifier().verify_service_credential_at(&credential, NOW + TTL_SECONDS);
    let text = expect_rejection_text(err);
    // No token/secret material in the rejection.
    assert!(!text.contains(SIGNING_KEY));
    assert!(!text.contains(&credential));
}

#[test]
fn not_yet_valid_credential_is_rejected() {
    let future = NOW + 10_000;
    let credential = sign(&scopes(), future, TTL_SECONDS);
    verifier()
        .verify_service_credential_at(&credential, NOW)
        .expect_err("credential minted in the future must fail");
}

fn expect_rejection_text(result: Result<VerifiedTeamManagerService, bcs_service_api::ServiceError>) -> String {
    match result {
        Err(err) => err.to_string(),
        Ok(_) => panic!("expected a rejection"),
    }
}

#[test]
fn group_session_purpose_is_isolated_from_the_team_purpose() {
    // purpose isolation: a group-session token signed with the SAME secret
    // must never verify as a team-manager credential.
    let group_service = GroupSessionJwtService::new(SIGNING_KEY).expect("group service");
    let ported = bcs_service_api::port::GroupSessionTokenScope {
        tenant: None,
        user_id: "user-a".to_string(),
        group_id: "group-1".to_string(),
        session_id: "session-1".to_string(),
    };
    let issued = group_service
        .issue_at(ported, bcs_service_api::application::v1::GROUP_SESSION_WS_TOKEN_TTL_SECONDS, NOW)
        .expect("group session token");
    verifier()
        .verify_service_credential_at(&issued.token, NOW + 1)
        .expect_err("group-session tokens are not team-manager credentials");
}

#[test]
fn register_or_session_claims_never_verify_as_team_credentials() {
    // The generic session Claims JWT (the register/session family of
    // tokens) carries a different claim shape AND no team purpose: it must
    // fail closed even when signed with the same key.
    let sessions = bcs_jwt::JwtService::new(SIGNING_KEY);
    let claims = bcs_jwt::Claims {
        sub: "team-sync-1".to_string(),
        src: "agentpass".to_string(),
        iat: NOW,
        exp: NOW + TTL_SECONDS,
        name: None,
    };
    let token = sessions.sign(&claims).expect("session token");
    verifier()
        .verify_service_credential_at(&token, NOW + 1)
        .expect_err("session tokens are not team-manager credentials");
    assert_eq!(TEAM_MANAGER_CREDENTIAL_PURPOSE, "team_manager_sync");
}

#[test]
fn credentials_with_a_foreign_purpose_claim_are_rejected() {
    // Same key, team-shaped claims, but a WRONG purpose string inside.
    let payload = format!(
        "{{\"iss\":\"bcn\",\"aud\":\"bcn-team-manager-sync\",\"purpose\":\"register\",\"sub\":\"team-sync-1\",\"env\":\"test-env\",\"iat\":{NOW},\"exp\":{}}}",
        NOW + TTL_SECONDS
    );
    let credential = manually_signed_token(&payload);
    verifier()
        .verify_service_credential_at(&credential, NOW + 1)
        .expect_err("wrong-purpose credential must fail");
}

#[test]
fn unknown_or_foreign_claims_are_rejected_fail_closed() {
    // The claims decode is deny_unknown_fields: extra fields (and missing
    // required fields) reject the whole credential.
    let payload = format!(
        "{{\"iss\":\"bcn\",\"aud\":\"bcn-team-manager-sync\",\"purpose\":\"{TEAM_MANAGER_CREDENTIAL_PURPOSE}\",\"sub\":\"team-sync-1\",\"env\":\"test-env\",\"iat\":{NOW},\"exp\":{},\"extra\":\"forged\"}}",
        NOW + TTL_SECONDS
    );
    let credential = manually_signed_token(&payload);
    verifier()
        .verify_service_credential_at(&credential, NOW + 1)
        .expect_err("unknown claim must fail closed");

    let payload = format!(
        "{{\"iss\":\"bcn\",\"aud\":\"bcn-team-manager-sync\",\"purpose\":\"{TEAM_MANAGER_CREDENTIAL_PURPOSE}\",\"sub\":\"team-sync-1\",\"iat\":{NOW},\"exp\":{}}}",
        NOW + TTL_SECONDS
    );
    let credential = manually_signed_token(&payload);
    verifier()
        .verify_service_credential_at(&credential, NOW + 1)
        .expect_err("missing env claim must fail closed");
}

#[test]
fn malformed_or_truncated_credentials_are_rejected() {
    for candidate in [
        "",
        "not-a-jwt",
        "a.b",
        "a.b.c.d",
        "////.////.////",
    ] {
        verifier()
            .verify_service_credential_at(candidate, NOW + 1)
            .expect_err("malformed credential must fail closed");
    }
}

#[test]
fn invalid_scope_tokens_inside_the_credential_are_rejected() {
    // The verify side is the boundary that rejects a forged operation
    // scope value even when signing refuses to mint it.
    let payload = format!(
        "{{\"iss\":\"bcn\",\"aud\":\"bcn-team-manager-sync\",\"purpose\":\"{TEAM_MANAGER_CREDENTIAL_PURPOSE}\",\"sub\":\"team-sync-1\",\"env\":\"test-env\",\"allowed_operations\":[\"wield\"],\"iat\":{NOW},\"exp\":{}}}",
        NOW + TTL_SECONDS
    );
    let credential = manually_signed_token(&payload);
    verifier()
        .verify_service_credential_at(&credential, NOW + 1)
        .expect_err("unknown operation scope value must fail closed");

    let payload = format!(
        "{{\"iss\":\"bcn\",\"aud\":\"bcn-team-manager-sync\",\"purpose\":\"{TEAM_MANAGER_CREDENTIAL_PURPOSE}\",\"sub\":\"team-sync-1\",\"env\":\"   \",\"iat\":{NOW},\"exp\":{}}}",
        NOW + TTL_SECONDS
    );
    let credential = manually_signed_token(&payload);
    verifier()
        .verify_service_credential_at(&credential, NOW + 1)
        .expect_err("blank env must fail closed");

    let payload = format!(
        "{{\"iss\":\"bcn\",\"aud\":\"bcn-team-manager-sync\",\"purpose\":\"{TEAM_MANAGER_CREDENTIAL_PURPOSE}\",\"sub\":\"team-sync-1\",\"env\":\"test-env\",\"allowed_bots\":[\"\"],\"iat\":{NOW},\"exp\":{}}}",
        NOW + TTL_SECONDS
    );
    let credential = manually_signed_token(&payload);
    verifier()
        .verify_service_credential_at(&credential, NOW + 1)
        .expect_err("blank scope entry must fail closed");
}

#[test]
fn empty_signing_key_is_a_configuration_error() {
    let result = TeamManagerJwtVerifier::new("   ");
    let err = match result {
        Err(err) => err,
        Ok(_) => panic!("blank key must be rejected"),
    };
    assert!(err.contains("team-manager"));
}

/// Hand-rolled HS256 mint ONLY for forgery tests: signs an arbitrary claims
/// payload with the same key to prove verification (not issuance) is the
/// boundary that rejects claim-shape/purpose attacks.
fn manually_signed_token(payload_json: &str) -> String {
    use base64::Engine;
    use base64::engine::general_purpose::URL_SAFE_NO_PAD;
    use hmac::{Hmac, Mac};
    use sha2::Sha256;

    let header_b64 = URL_SAFE_NO_PAD.encode(r#"{"alg":"HS256","typ":"JWT"}"#);
    let payload_b64 = URL_SAFE_NO_PAD.encode(payload_json);
    let signing_input = format!("{header_b64}.{payload_b64}");
    let mut mac =
        Hmac::<Sha256>::new_from_slice(SIGNING_KEY.as_bytes()).expect("hmac key");
    mac.update(signing_input.as_bytes());
    let sig_b64 = URL_SAFE_NO_PAD.encode(mac.finalize().into_bytes());
    format!("{signing_input}.{sig_b64}")
}

/// Decode the verified scopes' operation kinds ("sync"/"move").
fn allowed_operation_kinds(verified: &VerifiedTeamManagerService) -> Vec<&'static str> {
    verified
        .allowed_operations
        .as_ref()
        .map(|operations| {
            operations
                .iter()
                .map(|operation| operation.storage_str())
                .collect()
        })
        .unwrap_or_default()
}