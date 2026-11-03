//! Provider-independent Caller Identity Plugin API v1.1 conformance.
use bcs_caller_identity_api::{
    CallerCredentialProfile, CallerIdentityError, CallerIdentityPlugin, ExpectedCallerExecution,
    IssueCallerExecution, VerifyCallerExecution,
};

/// The fixture must produce an authorized request for this implementation.
/// The same contract is exercised by local JWT and internal identity adapters.
/// Identity windows are gone: expiry values on this seam are advisory token
/// lifetimes and never a rejection basis.
pub async fn caller_identity_plugin_contract_tests(
    plugin: &dyn CallerIdentityPlugin,
    make_input: impl Fn() -> IssueCallerExecution<'static>,
) {
    let input = make_input();
    let now_ms = input.now_ms;
    let subject = input.subject.expose_ref().to_owned();
    let expected = ExpectedCallerExecution {
        scope: input.scope.clone(), run: input.run.clone(), purpose: input.purpose,
        isolation: input.isolation.clone(),
    };
    let token = plugin.issue_execution(input).await.expect("valid issuance");
    // The advisory lifetime is a positive value strictly after issuance.
    assert!(token.expires_at_ms() > now_ms);
    assert!(!format!("{token:?}").contains(token.expose_token()));
    let verification = plugin.verify_execution(VerifyCallerExecution {
        token, expected, now_ms,
    }).await;
    match plugin.credential_profile() {
        CallerCredentialProfile::ExecutionBound => {
            let verified = verification.expect("exact expectations verify");
            assert_eq!(verified.subject_ref.expose_ref(), subject);
            // Advisory only: verification surfaces the token's own lifetime,
            // never a rejection basis.
            assert!(verified.expires_at_ms > 0);
        }
        CallerCredentialProfile::IdentityRelay => {
            assert_eq!(verification.unwrap_err(), CallerIdentityError::ExecutionVerificationUnsupported);
        }
    }

    let mut invalid = make_input();
    invalid.run.attempt = 0;
    assert_eq!(plugin.issue_execution(invalid).await.unwrap_err(), CallerIdentityError::InvalidRequest);
}
