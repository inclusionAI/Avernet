//! Ordered principal-verifier chain.
//!
//! `CompositePrincipalVerifier::new(verifiers)` runs the verifiers in the
//! GIVEN order (config order; never AuthPlugin::priority). A verifier
//! returning `Missing` continues to the next verifier; ANY other outcome
//! (Ok or Err other than Missing) ends the chain immediately — there is NO
//! fallback after a successful Ok or after an Invalid/Forbidden/Unavailable/
//! Internal. All-Missing → Missing.
//!
//! The per-request `VerificationAttempt` is created fresh inside every
//! `verify` call so the cached OAuth result is never reused across requests.

use std::sync::Arc;

use async_trait::async_trait;
use axum::http::HeaderMap;

use super::PrincipalVerificationError;
use super::PrincipalVerifier;
use super::authentication::{VerifiedRequestIdentity, VerificationAttempt};

/// Chain of verifiers executed in config order. Production bootstrap still
/// wires ONLY the Gateway verifier today; OAuth/Sso cookie verifiers arrive
/// in Tasks 8/12. The chain behavior is fixed by Task 7's plan vocabulary
/// and must not change when more verifiers are added.
pub struct CompositePrincipalVerifier {
    verifiers: Vec<Arc<dyn PrincipalVerifier>>,
}

impl CompositePrincipalVerifier {
    pub fn new(verifiers: Vec<Arc<dyn PrincipalVerifier>>) -> Self {
        Self { verifiers }
    }

    /// Read-only view of the configured verifiers. Useful for diagnostics and
    /// tests; never mutated at runtime.
    pub fn verifiers(&self) -> &[Arc<dyn PrincipalVerifier>] {
        &self.verifiers
    }
}

#[async_trait]
impl PrincipalVerifier for CompositePrincipalVerifier {
    async fn verify(
        &self,
        headers: &HeaderMap,
    ) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
        let mut attempt = VerificationAttempt::default();
        for verifier in &self.verifiers {
            match verifier.verify_in_attempt(headers, &mut attempt).await {
                Ok(identity) => return Ok(identity),
                Err(PrincipalVerificationError::Missing) => continue,
                Err(other) => return Err(other),
            }
        }
        Err(PrincipalVerificationError::Missing)
    }
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Mutex;

    use axum::http::HeaderValue;
    use bcs_service_api::application::v1::AuthenticatedCaller;

    use super::*;
    use super::super::authentication::{AuthenticationContext, CredentialKind};

    /// Counting fake verifier that records calls and returns a pre-scripted
    /// outcome. `verify_in_attempt` is overridden only when the test needs
    /// the OAuth-cache path; otherwise the default-delegating path is used.
    struct Counting {
        outcome: Mutex<Result<VerifiedRequestIdentity, PrincipalVerificationError>>,
        calls: Arc<AtomicUsize>,
        is_oauth: bool,
    }

    impl Counting {
        fn ok_gateway() -> Self {
            Self::ok_with("gateway", CredentialKind::GatewayPrincipalHeader)
        }
        fn ok_with(source: &str, kind: CredentialKind) -> Self {
            Self {
                outcome: Mutex::new(Ok(VerifiedRequestIdentity {
                    caller: AuthenticatedCaller {
                        tenant: None,
                        user: None,
                        bot: None,
                        app: None,
                        access_key: None,
                    },
                    authentication_context: AuthenticationContext {
                        source: source.to_string(),
                        credential_kind: kind,
                    },
                    display: Default::default(),
                })),
                calls: Arc::new(AtomicUsize::new(0)),
                is_oauth: false,
            }
        }
        fn err(kind: PrincipalVerificationError) -> Self {
            Self {
                outcome: Mutex::new(Err(kind)),
                calls: Arc::new(AtomicUsize::new(0)),
                is_oauth: false,
            }
        }
        fn calls(&self) -> usize {
            self.calls.load(Ordering::SeqCst)
        }
    }

    #[async_trait]
    impl PrincipalVerifier for Counting {
        async fn verify(
            &self,
            _headers: &HeaderMap,
        ) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
            self.calls.fetch_add(1, Ordering::SeqCst);
            self.outcome
                .lock()
                .expect("outcome lock")
                .clone()
                .map_err(|e| {
                    // Clone-safe: PrincipalVerificationError is Clone.
                    PrincipalVerificationError::clone(&e)
                })
        }

        async fn verify_in_attempt(
            &self,
            _headers: &HeaderMap,
            attempt: &mut VerificationAttempt,
        ) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
            if !self.is_oauth {
                return self.verify(_headers).await;
            }
            if attempt.oauth_session.is_none() {
                self.calls.fetch_add(1, Ordering::SeqCst);
                let outcome = self
                    .outcome
                    .lock()
                    .expect("outcome lock")
                    .clone()
                    .map_err(|e| PrincipalVerificationError::clone(&e));
                attempt.oauth_session = Some(outcome);
            }
            match &attempt.oauth_session {
                Some(Ok(id)) => Ok(id.clone()),
                Some(Err(e)) => Err(PrincipalVerificationError::clone(e)),
                None => Err(PrincipalVerificationError::Internal),
            }
        }
    }

    fn empty_headers() -> HeaderMap {
        HeaderMap::new()
    }

    fn headers_with_principal() -> HeaderMap {
        let mut headers = HeaderMap::new();
        headers.insert("x-avernet-principal", HeaderValue::from_static("dummy"));
        headers
    }

    #[tokio::test]
    async fn first_success_stops_the_chain_no_fallback_to_a_later_verifier() {
        let gateway = Arc::new(Counting::ok_gateway());
        let cookie = Arc::new(Counting::err(PrincipalVerificationError::Invalid));
        let composite = CompositePrincipalVerifier::new(vec![
            gateway.clone() as Arc<dyn PrincipalVerifier>,
            cookie.clone() as Arc<dyn PrincipalVerifier>,
        ]);
        let verified = composite.verify(&headers_with_principal()).await.expect("Ok");
        assert_eq!(gateway.calls(), 1);
        assert_eq!(cookie.calls(), 0, "later verifier must NOT be consulted after success");
        assert_eq!(verified.authentication_context.source, "gateway");
        assert_eq!(
            verified.authentication_context.credential_kind,
            CredentialKind::GatewayPrincipalHeader
        );
    }

    #[tokio::test]
    async fn gateway_invalid_stops_chain_with_invalid_no_fallback_to_cookie() {
        let gateway = Arc::new(Counting::err(PrincipalVerificationError::Invalid));
        let cookie = Arc::new(Counting::ok_with("github", CredentialKind::OAuthSessionCookie));
        let composite = CompositePrincipalVerifier::new(vec![
            gateway.clone() as Arc<dyn PrincipalVerifier>,
            cookie.clone() as Arc<dyn PrincipalVerifier>,
        ]);
        let err = composite
            .verify(&empty_headers())
            .await
            .expect_err("Invalid stops the chain");
        assert!(matches!(err, PrincipalVerificationError::Invalid));
        assert_eq!(gateway.calls(), 1);
        assert_eq!(cookie.calls(), 0, "cookie verifier must NOT fall back after Invalid");
    }

    #[tokio::test]
    async fn all_missing_returns_missing() {
        let a = Arc::new(Counting::err(PrincipalVerificationError::Missing));
        let b = Arc::new(Counting::err(PrincipalVerificationError::Missing));
        let composite = CompositePrincipalVerifier::new(vec![
            a.clone() as Arc<dyn PrincipalVerifier>,
            b.clone() as Arc<dyn PrincipalVerifier>,
        ]);
        let err = composite.verify(&empty_headers()).await.expect_err("Missing");
        assert!(matches!(err, PrincipalVerificationError::Missing));
        assert_eq!(a.calls(), 1);
        assert_eq!(b.calls(), 1);
    }

    #[tokio::test]
    async fn forbidden_and_unavailable_and_internal_stop_the_chain_immediately() {
        for terminal in [
            PrincipalVerificationError::Forbidden,
            PrincipalVerificationError::Unavailable,
            PrincipalVerificationError::Internal,
        ] {
            let first = Arc::new(Counting::err(PrincipalVerificationError::clone(&terminal)));
            let second = Arc::new(Counting::ok_gateway());
            let composite = CompositePrincipalVerifier::new(vec![
                first.clone() as Arc<dyn PrincipalVerifier>,
                second.clone() as Arc<dyn PrincipalVerifier>,
            ]);
            let err = composite.verify(&empty_headers()).await.expect_err("terminal");
            assert!(std::mem::discriminant(&err) == std::mem::discriminant(&terminal));
            assert_eq!(first.calls(), 1);
            assert_eq!(second.calls(), 0, "later verifier not consulted after terminal");
        }
    }

    #[tokio::test]
    async fn missing_continues_to_next_verifier_until_success() {
        let first_missing = Arc::new(Counting::err(PrincipalVerificationError::Missing));
        let success = Arc::new(Counting::ok_gateway());
        let composite = CompositePrincipalVerifier::new(vec![
            first_missing.clone() as Arc<dyn PrincipalVerifier>,
            success.clone() as Arc<dyn PrincipalVerifier>,
        ]);
        let _ = composite.verify(&headers_with_principal()).await.expect("Ok");
        assert_eq!(first_missing.calls(), 1);
        assert_eq!(success.calls(), 1);
    }
}
