//! Set-Cookie protocol for the OAuth auth entrypoint (spec §8.4, Task 11).
//!
//! The application service replies pure-data [`BrowserCookieChange`]
//! instructions; THIS module is the HTTP layer's authoritative encoder:
//!
//! - Temporary login challenge (production): `__Host-bcs_oauth_login`,
//!   `Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=300`. Local plain-HTTP
//!   dev uses a DIFFERENT name (`bcs_oauth_login_local`, no `__Host` prefix
//!   and no `Secure` attribute) because browsers reject the `__Host`
//!   prefix without a secure context. Production-vs-local is derived from
//!   the configured public base URL scheme (`https` → production), the
//!   same derivation the session cookie's `Secure` flag already uses.
//! - Session: `bcs_session=<token>; HttpOnly; SameSite=Lax; Path=/` with
//!   `Secure` appended over https — byte-compatible with the pre-existing
//!   session cookie shape all shared readers rely on.
//! - Multiple cookie changes become MULTIPLE `Set-Cookie` headers via
//!   `append` — never one comma-joined value.
//! - Responses carrying challenge/session cookies get
//!   `Cache-Control: no-store`.
//! - The nonce never appears in any JSON body; it travels only inside the
//!   challenge cookie's value.

use axum::http::header::{CACHE_CONTROL, COOKIE, SET_COOKIE};
use axum::http::{HeaderMap, HeaderValue};

use bcs_service_api::application::v1::BrowserCookieChange;

/// Production challenge cookie name (host-prefixed, https only).
pub const CHALLENGE_COOKIE_SECURE: &str = "__Host-bcs_oauth_login";
/// Local plain-HTTP dev challenge cookie name (no `__Host` prefix).
pub const CHALLENGE_COOKIE_LOCAL: &str = "bcs_oauth_login_local";
/// Shared session cookie name (unchanged across entrypoints).
pub const SESSION_COOKIE_NAME: &str = "bcs_session";
/// Challenge TTL in seconds (`Max-Age=300`).
pub const CHALLENGE_MAX_AGE: u64 = 300;

/// Encoder for one deployment's cookie attributes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CookieProtocol {
    secure: bool,
}

impl CookieProtocol {
    /// Derive production-vs-local from the public base URL scheme: https →
    /// production (`__Host-…` + `Secure`), http → local names without
    /// `Secure`. This mirrors the existing session-cookie derivation and
    /// avoids a second config knob for the same fact.
    pub fn from_public_base_url(base_url: &str) -> Self {
        Self {
            secure: base_url.trim().starts_with("https://"),
        }
    }

    /// Explicit constructor for tests and non-URL-driven call sites.
    pub fn new(secure: bool) -> Self {
        Self { secure }
    }

    pub fn challenge_cookie_name(&self) -> &'static str {
        if self.secure {
            CHALLENGE_COOKIE_SECURE
        } else {
            CHALLENGE_COOKIE_LOCAL
        }
    }

    /// The shared session cookie name (stable name across entrypoints).
    pub fn session_cookie_name(&self) -> &'static str {
        SESSION_COOKIE_NAME
    }

    fn secure_attr(&self) -> &'static str {
        if self.secure {
            "; Secure"
        } else {
            ""
        }
    }

    /// `Set-Cookie` header value installing the login challenge.
    pub fn set_challenge(&self, nonce: &str) -> String {
        format!(
            "{}={nonce}; Path=/; HttpOnly{}; SameSite=Lax; Max-Age={CHALLENGE_MAX_AGE}",
            self.challenge_cookie_name(),
            self.secure_attr(),
        )
    }

    /// `Set-Cookie` header value clearing the login challenge.
    pub fn clear_challenge(&self) -> String {
        format!(
            "{}=; Path=/; HttpOnly{}; SameSite=Lax; Max-Age=0",
            self.challenge_cookie_name(),
            self.secure_attr(),
        )
    }

    /// `Set-Cookie` header value installing the session cookie.
    pub fn set_session(&self, token: &str) -> String {
        format!(
            "{SESSION_COOKIE_NAME}={token}; HttpOnly{}; SameSite=Lax; Path=/",
            self.secure_attr(),
        )
    }

    /// `Set-Cookie` header value clearing the session cookie.
    pub fn clear_session(&self) -> String {
        format!(
            "{SESSION_COOKIE_NAME}=; HttpOnly{}; SameSite=Lax; Path=/; Max-Age=0",
            self.secure_attr(),
        )
    }

    /// Translate the application service's cookie changes into appended
    /// `Set-Cookie` headers, in order.
    pub fn apply_cookie_changes(&self, headers: &mut HeaderMap, changes: &[BrowserCookieChange]) {
        for change in changes {
            let value = match change {
                BrowserCookieChange::SetLoginChallenge { nonce, .. } => self.set_challenge(nonce),
                BrowserCookieChange::ClearLoginChallenge => self.clear_challenge(),
                BrowserCookieChange::SetSession { token, .. } => self.set_session(token),
                BrowserCookieChange::ClearSession => self.clear_session(),
            };
            headers.append(SET_COOKIE, HeaderValue::from_str(&value).expect("ascii cookie"));
        }
    }
}

/// Extract every value bound to `cookie_name` from the request's `Cookie`
/// header(s), in order. Duplicates are surfaced to the caller: the
/// callback validation requires EXACTLY ONE non-empty occurrence.
pub fn extract_cookie_values(headers: &HeaderMap, cookie_name: &str) -> Vec<String> {
    let mut values = Vec::new();
    let prefix = format!("{cookie_name}=");
    for value in headers.get_all(COOKIE) {
        let Ok(value) = value.to_str() else {
            continue;
        };
        for pair in value.split(';') {
            let pair = pair.trim();
            if let Some(value) = pair.strip_prefix(&prefix) {
                values.push(value.to_string());
            }
        }
    }
    values
}

/// Exactly one non-empty occurrence — the callback's binding-cookie rule
/// (missing / duplicate / empty → `invalid_state` reject).
pub fn unique_cookie_value(headers: &HeaderMap, cookie_name: &str) -> Option<String> {
    let values = extract_cookie_values(headers, cookie_name);
    if values.len() != 1 || values[0].is_empty() {
        return None;
    }
    Some(values.into_iter().next().expect("length checked"))
}

/// The Cookie header was unreadable, or the carrier was PRESENT but
/// malformed (blank value or duplicate carriers). Not interchangeable with
/// "no cookie at all": the absent-cookie path (e.g. idempotent logout) must stay
/// reserved for requests that truly carry no session carrier.
#[derive(Debug)]
pub struct AmbiguousCookieCarrier;

/// Strict session-carrier extraction (review 2026-09-20 #6; contract §4:
/// enabled source 的 blank/malformed credential 必须拒绝).
///
/// Name-aware scan mirroring the chain's `extract_strict_session_cookie`:
/// a bare `bcs_session` (no `=`, no value) counts as PRESENT.
///
/// - `Ok(None)` — the cookie is TRULY absent.
/// - `Ok(Some(token))` — exactly one non-blank carrier.
/// - `Err(AmbiguousCookieCarrier)` — unreadable header or blank/duplicate carrier:
///   the caller must REJECT the request (401), not take the absent-cookie
///   success/identity path.
pub fn strict_unique_cookie_value(
    headers: &HeaderMap,
    cookie_name: &str,
) -> Result<Option<String>, AmbiguousCookieCarrier> {
    let mut found: Option<String> = None;
    let mut duplicated = false;
    for header in headers.get_all(COOKIE) {
        // An unreadable header may contain the session carrier. Never skip
        // it and report an absent cookie (or accept another readable header).
        let raw = header.to_str().map_err(|_| AmbiguousCookieCarrier)?;
        for pair in raw.split(';') {
            let mut parts = pair.trim().splitn(2, '=');
            let name = parts.next().unwrap_or("").trim();
            if name != cookie_name {
                continue;
            }
            let value = parts.next().unwrap_or("").trim();
            if found.is_some() || duplicated {
                duplicated = true;
                continue;
            }
            found = Some(value.to_string());
        }
    }
    match found {
        None => Ok(None),
        // Present but blank / value-less → malformed carrier, NOT absent.
        Some(value) if value.is_empty() => Err(AmbiguousCookieCarrier),
        Some(_) if duplicated => Err(AmbiguousCookieCarrier),
        Some(value) => Ok(Some(value)),
    }
}

/// Attach the mandatory `Cache-Control: no-store` header to a response
/// carrying challenge/session cookies (spec §8.4).
pub fn no_store(headers: &mut HeaderMap) {
    headers.insert(CACHE_CONTROL, HeaderValue::from_static("no-store"));
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::{HeaderMap, HeaderName};

    #[test]
    fn https_base_uses_host_prefixed_challenge_with_secure() {
        let cookie = CookieProtocol::from_public_base_url("https://bcs.example.com/openapi/v1/auth");
        assert_eq!(cookie.challenge_cookie_name(), CHALLENGE_COOKIE_SECURE);
        let set = cookie.set_challenge("nonce-1");
        let attrs = set.split("; ").skip(1).collect::<Vec<_>>();
        assert_eq!(attrs, ["Path=/", "HttpOnly", "Secure", "SameSite=Lax", "Max-Age=300"]);
        assert!(set.starts_with("__Host-bcs_oauth_login=nonce-1"));
    }

    #[test]
    fn http_dev_base_never_uses_host_prefix_or_secure() {
        let cookie = CookieProtocol::from_public_base_url("http://127.0.0.1:21000/openapi/v1/auth");
        // Local HTTP dev must NOT use the __Host prefix: browsers reject
        // __Host cookies without the Secure attribute, and Secure cookies
        // are silently dropped over plain HTTP.
        assert_eq!(cookie.challenge_cookie_name(), CHALLENGE_COOKIE_LOCAL);
        let set = cookie.set_challenge("nonce-2");
        assert!(set.starts_with("bcs_oauth_login_local=nonce-2"));
        assert!(!set.contains("Secure"), "plain-HTTP dev cookie must not be Secure");
        assert!(set.contains("HttpOnly"));
        assert!(set.contains("SameSite=Lax"));
        assert!(set.contains("Max-Age=300"));
        let clear = cookie.clear_challenge();
        assert!(!clear.contains("Secure"));
        assert!(clear.contains("Max-Age=0"));
    }

    #[test]
    fn session_cookie_preserves_legacy_shape_and_clears_with_max_age_zero() {
        let cookie = CookieProtocol::new(true);
        assert_eq!(
            cookie.set_session("jwt-value"),
            "bcs_session=jwt-value; HttpOnly; Secure; SameSite=Lax; Path=/"
        );
        assert_eq!(
            cookie.clear_session(),
            "bcs_session=; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=0"
        );
        let plain = CookieProtocol::new(false);
        assert_eq!(plain.set_session("t"), "bcs_session=t; HttpOnly; SameSite=Lax; Path=/");
    }

    #[test]
    fn cookie_changes_append_as_distinct_headers() {
        let cookie = CookieProtocol::new(true);
        let mut headers = HeaderMap::new();
        cookie.apply_cookie_changes(
            &mut headers,
            &[
                BrowserCookieChange::SetSession {
                    token: "s".to_string(),
                    expires_at: 10,
                },
                BrowserCookieChange::ClearLoginChallenge,
            ],
        );
        let values = headers
            .get_all(SET_COOKIE)
            .iter()
            .map(|v| v.to_str().unwrap().to_string())
            .collect::<Vec<_>>();
        assert_eq!(
            values,
            vec![
                "bcs_session=s; HttpOnly; Secure; SameSite=Lax; Path=/".to_string(),
                "__Host-bcs_oauth_login=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0"
                    .to_string(),
            ],
            "two instructions must stay two distinct Set-Cookie headers"
        );
    }

    #[test]
    fn unique_cookie_value_rejects_missing_duplicate_and_empty() {
        let mut headers = HeaderMap::new();
        headers.insert(
            HeaderName::from_static("cookie"),
            HeaderValue::from_static("other=1"),
        );
        assert_eq!(unique_cookie_value(&headers, CHALLENGE_COOKIE_SECURE), None);

        headers.insert(
            HeaderName::from_static("cookie"),
            HeaderValue::from_static("__Host-bcs_oauth_login=; other=1"),
        );
        assert_eq!(unique_cookie_value(&headers, CHALLENGE_COOKIE_SECURE), None);

        headers.insert(
            HeaderName::from_static("cookie"),
            HeaderValue::from_static("__Host-bcs_oauth_login=n1"),
        );
        assert_eq!(
            unique_cookie_value(&headers, CHALLENGE_COOKIE_SECURE).as_deref(),
            Some("n1")
        );

        headers.append(
            HeaderName::from_static("cookie"),
            HeaderValue::from_static("__Host-bcs_oauth_login=n2"),
        );
        assert_eq!(unique_cookie_value(&headers, CHALLENGE_COOKIE_SECURE), None);
    }

    #[test]
    fn strict_session_cookie_rejects_unreadable_headers_in_any_position() {
        let invalid = HeaderValue::from_bytes(b"bcs_session=\x80").expect("header value");
        let valid = HeaderValue::from_static("bcs_session=valid-token");
        for values in [
            vec![invalid.clone()],
            vec![invalid.clone(), valid.clone()],
            vec![valid, invalid],
            vec![HeaderValue::from_bytes(b"other=\xff; bcs_session=token").unwrap()],
        ] {
            let mut headers = HeaderMap::new();
            for value in values {
                headers.append(COOKIE, value);
            }
            assert!(strict_unique_cookie_value(&headers, "bcs_session").is_err());
        }
        assert!(strict_unique_cookie_value(&HeaderMap::new(), "bcs_session")
            .unwrap()
            .is_none());
        let mut headers = HeaderMap::new();
        headers.insert(COOKIE, HeaderValue::from_static("other=value; bcs_session=token"));
        assert_eq!(
            strict_unique_cookie_value(&headers, "bcs_session").unwrap().as_deref(),
            Some("token"),
        );
    }
}
