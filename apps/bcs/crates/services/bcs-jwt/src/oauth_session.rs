//! Strict HS256 session-instance JWT for the V1 API OAuth plugin chain.
//!
//! Unlike the legacy `Claims` (where only `sub`/`src`/`iat`/`exp` are
//! required), the V1 session JWT pins the exact session instance inside the
//! token: `env` participates in scope addressing and `session_id`/`revision`
//! identify the atomic session version the token was signed for. All three
//! are REQUIRED claims — a legacy token missing them is rejected by this
//! verifier, and a session id or revision change under identical `iat`/`exp`
//! produces a different token.
//!
//! Redaction (plan §8.4 凭据日志红线): `OAuthSessionClaims` carries bearer
//! material, so its `Debug` is hand-written and prints only the type name
//! and an elided-fields marker — never a claim value. `OAuthSessionJwt`
//! does not derive `Debug` (the signing secret must not leak), and errors
//! carry only fixed stable-category text, never claim or key material.
//!
//! Storage conformance (Task 5 forward constraint): the token fingerprint
//! persisted via [`crate::token_hash`] is lowercase-hex SHA-256 (exactly 64
//! chars — a MySQL `VARCHAR(64)` holds it with zero headroom), and
//! `session_id` values must stay within 64 chars (e.g. 128-bit random hex
//! or a dash-less UUID). This type does not assume shorter or longer bounds.

use std::fmt;

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use serde::{Deserialize, Serialize};

use crate::{hs256_sign, hs256_verify, JwtError};

/// Fixed HS256 header; the strict verifier accepts no other `alg`.
const STRICT_HEADER: &str = r#"{"alg":"HS256","typ":"JWT"}"#;

/// Claims of one V1 OAuth session instance. Required fields are never
/// Optional: a token missing `env`, `session_id`, or `revision` fails
/// strict verification instead of decoding to a degraded value.
#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OAuthSessionClaims {
    /// Subject — the internal BCS `user_id` of the verified identity.
    pub sub: String,
    /// Source — the OAuth provider that established the identity.
    pub src: String,
    /// Environment — part of the session scope address; the same
    /// user/provider pair has independent sessions per env.
    pub env: String,
    /// Session id — identifies the atomic session instance. Values must
    /// fit 64 chars (storage `VARCHAR(64)`); a dash-less UUID fits.
    pub session_id: String,
    /// CAS revision of the session version this token was signed for.
    /// Revocation and rotation bump it, so a superseded token no longer
    /// names the current revision.
    pub revision: u64,
    /// Issued-at (unix seconds).
    pub iat: u64,
    /// Expiration (unix seconds). Enforced inclusively: `exp <= now`
    /// rejects (see [`OAuthSessionJwt::verify_at`]).
    pub exp: u64,
    /// Optional display-name snapshot. Omitted from the payload when
    /// `None`; present-day verification joins the live name from the
    /// identity row instead of trusting this claim.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
}

impl fmt::Debug for OAuthSessionClaims {
    /// Hand-written redacted `Debug` (plan §8.4): the type name and an
    /// elided-fields marker only — no claim value (no `sub`, no
    /// `session_id`, no `name`) and no signing material.
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("OAuthSessionClaims").finish_non_exhaustive()
    }
}

/// Pure sign/verify service for V1 OAuth session-instance JWTs.
///
/// No `Debug` derive: the struct holds the HMAC signing secret. Errors
/// carry only fixed stable categories (`JwtError`), never claim values.
pub struct OAuthSessionJwt {
    secret: Vec<u8>,
}

impl OAuthSessionJwt {
    /// Create a new service with the given HMAC secret.
    pub fn new(secret: &str) -> Self {
        Self {
            secret: secret.as_bytes().to_vec(),
        }
    }

    /// Sign the session claims and return a compact JWT string. The fixed
    /// `HS256` header is used for every token this service issues.
    pub fn sign(&self, claims: &OAuthSessionClaims) -> Result<String, JwtError> {
        let header_b64 = URL_SAFE_NO_PAD.encode(STRICT_HEADER);
        let payload_json =
            serde_json::to_string(claims).map_err(|_| JwtError::SignFailed("claims encoding failed".into()))?;
        let payload_b64 = URL_SAFE_NO_PAD.encode(payload_json);

        let signing_input = format!("{header_b64}.{payload_b64}");
        let sig = hs256_sign(&self.secret, signing_input.as_bytes())?;
        Ok(format!("{signing_input}.{}", URL_SAFE_NO_PAD.encode(sig)))
    }

    /// Verify a token at a caller-supplied instant: signature, claim
    /// integrity, AND `exp` (`exp <= now` rejects; `now` is injected so
    /// tests and stores never depend on wall clock).
    pub fn verify_at(&self, token: &str, now: u64) -> Result<OAuthSessionClaims, JwtError> {
        let claims = self.verify_integrity(token)?;
        if claims.exp <= now {
            return Err(JwtError::Expired);
        }
        Ok(claims)
    }

    /// Verify a token for the revoke/logout path. Skips ONLY `exp` — a
    /// stale browser may log out an already-expired session — while the
    /// signature and claim integrity (including the required
    /// `env`/`session_id`/`revision` claims) are still enforced, so a
    /// forged or legacy token is never accepted as revocable.
    pub fn verify_for_revoke(&self, token: &str) -> Result<OAuthSessionClaims, JwtError> {
        self.verify_integrity(token)
    }

    /// Shared strict pipeline: three-part token, HS256 header, constant-time
    /// signature, then decode into the strict claims type. Missing required
    /// claims (legacy tokens) fail payload decoding with the fixed
    /// `InvalidToken` category.
    fn verify_integrity(&self, token: &str) -> Result<OAuthSessionClaims, JwtError> {
        let mut parts = token.split('.');
        let header_b64 = parts.next().ok_or(JwtError::InvalidToken("expected 3 parts".into()))?;
        let payload_b64 = parts.next().ok_or(JwtError::InvalidToken("expected 3 parts".into()))?;
        let sig_b64 = parts.next().ok_or(JwtError::InvalidToken("expected 3 parts".into()))?;
        if parts.next().is_some() {
            return Err(JwtError::InvalidToken("expected 3 parts".into()));
        }

        let header_bytes = URL_SAFE_NO_PAD
            .decode(header_b64)
            .map_err(|_| JwtError::InvalidToken("header decode".into()))?;
        let header: serde_json::Value = serde_json::from_slice(&header_bytes)
            .map_err(|_| JwtError::InvalidToken("header parse".into()))?;
        if header.get("alg").and_then(|v| v.as_str()) != Some("HS256") {
            return Err(JwtError::InvalidToken("unsupported alg".into()));
        }

        let signature = URL_SAFE_NO_PAD
            .decode(sig_b64)
            .map_err(|_| JwtError::InvalidToken("signature decode".into()))?;
        let signing_input = format!("{header_b64}.{payload_b64}");
        hs256_verify(&self.secret, signing_input.as_bytes(), &signature)?;

        let payload_bytes = URL_SAFE_NO_PAD
            .decode(payload_b64)
            .map_err(|_| JwtError::InvalidToken("payload decode".into()))?;
        let claims: OAuthSessionClaims = serde_json::from_slice(&payload_bytes)
            .map_err(|_| JwtError::InvalidToken("invalid session claims".into()))?;
        Ok(claims)
    }
}
