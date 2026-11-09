//! Cookie encoding for the V1 auth flow (V1 API auth plan §8.4).
//!
//! The application service answers with transport-neutral
//! [`BrowserCookieChange`] instructions; THIS module is the only place the
//! V1 adapter turns them into `Set-Cookie` header values:
//!
//! - the production login-challenge cookie is the host-only
//!   `__Host-bcs_oauth_login` (Path=/, HttpOnly, Secure, SameSite=Lax,
//!   Max-Age=300);
//! - plain-HTTP local development uses a DIFFERENT, non-`__Host` name —
//!   production never downgrades;
//! - the session cookie keeps the existing `bcs_session` name and flags
//!   (`HttpOnly`, `Secure` on https, `SameSite=Lax`, `Path=/`).
//!
//! Nonce, state, and session-token values are never logged by this module
//! (only the cookie names and attributes are format-safe).

use axum::http::HeaderValue;

use bcs_auth_api::BCS_SESSION_COOKIE;
use bcs_service_api::application::v1::BrowserCookieChange;

/// Pinned challenge cookie contract (plan §8.4): 5-minute window.
pub const CHALLENGE_MAX_AGE_SECS: u64 = 300;

/// The production, host-only challenge cookie name (`__Host-` prefix and
/// the `Secure`/no-`Domain` attributes it requires).
pub const HOST_CHALLENGE_COOKIE: &str = "__Host-bcs_oauth_login";

/// The local plain-HTTP development challenge cookie name. A `__Host-`
/// cookie would be dropped by browsers over plain HTTP, and the production
/// name must never learn to work without `Secure` — so local HTTP uses a
/// different name, never a downgraded `__Host-` one.
pub const LOCAL_CHALLENGE_COOKIE: &str = "bcs_oauth_login";

/// The one challenge cookie name for a transport mode. EXACTLY ONE name is
/// valid per mode: over HTTPS production only the host-only `__Host-` name
/// is honored (a `Domain`-scoped cookie from a hostile subdomain can never
/// satisfy the browser binding — that is the `__Host-` guarantee), and
/// plain-HTTP local development uses the separate non-`__Host` name (a
/// `Secure` cookie would be dropped there). The OTHER mode's name is never
/// accepted (plan §8.4: 生产不能降级).
pub fn challenge_cookie_name(secure: bool) -> &'static str {
    if secure {
        HOST_CHALLENGE_COOKIE
    } else {
        LOCAL_CHALLENGE_COOKIE
    }
}

/// Encode one cookie instruction (plan Task 11 step 3). `secure` selects
/// the production (`https`) vs local-HTTP (`http`) attribute set and,
/// with it, the challenge cookie NAME. The caller APPENDS the produced
/// values — two instructions must never be merged into one comma-
/// separated `Set-Cookie`.
pub fn encode_cookie_change(change: &BrowserCookieChange, secure: bool) -> HeaderValue {
    let secure_attr = if secure { "; Secure" } else { "" };
    let value = match change {
        BrowserCookieChange::SetLoginChallenge { nonce, .. } => format!(
            "{}={}; Path=/; HttpOnly{secure_attr}; SameSite=Lax; Max-Age={CHALLENGE_MAX_AGE_SECS}",
            challenge_cookie_name(secure),
            nonce
        ),
        BrowserCookieChange::ClearLoginChallenge => format!(
            "{}=; Path=/; HttpOnly{secure_attr}; SameSite=Lax; Max-Age=0",
            challenge_cookie_name(secure)
        ),
        BrowserCookieChange::SetSession { token, .. } => format!(
            "{BCS_SESSION_COOKIE}={token}; HttpOnly{secure_attr}; SameSite=Lax; Path=/"
        ),
        BrowserCookieChange::ClearSession => format!(
            "{BCS_SESSION_COOKIE}=; HttpOnly{secure_attr}; SameSite=Lax; Path=/; Max-Age=0"
        ),
    };
    // Only the cookie names/attributes land here by construction; the
    // token/nonce values are opaque strings that the cookie grammar allows.
    HeaderValue::from_str(&value).expect("encoded Set-Cookie value is header-safe")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn set_challenge(secure: bool) -> String {
        encode_cookie_change(
            &BrowserCookieChange::SetLoginChallenge {
                nonce: "n".to_string(),
                expires_at: 5,
            },
            secure,
        )
        .to_str()
        .expect("utf-8")
        .to_string()
    }

    #[test]
    fn production_challenge_uses_the_host_prefixed_hardened_form() {
        let cookie = set_challenge(true);
        assert!(cookie.starts_with("__Host-bcs_oauth_login=n;"));
        assert!(cookie.contains("Path=/"));
        assert!(cookie.contains("HttpOnly"));
        assert!(cookie.contains("Secure"));
        assert!(cookie.contains("SameSite=Lax"));
        assert!(cookie.contains("Max-Age=300"));
    }

    #[test]
    fn local_http_challenge_uses_a_different_non_host_name() {
        let cookie = set_challenge(false);
        assert!(cookie.starts_with("bcs_oauth_login=n;"));
        assert!(!cookie.contains("__Host-"));
        assert!(!cookie.contains("Secure"));
        assert!(cookie.contains("Max-Age=300"));
    }

    #[test]
    fn session_and_clear_instructions_keep_the_existing_cookie_contract() {
        let session = encode_cookie_change(
            &BrowserCookieChange::SetSession {
                token: "t".to_string(),
                expires_at: 9,
            },
            true,
        );
        assert!(session
            .to_str()
            .expect("utf-8")
            .starts_with("bcs_session=t; HttpOnly; Secure; SameSite=Lax; Path=/"));
        let clear = encode_cookie_change(&BrowserCookieChange::ClearSession, false);
        assert_eq!(
            clear.to_str().expect("utf-8"),
            "bcs_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"
        );
    }
}
