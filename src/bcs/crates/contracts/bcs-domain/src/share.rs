use base64::Engine;
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;

const HMAC_LEN: usize = 32;
const CURRENT_VERSION: u8 = 1;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ShareTokenPayload {
    pub v: u8,
    pub file_id: String,
    pub exp: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum ShareTokenError {
    #[error("invalid share token encoding")]
    InvalidEncoding,
    #[error("invalid share token signature")]
    InvalidSignature,
    #[error("share link has expired")]
    Expired,
    #[error("unsupported share token version")]
    UnsupportedVersion,
    #[error("malformed share token payload: {0}")]
    MalformedPayload(String),
}

type HmacSha256 = Hmac<Sha256>;

pub fn share_token_encode(payload: &ShareTokenPayload, secret: &[u8]) -> String {
    let payload_bytes =
        serde_json::to_vec(payload).expect("ShareTokenPayload is always serializable");
    let mut mac = HmacSha256::new_from_slice(secret).expect("HMAC accepts any key length");
    mac.update(&payload_bytes);
    let signature = mac.finalize().into_bytes();

    let mut combined = payload_bytes;
    combined.extend_from_slice(&signature);
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(&combined)
}

pub fn share_token_decode_and_verify(
    token: &str,
    secret: &[u8],
) -> Result<ShareTokenPayload, ShareTokenError> {
    let payload = share_token_decode_and_verify_no_expiry(token, secret)?;
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    if payload.exp < now {
        return Err(ShareTokenError::Expired);
    }
    Ok(payload)
}

fn share_token_decode_and_verify_no_expiry(
    token: &str,
    secret: &[u8],
) -> Result<ShareTokenPayload, ShareTokenError> {
    use base64::Engine;
    let combined = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(token)
        .map_err(|_| ShareTokenError::InvalidEncoding)?;
    if combined.len() < HMAC_LEN {
        return Err(ShareTokenError::InvalidEncoding);
    }
    let (payload_bytes, signature) = combined.split_at(combined.len() - HMAC_LEN);

    let mut mac = HmacSha256::new_from_slice(secret).expect("HMAC accepts any key length");
    mac.update(payload_bytes);
    mac.verify_slice(signature)
        .map_err(|_| ShareTokenError::InvalidSignature)?;

    let payload: ShareTokenPayload =
        serde_json::from_slice(payload_bytes).map_err(|e| ShareTokenError::MalformedPayload(e.to_string()))?;
    if payload.v != CURRENT_VERSION {
        return Err(ShareTokenError::UnsupportedVersion);
    }
    Ok(payload)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn future_exp() -> u64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs()
            + 3600
    }

    #[test]
    fn roundtrip_preserves_payload() {
        let secret = b"share-secret-0123456789abcdef";
        let p = ShareTokenPayload { v: 1, file_id: "01HZXABCDEFGHJKMNPQRSTVWXY".to_string(), exp: future_exp() };
        let token = share_token_encode(&p, secret);
        let decoded = share_token_decode_and_verify(&token, secret).unwrap();
        assert_eq!(decoded, p);
    }

    #[test]
    fn wrong_secret_rejected() {
        let p = ShareTokenPayload { v: 1, file_id: "01HZX".to_string(), exp: future_exp() };
        let token = share_token_encode(&p, b"secret-a");
        assert_eq!(
            share_token_decode_and_verify(&token, b"secret-b"),
            Err(ShareTokenError::InvalidSignature)
        );
    }

    #[test]
    fn expired_rejected() {
        let p = ShareTokenPayload { v: 1, file_id: "01HZX".to_string(), exp: 1 };
        let token = share_token_encode(&p, b"secret");
        assert_eq!(
            share_token_decode_and_verify(&token, b"secret"),
            Err(ShareTokenError::Expired)
        );
    }

    #[test]
    fn unsupported_version_rejected() {
        let p = ShareTokenPayload { v: 99, file_id: "01HZX".to_string(), exp: future_exp() };
        let token = share_token_encode(&p, b"secret");
        assert_eq!(
            share_token_decode_and_verify(&token, b"secret"),
            Err(ShareTokenError::UnsupportedVersion)
        );
    }

    #[test]
    fn malformed_encoding_rejected() {
        assert_eq!(
            share_token_decode_and_verify("!!!not-base64!!!", b"secret"),
            Err(ShareTokenError::InvalidEncoding)
        );
    }
}