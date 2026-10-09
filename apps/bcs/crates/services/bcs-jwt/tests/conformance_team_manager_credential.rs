//! Rule 25 driver: `TeamManagerJwtVerifier` — the production
//! `TeamManagerCredentialVerifierPort` implementation (plan Task 13/18).
//!
//! Runs the SHARED port conformance suite against the REAL HS256 boundary
//! with a dedicated `team_manager_sync`-purpose test key; the positive
//! round-trip uses the same issuer API the platform's credential issuer
//! uses. The exhaustive negative lattice keeps living in
//! `tests/team_manager_credential.rs` (plan Task 13); this driver
//! registers the production implementation against the shared harness.

use std::sync::Arc;

use bcs_jwt::team_manager_credential::TeamManagerServiceScopes;
use bcs_jwt::TeamManagerJwtVerifier;
use bcs_service_api::application::v1::team_manager_sync::VerifiedTeamService;
use bcs_service_api::port::TeamManagerCredentialVerifierPort;
use bcs_test_support::contract::port::team_manager_credential::{
    TeamManagerCredentialVerifierHarness, team_manager_credential_verifier_port_contract_tests,
};

const CONFORMANCE_ENV: &str = "conformance-env";
const CONFORMANCE_KEY: &str = "conformance-team-manager-signing-key";

#[test]
fn conformance_team_manager_credential_verifier_over_the_real_hs256_boundary() {
    let signer = TeamManagerJwtVerifier::new(CONFORMANCE_KEY)
        .expect("the dedicated conformance signing key is non-blank material");
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("current time is after the Unix epoch")
        .as_secs();
    let scopes = TeamManagerServiceScopes {
        service_id: "conformance-verifier-service".into(),
        env: CONFORMANCE_ENV.into(),
        allowed_bots: Some(vec!["bot-conformance".into()]),
        allowed_teams: Some(vec!["team-conformance".into()]),
        allowed_operations: None,
    };
    let valid_credential = signer
        .sign_service_credential(&scopes, now, 600)
        .expect("sign the conformance credential");
    let expected = VerifiedTeamService {
        service_id: "conformance-verifier-service".into(),
        env: CONFORMANCE_ENV.into(),
        allowed_bots: Some(vec!["bot-conformance".into()]),
        allowed_teams: Some(vec!["team-conformance".into()]),
        allowed_operations: None,
    };

    let verifier = TeamManagerJwtVerifier::new(CONFORMANCE_KEY)
        .expect("the production verifier builds");
    let harness = TeamManagerCredentialVerifierHarness {
        verifier: Arc::new(verifier),
        valid_credential,
        expected,
    };
    team_manager_credential_verifier_port_contract_tests(&harness);

    // The port trait stays object-safe (the R25 registration surface).
    let _object_safe: Arc<dyn TeamManagerCredentialVerifierPort> = harness.verifier.clone();
}