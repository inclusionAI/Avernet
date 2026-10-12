//! `TeamManagerCredentialVerifierPort` shared conformance harness (plan
//! Task 18 R25, plan 公共命名:
//! `team_manager_credential_verifier_port_contract_tests`).
//!
//! The driver supplies the PRODUCTION verifier plus ONE validly signed
//! credential and its expected verified shape; the suite pins the port's
//! fail-closed contract:
//!
//! - a blank credential is rejected (never accepted);
//! - a malformed/unverifiable credential is the FORBIDDEN deny branch —
//!   never a silent `Ok`, and the fixed message never embeds the
//!   credential or the key;
//! - the valid credential verifies to EXACTLY the driver's expected
//!   service identity + scopes (the verified result is the only accepted
//!   actor shape).

use std::sync::Arc;

use bcs_service_api::port::TeamManagerCredentialVerifierPort;
use bcs_service_api::types::error::AuthorityError;
use bcs_service_api::{ServiceError, ServiceResult};
use bcs_service_api::application::v1::team_manager_sync::VerifiedTeamService;

/// Driver-supplied wiring bundle: the production verifier, one
/// driver-minted valid credential, and the exact verified shape it must
/// reproduce.
pub struct TeamManagerCredentialVerifierHarness {
    /// The driver's production verifier (`TeamManagerJwtVerifier` or an
    /// equivalent port implementation).
    pub verifier: Arc<dyn TeamManagerCredentialVerifierPort>,
    /// A credential legitimately minted for this harness's expectations.
    pub valid_credential: String,
    /// The verified service identity + scopes `valid_credential` must
    /// reproduce exactly.
    pub expected: VerifiedTeamService,
}

/// The shared `TeamManagerCredentialVerifierPort` conformance suite.
pub fn team_manager_credential_verifier_port_contract_tests(
    h: &TeamManagerCredentialVerifierHarness,
) {
    // Blank credentials never verify (the transport's 401 lane; the port
    // must not turn a missing credential into a scope decision).
    let error = h
        .verifier
        .verify("  ")
        .err()
        .unwrap_or_else(|| panic!("blank credentials must never verify"));
    assert!(
        matches!(
            error,
            ServiceError::Authority(AuthorityError::Forbidden(_)) | ServiceError::Unauthorized(_)
        ),
        "blank credentials stay a deny branch: {error:?}"
    );

    // Malformed/unverifiable credentials: the fail-closed Forbidden
    // family, message free of credential material.
    let bogus = "not.a.team-manager.credential";
    let error = h
        .verifier
        .verify(bogus)
        .err()
        .unwrap_or_else(|| panic!("an unverifiable credential must never verify"));
    assert!(
        matches!(&error, ServiceError::Authority(AuthorityError::Forbidden(_))),
        "unverifiable credentials are the Forbidden deny branch: {error:?}"
    );
    let message = error.to_string();
    assert!(
        !message.contains(bogus),
        "the fixed message never embeds the credential: {message}"
    );

    // The legitimate credential reproduces the expected verified shape
    // exactly — same service id, env and scopes.
    let verified = h
        .verifier
        .verify(&h.valid_credential)
        .expect("the valid credential verifies");
    assert_eq!(verified, h.expected, "the verified shape matches byte-for-byte");

    // Fail-closed typing: a successful verification keeps the<ServiceResult>
    // channel honest (compile-time anchor; object-safe value stays boxed).
    let _: ServiceResult<VerifiedTeamService> = Ok(verified);
}