//! Authentication provenance types for the V1 delivery layer.
//!
//! These types record HOW a request was authenticated so that downstream
//! CSRF checks can key off the actual credential kind (`what source actually
//! authenticated the request`) instead of "this request happens to carry a
//! cookie/header" — see `csrf.rs` and `principal.rs::verify_principal`.
//!
//! Production bootstrap still wires ONLY the Gateway verifier after Task 7;
//! OAuth/Sso cookie verifiers arrive in Tasks 8/12. The provenance vocabulary
//! is introduced now so the chain and CSRF can already match its shape.

use bcs_service_api::application::v1::AuthenticatedCaller;

use super::PrincipalVerificationError;

/// Fixed source name for the Gateway signed-Principal verifier.
pub const GATEWAY_PRINCIPAL_SOURCE: &str = "gateway";

/// Logical credential kind that authenticated the request.
///
/// The CSRF rule (csrf.rs::TrustedBrowserOrigins) treats cookie-backed kinds
/// (`OAuthSessionCookie`, `SsoCookie`) as requiring Origin protection on
/// unsafe methods; header/explicit-token kinds do not.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum CredentialKind {
    /// Gateway-signed `x-avernet-principal` JWT header. Production today.
    GatewayPrincipalHeader,
    /// OAuth session cookie (e.g. `bcs_session=...`).
    OAuthSessionCookie,
    /// Single-sign-on cookie (corporate SSO).
    SsoCookie,
    /// Explicitly-supplied bearer token (Authorization: Bearer ...).
    ExplicitToken,
}

/// Metadata captured at the moment a verifier succeeded. Source names come
/// exclusively from bootstrap-injected registration descriptors / verifier
/// internals — they are never settable by request headers. CSRF uses
/// `credential_kind` to decide whether the request is cookie-backed.
#[derive(Clone, Debug)]
pub struct AuthenticationContext {
    pub source: String,
    pub credential_kind: CredentialKind,
}

/// Trusted DISPLAY-ONLY metadata captured at verification time (e.g. the
/// strict OAuth session snapshot's avatar). It is deliberately separate
/// from `caller`: nothing in this struct is authorization input, and
/// consumers may only project it into user-facing response shapes such as
/// `/auth/user` (the OAuth avatar display regression from review
/// 2026-09-20 #7).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct DisplayMetadata {
    pub avatar: Option<String>,
}

/// Result of a successful verification: the business caller identity plus
/// the provenance metadata. The caller is what downstream use cases read;
/// the authentication_context is what the CSRF middleware consults.
#[derive(Clone, Debug)]
pub struct VerifiedRequestIdentity {
    pub caller: AuthenticatedCaller,
    pub authentication_context: AuthenticationContext,
    /// Display-only extras — never consulted by authorization, ownership
    /// or policy checks.
    pub display: DisplayMetadata,
}

/// Per-request cache for the strict OAuth session verifier result.
///
/// Created fresh by every `CompositePrincipalVerifier::verify` call — the
/// cache lives for the current request only. The cached `Result` is reused
/// if multiple verifiers in the chain need the OAuth outcome (so a later
/// cookie verifier does not re-issue the token verification). No token or
/// header value is stored; the cached value is the `Result` only. The
/// attempt struct is never reused across requests.
#[derive(Debug, Default)]
pub struct VerificationAttempt {
    /// Cached outcome of the strict OAuth session verifier. `None` means
    /// "not yet attempted for this request".
    pub oauth_session: Option<Result<VerifiedRequestIdentity, PrincipalVerificationError>>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn credential_kind_equality_is_total_across_all_variants() {
        assert_eq!(CredentialKind::GatewayPrincipalHeader, CredentialKind::GatewayPrincipalHeader);
        assert_ne!(CredentialKind::OAuthSessionCookie, CredentialKind::SsoCookie);
        assert_ne!(CredentialKind::OAuthSessionCookie, CredentialKind::ExplicitToken);
        assert_ne!(CredentialKind::GatewayPrincipalHeader, CredentialKind::ExplicitToken);
    }

    #[test]
    fn verification_attempt_default_starts_with_no_cached_result() {
        let attempt = VerificationAttempt::default();
        assert!(attempt.oauth_session.is_none());
    }

    #[test]
    fn verification_attempt_holds_result_without_storing_tokens() {
        let caller = AuthenticatedCaller {
            tenant: None,
            user: None,
            bot: None,
            app: None,
            access_key: None,
        };
        let verified = VerifiedRequestIdentity {
            caller,
            authentication_context: AuthenticationContext {
                source: "gateway".to_string(),
                credential_kind: CredentialKind::GatewayPrincipalHeader,
            },
            display: Default::default(),
        };
        let mut attempt = VerificationAttempt::default();
        attempt.oauth_session = Some(Ok(verified.clone()));
        assert!(matches!(attempt.oauth_session, Some(Ok(_))));
        attempt.oauth_session = Some(Err(PrincipalVerificationError::Missing));
        assert!(matches!(attempt.oauth_session, Some(Err(PrincipalVerificationError::Missing))));
    }
}
