//! Strict OAuth session JWT claims and HS256 sign/verify service.
//!
//! This module is the V1 auth vertical slice's session-token primitive. It
//! pins a strict [`OAuthSessionClaims`] shape whose signed payload carries
//! the session's `revision`, `session_id`, and `env` — the fields the strict
//! [`AuthSessionRepoPort`](bcs_service_api::port::repo::auth_session::AuthSessionRepoPort)
//! CAS surface revolves around — so two tokens with the same `iat`/`exp` for
//! the same subject but at different revisions are observably different
//! tokens.
//!
//! # Reuse of the HMAC primitives, not the group-session shape
//!
//! The service reuses the same HS256 HMAC approach as the existing
//! [`crate::JwtService`] and [`crate::GroupSessionJwtService`], but pins its
//! own claim type. The group-session JWT (whose `sub`/`uid`/`gid`/`sid`/
//! `tenant` shape serves the WS auth path) is intentionally NOT modified;
//! OAuth session tokens have different audience/purpose semantics and a
//! different lifecycle (rotate/revoke), so they live in their own type with
//! their own wire shape.
//!
//! # Claim completeness — no `Optional` fallbacks
//!
//! `OAuthSessionClaims` requires `sub`/`src`/`env`/`session_id`/`revision`/
//! `iat`/`exp` to be present in the payload; only `name` is `Option`. The
//! deserializer is `deny_unknown_fields`, so a legacy-style token missing
//! `env`/`session_id`/`revision` is rejected — both by absence (required
//! fields) and by the absence of any `#[serde(default)]` Optional fallback.
//! This matches the plan's "no Optional fallbacks like legacy `Claims` had"
//! requirement: legacy `claude`-style tokens cannot be verified as OAuth
//! session tokens by accident.
//!
//! # `verify_at` vs `verify_for_revoke`
//!
//! [`OAuthSessionJwt::verify_at`] enforces signature, claim completeness,
//! and `exp <= now`. [`OAuthSessionJwt::verify_for_revoke`] enforces
//! signature and claim completeness but skips ONLY the `exp` check: a
//! revocation endpoint must accept a token whose natural expiry has passed
//! (a user can log out after `exp`), but must never accept a tampered
//! signature or a token missing `session_id`/`revision`/`env` (those are
//! load-bearing for the revoke-by-`session_id` lookup).
//!
//! # `revision` is part of the signed payload
//!
//! `revision` is a required field on the wire and is covered by the HMAC.
//! The signed-payload membership is what makes "same `iat`/`exp`, different
//! `revision`" produce a different token; the harness asserts this directly.

use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;

use crate::JwtError;

type HmacSha256 = Hmac<Sha256>;

/// JWT header used by [`OAuthSessionJwt`]. The fixed wire shape
/// `{"alg":"HS256","typ":"JWT"}` matches the existing JWT services; the
/// deserializer rejects differently-shaped headers so legacy alg=none
/// confusion attacks cannot reach the verifier.
#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct JwtHeader {
    alg: String,
    typ: String,
}

/// Strict OAuth session JWT claims.
///
/// Field names on the wire: `sub`, `src`, `env`, `session_id`, `revision`,
/// `iat`, `exp`, and optionally `name`. These names are part of the signed
/// payload and MUST remain stable: any change to a field's serialized
/// representation produces a different signature or breaks verification.
///
/// All non-`Option` fields are required. A legacy token missing
/// `env`/`session_id`/`revision` is rejected by the verifier — there is no
/// `#[serde(default)]` fallback to `""` or `0` for these fields.
///
/// `revision` is covered by the HMAC, so two tokens sharing every other
/// field but at different revisions are observably different tokens. The
/// session-rotation flow uses this to distinguish pre- and post-rotate
/// tokens signed within the same second.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct OAuthSessionClaims {
    /// Subject — the internal BCS user id allocated by `ensure_identity`.
    pub sub: String,
    /// Source — the OAuth provider name (e.g. `github`, `google`, `alipay`).
    pub src: String,
    /// Environment partition (the row's `env`). Part of the trio
    /// `(sub, src, env)` that selects a session row.
    pub env: String,
    /// Session id — the live `AuthSessionVersion::session_id` for this token.
    /// Used by the revoke path to look up the session row by `session_id`.
    pub session_id: String,
    /// Revision — the live `AuthSessionVersion::revision` for this token.
    /// Part of the signed payload so rotate produces a different token even
    /// when `iat`/`exp` happen to match.
    pub revision: u64,
    /// Issued-at (unix seconds).
    pub iat: u64,
    /// Expiration (unix seconds). The `verify_at` path treats `exp <= now`
    /// as expired; `verify_for_revoke` skips this check.
    pub exp: u64,
    /// Optional display-name snapshot. Omitted from the payload when `None`
    /// (via `skip_serializing_if`); on decode, a missing `name` field maps to
    /// `None`. Carries no security semantics.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
}

/// HS256 sign/verify service for [`OAuthSessionClaims`].
///
/// Reuses the HMAC primitives of the existing JWT services but pins its own
/// claim shape, so the V1 auth vertical slice can issue and verify session
/// tokens without coupling to the group-session JWT shape. The secret is the
/// only state; sign and verify are pure functions over the secret and the
/// header/claims/signature.
pub struct OAuthSessionJwt {
    secret: Vec<u8>,
}

impl OAuthSessionJwt {
    /// Construct the service with an HMAC secret. The caller is responsible
    /// for sourcing real signing material (matching the existing
    /// [`crate::JwtService`] convention; the group-session JWT enforces
    /// non-empty separately, but this service leaves that to the wiring).
    pub fn new(secret: &str) -> Self {
        Self {
            secret: secret.as_bytes().to_vec(),
        }
    }

    /// Sign `claims` and return the compact JWT string
    /// `<header_b64>.<claims_b64>.<sig_b64>` (URL-safe base64, no padding).
    pub fn sign(&self, claims: &OAuthSessionClaims) -> Result<String, JwtError> {
        let header = JwtHeader {
            alg: "HS256".to_string(),
            typ: "JWT".to_string(),
        };
        let header_json = serde_json::to_vec(&header)
            .map_err(|e| JwtError::SignFailed(e.to_string()))?;
        let claims_json = serde_json::to_vec(claims)
            .map_err(|e| JwtError::SignFailed(e.to_string()))?;
        let header_b64 = URL_SAFE_NO_PAD.encode(header_json);
        let claims_b64 = URL_SAFE_NO_PAD.encode(claims_json);
        let signing_input = format!("{header_b64}.{claims_b64}");
        let sig = self.hmac_sign(signing_input.as_bytes())?;
        Ok(format!(
            "{signing_input}.{}",
            URL_SAFE_NO_PAD.encode(sig)
        ))
    }

    /// Verify a token at instant `now`: check signature, claim completeness,
    /// and `exp <= now`. Returns the decoded claims on success.
    ///
    /// `now` is the caller's clock. A token with `exp <= now` returns
    /// [`JwtError::Expired`]; use [`OAuthSessionJwt::verify_for_revoke`] to
    /// inspect an expired token during the revoke flow.
    pub fn verify_at(
        &self,
        token: &str,
        now: u64,
    ) -> Result<OAuthSessionClaims, JwtError> {
        let claims = self.verify_no_exp(token)?;
        if claims.exp <= now {
            return Err(JwtError::Expired);
        }
        Ok(claims)
    }

    /// Verify a token's signature and claim completeness but skip the `exp`
    /// check. Used by the revocation endpoint, which must accept an
    /// already-expired token (a user may log out after `exp`) but must still
    /// reject a tampered signature or a token missing `session_id` /
    /// `revision` / `env` — those are load-bearing for the
    /// revoke-by-`session_id` lookup.
    pub fn verify_for_revoke(&self, token: &str) -> Result<OAuthSessionClaims, JwtError> {
        self.verify_no_exp(token)
    }

    /// Verify signature and claim completeness (no `exp` check). The internal
    /// shared body of [`Self::verify_at`] and [`Self::verify_for_revoke`].
    fn verify_no_exp(&self, token: &str) -> Result<OAuthSessionClaims, JwtError> {
        let parts: Vec<&str> = token.split('.').collect();
        if parts.len() != 3 {
            return Err(JwtError::InvalidToken("expected 3 parts".into()));
        }
        let header_b64 = parts[0];
        let claims_b64 = parts[1];
        let sig_b64 = parts[2];
        if header_b64.is_empty() || claims_b64.is_empty() || sig_b64.is_empty() {
            return Err(JwtError::InvalidToken("empty segment".into()));
        }

        // Decode and validate header. The fixed alg/typ blocks alg=none
        // confusion attacks and any non-HS256 token routing through this
        // verifier.
        let header_bytes = URL_SAFE_NO_PAD
            .decode(header_b64)
            .map_err(|e| JwtError::InvalidToken(format!("header decode: {e}")))?;
        let header: JwtHeader = serde_json::from_slice(&header_bytes)
            .map_err(|e| JwtError::InvalidToken(format!("header parse: {e}")))?;
        if header.alg != "HS256" || header.typ != "JWT" {
            return Err(JwtError::InvalidToken("unsupported alg/typ".into()));
        }

        // Verify the signature with a constant-time HMAC comparison.
        let signing_input = format!("{header_b64}.{claims_b64}");
        let provided_sig = URL_SAFE_NO_PAD
            .decode(sig_b64)
            .map_err(|e| JwtError::InvalidToken(format!("signature decode: {e}")))?;
        let mut mac = HmacSha256::new_from_slice(&self.secret)
            .map_err(|e| JwtError::SignFailed(e.to_string()))?;
        mac.update(signing_input.as_bytes());
        mac.verify_slice(&provided_sig)
            .map_err(|_| JwtError::InvalidSignature)?;

        // Decode claims. `deny_unknown_fields` + required non-Option fields
        // mean legacy tokens missing `env`/`session_id`/`revision` are
        // rejected here, with no Optional fallback to the legacy shape.
        let claims_bytes = URL_SAFE_NO_PAD
            .decode(claims_b64)
            .map_err(|e| JwtError::InvalidToken(format!("payload decode: {e}")))?;
        let claims: OAuthSessionClaims = serde_json::from_slice(&claims_bytes)
            .map_err(|e| JwtError::InvalidToken(format!("payload parse: {e}")))?;

        Ok(claims)
    }

    /// Compute the HS256 HMAC over `data` and return the raw digest bytes.
    fn hmac_sign(&self, data: &[u8]) -> Result<Vec<u8>, JwtError> {
        let mut mac = HmacSha256::new_from_slice(&self.secret)
            .map_err(|e| JwtError::SignFailed(e.to_string()))?;
        mac.update(data);
        Ok(mac.finalize().into_bytes().to_vec())
    }
}

#[cfg(test)]
mod tests {
    //! Unit tests for in-crate sanity. The full RED→GREEN contract test lives
    //! in `tests/oauth_session.rs` per the TDD plan.

    #![allow(
        clippy::expect_used,
        clippy::unwrap_used,
        reason = "test assertions intentionally fail fast"
    )]

    use super::*;

    fn sample_claims() -> OAuthSessionClaims {
        OAuthSessionClaims {
            sub: "u".into(),
            src: "github".into(),
            env: "local".into(),
            session_id: "sid-a".into(),
            revision: 1,
            iat: 100,
            exp: 200,
            name: None,
        }
    }

    #[test]
    fn roundtrip_with_no_exp_inline() {
        let jwt = OAuthSessionJwt::new("k");
        let claims = sample_claims();
        let token = jwt.sign(&claims).expect("sign");
        let decoded = jwt.verify_no_exp(&token).expect("verify");
        assert_eq!(decoded, claims);
    }

    #[test]
    fn empty_segments_rejected() {
        let jwt = OAuthSessionJwt::new("k");
        assert!(matches!(
            jwt.verify_no_exp(".."),
            Err(JwtError::InvalidToken(_))
        ));
    }
}
