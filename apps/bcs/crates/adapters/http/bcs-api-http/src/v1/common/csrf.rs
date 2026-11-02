//! Per-actual-source browser Origin (CSRF) boundary (V1 API auth plan,
//! spec §7.4 / S1 / S2).
//!
//! The Origin check is governed by the ACTUAL source that authenticated the
//! request — never by which headers or cookies happen to be attached. The
//! caller must reject duplicate / non-UTF-8 `Origin` headers before calling
//! `validate`; `HeaderMap::get` never silently selects the first value.

use axum::http::Method;

use super::{
    AuthenticationContext, CredentialKind, PrincipalVerificationError,
};

/// Exact-match allow-list of browser origins trusted for cookie-authenticated
/// unsafe requests: `scheme://host[:port]`, no wildcards, no user info, no
/// path/query/fragment. Built once at composition time; `new` fails on any
/// invalid entry so a mis-typed configuration takes the process down at
/// startup instead of silently weakening CSRF enforcement.
#[derive(Clone, Debug)]
pub struct TrustedBrowserOrigins {
    origins: Vec<String>,
}

impl TrustedBrowserOrigins {
    /// Validate and pin the configured origins (Task 7 constructor holds the
    /// structural rules; Task 12 must call this before exposing routes).
    pub fn new(origins: Vec<String>) -> Result<Self, String> {
        if origins.is_empty() {
            return Err("trusted browser origins must not be empty".to_string());
        }
        let mut seen: Vec<&str> = Vec::with_capacity(origins.len());
        for origin in &origins {
            validate_origin_form(origin)?;
            if seen.contains(&origin.as_str()) {
                return Err(format!("duplicate trusted browser origin: {origin}"));
            }
            seen.push(origin.as_str());
        }
        Ok(Self { origins })
    }

    /// The pinned list, in configuration order (for diagnostics only).
    pub fn origins(&self) -> &[String] {
        &self.origins
    }

    /// Enforce the per-actual-source Origin rule.
    ///
    /// - Safe methods never need an Origin.
    /// - Cookie-kind provenance requires an exact trusted origin on unsafe
    ///   methods; missing or untrusted is `Forbidden` (HTTP 403 and the
    ///   chain is not re-entered to try another source).
    /// - Header/token provenance carries no ambient authority: no Origin
    ///   requirement (a stray cookie attached to a Gateway-authenticated
    ///   request must not trip cookie rules).
    /// - `LegacyBrowserBoundary` never reaches the new chain (the composite
    ///   rejects it as `Internal`); if a caller nonetheless asks, refuse
    ///   rather than letting the legacy marker short-circuit CSRF policy.
    pub fn validate(
        &self,
        method: &Method,
        origin: Option<&str>,
        context: &AuthenticationContext,
    ) -> Result<(), PrincipalVerificationError> {
        match context.credential_kind {
            CredentialKind::GatewayPrincipalHeader | CredentialKind::ExplicitToken => Ok(()),
            CredentialKind::LegacyBrowserBoundary => Err(PrincipalVerificationError::Internal(
                "legacy browser boundary is not accepted by the V1 authentication chain".into(),
            )),
            CredentialKind::OAuthSessionCookie | CredentialKind::SsoCookie => {
                if method.is_safe() {
                    return Ok(());
                }
                let origin = origin.ok_or_else(|| {
                    PrincipalVerificationError::Forbidden(
                        "trusted browser Origin is required for cookie-authenticated writes"
                            .into(),
                    )
                })?;
                if self.origins.iter().any(|allowed| allowed == origin) {
                    Ok(())
                } else {
                    Err(PrincipalVerificationError::Forbidden(
                        "browser Origin is not trusted for cookie-authenticated writes".into(),
                    ))
                }
            }
        }
    }
}

/// Structural rules for one configured origin: `scheme://host[:port]` with
/// `http`/`https` only, exact-match semantics (no wildcards), and no user
/// info or path/query/fragment. Control characters and surrounding
/// whitespace are rejected.
fn validate_origin_form(origin: &str) -> Result<(), String> {
    if origin.trim().is_empty() || origin.trim() != origin {
        return Err(format!("trusted browser origin is blank or padded: {origin:?}"));
    }
    if origin.chars().any(|c| c.is_control() || c.is_whitespace()) {
        return Err(format!("trusted browser origin contains control characters: {origin:?}"));
    }
    if origin.contains('*') {
        return Err(format!("trusted browser origins do not support wildcards: {origin:?}"));
    }
    let (scheme, remainder) = origin
        .split_once("://")
        .ok_or_else(|| format!("trusted browser origin must be scheme://host[:port]: {origin:?}"))?;
    if !matches!(scheme, "http" | "https") {
        return Err(format!("trusted browser origin must use http/https: {origin:?}"));
    }
    if remainder.contains('@') {
        return Err(format!("trusted browser origins do not allow user info: {origin:?}"));
    }
    if remainder.contains('/') || remainder.contains('?') || remainder.contains('#') {
        return Err(format!(
            "trusted browser origins must not contain a path, query, or fragment: {origin:?}"
        ));
    }
    let (host, port) = split_host_port(remainder)?;
    if host.is_empty() {
        return Err(format!("trusted browser origin host is empty: {origin:?}"));
    }
    if let Some(port) = port {
        if port.is_empty() || !port.bytes().all(|b| b.is_ascii_digit()) {
            return Err(format!("trusted browser origin port is invalid: {origin:?}"));
        }
    }
    Ok(())
}

/// Split `host[:port]` including bracketed IPv6 hosts.
fn split_host_port(remainder: &str) -> Result<(String, Option<&str>), String> {
    if let Some(close) = remainder.strip_prefix('[') {
        // IPv6 literal: [::1] or [::1]:8080 — a close bracket is required.
        let (host, after) = close
            .split_once(']')
            .ok_or_else(|| format!("trusted browser origin has an unterminated IPv6 host: {remainder:?}"))?;
        let mut normalized = String::with_capacity(host.len() + 2);
        normalized.push('[');
        normalized.push_str(host);
        normalized.push(']');
        match after.strip_prefix(':') {
            Some(port) => Ok((normalized, Some(port))),
            None if after.is_empty() => Ok((normalized, None)),
            None => Err(format!(
                "trusted browser origin has trailing characters after the IPv6 host: {after:?}"
            )),
        }
    } else {
        match remainder.rsplit_once(':') {
            // Task 7 review tightening: a non-bracketed host may contain at
            // most ONE colon (the port separator). Extra colons — an
            // unbracketed IPv6 literal or a malformed authority — must
            // reject, not silently parse as `host = "a:b"` with a bogus
            // port string that the digit check would then catch by
            // accident, or worse, accept.
            Some((host, port)) => {
                if host.contains(':') {
                    return Err(format!(
                        "trusted browser origin has an unbracketed multi-colon host: {remainder:?}"
                    ));
                }
                Ok((host.to_string(), Some(port)))
            }
            None => Ok((remainder.to_string(), None)),
        }
    }
}
