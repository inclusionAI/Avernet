//! V1 API auth application-port conformance harnesses (plugin chain, plan
//! Task 14 / R25).
//!
//! The three `bcs_service_api::port::oauth` ports are the ONLY surface the
//! auth application layer consumes for login. The harness names are the
//! R25 gate's canonical `to_snake` of the trait names (e.g.
//! `OAuthProviderPort` → `o_auth_provider_port_contract_tests`), so the
//! gate greps for exactly these declarations.
//!
//! The harnesses assert the PORT-level contract — properties every
//! correct implementation must hold (bootstrap's production bridges drive
//! them in `crates/bootstrap/bcs/tests/conformance_api_auth_ports.rs`):
//!
//! - `OAuthProviderPort`: `names()` is the enabled chain (unique, in
//!   order); the state reaches the authorize URL (two states never map to
//!   one URL); a provider outside the chain is rejected on BOTH
//!   `auth_url` and `exchange_user` before any provider work;
//! - `OAuthSessionPort` (plan §8.5, at the port vocabulary): a fresh login
//!   installs; the SAME-instant rotation still rotates the credential
//!   (the revision lives in the claims); revoke commits for the current
//!   instance, then the revoked credential is dead; a token that does not
//!   verify never revokes anything; a repeated revoke of the dead
//!   instance is an idempotent success;
//! - `PendingOAuthLoginPort` (plan §8.4, at the port vocabulary): issue
//!   keeps provider order and pairs a fresh nonce with one state per
//!   provider; a consume under the WRONG nonce is rejected WITHOUT
//!   invalidating the batch; the genuine consume works; any consume of
//!   the batch (replay, second provider) is dead afterwards; re-issue
//!   rotates the nonce; batches stay independent; an empty provider list
//!   never yields a batch.
//!
//! Redaction discipline (plan §8.4): assertions never format replies
//! (`assert_ne!` on tokens would print them) — every check uses `matches!`
//! or boolean predicates, so failures print fixed category text only.

use bcs_service_api::port::oauth::{
    ExternalLoginIdentity, OAuthProviderPort, OAuthSessionPort, PendingOAuthLoginPort,
};

const HARNESS_REDIRECT_URI: &str =
    "https://harness.example/openapi/v1/auth/callback/harness_provider";
const NOW: u64 = 1_800_000_000;

/// `OAuthProviderPort` contract driver.
#[allow(clippy::expect_used, reason = "test harness — panic on failure is the contract")]
pub async fn o_auth_provider_port_contract_tests(port: &dyn OAuthProviderPort) {
    let names = port.names();
    assert!(
        !names.is_empty(),
        "driver contract: at least one provider must be enabled"
    );
    let mut seen: Vec<&str> = Vec::with_capacity(names.len());
    for name in &names {
        assert!(!name.trim().is_empty(), "an enabled provider name is blank");
        assert!(
            !seen.contains(&name.as_str()),
            "the enabled chain repeats provider {name}"
        );
        seen.push(name);

        let first = port
            .auth_url(name, "harness-state-alpha", HARNESS_REDIRECT_URI)
            .await
            .expect("auth_url succeeds for an enabled provider");
        assert!(!first.is_empty());
        let second = port
            .auth_url(name, "harness-state-beta", HARNESS_REDIRECT_URI)
            .await
            .expect("auth_url succeeds for an enabled provider");
        assert!(
            first != second,
            "the state must reach the authorize URL (two states, one provider, one redirect)"
        );

        // A provider outside the chain is rejected on both entries — the
        // bridge/chain never falls through to a provider it did not register.
        let unenrolled = format!("{name}-harness-not-in-chain");
        assert!(
            port.auth_url(&unenrolled, "harness-state", HARNESS_REDIRECT_URI)
                .await
                .is_err(),
            "auth_url must reject a provider outside the enabled chain"
        );
        assert!(
            port.exchange_user(&unenrolled, "harness-code", HARNESS_REDIRECT_URI)
                .await
                .is_err(),
            "exchange_user must reject a provider outside the enabled chain"
        );
    }
}

/// `OAuthSessionPort` contract driver — the §8.5 atomic lifecycle through
/// the port's own vocabulary (install → rotate → revoke).
#[allow(clippy::expect_used, reason = "test harness — panic on failure is the contract")]
pub async fn o_auth_session_port_contract_tests(port: &dyn OAuthSessionPort) {
    let identity = ExternalLoginIdentity {
        provider: "session-contract-provider".to_string(),
        external_user_id: "session-contract-ext-user".to_string(),
        name: Some("Session Contract User".to_string()),
        avatar: None,
    };

    // New login: install commits and issues a non-empty credential valid
    // into the future.
    let installed = port
        .install_identity(identity.clone(), NOW)
        .await
        .expect("install applies for a fresh login identity");
    assert!(!installed.token.is_empty());
    assert!(installed.expires_at > NOW);

    // Same-instant rotation still rotates: the revision inside the claims
    // makes every issue a different credential.
    let rotated = port
        .refresh(&installed.token, NOW)
        .await
        .expect("refresh rotates a live session");
    assert!(
        rotated.token != installed.token,
        "the same-second rotation must yield a different credential (revision)"
    );
    let rotated_twice = port
        .refresh(&rotated.token, NOW)
        .await
        .expect("the rotated session refreshes again");
    assert!(
        rotated_twice.token != rotated.token,
        "every rotation yields a new credential"
    );
    assert!(
        rotated_twice.expires_at >= rotated.expires_at,
        "rotation never shortens the trusted window below the same instant"
    );

    // Revoke commits for the current instance; the revoked credential is
    // dead and a token that does not verify never revokes anything; the
    // repeated revoke of the dead instance is an idempotent success.
    port.revoke(&rotated_twice.token)
        .await
        .expect("revoke of the current session instance commits");
    assert!(
        port.refresh(&rotated_twice.token, NOW).await.is_err(),
        "a revoked credential must never refresh again"
    );
    port.revoke(&rotated_twice.token)
        .await
        .expect("repeated revoke of the superseded instance is an idempotent success");
    assert!(
        port.revoke("harness-unverifiable-session-token").await.is_err(),
        "a token that does not verify must never revoke anything"
    );

    // A lost race at the port: refreshing the rotated-out ancestor
    // credential (the one `rotated` replaced) must not succeed either —
    // only ONE lineage stays live.
    assert!(
        port.refresh(&installed.token, NOW).await.is_err(),
        "a rotated-out credential must not refresh after its descendant exists"
    );
}

/// `PendingOAuthLoginPort` contract driver — the §8.4 browser-bound
/// batch semantics through the port's own vocabulary.
#[allow(clippy::expect_used, reason = "test harness — panic on failure is the contract")]
pub async fn pending_o_auth_login_port_contract_tests(port: &dyn PendingOAuthLoginPort) {
    const CALLBACK_BASE: &str = "https://harness.example/openapi/v1/auth/callback";
    const EXACT_GITHUB: &str = "https://harness.example/openapi/v1/auth/callback/github";
    const EXACT_GOOGLE: &str = "https://harness.example/openapi/v1/auth/callback/google";
    const FLOW: &str = "conformance";

    let batch = port
        .issue_batch(
            &["github".to_string(), "google".to_string()],
            CALLBACK_BASE,
            FLOW,
            NOW,
        )
        .await
        .expect("issue succeeds for a two-provider chain");
    assert_eq!(
        batch.provider_states.len(),
        2,
        "one state per requested provider, chain order kept"
    );
    assert_eq!(batch.provider_states[0].0, "github");
    assert_eq!(batch.provider_states[1].0, "google");
    assert!(!batch.browser_nonce.is_empty());
    assert!(batch.expires_at > NOW);
    let (github_state, google_state) = (
        batch.provider_states[0].1.clone(),
        batch.provider_states[1].1.clone(),
    );
    assert!(github_state != batch.browser_nonce, "state != own nonce");
    assert!(github_state != google_state, "provider states are distinct");

    // Cross-browser consume is rejected WITHOUT burning the batch; the
    // initiating browser then completes.
    assert!(
        port.consume(&github_state, "harness-wrong-nonce", "github", EXACT_GITHUB, FLOW, NOW + 1)
            .await
            .is_err(),
        "another browser's nonce must not consume this batch"
    );
    assert!(
        port.consume(&github_state, &batch.browser_nonce, "github", EXACT_GITHUB, FLOW, NOW + 1)
            .await
            .is_ok(),
        "the initiating browser can still complete after a cross-browser attempt"
    );

    // The batch is dead: replay and any other provider state are dead.
    assert!(
        port.consume(&github_state, &batch.browser_nonce, "github", EXACT_GITHUB, FLOW, NOW + 1)
            .await
            .is_err(),
        "a consumed state never completes twice"
    );
    assert!(
        port.consume(&google_state, &batch.browser_nonce, "google", EXACT_GOOGLE, FLOW, NOW + 1)
            .await
            .is_err(),
        "the first consume invalidated the whole batch"
    );

    // A fresh batch: wrong provider, wrong exact callback, and wrong flow
    // are all rejected while the genuine consume still works afterwards.
    let batch = port
        .issue_batch(&["github".to_string()], CALLBACK_BASE, FLOW, NOW)
        .await
        .expect("issue succeeds");
    let state = batch.provider_states[0].1.clone();
    assert!(
        port.consume(&state, &batch.browser_nonce, "google", EXACT_GOOGLE, FLOW, NOW + 1)
            .await
            .is_err(),
        "a provider rebinding must be rejected"
    );
    assert!(
        port.consume(
            &state,
            &batch.browser_nonce,
            "github",
            "https://harness.example/openapi/v1/auth/callback/evil",
            FLOW,
            NOW + 1
        )
        .await
        .is_err(),
        "a mismatched exact callback must be rejected"
    );
    assert!(
        port.consume(&state, &batch.browser_nonce, "github", EXACT_GITHUB, "other-flow", NOW + 1)
            .await
            .is_err(),
        "a wrong flow namespace must be rejected"
    );
    assert!(
        port.consume(&state, &batch.browser_nonce, "github", EXACT_GITHUB, FLOW, NOW + 1)
            .await
            .is_ok(),
        "the record survives rejected attempts and completes"
    );

    // Re-issue rotates the nonce and batches stay independent; an
    // exhausted batch cannot be unlocked by a neighbour's nonce.
    let batch_a = port
        .issue_batch(&["github".to_string()], CALLBACK_BASE, FLOW, NOW)
        .await
        .expect("batch A issues");
    let batch_b = port
        .issue_batch(&["github".to_string()], CALLBACK_BASE, FLOW, NOW)
        .await
        .expect("batch B issues");
    assert!(
        batch_a.browser_nonce != batch_b.browser_nonce,
        "re-issue rotates the browser nonce"
    );
    assert!(
        port.consume(
            &batch_a.provider_states[0].1,
            &batch_b.browser_nonce,
            "github",
            EXACT_GITHUB,
            FLOW,
            NOW + 1
        )
        .await
        .is_err(),
        "batch B's nonce must not unlock batch A's state"
    );
    assert!(
        port.consume(
            &batch_a.provider_states[0].1,
            &batch_a.browser_nonce,
            "github",
            EXACT_GITHUB,
            FLOW,
            NOW + 1
        )
        .await
        .is_ok(),
        "batch A remains completable"
    );

    // An empty provider list never yields a usable batch.
    assert!(
        port.issue_batch(&[], CALLBACK_BASE, FLOW, NOW).await.is_err(),
        "an empty provider list must not yield a login batch"
    );
}
