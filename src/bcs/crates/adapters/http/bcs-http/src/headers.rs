//! Shared HTTP header extraction helpers.
//!
//! Used by both route handlers and `state` to avoid duplicated bearer-token
//! extraction across modules. Kept as a leaf module here (alongside
//! [`crate::service_key`]) so it can be depended on from `state` and `routes`
//! without introducing a `routes -> state` cycle.

use axum::http::{HeaderMap, header};

/// Bearer scheme prefix, lowercase, used for case-insensitive comparison.
const BEARER_PREFIX: &[u8] = b"bearer ";

/// Extract the credential from an `Authorization: Bearer <token>` header.
///
/// The scheme is matched case-insensitively per RFC 7235 (so `bearer`,
/// `Bearer`, `BEARER`, etc. all work). The comparison runs on the raw byte
/// slice and is guarded by a length check, so taking `&value[BEARER_PREFIX
/// .len()..]` afterwards is safe: byte 7 is an ASCII space boundary and never
/// lands inside a multi-byte UTF-8 codepoint.
///
/// Returns the trimmed, non-empty token, or `None` if the header is absent,
/// not a bearer token, or empty after trimming.
pub fn extract_bearer_token(headers: &HeaderMap) -> Option<String> {
    headers
        .get(header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .filter(|value| value.len() >= BEARER_PREFIX.len())
        .and_then(|value| {
            if value.as_bytes()[..BEARER_PREFIX.len()].eq_ignore_ascii_case(BEARER_PREFIX) {
                Some(&value[BEARER_PREFIX.len()..])
            } else {
                None
            }
        })
        .map(str::trim)
        .filter(|token| !token.is_empty())
        .map(str::to_string)
}
