use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;

type HmacSha256 = Hmac<Sha256>;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RegisterTokenPayload {
    pub v: u8,
    pub id: String,
    pub exp: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum RegisterTokenError {
    #[error("invalid register token encoding")]
    InvalidEncoding,
    #[error("invalid register token signature")]
    InvalidSignature,
    #[error("register token has expired")]
    Expired,
    #[error("unsupported register token version")]
    UnsupportedVersion,
    #[error("malformed register token payload: {0}")]
    MalformedPayload(String),
    #[error("register token id is not a human token")]
    NotHumanToken,
}

const HMAC_LEN: usize = 32;
const CURRENT_VERSION: u8 = 1;

pub fn encode(payload: &RegisterTokenPayload, secret: &[u8]) -> String {
    let payload_bytes = serde_json::to_vec(payload).expect("RegisterTokenPayload is always serializable");
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
) -> Result<RegisterTokenPayload, RegisterTokenError> {
    use base64::Engine;
    let raw = base64::engine::general_purpose::URL_SAFE_NO_PAD
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

    let payload: RegisterTokenPayload = serde_json::from_slice(payload_bytes)
        .map_err(|e| RegisterTokenError::MalformedPayload(e.to_string()))?;

    if payload.v != CURRENT_VERSION {
        return Err(RegisterTokenError::UnsupportedVersion);
    }

    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    if payload.exp < now {
        return Err(RegisterTokenError::Expired);
    }

    if !payload.id.starts_with("human_") {
        return Err(RegisterTokenError::NotHumanToken);
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
        let payload = RegisterTokenPayload {
            v: 1,
            id: "human_user123".to_string(),
            exp: future_exp(),
        };
        let token = encode(&payload, SECRET);
        let decoded = decode_and_verify(&token, SECRET).unwrap();
        assert_eq!(decoded.id, "human_user123");
        assert_eq!(decoded.v, 1);
    }

    #[test]
    fn rejects_expired_token() {
        let payload = RegisterTokenPayload {
            v: 1,
            id: "human_user123".to_string(),
            exp: 1000,
        };
        let token = encode(&payload, SECRET);
        let result = decode_and_verify(&token, SECRET);
        assert!(matches!(result, Err(RegisterTokenError::Expired)));
    }

    #[test]
    fn rejects_wrong_secret() {
        let payload = RegisterTokenPayload {
            v: 1,
            id: "human_user123".to_string(),
            exp: future_exp(),
        };
        let token = encode(&payload, SECRET);
        let result = decode_and_verify(&token, b"wrong-secret");
        assert!(matches!(result, Err(RegisterTokenError::InvalidSignature)));
    }

    #[test]
    fn rejects_tampered_payload() {
        let payload = RegisterTokenPayload {
            v: 1,
            id: "human_user123".to_string(),
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
                Err(RegisterTokenError::InvalidSignature)
                    | Err(RegisterTokenError::InvalidEncoding)
                    | Err(RegisterTokenError::MalformedPayload(_))
            ),
        );
    }

    #[test]
    fn rejects_non_human_token() {
        let payload = RegisterTokenPayload {
            v: 1,
            id: "bot_agent007".to_string(),
            exp: future_exp(),
        };
        let token = encode(&payload, SECRET);
        let result = decode_and_verify(&token, SECRET);
        assert!(matches!(result, Err(RegisterTokenError::NotHumanToken)));
    }

    #[test]
    fn rejects_unsupported_version() {
        let payload = RegisterTokenPayload {
            v: 99,
            id: "human_user123".to_string(),
            exp: future_exp(),
        };
        let token = encode(&payload, SECRET);
        let result = decode_and_verify(&token, SECRET);
        assert!(matches!(result, Err(RegisterTokenError::UnsupportedVersion)));
    }
}
