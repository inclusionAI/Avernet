use bcs_jwt::{Claims, JwtError, JwtService};

fn make_claims(exp: u64) -> Claims {
    Claims {
        sub: "bot-42".into(),
        src: "google".into(),
        iat: 1000,
        exp,
    }
}

#[test]
fn sign_and_verify_roundtrip() {
    let svc = JwtService::new("secret");
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("epoch")
        .as_secs();
    let claims = Claims {
        sub: "user-1".into(),
        src: "google".into(),
        iat: now,
        exp: now + 3600,
    };
    let token = svc.sign(&claims).expect("sign");
    let verified = svc.verify(&token).expect("verify");
    assert_eq!(verified.sub, "user-1");
    assert_eq!(verified.src, "google");
    assert_eq!(verified.iat, now);
    assert_eq!(verified.exp, now + 3600);
}

#[test]
fn verify_rejects_wrong_secret() {
    let svc_a = JwtService::new("secret-a");
    let svc_b = JwtService::new("secret-b");
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("epoch")
        .as_secs();
    let claims = Claims {
        sub: "bot".into(),
        src: "test".into(),
        iat: now,
        exp: now + 3600,
    };
    let token = svc_a.sign(&claims).expect("sign");
    let result = svc_b.verify(&token);
    assert!(matches!(result, Err(JwtError::InvalidSignature)));
}

#[test]
fn verify_rejects_expired() {
    let svc = JwtService::new("secret");
    // exp in the past
    let claims = make_claims(100);
    let token = svc.sign(&claims).expect("sign");
    let result = svc.verify(&token);
    assert!(matches!(result, Err(JwtError::Expired)));
}

#[test]
fn verify_rejects_malformed_token() {
    let svc = JwtService::new("secret");

    // empty
    let r = svc.verify("");
    assert!(matches!(r, Err(JwtError::InvalidToken(_))));

    // wrong number of parts
    let r = svc.verify("abc.def");
    assert!(matches!(r, Err(JwtError::InvalidToken(_))));

    // garbage base64
    let r = svc.verify("!!!.!!!.!!!");
    assert!(matches!(r, Err(JwtError::InvalidToken(_))));
}

#[test]
fn claims_serialization_minimal() {
    let claims = Claims {
        sub: "s".into(),
        src: "g".into(),
        iat: 1,
        exp: 2,
    };
    let json = serde_json::to_value(&claims).expect("serde");
    // Exactly 4 fields
    let obj = json.as_object().expect("object");
    assert_eq!(obj.len(), 4);
    assert_eq!(obj["sub"], "s");
    assert_eq!(obj["src"], "g");
    assert_eq!(obj["iat"], 1);
    assert_eq!(obj["exp"], 2);
}

#[test]
fn should_refresh_at_50_percent_threshold() {
    // iat=0, exp=1800 → lifetime=1800, threshold=900
    let claims = Claims {
        sub: "bot".into(),
        src: "test".into(),
        iat: 0,
        exp: 1800,
    };

    // now=800 → elapsed 800 < 900 → no refresh
    assert!(!claims.should_refresh(800));

    // now=900 → elapsed 900 >= 900 → refresh
    assert!(claims.should_refresh(900));

    // now=1000 → elapsed 1000 >= 900 → refresh
    assert!(claims.should_refresh(1000));
}

#[test]
fn verify_no_exp_allows_expired_token() {
    let svc = JwtService::new("secret");
    let claims = make_claims(100); // expired
    let token = svc.sign(&claims).expect("sign");
    // verify_no_exp should succeed (signature valid, expiry ignored)
    let result = svc.verify_no_exp(&token);
    assert!(result.is_ok());
    assert_eq!(result.expect("ok").sub, "bot-42");
}
