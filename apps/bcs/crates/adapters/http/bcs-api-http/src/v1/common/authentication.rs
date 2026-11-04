//! Delivery-side authentication provenance (V1 API auth plan, spec §7.4).
//!
//! `AuthenticatedCaller` stays the business identity and gains no fields.
//! Provenance — which registered source actually authenticated this
//! request and with what kind of credential — lives only in these delivery
//! types: the source name is pinned by the registering wrapper
//! (bootstrap-injected), never taken from request headers, cookies, or
//! unverified claims.

use bcs_service_api::application::v1::AuthenticatedCaller;

/// How the actual successful source authenticated the request.
///
/// Cookie-kind credentials are ambient (a browser sends them on every
/// cross-site write), so they require the per-actual-source Origin check
/// before an unsafe request may proceed. Header/token kinds are explicit
/// per-request credentials and do not.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum CredentialKind {
    /// The signed `x-avernet-principal` Gateway header (trusted hop).
    GatewayPrincipalHeader,
    /// The strict, versioned OAuth session cookie (`bcs_session`).
    OAuthSessionCookie,
    /// An SSO-domain cookie shared with the deployment's SSO boundary.
    SsoCookie,
    /// An explicit per-request token (bearer / connection token).
    ExplicitToken,
    /// The legacy native Human browser router and compat current-user GET.
    /// The browser router keeps its existing
    /// `browser_boundary` write protection and is never registered in the
    /// new ordered chain: an ordinary V1/connection-token chain that
    /// nonetheless observes it rejects with `Internal` (interfaces §Delivery).
    LegacyBrowserBoundary,
}

impl CredentialKind {
    /// Cookie-kind credentials (ambient authority) for CSRF purposes.
    pub fn is_cookie_credential(&self) -> bool {
        matches!(self, CredentialKind::OAuthSessionCookie | CredentialKind::SsoCookie)
    }
}

/// The provenance of a verified request: the registered source that
/// actually succeeded, and the credential kind it actually used.
///
/// A plugin supporting several carriers reports the carrier that actually
/// authenticated this request; the name is never inferred from the plugin's
/// static identity nor from client-supplied headers.
#[derive(Clone, Debug)]
pub struct AuthenticationContext {
    pub source: String,
    pub credential_kind: CredentialKind,
}

/// What the delivery boundary hands downstream: the unchanged business
/// caller plus the pinned authentication provenance. The middleware pins
/// this on first success; application/core code never consumes it.
#[derive(Clone, Debug)]
pub struct VerifiedRequestIdentity {
    pub caller: AuthenticatedCaller,
    pub authentication_context: AuthenticationContext,
    /// Optional verified-source display data, never authorization input.
    /// OAuth sources copy it from the existing session snapshot.
    pub avatar: Option<String>,
}

impl VerifiedRequestIdentity {
    /// Pin provenance alongside a verified caller. Used by registering
    /// wrappers that own their fixed source name.
    pub fn pin(caller: AuthenticatedCaller, source: impl Into<String>, credential_kind: CredentialKind) -> Self {
        Self {
            caller,
            authentication_context: AuthenticationContext {
                source: source.into(),
                credential_kind,
            },
            avatar: None,
        }
    }

    /// Attach display data from the same verification result as the caller.
    /// This must not trigger another credential lookup or change provenance.
    pub fn with_avatar(mut self, avatar: Option<String>) -> Self {
        self.avatar = avatar;
        self
    }
}

/// Request-local scratch space for one ordered verification pass, created
/// by `CompositePrincipalVerifier` per `verify` call and passed to every
/// verifier's `verify_in_attempt`.
///
/// It holds only the strict-OAuth outcome for THIS request, so a chain in
/// which the OAuth-backed source is consulted more than once (cookie read
/// after a Missing, re-ordered attempts…) cannot re-run the strict engine
/// for the same request. It stores no token material and is never reused
/// across requests.
#[derive(Default)]
pub struct VerificationAttempt {
    oauth_session: Option<Result<VerifiedRequestIdentity, super::PrincipalVerificationError>>,
}

impl VerificationAttempt {
    pub fn new() -> Self {
        Self::default()
    }

    /// The cached strict-OAuth outcome for this request, if the source ran.
    pub fn cached_oauth_session(
        &self,
    ) -> Option<Result<VerifiedRequestIdentity, super::PrincipalVerificationError>> {
        self.oauth_session.clone()
    }

    /// Cache the strict-OAuth outcome for this request. Overwrites are
    /// idempotent for the same request; there is exactly one live instance
    /// per chain pass.
    pub fn store_oauth_session(
        &mut self,
        outcome: Result<VerifiedRequestIdentity, super::PrincipalVerificationError>,
    ) {
        self.oauth_session = Some(outcome);
    }
}
