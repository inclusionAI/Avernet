//! Outbound half of the gateway principal wire contract: re-addressing a
//! verified inbound token when BCN has to hand the **backend** a credential.
//!
//! The backend's ordinary-HTTP seam trusts only `gateway`- and `bcs`-issued
//! principals (`ORDINARY_HTTP_ISSUERS` in
//! `agentclaw/community/utils/gateway_principal_config.py`), but the friend
//! work-order approval callback calls BCN with a principal **the backend
//! re-addressed itself** (`iss=backend`). Echoing that credential back — which
//! is what the friend-auth-sync port otherwise does verbatim — answers 401,
//! so BCN re-signs what it verified before forwarding: every claim (the
//! approver's identities and anything else the gateway asserted) rides along
//! untouched, while `iss`/`aud`/`iat`/`exp`/`kid` name the token BCN just
//! minted. That is the same envelope `bcs-human-notify-work-order` puts on a
//! mentioned user's principal, and the mirror of the backend's own re-address
//! seam (`core/gateway_principal/signer.py`).

use std::fmt;

use jsonwebtoken::{Algorithm, DecodingKey, EncodingKey, Header, Validation, decode, encode};
use serde_json::Value;
use time::OffsetDateTime;

use super::verifier::{
    GatewayPrincipalTokenVerifier, GatewayPrincipalTrust, GatewayPrincipalVerificationError,
    GatewayPrincipalVerifierBuildError,
};

/// `iss` of a BCN-minted principal — `_BCN_COMPONENT_NAME` on the backend.
pub(super) const ADDRESSED_ISSUER: &str = "bcs";

/// `aud` of a principal addressed to the backend — `_AUDIENCE` there.
pub(super) const ADDRESSED_AUDIENCE: &str = "backend";

/// JOSE `kid` naming the shared bare signing key — `_BCN_KEY_ID` on the
/// backend and `PRINCIPAL_KEY_ID` in `bcs-human-notify-work-order`.
const ADDRESSED_KEY_ID: &str = "bare";

/// Short-lived by construction, mirroring the notify plugin's
/// `PRINCIPAL_TTL_SECS`.
const ADDRESSED_TTL_SECS: u64 = 60;

#[derive(Debug, thiserror::Error)]
pub enum GatewayPrincipalReAddressError {
    /// The inbound token failed the admission gate — bad signature, issuer,
    /// audience, key id, time window, or claim schema. A credential BCN
    /// cannot verify is not one it should re-assert.
    #[error("principal token failed the admission gate: {0}")]
    Verification(#[from] GatewayPrincipalVerificationError),
    /// Verified, but names no end user. The endpoint this token is destined
    /// for requires a verified user principal and answers 401 to anything
    /// else, so there is nothing worth signing.
    #[error("principal token names no end user")]
    NoUserPrincipal,
    /// The admitted payload could not be re-read (or signed) as JSON claims.
    #[error("principal token could not be re-signed: {0}")]
    Signing(String),
}

/// Re-addresses verified inbound principals as BCN-minted tokens.
///
/// Admission is the existing [`GatewayPrincipalTokenVerifier`] with the same
/// trust an openapi route was admitted under; only after a token passes the
/// full gate (signature, algorithm, type, key id, issuer, audience, time
/// window, claim schema, principal projection) is the payload re-read and the
/// envelope rewritten — never before, so an unverified header value can never
/// be turned into a BCN-signed credential.
pub struct GatewayPrincipalReSigner {
    signing_key: Vec<u8>,
    trust: GatewayPrincipalTrust,
}

impl GatewayPrincipalReSigner {
    pub fn new(
        signing_key: &[u8],
        trust: GatewayPrincipalTrust,
    ) -> Result<Self, GatewayPrincipalVerifierBuildError> {
        // Refuse empty keys exactly as the verifier does: a token signed with
        // nothing is not a lesser credential but a forgeable one.
        if signing_key.is_empty() {
            return Err(GatewayPrincipalVerifierBuildError::EmptySigningKey);
        }
        Ok(Self {
            signing_key: signing_key.to_vec(),
            trust,
        })
    }

    /// Return `token`'s claims re-addressed to the backend, as a `bcs`-issued
    /// principal addressed to `aud=backend`.
    ///
    /// Identities and any other claims the gateway asserted are carried
    /// forward untouched; only the envelope (`iss`/`aud`/`iat`/`exp`/`kid`)
    /// changes, so the backend sees the same caller its callback vouched
    /// for, authorized under an issuer it trusts.
    pub fn readdress(&self, token: &str) -> Result<String, GatewayPrincipalReAddressError> {
        let verifier = GatewayPrincipalTokenVerifier::new(&self.signing_key, self.trust.clone())
            .map_err(|error| {
                GatewayPrincipalReAddressError::Signing(error.to_string())
            })?;
        let caller = verifier.verify(token)?;
        if caller.user.is_none() {
            return Err(GatewayPrincipalReAddressError::NoUserPrincipal);
        }

        // The gate above authenticated this payload; re-read it to carry every
        // claim forward. Signature is checked again (cheap, same key); the
        // time window, audience, and issuer already passed admission.
        let mut validation = Validation::new(Algorithm::HS256);
        validation.validate_exp = false;
        validation.validate_aud = false;
        let data = decode::<Value>(
            token,
            &DecodingKey::from_secret(&self.signing_key),
            &validation,
        )
        .map_err(|error| {
            GatewayPrincipalReAddressError::Signing(format!("payload re-read failed: {error}"))
        })?;
        let mut claims = data.claims;
        let now = u64::try_from(OffsetDateTime::now_utc().unix_timestamp()).map_err(|_| {
            GatewayPrincipalReAddressError::Signing("clock is before the unix epoch".into())
        })?;
        let Some(object) = claims.as_object_mut() else {
            return Err(GatewayPrincipalReAddressError::Signing(
                "claims are not a JSON object".into(),
            ));
        };
        object.insert("iss".into(), Value::String(ADDRESSED_ISSUER.into()));
        object.insert("aud".into(), Value::String(ADDRESSED_AUDIENCE.into()));
        object.insert("iat".into(), Value::from(now));
        object.insert("exp".into(), Value::from(now + ADDRESSED_TTL_SECS));

        let mut header = Header::new(Algorithm::HS256);
        header.kid = Some(ADDRESSED_KEY_ID.to_string());
        encode(
            &header,
            &claims,
            &EncodingKey::from_secret(&self.signing_key),
        )
        .map_err(|error| {
            GatewayPrincipalReAddressError::Signing(format!("jwt encode failed: {error}"))
        })
    }
}

impl Clone for GatewayPrincipalReSigner {
    fn clone(&self) -> Self {
        Self {
            signing_key: self.signing_key.clone(),
            trust: self.trust.clone(),
        }
    }
}

impl fmt::Debug for GatewayPrincipalReSigner {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        // Never render the HMAC material.
        f.debug_struct("GatewayPrincipalReSigner")
            .field("signing_key", &"<redacted>")
            .finish_non_exhaustive()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const SIGNING_KEY: &[u8] = b"resigner-unit-test-signing-key-0123456";

    fn trust() -> GatewayPrincipalTrust {
        GatewayPrincipalTrust::new(
            vec!["gateway".to_string(), "backend".to_string()],
            "bcs",
            "bare",
        )
        .unwrap()
    }

    fn mint(key: &[u8], iss: &str, principal: Value, iat: u64, exp: u64) -> String {
        let mut header = Header::new(Algorithm::HS256);
        header.kid = Some("bare".to_string());
        encode(
            &header,
            &json!({
                "iss": iss, "aud": "bcs", "iat": iat, "exp": exp,
                "principals": principal,
                "tenant": {"id": "avernet"},  // unrelated claim rides along
            }),
            &EncodingKey::from_secret(key),
        )
        .unwrap()
    }

    fn user() -> Value {
        json!([{
            "type": "user",
            "subject": {"id": "152819", "username": "approver"}
        }])
    }

    fn now() -> u64 {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs()
    }

    #[test]
    fn readdress_keeps_unrelated_claims_and_sets_the_envelope() {
        let resigner = GatewayPrincipalReSigner::new(SIGNING_KEY, trust()).unwrap();
        let inbound = mint(SIGNING_KEY, "backend", user(), now(), now() + 3600);
        let adjusted = resigner.readdress(&inbound).unwrap();

        let mut validation = Validation::new(Algorithm::HS256);
        validation.set_issuer(&["bcs"]);
        validation.set_audience(&["backend"]);
        let data = decode::<Value>(
            &adjusted,
            &DecodingKey::from_secret(SIGNING_KEY),
            &validation,
        )
        .unwrap();
        assert_eq!(data.header.kid.as_deref(), Some("bare"));
        assert_eq!(data.claims["iss"], "bcs");
        assert_eq!(data.claims["aud"], "backend");
        assert_eq!(data.claims["principals"], user());
        assert_eq!(data.claims["tenant"], json!({"id": "avernet"}));
        assert_eq!(data.claims["exp"].as_u64().unwrap() - data.claims["iat"].as_u64().unwrap(), 60);
    }

    #[test]
    fn readdress_fails_closed_on_forged_and_userless_tokens() {
        let resigner = GatewayPrincipalReSigner::new(SIGNING_KEY, trust()).unwrap();

        let forged = mint(b"another-entirely-different-signing-key", "backend", user(), now(), now() + 3600);
        assert!(matches!(
            resigner.readdress(&forged),
            Err(GatewayPrincipalReAddressError::Verification(_))
        ));

        let userless = mint(
            SIGNING_KEY,
            "backend",
            json!([{
                "type": "app",
                "tenant": "avernet",
                "app": {
                    "app_id": 7, "app_name": "work-orders", "owners": "152819",
                    "tenant": "avernet", "app_type": "internal"
                }
            }]),
            now(),
            now() + 3600,
        );
        assert!(matches!(
            resigner.readdress(&userless),
            Err(GatewayPrincipalReAddressError::NoUserPrincipal)
        ));
    }
}