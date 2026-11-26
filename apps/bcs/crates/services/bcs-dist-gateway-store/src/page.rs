//! Signed page cursors of the Gateway's list surfaces (plan T8).
//!
//! Every cursor is a server-signed (HMAC-SHA256) token: `2.<hex(payload)>.<hex(mac)>`.
//! The MAC covers a canonical JSON claim set that binds the reading subject
//! (tenant + principal), the surface scope (session id, or group + filter +
//! sort + the materialized cut identity), the last key of the previous page
//! and the expiry. A client can neither forge a continuation nor move one
//! subject's cursor into another's hands; an expired or released cut is
//! rejected explicitly, never answered from live state.
//!
//! The signing key is deployment configuration (`GatewayStoreConfig`):
//! tokens stay valid across gateway restarts and replicas only when the same
//! key is shared; an unset key fails closed.

use bcs_dist_model::PageToken;
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_store_api::zset::ZSetEntry;
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;

type HmacSha256 = Hmac<Sha256>;

/// Token format tag of the signed cursor protocol. The unsigned `1.` format
/// is gone: a v1 tail decodes as garbage and is refused.
const FORMAT: &str = "2.";

/// Cursor lifetime of both list surfaces, in milliseconds: the candidate v1
/// profile, one bounded window for any client to finish a query.
pub const CURSOR_LIFETIME_MILLIS: u64 =
    bcs_service_api::port::repo::distributed::group_round::RoundBudgetProfile::v1()
        .cursor_lifetime_millis;

/// The fixed sort contract of the group public index rows.
pub const GROUP_SORT: &str = "status_updated_at:desc,tid:asc";

/// Claims of one continuation cursor. `surface` selects the reading surface
/// ("list" = the per-session task index; "group" = the group public index)
/// and with it the meaning of `binding`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub(crate) struct CursorClaims {
    pub surface: String,
    pub tenant: String,
    pub principal: String,
    /// The session id (`list`) or the group id (`group`).
    pub binding: String,
    /// Comma-joined lowercase status filter names; empty = no filter.
    pub statuses: String,
    /// Sort contract of the surface; a changed contract invalidates tokens.
    pub sort: String,
    /// Identity of the materialized snapshot the cursor pages through; the
    /// per-session index has no snapshot, so its value is "live".
    pub cut: String,
    pub last_score: i64,
    pub last_member: String,
    pub expires_at_ms: i64,
}

fn invalid(code: &str) -> StoreError {
    StoreError::Invalid {
        code: code.to_owned(),
    }
}

fn sign(payload: &[u8], secret: &[u8]) -> Result<Vec<u8>, StoreError> {
    let mut mac =
        <HmacSha256 as Mac>::new_from_slice(secret).map_err(|_| invalid("cursor_secret"))?;
    mac.update(payload);
    Ok(mac.finalize().into_bytes().to_vec())
}

/// Deep checks a claim set for the surface it names: subject, binding and
/// expiry. `statuses` names the real expected filter key the caller derived
/// (`Some`, the per-session list surface); the group surface pages through a
/// materialized snapshot, so its filter binding is checked against that
/// snapshot instead (`None` here). `now` comes from the caller's clock.
pub(crate) fn verify(
    claims: &CursorClaims,
    surface: &str,
    tenant: &str,
    principal: &str,
    binding: &str,
    statuses: Option<&str>,
    sort: &str,
    now: i64,
) -> Result<(), StoreError> {
    if claims.surface != surface || claims.binding != binding {
        return Err(invalid("cursor_binding"));
    }
    // The reading subject is its own refusal: a cursor only continues in the
    // hands of the subject it was issued to.
    if claims.tenant != tenant || claims.principal != principal {
        return Err(invalid("cursor_subject_mismatch"));
    }
    if claims.sort != sort {
        return Err(invalid("cursor_binding"));
    }
    if let Some(expected) = statuses
        && claims.statuses != expected
    {
        return Err(invalid("cursor_binding"));
    }
    if claims.last_member.is_empty() || claims.last_member.len() > 256 {
        return Err(invalid("cursor_binding"));
    }
    if now >= claims.expires_at_ms {
        return Err(invalid("cursor_expired"));
    }
    Ok(())
}

/// Encodes signed claims. An unconfigured or empty secret fails closed.
pub(crate) fn encode(claims: &CursorClaims, secret: &Option<Vec<u8>>) -> Result<PageToken, StoreError> {
    let Some(secret) = secret.as_ref().filter(|key| !key.is_empty()) else {
        return Err(StoreError::NotApplied {
            code: "cursor_secret_unconfigured".to_owned(),
        });
    };
    let payload = serde_json::to_vec(claims).map_err(|_| invalid("cursor_encoding"))?;
    let mac = sign(&payload, secret)?;
    Ok(PageToken(format!(
        "{FORMAT}{}:{}",
        hex::encode(payload),
        hex::encode(mac)
    )))
}

/// Verifies the signature and returns the claims. Refused: bad format, a
/// forged MAC, an unconfigured secret (in that order of checks the format
/// forbids probing the configuration).
pub(crate) fn decode(token: &PageToken, secret: &Option<Vec<u8>>) -> Result<CursorClaims, StoreError> {
    let Some(body) = token.0.strip_prefix(FORMAT) else {
        return Err(invalid("cursor_format"));
    };
    let Some((payload, mac)) = body.split_once(':') else {
        return Err(invalid("cursor_format"));
    };
    let payload = hex::decode(payload).map_err(|_| invalid("cursor_format"))?;
    let mac = hex::decode(mac).map_err(|_| invalid("cursor_format"))?;
    let Some(secret) = secret.as_ref().filter(|key| !key.is_empty()) else {
        return Err(StoreError::NotApplied {
            code: "cursor_secret_unconfigured".to_owned(),
        });
    };
    let mut mac_of = <HmacSha256 as Mac>::new_from_slice(secret).map_err(|_| invalid("cursor_secret"))?;
    mac_of.update(&payload);
    mac_of
        .verify_slice(&mac)
        .map_err(|_| invalid("cursor_signature"))?;
    if payload.len() > 2048 {
        return Err(invalid("cursor_format"));
    }
    let claims: CursorClaims = serde_json::from_slice(&payload).map_err(|_| invalid("cursor_format"))?;
    Ok(claims)
}

/// The last-key entry of a verified per-session index cursor, for the caller
/// that already checked the binding.
pub(crate) fn entry_of(claims: &CursorClaims) -> ZSetEntry {
    ZSetEntry {
        member: claims.last_member.clone(),
        score: claims.last_score as f64,
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used, reason = "cursor protocol assertions")]
    use super::*;

    fn claims(surface: &'static str, member: &str) -> CursorClaims {
        CursorClaims {
            surface: surface.to_owned(),
            tenant: "t-ant".into(),
            principal: "user-001".into(),
            binding: "s0042.7f3a9c".into(),
            statuses: String::new(),
            sort: "tid:asc".into(),
            cut: "live".into(),
            last_score: 12,
            last_member: member.into(),
            expires_at_ms: 300_000,
        }
    }

    #[test]
    fn a_cursor_round_trips_under_its_secret() {
        let secret = Some(vec![7u8; 32]);
        let original = claims("list", "s0042.7f3a9c.abc");
        let token = encode(&original, &secret).unwrap();
        assert!(token.0.starts_with(FORMAT));
        assert_eq!(decode(&token, &secret).unwrap(), original);
        // Another secret never validates the same token.
        let other = Some(vec![8u8; 32]);
        assert!(decode(&token, &other).is_err());
        // An unconfigured secret fails closed in both directions.
        assert!(encode(&original, &None).is_err());
        assert!(decode(&token, &None).is_err());
    }

    #[test]
    fn tampered_tokens_never_verify() {
        let secret = Some(vec![7u8; 32]);
        let token = encode(&claims("group", "s0042.7f3a9c.abc"), &secret).unwrap();
        let flipped = format!("{}x", token.0);
        assert!(decode(&PageToken(flipped), &secret).is_err());
        assert!(decode(&PageToken(String::new()), &secret).is_err());
        assert!(decode(&PageToken("1.czAwND".into()), &secret).is_err());
        // Grown payload under the original MAC: refused.
        let (prefix, mac) = token.0.rsplit_once(':').unwrap();
        let grown = format!("{prefix}ff:{mac}");
        assert_eq!(decode(&PageToken(grown), &secret).unwrap_err(), invalid("cursor_signature"));
    }

    #[test]
    fn subject_filter_sort_and_expiry_are_bound() {
        let secret = Some(vec![7u8; 32]);
        let claims = claims("list", "s0042.7f3a9c.abc");
        let token = encode(&claims, &secret).unwrap();
        let decoded = decode(&token, &secret).unwrap();
        // Match: valid; each changed field: binding rejection.
        verify(&decoded, "list", "t-ant", "user-001", "s0042.7f3a9c", Some(""), "tid:asc", 0).unwrap();
        assert_eq!(
            verify(&decoded, "group", "t-ant", "user-001", "s0042.7f3a9c", Some(""), "tid:asc", 0).unwrap_err(),
            invalid("cursor_binding")
        );
        assert_eq!(
            verify(&decoded, "list", "t-other", "user-001", "s0042.7f3a9c", Some(""), "tid:asc", 0).unwrap_err(),
            invalid("cursor_subject_mismatch")
        );
        assert_eq!(
            verify(&decoded, "list", "t-ant", "user-002", "s0042.7f3a9c", Some(""), "tid:asc", 0).unwrap_err(),
            invalid("cursor_subject_mismatch")
        );
        assert_eq!(
            verify(&decoded, "list", "t-ant", "user-001", "s0042", Some(""), "tid:asc", 0).unwrap_err(),
            invalid("cursor_binding")
        );
        assert_eq!(
            verify(&decoded, "list", "t-ant", "user-001", "s0042.7f3a9c", Some("completed"), "tid:asc", 0)
                .unwrap_err(),
            invalid("cursor_binding")
        );
        assert_eq!(
            verify(&decoded, "list", "t-ant", "user-001", "s0042.7f3a9c", Some(""), "other:asc", 0).unwrap_err(),
            invalid("cursor_binding")
        );
        assert_eq!(
            verify(&decoded, "list", "t-ant", "user-001", "s0042.7f3a9c", Some(""), "tid:asc", 300_000)
                .unwrap_err(),
            invalid("cursor_expired")
        );
        let mut long = claims.clone();
        long.last_member = "x".repeat(257);
        let claims = encode(&long, &secret).unwrap();
        let decoded = decode(&claims, &secret).unwrap();
        assert_eq!(
            verify(&decoded, "list", "t-ant", "user-001", "s0042.7f3a9c", Some(""), "tid:asc", 0).unwrap_err(),
            invalid("cursor_binding")
        );
    }
}
