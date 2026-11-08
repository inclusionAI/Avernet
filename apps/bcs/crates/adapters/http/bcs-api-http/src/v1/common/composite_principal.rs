//! Ordered, registration-driven verification chain (V1 API auth plan,
//! Task 7).
//!
//! `CompositePrincipalVerifier` runs the registered verifiers in the exact
//! order bootstrap assembled them. Only `Missing` continues to the next
//! source; the first success pins provenance and ends the chain; the first
//! non-Missing failure is final and never falls through. The
//! `LegacyBrowserBoundary` credential kind may not be registered in this
//! chain: if a verifier produces it anyway, the composite rejects with
//! `Internal` (spec §7.4, interfaces §Delivery).

use std::sync::Arc;

use async_trait::async_trait;
use axum::http::HeaderMap;

use super::{
    CredentialKind, PrincipalVerificationError, PrincipalVerifier, VerificationAttempt,
    VerifiedRequestIdentity,
};

pub struct CompositePrincipalVerifier {
    verifiers: Vec<Arc<dyn PrincipalVerifier>>,
}

impl CompositePrincipalVerifier {
    /// Assemble in dispatch order. The order is bootstrap's registry order;
    /// no priority is inferred from configuration or request content.
    pub fn new(verifiers: Vec<Arc<dyn PrincipalVerifier>>) -> Self {
        Self { verifiers }
    }

    /// Run the chain against one request-local attempt. First success wins;
    /// only `Missing` continues; non-Missing failures abort without calling
    /// later sources; all-Missing is `Missing` (401 for the caller). An
    /// empty chain is a wiring bug and behaves like all-Missing.
    pub async fn verify_with_attempt(
        &self,
        headers: &HeaderMap,
        attempt: &mut VerificationAttempt,
    ) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
        for verifier in &self.verifiers {
            match verifier.verify_in_attempt(headers, attempt).await {
                Ok(identity) => {
                    // The new registry never accepts the legacy native
                    // Human boundary; ordinary V1/connection-token chains
                    // treat it as an internal wiring error (spec §7.4).
                    if identity.authentication_context.credential_kind
                        == CredentialKind::LegacyBrowserBoundary
                    {
                        return Err(PrincipalVerificationError::Internal(
                            "legacy browser boundary credentials cannot be registered in the V1 chain"
                                .into(),
                        ));
                    }
                    return Ok(identity);
                }
                Err(PrincipalVerificationError::Missing) => continue,
                Err(error) => return Err(error),
            }
        }
        Err(PrincipalVerificationError::Missing)
    }
}

#[async_trait]
impl PrincipalVerifier for CompositePrincipalVerifier {
    async fn verify(
        &self,
        headers: &HeaderMap,
    ) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
        // One composite pass, one attempt. The attempt is request-local and
        // is never stored on this process-shared verifier object.
        let mut attempt = VerificationAttempt::new();
        self.verify_with_attempt(headers, &mut attempt).await
    }

    async fn verify_in_attempt(
        &self,
        headers: &HeaderMap,
        attempt: &mut VerificationAttempt,
    ) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
        self.verify_with_attempt(headers, attempt).await
    }
}
