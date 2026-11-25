//! `SessionAuthorizationCoreService` conformance harness
//! (bot-authority spec §8.3 Session snapshot reads).
//!
//! The Core is a strict, transparent read over the master
//! session data: no display cache, no cache fallback, strict
//! `Err` on storage failure, `Ok(None)` ONLY for a missing
//! Session, and every read reflects the CURRENT committed
//! facts (no stale snapshot).
//!
//! The fixture commits REAL resource changes through the
//! implementing store's own write path (create a session,
//! then add a participant for the freshness variant) and
//! returns a summary of the committed facts, so the harness
//! compares real read-backs against real commits, not
//! invented expectations.
//!
//! Conformance entry:
//! `bcs-app-session/tests/conformance_session_authorization.rs`.

#![allow(clippy::expect_used, reason = "test harness — panic on failure is the contract")]

use async_trait::async_trait;

use bcs_service_api::core::SessionAuthorizationCoreService;

/// The committed facts one variant produced.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SessionFacts {
    pub session_id: String,
    pub group_id: String,
    /// The store instance's bound environment as the store
    /// itself reports it.
    pub env: String,
    /// Participants visible in the committed facts.
    pub participants: usize,
}

/// Store-side access the harness uses to prepare state.
#[async_trait]
pub trait SessionAuthorizationCoreFixture: Send + Sync {
    /// The core under test (a real `SessionAuthorizationCoreService`
    /// over the store's real repo).
    fn core(&self) -> std::sync::Arc<dyn SessionAuthorizationCoreService>;

    /// Commit facts for THE SAME logical session across
    /// calls: `variant 0` creates a real session through the
    /// store's real write path; `variant 1` adds a
    /// participant to that same session (a real committed
    /// resource change). Returns the committed facts.
    async fn commit_facts(&self, variant: u8) -> SessionFacts;

    /// Remove the session (live rows only; deleted
    /// Sessions project to `Ok(None)`).
    async fn delete_session(&self, session_id: &str);
}

/// Real core-level semantics for the Session
/// authorization snapshot read.
pub async fn session_authorization_core_service_contract_tests(
    fixture: &dyn SessionAuthorizationCoreFixture,
) {
    let core = fixture.core();
    let missing = format!(
        "sess_harness_missing_{}",
        std::process::id()
    );

    // Missing Session → Ok(None), never an error, empty
    // snapshot, or a cache hit.
    assert_eq!(
        core.read_authorization_snapshot(&missing)
            .await
            .expect("missing session is Ok(None)"),
        None,
    );

    // Seeded facts read back; the read is strict and
    // un-cached: repeat reads agree, and the projection
    // matches the committed resource facts.
    let facts0 = fixture.commit_facts(0).await;
    let first = core
        .read_authorization_snapshot(&facts0.session_id)
        .await
        .expect("seeded read is Ok");
    let first = first.expect("the committed session must project");
    assert_eq!(first.env, facts0.env);
    assert_eq!(first.group_id, facts0.group_id);
    assert_eq!(first.participants.len(), facts0.participants);
    let second = core
        .read_authorization_snapshot(&facts0.session_id)
        .await
        .expect("repeat read is Ok");
    assert_eq!(first, second.expect("repeat projection"));

    // Freshness: a real committed resource change (a new
    // participant) is visible on the next read.
    let facts1 = fixture.commit_facts(1).await;
    let third = core
        .read_authorization_snapshot(&facts1.session_id)
        .await
        .expect("updated read is Ok");
    let third = third.expect("the updated session must project");
    assert_eq!(third.env, facts1.env);
    assert_eq!(third.group_id, facts1.group_id);
    assert_eq!(
        third.participants.len(),
        facts1.participants,
        "the next read sees the new facts"
    );
    assert!(third.participants.len() > first.participants.len());

    // Deleted Sessions project to Ok(None).
    fixture.delete_session(&facts1.session_id).await;
    assert_eq!(
        core.read_authorization_snapshot(&facts1.session_id)
            .await
            .expect("deleted session is Ok(None)"),
        None,
    );
}
