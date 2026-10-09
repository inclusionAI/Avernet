//! Rule 25 driver: `TeamManagerSyncServiceImpl` (plan Task 13/18).
//!
//! Runs the SHARED `TeamManagerSyncService` conformance suite against the
//! PRODUCTION team-sync facade over the REAL bcs-jwt verifier with a
//! dedicated test key, and additionally drives the facade's POSITIVE
//! credential round-trip through the same verifier (the verified identity
//! + scopes are the only actor shape the facade forwards).

use std::sync::Arc;

use bcs_app_bot::{TeamManagerSyncServiceConfig, TeamManagerSyncServiceImpl};
use bcs_jwt::team_manager_credential::TeamManagerServiceScopes;
use bcs_jwt::TeamManagerJwtVerifier;
use bcs_service_api::application::v1::team_manager_sync::VerifiedTeamService;
use bcs_service_api::port::TeamManagerCredentialVerifierPort;
use bcs_test_support::contract::application::team_manager_sync::{
    TeamManagerSyncServiceHarness, team_manager_sync_service_contract_tests,
};
use bcs_test_support::CountingBotAuthorityCore;

const CONFORMANCE_ENV: &str = "conformance-env";

#[tokio::test]
async fn conformance_team_manager_sync_over_the_production_facade() {
    let verifier = Arc::new(TeamManagerJwtVerifier::new("conformance-team-signing-key").expect(
        "the dedicated conformance signing key is non-blank material",
    ));
    let core = Arc::new(CountingBotAuthorityCore::fail_closed());
    let service = Arc::new(TeamManagerSyncServiceImpl::new(
        core.clone(),
        verifier.clone(),
        TeamManagerSyncServiceConfig {
            env: CONFORMANCE_ENV.to_string(),
        },
    ));

    let harness = TeamManagerSyncServiceHarness {
        service: service.clone(),
        verifier: verifier.clone(),
        core,
        env: CONFORMANCE_ENV.to_string(),
    };
    team_manager_sync_service_contract_tests(&harness).await;

    // Positive round-trip through the REAL verifier lane: the facade
    // verifies a legitimately minted credential to the exact verified
    // identity + scopes (body-supplied actors never enter the lane).
    let issuer = TeamManagerJwtVerifier::new("conformance-team-signing-key")
        .expect("the same signing material");
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("current time is after the Unix epoch")
        .as_secs();
    let scopes = TeamManagerServiceScopes {
        service_id: "conformance-sync-service".into(),
        env: CONFORMANCE_ENV.into(),
        allowed_bots: Some(vec!["bot-conformance".into()]),
        allowed_teams: Some(vec!["team-conformance".into()]),
        allowed_operations: None,
    };
    let credential = issuer
        .sign_service_credential(&scopes, now, 600)
        .expect("sign the conformance credential");
    let verified = harness
        .service
        .verify_service_credential(&credential)
        .await
        .expect("the facade verifies a legitimately minted credential");
    let expected = VerifiedTeamService {
        service_id: "conformance-sync-service".into(),
        env: CONFORMANCE_ENV.into(),
        allowed_bots: Some(vec!["bot-conformance".into()]),
        allowed_teams: Some(vec!["team-conformance".into()]),
        allowed_operations: None,
    };
    assert_eq!(verified, expected, "the verified shape is exactly the credential's claims");
}