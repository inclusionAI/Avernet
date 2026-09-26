//! V1 OAuth service-api port contract harnesses.
//!
//! Each harness exercises one of the V1 OpenAPI auth ports defined in
//! `bcs_service_api::port::oauth`. The harness is object-safe (`&dyn
//! Port`) and verifies the trait protocol shape against any concrete
//! implementation or recording fake. The conformance drivers live in
//! `crates/application/v1/bcs-app-auth/tests/conformance_*.rs`.

use bcs_service_api::port::oauth::{
    ExternalLoginIdentity, OAuthProviderPort, OAuthSessionPort, PendingOAuthLoginPort,
};

/// `OAuthProviderPort` contract suite.
///
/// The harness exercises three behaviors that every implementation MUST
/// satisfy under the supplied sample inputs (the conformance test
/// pre-programs its fake/real bridge to respond to these inputs):
///
/// 1. `names()` returns the configured chain subsequence, non-empty.
/// 2. `auth_url(provider, state, redirect_uri)` succeeds and embeds
///    both the `state` and the `redirect_uri` in the returned URL (the
///    canonical OAuth authorize-URL shape — the bridge MUST NOT drop
///    these values).
/// 3. `exchange_user(provider, code, redirect_uri)` returns a
///    [`ExternalLoginIdentity`] whose `provider` field equals the
///    requested `provider` (the bridge MUST preserve the configured
///    chain label, not invent one).
pub async fn o_auth_provider_port_contract_tests(
    port: &dyn OAuthProviderPort,
    sample_provider: &str,
    sample_state: &str,
    sample_redirect: &str,
    sample_code: &str,
) {
    let names = port.names().await;
    assert!(
        !names.is_empty(),
        "OAuthProviderPort::names MUST return the non-empty configured chain subsequence"
    );
    assert!(
        names.iter().any(|n| n == sample_provider),
        "OAuthProviderPort::names MUST include the supplied sample provider {sample_provider}; got {names:?}"
    );

    let url = port
        .auth_url(sample_provider, sample_state, sample_redirect)
        .await
        .expect("auth_url under a valid provider/state/redirect MUST return Ok");
    assert!(
        !url.is_empty(),
        "auth_url MUST return a non-empty authorize URL"
    );
    assert!(
        url.contains(sample_state),
        "auth_url MUST embed the supplied state parameter in the URL string"
    );
    assert!(
        url.contains(sample_redirect),
        "auth_url MUST embed the supplied redirect_uri in the URL string"
    );

    let identity = port
        .exchange_user(sample_provider, sample_code, sample_redirect)
        .await
        .expect("exchange_user under a valid code MUST return Ok");
    assert_eq!(
        identity.provider, sample_provider,
        "exchange_user MUST echo the configured provider label (not invent one)"
    );
    assert!(
        !identity.external_user_id.is_empty(),
        "exchange_user MUST return a non-empty external_user_id"
    );
}

/// `OAuthSessionPort` contract suite.
///
/// Exercises three lifecycle behaviors that every implementation MUST
/// satisfy:
///
/// 1. `install_identity(identity, now)` returns a [`BrowserSession`]
///    with a non-empty token and an expiry strictly greater than `now`.
/// 2. `refresh(token, now)` against the just-issued token returns
///    `Ok(BrowserSession)` with a non-empty token (the harness does
///    NOT assume the token differs from the install token — the
///    bridge MAY rotate or preserve it; either is contract-compliant
///    per spec §8.5).
/// 3. `revoke(token)` returns `Ok` (idempotent success — the spec §8.5
///    explicitly collapses store outcomes `Revoked`/`NotCurrent` into
///    success at this port layer). The harness calls revoke twice to
///    verify the idempotent invariant.
pub async fn o_auth_session_port_contract_tests(port: &dyn OAuthSessionPort) {
    let identity = ExternalLoginIdentity {
        provider: "contract".to_string(),
        external_user_id: "contract-external-uid".to_string(),
        name: Some("Contract Harness".to_string()),
        avatar: None,
    };
    let now: u64 = 1_000_000_000;

    let installed = port
        .install_identity(identity, now)
        .await
        .expect("install_identity under a valid identity MUST return Ok");
    assert!(
        !installed.token.is_empty(),
        "install_identity MUST return a non-empty session token"
    );
    assert!(
        installed.expires_at > now,
        "install_identity MUST return an expiry strictly greater than now"
    );

    let refreshed = port
        .refresh(&installed.token, now + 1)
        .await
        .expect("refresh against a freshly issued token MUST return Ok");
    assert!(
        !refreshed.token.is_empty(),
        "refresh MUST return a non-empty session token"
    );
    assert!(
        refreshed.expires_at > now,
        "refresh MUST return an expiry in the future relative to the issue `now`"
    );

    port.revoke(&installed.token)
        .await
        .expect("revoke against a freshly issued token MUST return Ok (idempotent success)");
    // The second revoke MUST NOT error — store outcomes Revoked /
    // NotCurrent are both success at this port layer.
    port.revoke(&installed.token)
        .await
        .expect("revoke called twice MUST be idempotent (Revoked/NotCurrent both Ok)");
}

/// `PendingOAuthLoginPort` contract suite.
///
/// Exercises three behaviors:
///
/// 1. `issue_batch(providers, callback_base, flow, now)` returns a
///    [`PendingLoginBatch`] with: a non-empty `browser_nonce`, an
///    `expires_at > now`, and exactly one `provider_states` entry per
///    requested provider in the requested order.
/// 2. A full-matching `consume(...)`` returns `Ok(())` (the atomic
///    consume).
/// 3. A consume with a wrong `browser_nonce` against a valid state
///    returns `Err(ApplicationError)` AND does NOT consume the state
///    (a subsequent correct-nonce consume MUST still succeed — the
///    cross-browser defense per spec §8.4).
#[allow(
    clippy::expect_used,
    clippy::unwrap_used,
    reason = "test harness — panic on contract violation"
)]
pub async fn pending_o_auth_login_port_contract_tests(port: &dyn PendingOAuthLoginPort) {
    let providers = vec!["github".to_string()];
    let callback_base = "https://bcs.example.com/openapi/v1/auth";
    let flow = "v1";
    let now: u64 = 1_000_000;

    let batch = port
        .issue_batch(&providers, callback_base, flow, now)
        .await
        .expect("issue_batch under valid inputs MUST return Ok");
    assert!(
        !batch.browser_nonce.is_empty(),
        "issue_batch MUST return a non-empty browser nonce"
    );
    assert!(
        batch.expires_at > now,
        "issue_batch MUST return an expiry strictly greater than now"
    );
    assert_eq!(
        batch.provider_states.len(),
        providers.len(),
        "issue_batch MUST return one state per requested provider"
    );
    for (i, (name, state)) in batch.provider_states.iter().enumerate() {
        assert_eq!(
            name, &providers[i],
            "provider_states order MUST match the requested providers order"
        );
        assert!(
            !state.is_empty(),
            "issue_batch MUST return a non-empty state per provider"
        );
    }

    // Full-match consume → Ok
    let (provider, state) = &batch.provider_states[0];
    let exact_callback = format!("{callback_base}/{provider}");
    port.consume(
        state,
        &batch.browser_nonce,
        provider,
        &exact_callback,
        flow,
        now + 1,
    )
    .await
    .expect("full-match consume MUST succeed");

    // Issue a second batch for the cross-browser/nonce-mismatch
    // defense — the first batch was consumed.
    let batch2 = port
        .issue_batch(&providers, callback_base, flow, now + 2)
        .await
        .expect("second issue_batch MUST succeed");
    let (p2, s2) = &batch2.provider_states[0];
    let cb2 = format!("{callback_base}/{p2}");

    // Wrong nonce against valid state — must err AND must NOT consume
    // the batch.
    let _ = port
        .consume(s2, "wrong-nonce", p2, &cb2, flow, now + 3)
        .await
        .expect_err("wrong-nonce consume MUST be rejected");

    // Subsequent correct-nonce consume MUST still succeed — the wrong
    // attempt did NOT consume the batch.
    port.consume(
        s2,
        &batch2.browser_nonce,
        p2,
        &cb2,
        flow,
        now + 4,
    )
    .await
    .expect("correct-nonce consume after a wrong-nonce attempt MUST succeed");
}
