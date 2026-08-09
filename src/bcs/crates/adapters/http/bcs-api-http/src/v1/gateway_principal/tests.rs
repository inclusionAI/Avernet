use std::io::{self, Write};
use std::sync::{Arc, Mutex};

use bcs_service_api::application::v1::AuthenticatedCaller;
use axum::http::{HeaderMap, HeaderValue};
use jsonwebtoken::{Algorithm, EncodingKey, Header, encode};
use serde::Deserialize;
use serde_json::{Value, json};

use super::{
    GatewayPrincipalTokenVerifier, GatewayPrincipalTrust, GatewayPrincipalVerificationError,
    GatewayPrincipalVerifierBuildError,
};
use crate::v1::common::{PrincipalVerificationError, PrincipalVerifier};

const NOW: u64 = 1_785_657_600;
const TEST_KEY_TEXT: &str = "TEST-ONLY-bcs-principal-contract-key-32-bytes";
const TEST_KEY: &[u8] = TEST_KEY_TEXT.as_bytes();

#[derive(Clone, Default)]
struct SharedLogBuffer(Arc<Mutex<Vec<u8>>>);

struct SharedLogWriter(SharedLogBuffer);

impl Write for SharedLogWriter {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        self.0.0.lock().expect("log buffer lock").extend_from_slice(bytes);
        Ok(bytes.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

impl<'a> tracing_subscriber::fmt::MakeWriter<'a> for SharedLogBuffer {
    type Writer = SharedLogWriter;

    fn make_writer(&'a self) -> Self::Writer {
        SharedLogWriter(self.clone())
    }
}

fn capture_logs(run: impl FnOnce()) -> String {
    let buffer = SharedLogBuffer::default();
    let subscriber = tracing_subscriber::fmt()
        .without_time()
        .with_ansi(false)
        .with_target(false)
        .with_writer(buffer.clone())
        .finish();
    tracing::dispatcher::with_default(&tracing::Dispatch::new(subscriber), run);
    let bytes = buffer.0.lock().expect("log buffer lock").clone();
    String::from_utf8(bytes).expect("diagnostic logs are UTF-8")
}

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

fn current_token() -> String {
    let fixture = fixture();
    let now = u64::try_from(time::OffsetDateTime::now_utc().unix_timestamp())
        .expect("current timestamp is positive");
    mint_with(
        header("JWT", &fixture.key_id),
        &json!({
            "iss": fixture.issuer,
            "aud": fixture.audience,
            "iat": now.saturating_sub(1),
            "exp": now + 60,
            "principals": fixture.principals,
        }),
        TEST_KEY,
    )
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

    assert_eq!(caller.tenant.as_deref(), Some("tenant-a"));
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

#[tokio::test]
async fn header_verifier_extracts_one_signed_gateway_principal_token() {
    let mut headers = HeaderMap::new();
    headers.insert(
        "x-avernet-principal",
        HeaderValue::from_str(&current_token()).expect("valid header value"),
    );

    let caller = PrincipalVerifier::verify(&verifier(), &headers)
        .await
        .expect("valid signed Gateway caller");

    assert_eq!(caller.tenant.as_deref(), Some("tenant-a"));
    assert_eq!(caller.user.as_ref().map(|user| user.id.as_str()), Some("user-1"));
    assert_eq!(caller.bot.as_ref().map(|bot| bot.bot_uuid.as_str()), Some("bot-1"));
    assert!(caller.app.is_some());
    assert!(caller.access_key.is_some());
}

#[tokio::test]
async fn header_verifier_rejects_missing_duplicate_blank_and_non_utf8_values() {
    let verifier = verifier();
    assert!(matches!(
        PrincipalVerifier::verify(&verifier, &HeaderMap::new()).await,
        Err(PrincipalVerificationError::Missing)
    ));

    let mut duplicate = HeaderMap::new();
    duplicate.append("x-avernet-principal", HeaderValue::from_static("one"));
    duplicate.append("x-avernet-principal", HeaderValue::from_static("two"));
    assert!(matches!(
        PrincipalVerifier::verify(&verifier, &duplicate).await,
        Err(PrincipalVerificationError::Invalid(_))
    ));

    for value in [
        HeaderValue::from_static(""),
        HeaderValue::from_static("   "),
        HeaderValue::from_bytes(&[0xff]).expect("opaque non-UTF-8 header"),
    ] {
        let mut headers = HeaderMap::new();
        headers.insert("x-avernet-principal", value);
        assert!(matches!(
            PrincipalVerifier::verify(&verifier, &headers).await,
            Err(PrincipalVerificationError::Invalid(_))
        ));
    }
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
fn accepts_user_only_with_null_or_missing_tenant() {
    let fixture = fixture();
    let user_only = select_principals(&fixture.principals, &["user"]);

    let mut null_tenant = user_only.clone();
    null_tenant[0]["tenant"] = Value::Null;
    let caller = must_ok(
        verify_principals(null_tenant),
        "User with null tenant must verify",
    );
    assert_eq!(caller.tenant, None);

    let mut missing_tenant = user_only;
    must_some(
        missing_tenant[0].as_object_mut(),
        "User Principal object",
    )
    .remove("tenant");
    let caller = must_ok(
        verify_principals(missing_tenant),
        "User with missing tenant must verify",
    );
    assert_eq!(caller.tenant, None);
}

#[test]
fn nullable_user_tenant_does_not_weaken_required_principal_tenants() {
    let fixture = fixture();
    let mut principals = select_principals(&fixture.principals, &["user", "bot", "app"]);
    principals[0]["tenant"] = Value::Null;
    principals[0]["subject"]["tenant_id"] = Value::Null;

    let caller = must_ok(
        verify_principals(principals.clone()),
        "required Principal tenants establish the normalized tenant",
    );
    assert_eq!(caller.tenant.as_deref(), Some("tenant-a"));

    principals[1]["tenant"] = Value::Null;
    assert_eq!(
        verify_principals(principals),
        Err(GatewayPrincipalVerificationError::InvalidClaims),
    );
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
    ] {
        let mut principals = fixture().principals;
        *must_some(principals.pointer_mut(pointer), "fixture pointer") = json!("tenant-b");
        assert_eq!(
            verify_principals(principals),
            Err(GatewayPrincipalVerificationError::InvalidPrincipalSet),
            "tenant mutation at {pointer}",
        );
    }

    let mut principals = fixture().principals;
    principals[0]["tenant"] = json!("tenant-a");
    principals[0]["subject"]["tenant_id"] = json!("tenant-b");
    assert_eq!(
        verify_principals(principals),
        Err(GatewayPrincipalVerificationError::InvalidPrincipalSet),
        "present User tenant and subject.tenant_id must agree",
    );

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
fn invalid_claim_logs_exact_path_and_fingerprint_without_token_material() {
    let mut principals = fixture().principals;
    principals[0]["tenant"] = json!({"invalid": "shape"});
    let mut claims = valid_claims();
    claims["principals"] = principals;
    let token = mint_with(header("JWT", "bare"), &claims, TEST_KEY);
    let header_segment = token.split('.').next().expect("JWT header segment");

    let logs = capture_logs(|| {
        assert_eq!(
            verifier().verify_at(&token, NOW),
            Err(GatewayPrincipalVerificationError::InvalidClaims),
        );
    });

    assert!(logs.contains("claim_path=principals[0].tenant"), "{logs}");
    assert!(logs.contains("token_fingerprint="), "{logs}");
    for forbidden in [
        token.as_str(),
        header_segment,
        TEST_KEY_TEXT,
        "TEST_ONLY_BOT_TOKEN_MARKER",
        "TEST_ONLY_ACCESS_KEY_TOKEN_MARKER",
    ] {
        assert!(!logs.contains(forbidden), "logs exposed {forbidden}: {logs}");
    }
}

#[test]
fn public_verify_uses_the_current_system_time() {
    let now = jsonwebtoken::get_current_timestamp();
    let token = token_with_times(now, now + 60);

    assert!(verifier().verify(&token).is_ok());
}

#[test]
fn wrong_issuer_and_audience_log_specific_mismatch() {
    for (claim, value, needle) in [
        ("iss", "other-gateway", "issuer mismatch"),
        ("aud", "backend", "audience mismatch"),
    ] {
        let mut claims = valid_claims();
        claims[claim] = json!(value);
        let token = mint_with(header("JWT", "bare"), &claims, TEST_KEY);
        let logs = capture_logs(|| {
            assert_eq!(
                verifier().verify_at(&token, NOW),
                Err(GatewayPrincipalVerificationError::InvalidClaims),
            );
        });
        assert!(logs.contains(needle), "missing {needle:?} in:\n{logs}");
        assert!(
            !logs.contains("claims are invalid"),
            "generic decode log should not fire for {claim} mismatch:\n{logs}"
        );
    }
}

#[test]
fn rejects_array_valued_iss_and_aud() {
    // RFC 7519 permits `aud` as a string or array; jsonwebtoken accepts the
    // array form against set_audience. The gateway-principal contract
    // requires the exact single-string iss/aud (contract.md: iss=gateway,
    // aud=bcs), so array-form claims must be rejected by shape regardless of
    // whether the value matches. The shape log must classify the failure
    // without leaking the array contents (contract.md: no claim value logged).
    let fixture = fixture();
    let verifier = verifier_from(&fixture);
    // The array includes the configured value so `decode` accepts it (forcing
    // the shape guard, not value validation, to reject); the marker element
    // proves the array contents are not logged.
    for (claim, configured, marker) in [
        ("iss", fixture.issuer.clone(), "SECRET_ISS_ARRAY_MARKER"),
        ("aud", fixture.audience.clone(), "SECRET_AUD_ARRAY_MARKER"),
    ] {
        let mut claims = valid_claims();
        claims[claim] = json!([configured, marker]);
        let token = mint_with(header("JWT", "bare"), &claims, TEST_KEY);
        let logs = capture_logs(|| {
            assert_eq!(
                verifier.verify_at(&token, NOW),
                Err(GatewayPrincipalVerificationError::InvalidClaims),
                "array-form {claim} must be rejected (exact-string contract)",
            );
        });
        assert!(
            logs.contains("must be a single string"),
            "shape log missing for {claim}:\n{logs}"
        );
        assert!(
            logs.contains("observed=array"),
            "observed=array missing for {claim}:\n{logs}"
        );
        assert!(
            !logs.contains(marker),
            "array contents leaked into log for {claim}:\n{logs}"
        );
    }
}
