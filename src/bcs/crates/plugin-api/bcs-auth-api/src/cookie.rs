//! Session cookie wire contract shared between the OAuth session plugin
//! (`bcs-auth-oauth`) and the OAuth delivery routes (`bcs-http`).

use axum::http::HeaderMap;

/// Cookie name used to carry the JWT session token.
pub const BCS_SESSION_COOKIE: &str = "bcs_session";

/// Extract the `bcs_session` cookie value from request headers.
pub fn extract_session_cookie(headers: &HeaderMap) -> Option<String> {
    headers
        .get("cookie")
        .and_then(|v| v.to_str().ok())
        .and_then(|cookie_str| {
            cookie_str.split(';').find_map(|pair| {
                let mut parts = pair.splitn(2, '=');
                let name = parts.next()?.trim();
                if name == BCS_SESSION_COOKIE {
                    parts.next().map(|v| v.trim().to_string())
                } else {
                    None
                }
            })
        })
}
