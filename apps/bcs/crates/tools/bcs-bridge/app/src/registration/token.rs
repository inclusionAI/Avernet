//! Unverified selection metadata of v2 Provider bot registration tokens.

use std::time::{SystemTime, UNIX_EPOCH};

use anyhow::{Result, anyhow, bail};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use serde::Deserialize;

use crate::credentials::MAX_TOKEN_BYTES;

const HMAC_BYTES: usize = 32;

#[derive(Deserialize)]
struct RegistrationScope {
    v: u8,
    purpose: String,
    id: String,
    provider_id: String,
    allowed_modes: Vec<Mode>,
    exp: u64,
}

/// Delivery mode of a registered Bot; matches the BCS registration wire values.
#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, clap::ValueEnum)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    Plugin,
    Gateway,
}

impl Mode {
    pub fn name(self) -> &'static str {
        match self { Self::Plugin => "plugin", Self::Gateway => "gateway" }
    }
}

/// Read unverified metadata solely to select a locally supported Provider.
/// BCS must authenticate the original token; webhook credentials are not its
/// signing keys. This function does not grant registration authorization.
pub fn registration_provider(token: &str, required: Mode) -> Result<String> {
    if token.len() > MAX_TOKEN_BYTES {
        bail!("Registration token exceeds the 16 KiB size limit");
    }
    let bytes = URL_SAFE_NO_PAD.decode(token)
        .map_err(|_| anyhow!("Registration token has invalid base64url encoding"))?;
    if bytes.len() <= HMAC_BYTES {
        bail!("Registration token is missing its payload or signature");
    }
    let payload: RegistrationScope = serde_json::from_slice(&bytes[..bytes.len() - HMAC_BYTES])
        .map_err(|_| anyhow!("Registration token has malformed v2 selection metadata"))?;
    if payload.v != 2 || payload.purpose != "provider_bot_registration" {
        bail!("Registration requires a v2 Provider bot registration token");
    }
    if !payload.id.strip_prefix("human_").is_some_and(|suffix| !suffix.trim().is_empty()) {
        bail!("Registration token must identify a human account");
    }
    if payload.provider_id.trim().is_empty() {
        bail!("Registration token is missing its Provider ID");
    }
    if !payload.allowed_modes.contains(&required) {
        bail!("Registration token does not allow {} mode", required.name());
    }
    if payload.allowed_modes.iter().enumerate().any(|(index, mode)| payload.allowed_modes[..index].contains(mode)) {
        bail!("Registration token contains duplicate allowed modes");
    }
    let now = SystemTime::now().duration_since(UNIX_EPOCH)
        .map_err(|_| anyhow!("Cannot validate registration token expiry with the current system clock"))?
        .as_secs();
    if payload.exp <= now {
        bail!("Registration token has expired");
    }
    Ok(payload.provider_id)
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;
    use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
    use serde_json::{Value, json};

    fn payload() -> Value {
        json!({
            "v": 2,
            "purpose": "provider_bot_registration",
            "id": "human_test",
            "provider_id": "prv_test",
            "allowed_modes": ["plugin", "gateway"],
            "exp": u64::MAX,
        })
    }

    // The local reader must not verify these fake signature bytes. BCS performs
    // signature verification; this layer only selects a configured Provider.
    fn token_bytes(payload: &[u8]) -> String {
        let mut bytes = payload.to_vec();
        bytes.extend_from_slice(&[0; 32]);
        URL_SAFE_NO_PAD.encode(bytes)
    }

    fn token(value: &Value) -> String {
        token_bytes(&serde_json::to_vec(value).unwrap())
    }

    #[test]
    fn reads_provider_from_unverified_v2_selection_metadata() {
        assert_eq!(registration_provider(&token(&payload()), Mode::Gateway).unwrap(), "prv_test");
        assert_eq!(registration_provider(&token(&payload()), Mode::Plugin).unwrap(), "prv_test");
    }

    #[test]
    fn rejects_invalid_registration_encoding_and_shape() {
        for value in [String::new(), "not base64".to_string(), URL_SAFE_NO_PAD.encode([0; 32]), token_bytes(b"not-json"), token_bytes(b"[]"), token_bytes(b"{}"), format!("{}=", token(&payload()))] {
            assert!(registration_provider(&value, Mode::Gateway).is_err(), "invalid registration token accepted");
        }
        for field in ["v", "purpose", "id", "provider_id", "allowed_modes", "exp"] {
            let mut value = payload();
            value.as_object_mut().unwrap().remove(field);
            assert!(registration_provider(&token(&value), Mode::Gateway).is_err(), "missing {field} accepted");
        }
        let duplicate = serde_json::to_string(&payload()).unwrap().replacen("{", r#"{"provider_id":"other","#, 1);
        assert!(registration_provider(&token_bytes(duplicate.as_bytes()), Mode::Gateway).is_err());
    }

    #[test]
    fn validates_registration_version_purpose_human_and_provider() {
        for (field, invalid_values) in [
            ("v", vec![json!(0), json!(1), json!(3), json!("2")]),
            ("purpose", vec![json!(""), json!("bot_registration"), json!("provider_bot_registration ")]),
            ("id", vec![json!(""), json!("human_"), json!("human_ \t"), json!("bot_test"), json!(" human_test")]),
            ("provider_id", vec![json!(""), json!(" \t\n"), json!("\u{2003}"), json!(12)]),
        ] {
            for invalid in invalid_values {
                let mut value = payload();
                value[field] = invalid;
                assert!(registration_provider(&token(&value), Mode::Gateway).is_err(), "invalid {field} accepted");
            }
        }
    }

    #[test]
    fn registration_requires_the_requested_mode_in_distinct_supported_modes() {
        for modes in [json!([]), json!(["Gateway"]), json!(["gateway", "gateway"]), json!(["gateway", "upstream"]), json!(["gateway", "plugin", "plugin"])] {
            let mut value = payload();
            value["allowed_modes"] = modes;
            assert!(registration_provider(&token(&value), Mode::Gateway).is_err(), "invalid modes accepted");
        }
        let mut value = payload();
        value["allowed_modes"] = json!(["gateway"]);
        assert_eq!(registration_provider(&token(&value), Mode::Gateway).unwrap(), "prv_test");
        assert!(registration_provider(&token(&value), Mode::Plugin).is_err(), "plugin mode accepted by a gateway-only token");
        let mut value = payload();
        value["allowed_modes"] = json!(["plugin"]);
        assert_eq!(registration_provider(&token(&value), Mode::Plugin).unwrap(), "prv_test");
        assert!(registration_provider(&token(&value), Mode::Gateway).is_err(), "gateway mode accepted by a plugin-only token");
    }

    #[test]
    fn registration_expiry_is_unix_seconds_strictly_in_future() {
        let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();
        for exp in [json!(0), json!(now - 1), json!(now), json!(-1), json!("9999999999")] {
            let mut value = payload();
            value["exp"] = exp;
            assert!(registration_provider(&token(&value), Mode::Gateway).is_err(), "invalid expiry accepted");
        }
        let mut value = payload();
        value["exp"] = json!(now + 60);
        assert_eq!(registration_provider(&token(&value), Mode::Gateway).unwrap(), "prv_test");
    }

    #[test]
    fn registration_errors_never_echo_token_or_payload_values() {
        let secret = "registration-secret-do-not-disclose";
        let mut value = payload();
        value["allowed_modes"] = json!([secret]);
        let input = token(&value);
        let error = registration_provider(&input, Mode::Gateway).unwrap_err();
        for output in [format!("{error:#}"), format!("{error:?}")] {
            assert!(!output.contains(secret));
            assert!(!output.contains(&input));
        }
    }

    #[test]
    fn bounds_registration_input() {
        let mut value = payload();
        value["provider_id"] = json!("p".repeat(64 * 1024));
        assert!(registration_provider(&token(&value), Mode::Gateway).is_err());
    }
}
