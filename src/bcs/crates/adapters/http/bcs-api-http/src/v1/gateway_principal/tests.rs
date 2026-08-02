use bcs_service_api::application::v1::AuthenticatedCaller;
use jsonwebtoken::{Algorithm, EncodingKey, Header, encode};
use serde::Deserialize;
use serde_json::{Value, json};

use super::{
    GatewayPrincipalTokenVerifier, GatewayPrincipalTrust, GatewayPrincipalVerificationError,
    GatewayPrincipalVerifierBuildError,
};

const NOW: u64 = 1_785_657_600;
const TEST_KEY_TEXT: &str = "TEST-ONLY-bcs-principal-contract-key-32-bytes";
const TEST_KEY: &[u8] = TEST_KEY_TEXT.as_bytes();

#[derive(Deserialize)]
struct ContractFixture {
    issuer: String,
    audience: String,
    key_id: String,
    principals: Value,
}

fn must_ok<T, E>(result: Result<T, E>, context: &str) -> T {
    match result {
        Ok(value) => value,
        Err(_) => panic!("{context}"),
    }
}

fn must_some<T>(value: Option<T>, context: &str) -> T {
    match value {
        Some(value) => value,
        None => panic!("{context}"),
    }
}

fn must_err<T, E>(result: Result<T, E>, context: &str) -> E {
    match result {
        Err(error) => error,
        Ok(_) => panic!("{context}"),
    }
}

fn fixture() -> ContractFixture {
    must_ok(
        serde_json::from_str(include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../../api-contracts/v1/gateway-principal/principal-set.json"
        ))),
        "valid shared Principal fixture",
    )
}

fn mint(fixture: &ContractFixture, principals: Value) -> String {
    mint_with(
        header("JWT", &fixture.key_id),
        &json!({
            "iss": fixture.issuer,
            "aud": fixture.audience,
            "iat": NOW,
            "exp": NOW + 60,
            "principals": principals,
        }),
        TEST_KEY,
    )
}

fn header(typ: &str, kid: &str) -> Header {
    let mut header = Header::new(Algorithm::HS256);
    header.typ = Some(typ.into());
    header.kid = Some(kid.into());
    header
}

fn mint_with(header: Header, claims: &Value, signing_key: &[u8]) -> String {
    must_ok(
        encode(&header, claims, &EncodingKey::from_secret(signing_key)),
        "test token signs",
    )
}

fn verifier_from(fixture: &ContractFixture) -> GatewayPrincipalTokenVerifier {
    let trust = must_ok(
        GatewayPrincipalTrust::new(
            fixture.issuer.clone(),
            fixture.audience.clone(),
            fixture.key_id.clone(),
        ),
        "valid trust",
    );
    must_ok(
        GatewayPrincipalTokenVerifier::new(TEST_KEY, trust),
        "valid verifier",
    )
}

fn verifier() -> GatewayPrincipalTokenVerifier {
    let fixture = fixture();
    verifier_from(&fixture)
}

fn valid_claims() -> Value {
    let fixture = fixture();
    json!({
        "iss": fixture.issuer,
        "aud": fixture.audience,
        "iat": NOW,
        "exp": NOW + 60,
        "principals": fixture.principals,
    })
}

fn token_with_times(iat: u64, exp: u64) -> String {
    let mut claims = valid_claims();
    claims["iat"] = json!(iat);
    claims["exp"] = json!(exp);
    mint_with(header("JWT", "bare"), &claims, TEST_KEY)
}

fn verify_principals(
    principals: Value,
) -> Result<AuthenticatedCaller, GatewayPrincipalVerificationError> {
    let mut claims = valid_claims();
    claims["principals"] = principals;
    let token = mint_with(header("JWT", "bare"), &claims, TEST_KEY);
    verifier().verify_at(&token, NOW)
}

fn select_principals(principals: &Value, kinds: &[&str]) -> Value {
    Value::Array(
        must_some(principals.as_array(), "fixture principals array")
            .iter()
            .filter(|principal| {
                principal["type"]
                    .as_str()
                    .is_some_and(|kind| kinds.contains(&kind))
            })
            .cloned()
            .collect(),
    )
}

#[test]
fn verifies_the_shared_all_identity_fixture_without_projecting_secrets() {
    let fixture = fixture();
    let token = mint(&fixture, fixture.principals.clone());

    let caller = must_ok(
        verifier_from(&fixture).verify_at(&token, NOW),
        "verified caller",
    );

    assert_eq!(caller.tenant, "tenant-a");
    assert_eq!(
        caller.user.as_ref().map(|value| value.id.as_str()),
        Some("user-1")
    );
    assert_eq!(
        caller.bot.as_ref().map(|value| value.bot_uuid.as_str()),
        Some("bot-1")
    );
    assert_eq!(caller.app.as_ref().map(|value| value.app_id), Some(7));
    assert_eq!(
        caller
            .access_key
            .as_ref()
            .map(|value| value.access_key.as_str()),
        Some("ak-test-1"),
    );
    let debug = format!("{caller:?}");
    assert!(!debug.contains("TEST_ONLY_BOT_TOKEN_MARKER"));
    assert!(!debug.contains("TEST_ONLY_ACCESS_KEY_TOKEN_MARKER"));
}

#[test]
fn accepts_user_only_bot_only_and_user_plus_bot() {
    let fixture = fixture();
    for (kinds, expect_user, expect_bot) in [
        (&["user"][..], true, false),
        (&["bot"][..], false, true),
        (&["user", "bot"][..], true, true),
    ] {
        let caller = must_ok(
            verifier_from(&fixture).verify_at(
                &mint(&fixture, select_principals(&fixture.principals, kinds)),
                NOW,
            ),
            "valid identity combination",
        );
        assert_eq!(caller.user.is_some(), expect_user);
        assert_eq!(caller.bot.is_some(), expect_bot);
    }
}

#[test]
fn principal_order_does_not_change_the_normalized_caller() {
    let fixture = fixture();
    let forward = must_ok(
        verifier_from(&fixture).verify_at(&mint(&fixture, fixture.principals.clone()), NOW),
        "forward order",
    );
    let mut reversed = must_some(fixture.principals.as_array(), "fixture principals array").clone();
    reversed.reverse();
    let reverse = must_ok(
        verifier_from(&fixture).verify_at(&mint(&fixture, Value::Array(reversed)), NOW),
        "reverse order",
    );
    assert_eq!(forward, reverse);
}

#[test]
fn rejects_untrusted_algorithm_token_type_and_key_id() {
    let fixture = fixture();
    let claims = json!({
        "iss": fixture.issuer,
        "aud": fixture.audience,
        "iat": NOW,
        "exp": NOW + 60,
        "principals": fixture.principals,
    });
    let mut wrong_algorithm = header("JWT", "bare");
    wrong_algorithm.alg = Algorithm::HS512;

    for (token, expected) in [
        (
            mint_with(wrong_algorithm, &claims, TEST_KEY),
            GatewayPrincipalVerificationError::UnsupportedAlgorithm,
        ),
        (
            mint_with(header("NOT-JWT", "bare"), &claims, TEST_KEY),
            GatewayPrincipalVerificationError::InvalidTokenType,
        ),
        (
            mint_with(header("JWT", "rotated"), &claims, TEST_KEY),
            GatewayPrincipalVerificationError::InvalidKeyId,
        ),
    ] {
        assert_eq!(
            verifier_from(&fixture).verify_at(&token, NOW),
            Err(expected)
        );
    }
}

#[test]
fn rejects_empty_trust_material() {
    let valid = must_ok(
        GatewayPrincipalTrust::new("gateway", "bcs", "bare"),
        "valid trust",
    );
    assert_eq!(
        GatewayPrincipalTokenVerifier::new(b"", valid).err(),
        Some(GatewayPrincipalVerifierBuildError::EmptySigningKey),
    );
    for values in [
        ("", "bcs", "bare"),
        ("gateway", "", "bare"),
        ("gateway", "bcs", ""),
        ("   ", "bcs", "bare"),
    ] {
        assert!(matches!(
            GatewayPrincipalTrust::new(values.0, values.1, values.2),
            Err(GatewayPrincipalVerifierBuildError::InvalidTrustConfiguration),
        ));
    }
}

#[test]
fn rejects_empty_malformed_and_unsigned_tokens() {
    let verifier = verifier();
    assert_eq!(
        verifier.verify_at("", NOW),
        Err(GatewayPrincipalVerificationError::EmptyToken),
    );
    for token in ["not-a-jwt", "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.e30."] {
        assert_eq!(
            verifier.verify_at(token, NOW),
            Err(GatewayPrincipalVerificationError::InvalidHeader),
        );
    }
}

#[test]
fn rejects_wrong_signature_issuer_and_audience() {
    let claims = valid_claims();
    let wrong_key = mint_with(header("JWT", "bare"), &claims, b"different-test-key");
    assert_eq!(
        verifier().verify_at(&wrong_key, NOW),
        Err(GatewayPrincipalVerificationError::InvalidSignature),
    );
    for (claim, value) in [("iss", "other-gateway"), ("aud", "backend")] {
        let mut claims = valid_claims();
        claims[claim] = json!(value);
        let token = mint_with(header("JWT", "bare"), &claims, TEST_KEY);
        assert_eq!(
            verifier().verify_at(&token, NOW),
            Err(GatewayPrincipalVerificationError::InvalidClaims),
        );
    }
}

#[test]
fn rejects_missing_required_claims_and_invalid_shapes() {
    for claim in ["iss", "aud", "iat", "exp", "principals"] {
        let mut claims = valid_claims();
        must_some(claims.as_object_mut(), "claims object").remove(claim);
        let token = mint_with(header("JWT", "bare"), &claims, TEST_KEY);
        assert_eq!(
            verifier().verify_at(&token, NOW),
            Err(GatewayPrincipalVerificationError::InvalidClaims),
            "missing {claim}",
        );
    }

    for (claim, value) in [
        ("iat", json!("1785657600")),
        ("exp", json!(null)),
        ("principals", json!({})),
    ] {
        let mut claims = valid_claims();
        claims[claim] = value;
        let token = mint_with(header("JWT", "bare"), &claims, TEST_KEY);
        assert_eq!(
            verifier().verify_at(&token, NOW),
            Err(GatewayPrincipalVerificationError::InvalidClaims),
            "invalid shape for {claim}",
        );
    }
}

#[test]
fn enforces_exact_five_second_clock_skew() {
    let accepted_future = token_with_times(NOW + 5, NOW + 65);
    let rejected_future = token_with_times(NOW + 6, NOW + 66);
    let accepted_expired = token_with_times(NOW - 65, NOW - 4);
    let rejected_expired = token_with_times(NOW - 66, NOW - 5);
    assert!(verifier().verify_at(&accepted_future, NOW).is_ok());
    assert_eq!(
        verifier().verify_at(&rejected_future, NOW),
        Err(GatewayPrincipalVerificationError::InvalidClaims),
    );
    assert!(verifier().verify_at(&accepted_expired, NOW).is_ok());
    assert_eq!(
        verifier().verify_at(&rejected_expired, NOW),
        Err(GatewayPrincipalVerificationError::InvalidClaims),
    );
}

#[test]
fn rejects_non_positive_token_lifetime() {
    for (iat, exp) in [(NOW, NOW), (NOW + 1, NOW)] {
        let token = token_with_times(iat, exp);
        assert_eq!(
            verifier().verify_at(&token, NOW),
            Err(GatewayPrincipalVerificationError::InvalidClaims),
        );
    }
}

#[test]
fn rejects_empty_unknown_and_duplicate_principal_types() {
    assert_eq!(
        verify_principals(json!([])),
        Err(GatewayPrincipalVerificationError::InvalidPrincipalSet),
    );

    let mut unknown = fixture().principals;
    unknown[0]["type"] = json!("future_identity");
    assert_eq!(
        verify_principals(unknown),
        Err(GatewayPrincipalVerificationError::InvalidClaims),
    );

    let mut duplicate = fixture().principals;
    let repeated_user = duplicate[0].clone();
    must_some(duplicate.as_array_mut(), "principals array").push(repeated_user);
    assert_eq!(
        verify_principals(duplicate),
        Err(GatewayPrincipalVerificationError::InvalidPrincipalSet),
    );
}

#[test]
fn rejects_missing_required_known_principal_fields() {
    for (index, field) in [(0, "subject"), (1, "bot"), (2, "app"), (3, "access_key")] {
        let mut principals = fixture().principals;
        must_some(principals[index].as_object_mut(), "principal object").remove(field);
        assert_eq!(
            verify_principals(principals),
            Err(GatewayPrincipalVerificationError::InvalidClaims),
            "missing {field}",
        );
    }
}

#[test]
fn rejects_mixed_and_contradictory_tenants() {
    for pointer in [
        "/1/tenant",
        "/1/bot/tenant",
        "/2/app/tenant",
        "/0/subject/tenant_id",
    ] {
        let mut principals = fixture().principals;
        *must_some(principals.pointer_mut(pointer), "fixture pointer") = json!("tenant-b");
        assert_eq!(
            verify_principals(principals),
            Err(GatewayPrincipalVerificationError::InvalidPrincipalSet),
            "tenant mutation at {pointer}",
        );
    }

    for value in ["", "   "] {
        let mut principals = fixture().principals;
        principals[0]["subject"]["tenant_id"] = json!(value);
        assert_eq!(
            verify_principals(principals),
            Err(GatewayPrincipalVerificationError::InvalidPrincipalSet),
        );
    }
}

#[test]
fn rejects_blank_stable_identities_and_invalid_access_key_time() {
    for pointer in [
        "/0/tenant",
        "/0/subject/id",
        "/0/subject/username",
        "/1/bot/bot_uuid",
        "/1/bot/owner_id",
        "/1/bot/agent_code",
        "/3/access_key/access_key",
    ] {
        let mut principals = fixture().principals;
        *must_some(principals.pointer_mut(pointer), "fixture pointer") = json!("   ");
        assert_eq!(
            verify_principals(principals),
            Err(GatewayPrincipalVerificationError::InvalidPrincipalSet),
            "blank identity at {pointer}",
        );
    }

    let mut principals = fixture().principals;
    principals[3]["access_key"]["expire_at"] = json!("not-rfc3339");
    assert_eq!(
        verify_principals(principals),
        Err(GatewayPrincipalVerificationError::InvalidPrincipalSet),
    );
}

#[test]
fn ignores_future_fields_within_known_principal_types() {
    let mut principals = fixture().principals;
    principals[0]["future_principal_field"] = json!(true);
    principals[0]["subject"]["future_user_field"] = json!(1);
    principals[1]["bot"]["future_bot_field"] = json!(2);
    principals[2]["app"]["future_app_field"] = json!(3);
    principals[3]["access_key"]["future_access_key_field"] = json!(4);
    assert!(verify_principals(principals).is_ok());
}

#[test]
fn verification_errors_do_not_expose_tokens_or_keys() {
    let mut principals = fixture().principals;
    principals[0]["tenant"] = json!("   ");
    let mut claims = valid_claims();
    claims["principals"] = principals;
    let token = mint_with(header("JWT", "bare"), &claims, TEST_KEY);

    let error = must_err(verifier().verify_at(&token, NOW), "blank tenant must fail");
    let message = error.to_string();
    for forbidden in [
        "TEST_ONLY_BOT_TOKEN_MARKER",
        "TEST_ONLY_ACCESS_KEY_TOKEN_MARKER",
        token.as_str(),
        TEST_KEY_TEXT,
    ] {
        assert!(!message.contains(forbidden));
    }
}

#[test]
fn public_verify_uses_the_current_system_time() {
    let now = jsonwebtoken::get_current_timestamp();
    let token = token_with_times(now, now + 60);

    assert!(verifier().verify(&token).is_ok());
}
