use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;

type HmacSha256 = Hmac<Sha256>;

/// Kind of resource an invite token grants access to.
///
/// V1 invite tokens carry this so the join path can distinguish group vs
/// session targets without inspecting the join URL. Legacy tokens minted
/// before this field existed decode to `target_type: None` (see
/// [`InviteTokenPayload::target_type`]).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InviteTargetType {
    Group,
    Session,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InviteTokenPayload {
    pub v: u8,
    pub id: String,
    pub exp: u64,
    /// Target kind the token grants access to.
    ///
    /// `None` on legacy tokens minted before this field existed: the JSON key
    /// is absent, so serde's `default` resolves to `None`. `skip_serializing_if`
    /// keeps legacy-minted tokens byte-identical (no `target_type` key) so they
    /// still verify against the HMAC computed before this field was added.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub target_type: Option<InviteTargetType>,
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum InviteTokenError {
    #[error("invalid invite token encoding")]
    InvalidEncoding,
    #[error("invalid invite token signature")]
    InvalidSignature,
    #[error("invite link has expired")]
    Expired,
    #[error("unsupported invite token version")]
    UnsupportedVersion,
    #[error("malformed invite token payload: {0}")]
    MalformedPayload(String),
}

const HMAC_LEN: usize = 32;
const CURRENT_VERSION: u8 = 1;

pub fn encode(payload: &InviteTokenPayload, secret: &[u8]) -> String {
    let payload_bytes = serde_json::to_vec(payload).expect("InviteTokenPayload is always serializable");
    let mut mac = HmacSha256::new_from_slice(secret).expect("HMAC accepts any key length");
    mac.update(&payload_bytes);
    let signature = mac.finalize().into_bytes();

    let mut combined = payload_bytes;
    combined.extend_from_slice(&signature);

    use base64::Engine;
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(&combined)
}

pub fn decode_and_verify(
    token: &str,
    secret: &[u8],
) -> Result<InviteTokenPayload, InviteTokenError> {
    let payload = decode_and_verify_no_expiry(token, secret)?;

    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    if payload.exp < now {
        return Err(InviteTokenError::Expired);
    }

    Ok(payload)
}

pub fn decode_and_verify_no_expiry(
    token: &str,
    secret: &[u8],
) -> Result<InviteTokenPayload, InviteTokenError> {
    use base64::Engine;
    let raw = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(token)
        .map_err(|_| InviteTokenError::InvalidEncoding)?;

    if raw.len() < HMAC_LEN + 1 {
        return Err(InviteTokenError::InvalidEncoding);
    }

    let (payload_bytes, signature) = raw.split_at(raw.len() - HMAC_LEN);

    let mut mac = HmacSha256::new_from_slice(secret).expect("HMAC accepts any key length");
    mac.update(payload_bytes);
    mac.verify_slice(signature)
        .map_err(|_| InviteTokenError::InvalidSignature)?;

    let payload: InviteTokenPayload = serde_json::from_slice(payload_bytes)
        .map_err(|e| InviteTokenError::MalformedPayload(e.to_string()))?;

    if payload.v != CURRENT_VERSION {
        return Err(InviteTokenError::UnsupportedVersion);
    }

    Ok(payload)
}

#[cfg(test)]
mod tests {
    use super::*;

    const SECRET: &[u8] = b"test-secret-key-32-bytes-long!!!";

    fn future_exp() -> u64 {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs()
            + 3600
    }

    #[test]
    fn roundtrip_encode_decode() {
        let payload = InviteTokenPayload {
            v: 1,
            id: "grp-001".to_string(),
            exp: future_exp(),
            target_type: None,
        };
        let token = encode(&payload, SECRET);
        let decoded = decode_and_verify(&token, SECRET).unwrap();
        assert_eq!(decoded.id, "grp-001");
        assert_eq!(decoded.v, 1);
    }

    #[test]
    fn rejects_tampered_payload() {
        let payload = InviteTokenPayload {
            v: 1,
            id: "grp-001".to_string(),
            exp: future_exp(),
            target_type: None,
        };
        let token = encode(&payload, SECRET);
        let mut chars: Vec<char> = token.chars().collect();
        let mid = chars.len() / 2;
        chars[mid] = if chars[mid] == 'A' { 'B' } else { 'A' };
        let tampered: String = chars.into_iter().collect();

        let result = decode_and_verify(&tampered, SECRET);
        assert!(
            matches!(
                result,
                Err(InviteTokenError::InvalidSignature)
                    | Err(InviteTokenError::InvalidEncoding)
                    | Err(InviteTokenError::MalformedPayload(_))
            ),
        );
    }

    #[test]
    fn rejects_wrong_secret() {
        let payload = InviteTokenPayload {
            v: 1,
            id: "grp-001".to_string(),
            exp: future_exp(),
            target_type: None,
        };
        let token = encode(&payload, SECRET);
        let result = decode_and_verify(&token, b"wrong-secret");
        assert!(matches!(result, Err(InviteTokenError::InvalidSignature)));
    }

    #[test]
    fn rejects_expired_token() {
        let payload = InviteTokenPayload {
            v: 1,
            id: "grp-001".to_string(),
            exp: 1000,
            target_type: None,
        };
        let token = encode(&payload, SECRET);
        let result = decode_and_verify(&token, SECRET);
        assert!(matches!(result, Err(InviteTokenError::Expired)));
    }

    #[test]
    fn rejects_unsupported_version() {
        let payload = InviteTokenPayload {
            v: 99,
            id: "grp-001".to_string(),
            exp: future_exp(),
            target_type: None,
        };
        let token = encode(&payload, SECRET);
        let result = decode_and_verify(&token, SECRET);
        assert!(matches!(result, Err(InviteTokenError::UnsupportedVersion)));
    }

    #[test]
    fn rejects_empty_token() {
        let result = decode_and_verify("", SECRET);
        assert!(matches!(result, Err(InviteTokenError::InvalidEncoding)));
    }

    #[test]
    fn rejects_short_token() {
        use base64::Engine;
        let short = base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(&[0u8; 10]);
        let result = decode_and_verify(&short, SECRET);
        assert!(matches!(result, Err(InviteTokenError::InvalidEncoding)));
    }

    #[test]
    fn roundtrip_target_type_group() {
        let payload = InviteTokenPayload {
            v: 1,
            id: "grp-001".to_string(),
            exp: future_exp(),
            target_type: Some(InviteTargetType::Group),
        };
        let token = encode(&payload, SECRET);
        let decoded = decode_and_verify(&token, SECRET).unwrap();
        assert_eq!(decoded.id, "grp-001");
        assert_eq!(decoded.target_type, Some(InviteTargetType::Group));
    }

    #[test]
    fn roundtrip_target_type_session() {
        let payload = InviteTokenPayload {
            v: 1,
            id: "ses-001".to_string(),
            exp: future_exp(),
            target_type: Some(InviteTargetType::Session),
        };
        let token = encode(&payload, SECRET);
        let decoded = decode_and_verify(&token, SECRET).unwrap();
        assert_eq!(decoded.id, "ses-001");
        assert_eq!(decoded.target_type, Some(InviteTargetType::Session));
    }

    #[test]
    fn legacy_token_with_none_target_type_decodes_none() {
        let payload = InviteTokenPayload {
            v: 1,
            id: "grp-001".to_string(),
            exp: future_exp(),
            target_type: None,
        };
        let token = encode(&payload, SECRET);
        let decoded = decode_and_verify(&token, SECRET).unwrap();
        assert_eq!(decoded.target_type, None);
    }

    #[test]
    fn legacy_token_json_without_target_type_field_decodes_none() {
        // A token whose payload JSON predates the target_type field (no such
        // key at all) must still verify and decode to target_type: None. This
        // is the real backward-compat guard: it proves the HMAC payload bytes
        // for a legacy token are unchanged and serdedefaults the missing field.
        use base64::Engine;
        let legacy_json = format!(r#"{{"v":1,"id":"grp-001","exp":{}}}"#, future_exp());
        let payload_bytes = legacy_json.as_bytes();
        let mut mac = HmacSha256::new_from_slice(SECRET).expect("HMAC accepts any key length");
        mac.update(payload_bytes);
        let signature = mac.finalize().into_bytes();
        let mut combined = payload_bytes.to_vec();
        combined.extend_from_slice(&signature);
        let token = base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(&combined);

        let decoded = decode_and_verify(&token, SECRET).unwrap();
        assert_eq!(decoded.id, "grp-001");
        assert_eq!(decoded.v, 1);
        assert_eq!(decoded.target_type, None);
    }

    #[test]
    fn legacy_token_with_none_omits_target_type_key() {
        // Tokens minted with target_type: None must remain byte-identical to
        // pre-field tokens (no "target_type" key in the payload JSON), so the
        // HMAC and on-wire form are unchanged for legacy callers.
        let payload = InviteTokenPayload {
            v: 1,
            id: "grp-001".to_string(),
            exp: future_exp(),
            target_type: None,
        };
        let payload_bytes = serde_json::to_vec(&payload).expect("serializable");
        let json = String::from_utf8(payload_bytes).expect("utf8");
        assert!(
            !json.contains("target_type"),
            "legacy token must not emit target_type key, got: {}",
            json
        );
    }
}
