//! Provider-scoped v2 registration tokens, separate from legacy v1 tokens.
//!
//! The wire format is unpadded URL-safe base64 of JSON followed by its
//! HMAC-SHA256 signature. Consumers supporting both versions should try the
//! legacy verifier first and invoke this verifier only on `UnsupportedVersion`.
//! A failed v2 verification must never fall back to legacy registration.

use crate::register::RegisterTokenError;
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;

type HmacSha256 = Hmac<Sha256>;

const HMAC_LEN: usize = 32;
const CURRENT_VERSION: u8 = 2;
const PURPOSE: &str = "provider_bot_registration";

/// Provider connection modes authorized by a registration token.
pub use crate::provider::ProviderBotConnectionMode as ProviderRegistrationMode;

/// Signed authorization for one human to register bots with one provider.
///
/// Verification requires version 2, purpose `provider_bot_registration`, a
/// `human_` ID with a nonempty suffix, a nonblank provider ID, at least one
/// distinct allowed mode, and an expiry strictly later than the current time.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProviderRegisterTokenPayload {
    pub v: u8,
    pub purpose: String,
    pub id: String,
    pub provider_id: String,
    pub allowed_modes: Vec<ProviderRegistrationMode>,
    pub exp: u64,
}

/// Sign the payload as supplied; semantic validation is performed on decode.
pub fn encode(payload: &ProviderRegisterTokenPayload, secret: &[u8]) -> String {
    let payload_bytes =
        serde_json::to_vec(payload).expect("ProviderRegisterTokenPayload is always serializable");
    let mut mac = HmacSha256::new_from_slice(secret).expect("HMAC accepts any key length");
    mac.update(&payload_bytes);
    let signature = mac.finalize().into_bytes();

    let mut combined = payload_bytes;
    combined.extend_from_slice(&signature);
    URL_SAFE_NO_PAD.encode(combined)
}

/// Authenticate and validate a v2 token using Unix time in seconds.
pub fn decode_and_verify(
    token: &str,
    secret: &[u8],
) -> Result<ProviderRegisterTokenPayload, RegisterTokenError> {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(0);
    decode_and_verify_at(token, secret, now)
}

fn decode_and_verify_at(
    token: &str,
    secret: &[u8],
    now: u64,
) -> Result<ProviderRegisterTokenPayload, RegisterTokenError> {
    let raw = URL_SAFE_NO_PAD
        .decode(token)
        .map_err(|_| RegisterTokenError::InvalidEncoding)?;
    if raw.len() < HMAC_LEN + 1 {
        return Err(RegisterTokenError::InvalidEncoding);
    }

    let (payload_bytes, signature) = raw.split_at(raw.len() - HMAC_LEN);
    let mut mac = HmacSha256::new_from_slice(secret).expect("HMAC accepts any key length");
    mac.update(payload_bytes);
    mac.verify_slice(signature)
        .map_err(|_| RegisterTokenError::InvalidSignature)?;

    // Check the authenticated version before requiring version-specific fields.
    #[derive(Deserialize)]
    struct Version {
        v: u8,
    }
    let version: Version = serde_json::from_slice(payload_bytes)
        .map_err(|error| RegisterTokenError::MalformedPayload(error.to_string()))?;
    if version.v != CURRENT_VERSION {
        return Err(RegisterTokenError::UnsupportedVersion);
    }

    let payload: ProviderRegisterTokenPayload = serde_json::from_slice(payload_bytes)
        .map_err(|error| RegisterTokenError::MalformedPayload(error.to_string()))?;
    if payload.exp <= now {
        return Err(RegisterTokenError::Expired);
    }
    if payload.purpose != PURPOSE {
        return Err(RegisterTokenError::MalformedPayload(
            "invalid purpose".to_string(),
        ));
    }
    if !payload
        .id
        .strip_prefix("human_")
        .is_some_and(|suffix| !suffix.is_empty())
    {
        return Err(RegisterTokenError::NotHumanToken);
    }
    if payload.provider_id.trim().is_empty() {
        return Err(RegisterTokenError::MalformedPayload(
            "provider_id must not be blank".to_string(),
        ));
    }
    if payload.allowed_modes.is_empty() {
        return Err(RegisterTokenError::MalformedPayload(
            "allowed_modes must not be empty".to_string(),
        ));
    }
    if payload
        .allowed_modes
        .iter()
        .enumerate()
        .any(|(index, mode)| payload.allowed_modes[..index].contains(mode))
    {
        return Err(RegisterTokenError::MalformedPayload(
            "allowed_modes must be unique".to_string(),
        ));
    }

    Ok(payload)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::register::{self, RegisterTokenError, RegisterTokenPayload};
    use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
    use hmac::{Hmac, Mac};
    use serde_json::{Value, json};
    use sha2::Sha256;

    const SECRET: &[u8] = b"provider-registration-test-secret";
    const NOW: u64 = 1_800_000_000;

    fn payload() -> ProviderRegisterTokenPayload {
        ProviderRegisterTokenPayload {
            v: 2,
            purpose: "provider_bot_registration".to_string(),
            id: "human_user123".to_string(),
            provider_id: "provider-a".to_string(),
            allowed_modes: vec![
                ProviderRegistrationMode::Plugin,
                ProviderRegistrationMode::Gateway,
            ],
            exp: u64::MAX,
        }
    }

    fn sign_bytes(bytes: &[u8]) -> String {
        let mut mac = Hmac::<Sha256>::new_from_slice(SECRET).unwrap();
        mac.update(bytes);
        let mut combined = bytes.to_vec();
        combined.extend_from_slice(&mac.finalize().into_bytes());
        URL_SAFE_NO_PAD.encode(combined)
    }

    fn sign_json(value: &Value) -> String {
        sign_bytes(&serde_json::to_vec(value).unwrap())
    }

    #[test]
    fn roundtrip_preserves_all_fields_and_allowed_mode_combinations() {
        for modes in [
            vec![ProviderRegistrationMode::Plugin],
            vec![ProviderRegistrationMode::Gateway],
            vec![
                ProviderRegistrationMode::Plugin,
                ProviderRegistrationMode::Gateway,
            ],
            vec![
                ProviderRegistrationMode::Gateway,
                ProviderRegistrationMode::Plugin,
            ],
        ] {
            let expected = ProviderRegisterTokenPayload {
                allowed_modes: modes,
                ..payload()
            };
            let decoded = decode_and_verify(&encode(&expected, SECRET), SECRET).unwrap();
            assert_eq!(decoded, expected);
        }
    }

    #[test]
    fn wire_format_is_unpadded_url_safe_json_followed_by_hmac_sha256() {
        let token = encode(&payload(), SECRET);
        assert!(
            token
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
        );
        let raw = URL_SAFE_NO_PAD.decode(&token).unwrap();
        let (bytes, signature) = raw.split_at(raw.len() - 32);
        assert_eq!(
            serde_json::from_slice::<Value>(bytes).unwrap(),
            json!({
                "v": 2,
                "purpose": "provider_bot_registration",
                "id": "human_user123",
                "provider_id": "provider-a",
                "allowed_modes": ["plugin", "gateway"],
                "exp": u64::MAX,
            })
        );
        let mut mac = Hmac::<Sha256>::new_from_slice(SECRET).unwrap();
        mac.update(bytes);
        assert!(mac.verify_slice(signature).is_ok());
    }

    #[test]
    fn accepts_independently_signed_payload() {
        let expected = payload();
        let token = sign_json(&serde_json::to_value(&expected).unwrap());
        assert_eq!(decode_and_verify(&token, SECRET).unwrap(), expected);
    }

    #[test]
    fn rejects_wrong_secret() {
        let token = sign_json(&serde_json::to_value(payload()).unwrap());
        assert_eq!(
            decode_and_verify(&token, b"wrong-secret"),
            Err(RegisterTokenError::InvalidSignature)
        );
    }

    #[test]
    fn rejects_tampered_payload_before_parsing() {
        let token = sign_json(&serde_json::to_value(payload()).unwrap());
        let mut raw = URL_SAFE_NO_PAD.decode(token).unwrap();
        raw[0] ^= 1;
        assert_eq!(
            decode_and_verify(&URL_SAFE_NO_PAD.encode(raw), SECRET),
            Err(RegisterTokenError::InvalidSignature)
        );
    }

    #[test]
    fn rejects_tampered_signature() {
        let token = sign_json(&serde_json::to_value(payload()).unwrap());
        let mut raw = URL_SAFE_NO_PAD.decode(token).unwrap();
        *raw.last_mut().unwrap() ^= 1;
        assert_eq!(
            decode_and_verify(&URL_SAFE_NO_PAD.encode(raw), SECRET),
            Err(RegisterTokenError::InvalidSignature)
        );
    }

    #[test]
    fn rejects_invalid_encoding_and_short_tokens() {
        for token in [
            String::new(),
            "%%%".to_string(),
            URL_SAFE_NO_PAD.encode([0_u8; 32]),
        ] {
            assert_eq!(
                decode_and_verify(&token, SECRET),
                Err(RegisterTokenError::InvalidEncoding)
            );
        }
    }

    #[test]
    fn rejects_signed_invalid_json() {
        assert!(matches!(
            decode_and_verify(&sign_bytes(b"not-json"), SECRET),
            Err(RegisterTokenError::MalformedPayload(_))
        ));
    }

    #[test]
    fn rejects_missing_required_fields() {
        for field in ["v", "purpose", "id", "provider_id", "allowed_modes", "exp"] {
            let mut value = serde_json::to_value(payload()).unwrap();
            value.as_object_mut().unwrap().remove(field);
            assert!(
                matches!(
                    decode_and_verify(&sign_json(&value), SECRET),
                    Err(RegisterTokenError::MalformedPayload(_))
                ),
                "missing {field}"
            );
        }
    }

    #[test]
    fn rejects_unknown_or_non_snake_case_modes() {
        for mode in ["upstream", "Plugin", "Gateway", ""] {
            let mut value = serde_json::to_value(payload()).unwrap();
            value["allowed_modes"] = json!([mode]);
            assert!(
                matches!(
                    decode_and_verify(&sign_json(&value), SECRET),
                    Err(RegisterTokenError::MalformedPayload(_))
                ),
                "mode {mode}"
            );
        }
    }

    #[test]
    fn rejects_versions_other_than_two() {
        for version in [0, 1, 3, u8::MAX] {
            let invalid = ProviderRegisterTokenPayload {
                v: version,
                ..payload()
            };
            let token = sign_json(&serde_json::to_value(invalid).unwrap());
            assert_eq!(
                decode_and_verify(&token, SECRET),
                Err(RegisterTokenError::UnsupportedVersion)
            );
        }
    }

    #[test]
    fn rejects_wrong_purpose() {
        for purpose in [
            "",
            "bot_registration",
            "provider_bot_registration ",
            "Provider_Bot_Registration",
        ] {
            let invalid = ProviderRegisterTokenPayload {
                purpose: purpose.to_string(),
                ..payload()
            };
            let token = sign_json(&serde_json::to_value(invalid).unwrap());
            assert!(matches!(
                decode_and_verify(&token, SECRET),
                Err(RegisterTokenError::MalformedPayload(_))
            ));
        }
    }

    #[test]
    fn rejects_blank_provider() {
        for provider_id in ["", " \t\n", "\u{2003}"] {
            let invalid = ProviderRegisterTokenPayload {
                provider_id: provider_id.to_string(),
                ..payload()
            };
            let token = sign_json(&serde_json::to_value(invalid).unwrap());
            assert!(matches!(
                decode_and_verify(&token, SECRET),
                Err(RegisterTokenError::MalformedPayload(_))
            ));
        }
    }

    #[test]
    fn rejects_empty_modes() {
        let invalid = ProviderRegisterTokenPayload {
            allowed_modes: vec![],
            ..payload()
        };
        let token = sign_json(&serde_json::to_value(invalid).unwrap());
        assert!(matches!(
            decode_and_verify(&token, SECRET),
            Err(RegisterTokenError::MalformedPayload(_))
        ));
    }

    #[test]
    fn rejects_duplicate_modes() {
        for modes in [
            vec![
                ProviderRegistrationMode::Plugin,
                ProviderRegistrationMode::Plugin,
            ],
            vec![
                ProviderRegistrationMode::Gateway,
                ProviderRegistrationMode::Gateway,
            ],
            vec![
                ProviderRegistrationMode::Plugin,
                ProviderRegistrationMode::Gateway,
                ProviderRegistrationMode::Plugin,
            ],
        ] {
            let invalid = ProviderRegisterTokenPayload {
                allowed_modes: modes,
                ..payload()
            };
            let token = sign_json(&serde_json::to_value(invalid).unwrap());
            assert!(matches!(
                decode_and_verify(&token, SECRET),
                Err(RegisterTokenError::MalformedPayload(_))
            ));
        }
    }

    #[test]
    fn rejects_non_human_ids() {
        for id in ["", "bot_agent007", "Human_user123", " human_user123"] {
            let invalid = ProviderRegisterTokenPayload {
                id: id.to_string(),
                ..payload()
            };
            let token = sign_json(&serde_json::to_value(invalid).unwrap());
            assert_eq!(
                decode_and_verify(&token, SECRET),
                Err(RegisterTokenError::NotHumanToken)
            );
        }
    }

    #[test]
    fn rejects_human_id_without_suffix() {
        let invalid = ProviderRegisterTokenPayload {
            id: "human_".to_string(),
            ..payload()
        };
        let token = sign_json(&serde_json::to_value(invalid).unwrap());
        assert_eq!(
            decode_and_verify(&token, SECRET),
            Err(RegisterTokenError::NotHumanToken)
        );
    }

    #[test]
    fn rejects_expiry_at_or_before_now() {
        for exp in [0, NOW - 1, NOW] {
            let expired = ProviderRegisterTokenPayload { exp, ..payload() };
            let token = sign_json(&serde_json::to_value(expired).unwrap());
            assert_eq!(
                decode_and_verify_at(&token, SECRET, NOW),
                Err(RegisterTokenError::Expired)
            );
        }
    }

    #[test]
    fn accepts_expiry_one_second_after_now() {
        let expected = ProviderRegisterTokenPayload {
            exp: NOW + 1,
            ..payload()
        };
        let token = sign_json(&serde_json::to_value(&expected).unwrap());
        assert_eq!(decode_and_verify_at(&token, SECRET, NOW).unwrap(), expected);
    }

    #[test]
    fn public_verifier_rejects_expired_token() {
        let expired = ProviderRegisterTokenPayload {
            exp: 0,
            ..payload()
        };
        let token = sign_json(&serde_json::to_value(expired).unwrap());
        assert_eq!(
            decode_and_verify(&token, SECRET),
            Err(RegisterTokenError::Expired)
        );
    }

    #[test]
    fn legacy_verifier_explicitly_rejects_v2() {
        let token = encode(&payload(), SECRET);
        assert!(matches!(
            register::decode_and_verify(&token, SECRET),
            Err(RegisterTokenError::UnsupportedVersion)
        ));
    }

    #[test]
    fn new_verifier_rejects_v1() {
        let legacy = RegisterTokenPayload {
            v: 1,
            id: "human_user123".to_string(),
            exp: u64::MAX,
        };
        let token = register::encode(&legacy, SECRET);
        assert!(register::decode_and_verify(&token, SECRET).is_ok());
        assert_eq!(
            decode_and_verify(&token, SECRET),
            Err(RegisterTokenError::UnsupportedVersion)
        );
    }

    #[test]
    fn verifies_signature_before_dispatching_version() {
        let legacy = RegisterTokenPayload {
            v: 1,
            id: "human_user123".to_string(),
            exp: u64::MAX,
        };
        let token = register::encode(&legacy, SECRET);
        assert_eq!(
            decode_and_verify(&token, b"wrong-secret"),
            Err(RegisterTokenError::InvalidSignature)
        );
    }
}
