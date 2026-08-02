use jsonwebtoken::{Algorithm, EncodingKey, Header, encode};
use serde::Deserialize;
use serde_json::{Value, json};

use super::{
    GatewayPrincipalTokenVerifier, GatewayPrincipalTrust, GatewayPrincipalVerificationError,
};

const NOW: u64 = 1_785_657_600;
const TEST_KEY: &[u8] = b"TEST-ONLY-bcs-principal-contract-key-32-bytes";

#[derive(Deserialize)]
struct ContractFixture {
    issuer: String,
    audience: String,
    key_id: String,
    principals: Value,
}

fn fixture() -> ContractFixture {
    serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../../api-contracts/v1/gateway-principal/principal-set.json"
    )))
    .expect("valid shared Principal fixture")
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
    encode(&header, claims, &EncodingKey::from_secret(signing_key)).expect("test token signs")
}

fn verifier_from(fixture: &ContractFixture) -> GatewayPrincipalTokenVerifier {
    let trust = GatewayPrincipalTrust::new(
        fixture.issuer.clone(),
        fixture.audience.clone(),
        fixture.key_id.clone(),
    )
    .expect("valid trust");
    GatewayPrincipalTokenVerifier::new(TEST_KEY, trust).expect("valid verifier")
}

fn select_principals(principals: &Value, kinds: &[&str]) -> Value {
    Value::Array(
        principals
            .as_array()
            .expect("fixture principals array")
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

    let caller = verifier_from(&fixture)
        .verify_at(&token, NOW)
        .expect("verified caller");

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
        let caller = verifier_from(&fixture)
            .verify_at(
                &mint(&fixture, select_principals(&fixture.principals, kinds)),
                NOW,
            )
            .expect("valid identity combination");
        assert_eq!(caller.user.is_some(), expect_user);
        assert_eq!(caller.bot.is_some(), expect_bot);
    }
}

#[test]
fn principal_order_does_not_change_the_normalized_caller() {
    let fixture = fixture();
    let forward = verifier_from(&fixture)
        .verify_at(&mint(&fixture, fixture.principals.clone()), NOW)
        .expect("forward order");
    let mut reversed = fixture
        .principals
        .as_array()
        .expect("fixture principals array")
        .clone();
    reversed.reverse();
    let reverse = verifier_from(&fixture)
        .verify_at(&mint(&fixture, Value::Array(reversed)), NOW)
        .expect("reverse order");
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
