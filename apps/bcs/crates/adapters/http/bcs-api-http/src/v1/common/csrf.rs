//! Trusted-browser-origin CSRF gating keyed off the authentication provenance.
//!
//! The decision keys off the AUTHENTICATION context (what source actually
//! authenticated the request), never off "request happens to carry a
//! cookie/header". Non-cookie credential kinds (e.g. GatewayPrincipalHeader)
//! are always OK; cookie credential kinds (OAuthSessionCookie/SsoCookie)
//! require a matching Origin on unsafe methods. Missing or non-matching
//! Origin on cookie-backed unsafe requests returns `Forbidden` (spec: 成功
//! 后的 Origin 拒绝是 403).

use axum::http::{HeaderValue, Method};
use url::Url;

use super::PrincipalVerificationError;
use super::authentication::{AuthenticationContext, CredentialKind};

/// Allow-list of trusted browser origins. Exact scheme/host/port match.
/// Empty list means no Origin is ever accepted for cookie-backed unsafe
/// requests (fail-closed); non-cookie verifiers always pass through.
#[derive(Clone, Debug)]
pub struct TrustedBrowserOrigins {
    origins: Vec<NormalizedOrigin>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct NormalizedOrigin {
    scheme: String,
    host: String,
    port: Option<u16>,
}

impl TrustedBrowserOrigins {
    /// Validate each origin: absolute http/https, no wildcard/userinfo/path/
    /// query/fragment. Mirrors the shape checks Task 6 put in bcs-config-api's
    /// api_auth.rs trusted_browser_origins validation, but implemented locally
    /// in the HTTP adapter so bcs-api-http does not depend on bcs-config-api.
    pub fn new(origins: Vec<String>) -> Result<Self, String> {
        let mut normalized: Vec<NormalizedOrigin> = Vec::with_capacity(origins.len());
        let mut seen: std::collections::HashSet<(String, String, Option<u16>)> =
            std::collections::HashSet::new();
        for raw in &origins {
            let parsed = Url::parse(raw.trim()).map_err(|e| {
                format!("trusted browser origin '{raw}' is not a valid URL: {e}")
            })?;
            if !matches!(parsed.scheme(), "http" | "https") {
                return Err(format!(
                    "trusted browser origin '{raw}' must use http(s) scheme"
                ));
            }
            let host = match parsed.host_str() {
                Some(host) if !host.is_empty() => host.to_string(),
                _ => {
                    return Err(format!(
                        "trusted browser origin '{raw}' must have a host"
                    ));
                }
            };
            if host.contains('*') {
                return Err(format!(
                    "trusted browser origin '{raw}' must not use a wildcard host"
                ));
            }
            if !parsed.username().is_empty() || parsed.password().is_some() {
                return Err(format!(
                    "trusted browser origin '{raw}' must not contain userinfo"
                ));
            }
            let path = parsed.path();
            if !path.is_empty() && path != "/" {
                return Err(format!(
                    "trusted browser origin '{raw}' must not contain a path"
                ));
            }
            if parsed.query().is_some() || parsed.fragment().is_some() {
                return Err(format!(
                    "trusted browser origin '{raw}' must not contain query or fragment"
                ));
            }
            let port = parsed.port();
            let key = (parsed.scheme().to_string(), host.clone(), port);
            if !seen.insert(key) {
                return Err(format!("trusted browser origin '{raw}' is a duplicate"));
            }
            normalized.push(NormalizedOrigin {
                scheme: parsed.scheme().to_string(),
                host,
                port,
            });
        }
        Ok(Self { origins: normalized })
    }

    /// Decide whether the request origin matches the allow-list given the
    /// actual authentication source. Returns Ok for header/credential-kinds
    /// that are not cookie-backed; returns Ok for idempotent-safe methods;
    /// otherwise requires the Origin header to match exactly one of the
    /// configured origins (scheme/host/port).
    pub fn validate(
        &self,
        method: &Method,
        origin: Option<&HeaderValue>,
        context: &AuthenticationContext,
    ) -> Result<(), PrincipalVerificationError> {
        // Non-cookie credential kinds (e.g. GatewayPrincipalHeader) are not
        // browser-cookie-controlled and require no Origin.
        if !matches!(
            context.credential_kind,
            CredentialKind::OAuthSessionCookie | CredentialKind::SsoCookie
        ) {
            return Ok(());
        }
        if is_safe_method(method) {
            return Ok(());
        }
        let raw = origin
            .ok_or(PrincipalVerificationError::Forbidden)?
            .to_str()
            .map_err(|_| PrincipalVerificationError::Forbidden)?;
        let parsed = Url::parse(raw.trim()).map_err(|_| PrincipalVerificationError::Forbidden)?;
        if !matches!(parsed.scheme(), "http" | "https") {
            return Err(PrincipalVerificationError::Forbidden);
        }
        let host = match parsed.host_str() {
            Some(host) if !host.is_empty() => host.to_string(),
            _ => return Err(PrincipalVerificationError::Forbidden),
        };
        let port = parsed.port();
        for configured in &self.origins {
            if configured.scheme == parsed.scheme()
                && configured.host == host
                && configured.port == port
            {
                return Ok(());
            }
        }
        Err(PrincipalVerificationError::Forbidden)
    }
}

/// RFC 7231 safe + idempotent-safe methods. GET/HEAD/OPTIONS/TRACE never
/// trigger an Origin check.
fn is_safe_method(method: &Method) -> bool {
    matches!(
        *method,
        Method::GET | Method::HEAD | Method::OPTIONS | Method::TRACE
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ctx(kind: CredentialKind) -> AuthenticationContext {
        AuthenticationContext {
            source: "any".to_string(),
            credential_kind: kind,
        }
    }

    #[test]
    fn rejects_wildcard_userinfo_path_query_fragment_and_non_http_origins() {
        for invalid in [
            "https://*.example.com",
            "https://user:pass@workbench.example.com",
            "https://workbench.example.com/path",
            "https://workbench.example.com/?q=1",
            "https://workbench.example.com#frag",
            "ftp://workbench.example.com",
            "not-a-url",
        ] {
            assert!(
                TrustedBrowserOrigins::new(vec![invalid.to_string()]).is_err(),
                "{invalid} should be rejected"
            );
        }
        assert!(TrustedBrowserOrigins::new(vec![
            "https://workbench.example.com".to_string(),
            "http://localhost:3000".to_string()
        ])
        .is_ok());
    }

    #[test]
    fn empty_origins_rejects_unsafe_cookie_requests_and_accepts_gateways() {
        let origins = TrustedBrowserOrigins::new(vec![]).expect("empty allow-list");
        let cookie = ctx(CredentialKind::OAuthSessionCookie);
        assert!(origins
            .validate(&Method::POST, None, &cookie)
            .is_err());
        let gateway = ctx(CredentialKind::GatewayPrincipalHeader);
        assert!(origins.validate(&Method::POST, None, &gateway).is_ok());
    }

    #[test]
    fn safe_methods_pass_without_origin_for_cookie_credential() {
        let origins = TrustedBrowserOrigins::new(vec!["https://workbench.example.com".into()])
            .expect("valid");
        let cookie = ctx(CredentialKind::OAuthSessionCookie);
        assert!(origins.validate(&Method::GET, None, &cookie).is_ok());
        assert!(origins.validate(&Method::HEAD, None, &cookie).is_ok());
        assert!(origins.validate(&Method::OPTIONS, None, &cookie).is_ok());
        assert!(origins.validate(&Method::TRACE, None, &cookie).is_ok());
    }

    #[test]
    fn cookie_unsafe_methods_require_matching_origin() {
        let origins = TrustedBrowserOrigins::new(vec!["https://workbench.example.com".into()])
            .expect("valid");
        let cookie = ctx(CredentialKind::OAuthSessionCookie);
        // Missing Origin on unsafe method -> Forbidden.
        assert!(origins.validate(&Method::POST, None, &cookie).is_err());
        // Mismatched scheme/host/port -> Forbidden.
        let http_origin = HeaderValue::from_static("http://workbench.example.com");
        let other_host = HeaderValue::from_static("https://attacker.example.com");
        let wrong_port = HeaderValue::from_static("https://workbench.example.com:8443");
        assert!(origins.validate(&Method::POST, Some(&http_origin), &cookie).is_err());
        assert!(origins.validate(&Method::POST, Some(&other_host), &cookie).is_err());
        assert!(origins.validate(&Method::POST, Some(&wrong_port), &cookie).is_err());
        let correct = HeaderValue::from_static("https://workbench.example.com");
        assert!(origins.validate(&Method::POST, Some(&correct), &cookie).is_ok());
    }

    #[test]
    fn sso_cookie_kind_treated_like_oauth_session_cookie() {
        let origins = TrustedBrowserOrigins::new(vec!["https://workbench.example.com".into()])
            .expect("valid");
        let sso = ctx(CredentialKind::SsoCookie);
        assert!(origins.validate(&Method::DELETE, None, &sso).is_err());
        let matching = HeaderValue::from_static("https://workbench.example.com");
        assert!(origins.validate(&Method::DELETE, Some(&matching), &sso).is_ok());
    }

    #[test]
    fn explicit_token_kind_does_not_require_origin_even_on_unsafe_methods() {
        let origins = TrustedBrowserOrigins::new(vec![]).expect("empty");
        let token = ctx(CredentialKind::ExplicitToken);
        assert!(origins.validate(&Method::POST, None, &token).is_ok());
        assert!(origins.validate(&Method::DELETE, None, &token).is_ok());
    }
}
