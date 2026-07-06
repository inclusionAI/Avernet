use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

type HmacSha256 = Hmac<Sha256>;

/// JWT claims payload.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Claims {
    /// Subject — the authenticated identity (e.g. bot id or user id).
    pub sub: String,
    /// Source — how the identity was established (e.g. "google", "agentpass").
    pub src: String,
    /// Issued-at (unix seconds).
    pub iat: u64,
    /// Expiration (unix seconds).
    pub exp: u64,
}

impl Claims {
    /// Returns `true` when the token has passed the 50 % lifetime threshold
    /// and should be refreshed.
    ///
    /// Lifetime is derived from the token itself (`exp - iat`). Refresh when
    /// elapsed >= lifetime / 2.
    pub fn should_refresh(&self, now: u64) -> bool {
        let lifetime = self.exp.saturating_sub(self.iat);
        let elapsed = now.saturating_sub(self.iat);
        let threshold = lifetime / 2;
        elapsed >= threshold
    }
}

/// Errors produced by JWT operations.
#[derive(Debug, thiserror::Error)]
pub enum JwtError {
    #[error("invalid token: {0}")]
    InvalidToken(String),
    #[error("invalid signature")]
    InvalidSignature,
    #[error("token expired")]
    Expired,
    #[error("sign failed: {0}")]
    SignFailed(String),
}

/// Pure HS256 JWT sign/verify service.
pub struct JwtService {
    secret: Vec<u8>,
}

impl JwtService {
    /// Create a new service with the given HMAC secret.
    pub fn new(secret: &str) -> Self {
        Self {
            secret: secret.as_bytes().to_vec(),
        }
    }

    /// Sign the given claims and return a compact JWT string.
    pub fn sign(&self, claims: &Claims) -> Result<String, JwtError> {
        let header = r#"{"alg":"HS256","typ":"JWT"}"#;
        let header_b64 = URL_SAFE_NO_PAD.encode(header);
        let payload_json = serde_json::to_string(claims)
            .map_err(|e| JwtError::SignFailed(e.to_string()))?;
        let payload_b64 = URL_SAFE_NO_PAD.encode(payload_json);

        let signing_input = format!("{header_b64}.{payload_b64}");
        let sig = self.hmac_sign(signing_input.as_bytes())?;
        let sig_b64 = URL_SAFE_NO_PAD.encode(sig);

        Ok(format!("{signing_input}.{sig_b64}"))
    }

    /// Verify a token: check signature and expiration.
    pub fn verify(&self, token: &str) -> Result<Claims, JwtError> {
        let claims = self.verify_no_exp(token)?;
        let now = now_secs();
        if claims.exp <= now {
            return Err(JwtError::Expired);
        }
        Ok(claims)
    }

    /// Verify a token's signature only (skip expiration check).
    /// Useful for sliding-expiry scenarios where the caller decides
    /// whether to accept an expired token.
    pub fn verify_no_exp(&self, token: &str) -> Result<Claims, JwtError> {
        let parts: Vec<&str> = token.split('.').collect();
        if parts.len() != 3 {
            return Err(JwtError::InvalidToken("expected 3 parts".into()));
        }

        let header_b64 = parts[0];
        let payload_b64 = parts[1];
        let sig_b64 = parts[2];

        // Decode and validate header.
        let header_bytes = URL_SAFE_NO_PAD
            .decode(header_b64)
            .map_err(|e| JwtError::InvalidToken(format!("header decode: {e}")))?;
        let header: serde_json::Value = serde_json::from_slice(&header_bytes)
            .map_err(|e| JwtError::InvalidToken(format!("header parse: {e}")))?;
        if header.get("alg").and_then(|v| v.as_str()) != Some("HS256") {
            return Err(JwtError::InvalidToken("unsupported alg".into()));
        }

        // Verify signature.
        let signing_input = format!("{header_b64}.{payload_b64}");
        let provided_sig = URL_SAFE_NO_PAD
            .decode(sig_b64)
            .map_err(|e| JwtError::InvalidToken(format!("signature decode: {e}")))?;

        // Constant-time comparison via hmac.
        let mut mac = HmacSha256::new_from_slice(&self.secret)
            .map_err(|e| JwtError::SignFailed(e.to_string()))?;
        mac.update(signing_input.as_bytes());
        mac.verify_slice(&provided_sig)
            .map_err(|_| JwtError::InvalidSignature)?;

        // Decode claims.
        let payload_bytes = URL_SAFE_NO_PAD
            .decode(payload_b64)
            .map_err(|e| JwtError::InvalidToken(format!("payload decode: {e}")))?;
        let claims: Claims = serde_json::from_slice(&payload_bytes)
            .map_err(|e| JwtError::InvalidToken(format!("payload parse: {e}")))?;

        Ok(claims)
    }

    /// Compute HMAC-SHA256. Returns `Result` so callers never need to
    /// `unwrap` (workspace denies `unwrap_used`).
    fn hmac_sign(&self, data: &[u8]) -> Result<Vec<u8>, JwtError> {
        let mut mac = HmacSha256::new_from_slice(&self.secret)
            .map_err(|e| JwtError::SignFailed(e.to_string()))?;
        mac.update(data);
        Ok(mac.finalize().into_bytes().to_vec())
    }
}

/// Compute the storage fingerprint of a session JWT: lowercase hex
/// `SHA-256(jwt)`. The raw JWT is a bearer credential and must never be
/// persisted in clear; only this irreversible 64-char hex digest is stored and
/// compared, so a database leak cannot yield usable session tokens.
pub fn token_hash(jwt: &str) -> String {
    let digest = Sha256::digest(jwt.as_bytes());
    let mut out = String::with_capacity(64);
    for byte in digest {
        use std::fmt::Write as _;
        // Infallible write into a String.
        let _ = write!(out, "{byte:02x}");
    }
    out
}

/// Return current unix seconds.
fn now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}
