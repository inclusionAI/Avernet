use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;

type HmacSha256 = Hmac<Sha256>;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InviteTokenPayload {
    pub v: u8,
    pub id: String,
    pub exp: u64,
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
}
