//! Set-Cookie protocol for the LEGACY `/auth/*` OAuth entrypoint
//! (spec §8.4 / §8.4 "契约传播" — backward implementation of the same
//! contract the V1 adapter uses).
//!
//! The shared application service (`bcs_service_api::application::v1::AuthService`)
//! replies pure-data [`BrowserCookieChange`] instructions; THIS module is the
//! delivery layer's encoder:
//!
//! - Temporary login challenge (production): `__Host-bcs_oauth_login`,
//!   `Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=300`. Local plain-HTTP
//!   dev uses `bcs_oauth_login_local` (no `__Host` prefix, no `Secure`
//!   attribute — browsers reject the prefix without a secure context and
//!   drop Secure cookies over plain HTTP). The choice is derived from the
//!   configured `[auth.oauth].base_url` scheme, the same derivation the
//!   session cookie's `Secure` flag has always used.
//! - Session: `bcs_session=<token>; HttpOnly; SameSite=Lax; Path=/` with
//!   `Secure` over https — byte-compatible with the pre-existing session
//!   cookie shape.
//! - Multiple cookie changes become MULTIPLE `Set-Cookie` headers via
//!   `append` — never one comma-joined value.
//! - Responses carrying challenge/session cookies get
//!   `Cache-Control: no-store`.
//! - The nonce never appears in any JSON body.

use axum::http::header::{CACHE_CONTROL, COOKIE, SET_COOKIE};
use axum::http::{HeaderMap, HeaderValue};

use bcs_auth_api::BCS_SESSION_COOKIE;
use bcs_service_api::application::v1::BrowserCookieChange;

/// Production challenge cookie name (host-prefixed, https only).
pub const CHALLENGE_COOKIE_SECURE: &str = "__Host-bcs_oauth_login";
/// Local plain-HTTP dev challenge cookie name (no `__Host` prefix).
pub const CHALLENGE_COOKIE_LOCAL: &str = "bcs_oauth_login_local";
/// Challenge TTL in seconds (`Max-Age=300`).
pub const CHALLENGE_MAX_AGE: u64 = 300;

/// Encoder for one deployment's cookie attributes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CookieProtocol {
    secure: bool,
}

impl CookieProtocol {
    /// Derive production-vs-local from the OAuth base URL scheme: https →
    /// production (`__Host-…` + `Secure`), http → local names without
    /// `Secure`.
    pub fn from_base_url(base_url: &str) -> Self {
        Self {
            secure: base_url.trim().starts_with("https://"),
        }
    }

    /// Explicit constructor for tests.
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

    /// The shared session cookie name (stable across entrypoints).
    pub fn session_cookie_name(&self) -> &'static str {
        BCS_SESSION_COOKIE
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
            "{BCS_SESSION_COOKIE}={token}; HttpOnly{}; SameSite=Lax; Path=/",
            self.secure_attr(),
        )
    }

    /// `Set-Cookie` header value clearing the session cookie.
    pub fn clear_session(&self) -> String {
        format!(
            "{BCS_SESSION_COOKIE}=; HttpOnly{}; SameSite=Lax; Path=/; Max-Age=0",
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
/// header(s), in order.
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

/// Strict session-carrier extraction (review 2026-09-20 #6; contract §4).
/// Mirrors the V1 `auth_cookies::strict_unique_cookie_value` semantics:
/// name-aware scan where a bare `bcs_session` (no `=`) counts as present.
///
/// - `Ok(None)` — truly absent.
/// - `Ok(Some(token))` — exactly one non-blank carrier.
/// - `Err(AmbiguousCookieCarrier)` — unreadable header or blank/duplicate carrier: the
///   caller must REJECT, never take the absent-cookie path.
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

/// Exact scheme/host/port Origin match against the configured trusted
/// allow-list (compat mode: the finite exact `cors.allowed_origins` set,
/// wildcard/`null` entries excluded at wiring time). Missing Origin or any
/// mismatch is a reject — this is CSRF defense, not identity.
pub fn origin_matches(allowed: &[String], origin: Option<&HeaderValue>) -> bool {
    let Some(origin) = origin else {
        return false;
    };
    let Ok(origin) = origin.to_str() else {
        return false;
    };
    let Ok(origin) = url::Url::parse(origin.trim()) else {
        return false;
    };
    for candidate in allowed {
        let Ok(candidate) = url::Url::parse(candidate.trim()) else {
            continue;
        };
        if candidate.scheme() == origin.scheme()
            && candidate.host_str() == origin.host_str()
            && candidate.port() == origin.port()
        {
            return true;
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::HeaderName;

    #[test]
    fn https_base_uses_host_prefixed_challenge_with_secure() {
        let cookie = CookieProtocol::from_base_url("https://bcs.example.com");
        assert_eq!(cookie.challenge_cookie_name(), CHALLENGE_COOKIE_SECURE);
        let set = cookie.set_challenge("nonce-1");
        assert!(set.starts_with("__Host-bcs_oauth_login=nonce-1"));
        for attr in ["Path=/", "HttpOnly", "Secure", "SameSite=Lax", "Max-Age=300"] {
            assert!(set.contains(attr), "{set} missing {attr}");
        }
        let clear = cookie.clear_challenge();
        assert!(clear.contains("Max-Age=0"));
        assert_eq!(
            cookie.set_session("jwt"),
            "bcs_session=jwt; HttpOnly; Secure; SameSite=Lax; Path=/"
        );
    }

    #[test]
    fn http_dev_base_never_uses_host_prefix_or_secure() {
        let cookie = CookieProtocol::from_base_url("http://127.0.0.1:21000");
        assert_eq!(cookie.challenge_cookie_name(), CHALLENGE_COOKIE_LOCAL);
        let set = cookie.set_challenge("nonce-2");
        assert!(set.starts_with("bcs_oauth_login_local=nonce-2"));
        assert!(!set.contains("Secure"), "plain-HTTP dev must not be Secure");
        let clear = cookie.clear_session();
        assert!(!clear.contains("Secure"));
        assert!(clear.contains("Max-Age=0"));
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
            "the two cookie instructions must stay distinct Set-Cookie headers"
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
    fn origin_match_is_exact_scheme_host_port() {
        let allowed = vec!["https://workbench.example".to_string()];
        let mut headers = HeaderMap::new();
        let good: HeaderValue = "https://workbench.example".parse().unwrap();
        assert!(origin_matches(&allowed, Some(&good)));
        let wrong_port: HeaderValue = "https://workbench.example:8443".parse().unwrap();
        assert!(!origin_matches(&allowed, Some(&wrong_port)));
        let wrong_scheme: HeaderValue = "http://workbench.example".parse().unwrap();
        assert!(!origin_matches(&allowed, Some(&wrong_scheme)));
        let attacker: HeaderValue = "https://attacker.example".parse().unwrap();
        assert!(!origin_matches(&allowed, Some(&attacker)));
        assert!(!origin_matches(&allowed, None), "missing Origin rejects");
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
