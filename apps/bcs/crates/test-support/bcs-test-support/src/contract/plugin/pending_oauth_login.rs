//! Pending OAuth login batch conformance harness (V1 API auth plugin
//! chain, Task 9).
//!
//! Drivers pass the `PendingOAuthLoginStore` under test (the in-crate
//! memory store now; Task 13's shared persistence store later); the
//! harness pins the browser-binding semantics from plan §8.4:
//!
//! - the plan's VERBATIM cross-browser sequence: a callback from another
//!   browser fails WITHOUT consuming the initiating browser's record, and
//!   the initiating browser then completes;
//! - a consumed batch is dead — replay of the same state never completes
//!   twice, and the first successful consume kills EVERY provider state
//!   of the batch;
//! - the batch TTL is exactly [`PENDING_LOGIN_TTL_SECS`] (300s) and an
//!   expired batch fails closed;
//! - wrong flow / provider / callback mismatch keeps the record pending;
//! - two batches stay independent — one browser's nonce never unlocks
//!   the other's state;
//! - a concurrent same-batch consume released at one barrier has exactly
//!   ONE winner (match and whole-batch invalidation under one lock);
//! - nonce and every state are independent CSPRNG draws — never equal,
//!   never containing one another;
//! - a store WRITE failure is `Unavailable`, which yields NO usable
//!   `LoginBatch` — never a valid login URL on a failed issue.
//!
//! Sentinels in this harness are non-credential stand-ins; failures
//! never print provider tokens or real codes.

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

use async_trait::async_trait;
use bcs_auth_api::login_state::{
    LoginBatch, LoginStateError, PendingOAuthLoginStore, PENDING_LOGIN_TTL_SECS,
};

/// Drive the plan Task 9 pending-login contract semantics through the
/// store under test.
#[allow(clippy::expect_used, reason = "test harness — panic on failure is the contract")]
pub async fn pending_oauth_login_contract_tests(store: Arc<dyn PendingOAuthLoginStore>) {
    pending_o_auth_login_store_contract_tests(store).await
}

/// The R25-gate-canonical entry for the same contract: the conformance
/// gate derives harness names by `to_snake` of the trait name
/// (`PendingOAuthLoginStore` → this identifier). Drivers should prefer
/// this name; the human-spelled alias above keeps existing imports
/// working.
#[allow(clippy::expect_used, reason = "test harness — panic on failure is the contract")]
pub async fn pending_o_auth_login_store_contract_tests(store: Arc<dyn PendingOAuthLoginStore>) {
    const CALLBACK_BASE: &str = "https://bcs.example.com/openapi/v1/auth/callback";
    const EXACT_GITHUB: &str = "https://bcs.example.com/openapi/v1/auth/callback/github";
    const EXACT_GOOGLE: &str = "https://bcs.example.com/openapi/v1/auth/callback/google";
    const FLOW: &str = "v1";
    const LEGACY_FLOW: &str = "legacy";

    // ------------------------------------------------------------------
    // Plan Task 9 step 1 — VERBATIM cross-browser sequence.
    // ------------------------------------------------------------------
    let batch = store
        .issue_batch(&["github".into()], "https://bcs.example.com/openapi/v1/auth/callback", "v1", 100)
        .await
        .expect("issue_batch must succeed for an enabled provider");
    let state = &batch.provider_states[0].1;
    assert!(store.consume(state, "browser-b", "github", "https://bcs.example.com/openapi/v1/auth/callback/github", "v1", 101).await.is_err());
    assert!(store.consume(state, &batch.browser_nonce, "github", "https://bcs.example.com/openapi/v1/auth/callback/github", "v1", 101).await.is_ok());

    // Issued batches carry the pinned TTL and preserve provider order.
    assert_eq!(
        batch.expires_at,
        100 + PENDING_LOGIN_TTL_SECS,
        "pending batch expires in exactly the pinned TTL"
    );
    assert_eq!(batch.provider_states[0].0, "github");

    // The consumed state is dead: replay never completes twice.
    assert!(
        store
            .consume(state, &batch.browser_nonce, "github", EXACT_GITHUB, FLOW, 101)
            .await
            .is_err(),
        "a consumed state must never complete a second callback"
    );

    // ------------------------------------------------------------------
    // Store write failure -> no usable login URLs. `Unavailable` must
    // carry no `LoginBatch` at all (plan §8.4: a failed issue never
    // hands out a valid URL).
    // ------------------------------------------------------------------
    let failing: Arc<dyn PendingOAuthLoginStore> = Arc::new(UnavailablePendingLoginStore);
    assert!(
        matches!(
            failing.issue_batch(&["github".into()], CALLBACK_BASE, FLOW, 100).await,
            Err(LoginStateError::Unavailable)
        ),
        "store write failure is Unavailable and yields no LoginBatch"
    );
    assert!(
        matches!(
            failing
                .consume("any-state", "any-nonce", "github", EXACT_GITHUB, FLOW, 101)
                .await,
            Err(LoginStateError::Unavailable)
        ),
        "store consume failure surfaces Unavailable"
    );

    // ------------------------------------------------------------------
    // Fresh two-provider batch: the first successful consume of ANY
    // provider state invalidates the WHOLE batch.
    // ------------------------------------------------------------------
    let batch = store
        .issue_batch(&["github".into(), "google".into()], CALLBACK_BASE, FLOW, 200)
        .await
        .expect("two-provider issue succeeds");
    assert_eq!(batch.provider_states.len(), 2);
    let (github_state, google_state) = (&batch.provider_states[0].1, &batch.provider_states[1].1);

    // Wrong flow keeps the record pending (legacy flow cannot spend a v1
    // batch).
    assert!(
        store
            .consume(github_state, &batch.browser_nonce, "github", EXACT_GITHUB, LEGACY_FLOW, 201)
            .await
            .is_err(),
        "wrong flow namespace must be rejected"
    );
    assert!(
        store
            .consume(github_state, &batch.browser_nonce, "github", EXACT_GITHUB, FLOW, 201)
            .await
            .is_ok(),
        "the record must remain pending after a wrong-flow attempt"
    );

    // Whole batch dead: the second provider's state can never complete.
    assert!(
        store
            .consume(google_state, &batch.browser_nonce, "google", EXACT_GOOGLE, FLOW, 201)
            .await
            .is_err(),
        "any single provider's consume invalidates the whole batch"
    );
    assert!(
        store
            .consume(github_state, &batch.browser_nonce, "github", EXACT_GITHUB, FLOW, 201)
            .await
            .is_err(),
        "the consumed provider's state is dead too"
    );

    // ------------------------------------------------------------------
    // Wrong provider / wrong exact callback: rejected WITHOUT consuming
    // the pending record (no cross-provider state rebinding).
    // ------------------------------------------------------------------
    let batch = store
        .issue_batch(&["github".into()], CALLBACK_BASE, FLOW, 300)
        .await
        .expect("issue succeeds");
    let state = batch.provider_states[0].1.clone();
    assert!(
        store
            .consume(&state, &batch.browser_nonce, "google", EXACT_GOOGLE, FLOW, 301)
            .await
            .is_err(),
        "a rebinding to another provider must fail"
    );
    assert!(
        store
            .consume(&state, &batch.browser_nonce, "github", "https://bcs.example.com/openapi/v1/auth/callback/evil", FLOW, 301)
            .await
            .is_err(),
        "a mismatched exact callback must fail"
    );
    assert!(
        store
            .consume(&state, &batch.browser_nonce, "github", EXACT_GITHUB, FLOW, 301)
            .await
            .is_ok(),
        "the record must survive wrong provider/ and wrong-callback attempts"
    );

    // ------------------------------------------------------------------
    // Expiry: an expired batch fails closed and is then cleaned up.
    // ------------------------------------------------------------------
    let batch = store
        .issue_batch(&["github".into()], CALLBACK_BASE, FLOW, 400)
        .await
        .expect("issue succeeds");
    let state = batch.provider_states[0].1.clone();
    assert_eq!(batch.expires_at, 400 + PENDING_LOGIN_TTL_SECS);
    assert!(
        store
            .consume(&state, &batch.browser_nonce, "github", EXACT_GITHUB, FLOW, 400 + PENDING_LOGIN_TTL_SECS)
            .await
            .is_err(),
        "the batch is expired at now == expires_at (fail closed)"
    );
    assert!(
        store
            .consume(&state, &batch.browser_nonce, "github", EXACT_GITHUB, FLOW, 600)
            .await
            .is_err(),
        "the expired batch stays dead"
    );

    // ------------------------------------------------------------------
    // Two batches stay independent browsers: one nonce never unlocks the
    // other batch, and every state/nonce is a distinct CSPRNG draw.
    // ------------------------------------------------------------------
    let batch_a = store
        .issue_batch(&["github".into()], CALLBACK_BASE, FLOW, 700)
        .await
        .expect("batch A issues");
    let batch_b = store
        .issue_batch(&["github".into()], CALLBACK_BASE, FLOW, 700)
        .await
        .expect("batch B issues");
    assert!(
        batch_a.browser_nonce != batch_b.browser_nonce,
        "re-issue rotates the browser nonce"
    );
    for (provider, state_a) in &batch_a.provider_states {
        assert!(!state_a.is_empty(), "provider {provider} gets a state");
        assert!(state_a != &batch_a.browser_nonce, "state != own nonce");
        for (_, state_b) in &batch_b.provider_states {
            assert!(state_a != state_b, "states of different batches differ");
        }
    }
    assert!(
        store
            .consume(
                &batch_b.provider_states[0].1,
                &batch_a.browser_nonce,
                "github",
                EXACT_GITHUB,
                FLOW,
                701
            )
            .await
            .is_err(),
        "batch A's nonce must not unlock batch B's state"
    );
    assert!(
        store
            .consume(
                &batch_a.provider_states[0].1,
                &batch_a.browser_nonce,
                "github",
                EXACT_GITHUB,
                FLOW,
                701
            )
            .await
            .is_ok(),
        "batch A remains completable after a cross-batch attempt"
    );

    // ------------------------------------------------------------------
    // Concurrent same-batch consume: barrier-released contenders, at
    // most ONE Ok — matching and whole-batch invalidation happen under
    // one lock.
    // ------------------------------------------------------------------
    let batch = store
        .issue_batch(&["github".into()], CALLBACK_BASE, FLOW, 800)
        .await
        .expect("issue succeeds");
    let state = Arc::new(batch.provider_states[0].1.clone());
    let nonce = Arc::new(batch.browser_nonce.clone());
    let barrier = Arc::new(tokio::sync::Barrier::new(8));
    let winners = Arc::new(AtomicUsize::new(0));
    let mut tasks = Vec::new();
    for _ in 0..8 {
        let store = Arc::clone(&store);
        let state = Arc::clone(&state);
        let nonce = Arc::clone(&nonce);
        let barrier = Arc::clone(&barrier);
        let winners = Arc::clone(&winners);
        tasks.push(tokio::spawn(async move {
            barrier.wait().await;
            if store
                .consume(&state, &nonce, "github", EXACT_GITHUB, FLOW, 801)
                .await
                .is_ok()
            {
                winners.fetch_add(1, Ordering::SeqCst);
            }
        }));
    }
    for task in tasks {
        task.await.expect("concurrent consume task must not panic");
    }
    let wins = winners.load(Ordering::SeqCst);
    assert_eq!(wins, 1, "concurrent consume of one batch has exactly one winner");

    // ------------------------------------------------------------------
    // Empty provider input: no usable batch is issued.
    // ------------------------------------------------------------------
    assert!(
        store.issue_batch(&[], CALLBACK_BASE, FLOW, 900).await.is_err(),
        "an empty provider list must not yield a login batch"
    );
}

/// Double whose backing "store" always fails: pins the classification
/// contract that an issue/consume outage is `Unavailable` and carries no
/// login URL material.
struct UnavailablePendingLoginStore;

#[async_trait]
impl PendingOAuthLoginStore for UnavailablePendingLoginStore {
    async fn issue_batch(
        &self,
        _providers: &[String],
        _callback_base: &str,
        _flow: &str,
        _now: u64,
    ) -> Result<LoginBatch, LoginStateError> {
        Err(LoginStateError::Unavailable)
    }

    async fn consume(
        &self,
        _state: &str,
        _browser_nonce: &str,
        _provider: &str,
        _exact_callback: &str,
        _flow: &str,
        _now: u64,
    ) -> Result<(), LoginStateError> {
        Err(LoginStateError::Unavailable)
    }
}
