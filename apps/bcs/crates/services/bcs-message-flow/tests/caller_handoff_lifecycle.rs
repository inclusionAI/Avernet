//! Lifecycle conformance for the real one-shot identity handoff (spec
//! §7.5.2). Runs against the production in-memory implementation; the
//! trusted envelopes move exactly once through it.

use bcs_message_flow::caller_delivery_handoff::HumanCallerDeliveryHandoff;
use bcs_service_api::application::caller_execution::{
    HandoffClaim, HandoffError, HandoffScope, HumanCallerDeliveryHandoffService,
    QueuedAdmissionReceipt,
};
use bcs_service_api::application::caller_identity::{
    AuthenticatedHumanCallerIngress, HumanCallerAuthSource,
};
use bcs_service_api::application::principal::HumanActor;
use std::sync::Arc;

const NOW: i64 = 1_780_000_000_000;

fn scope(suffix: &str) -> HandoffScope {
    HandoffScope {
        env: "prod".into(),
        session_id: format!("session-{suffix}"),
        group_id: None,
        source_message_id: format!("msg-{suffix}"),
        target_bot_id: "bot-a".into(),
    }
}

fn ingress(validity_ms: i64) -> AuthenticatedHumanCallerIngress {
    AuthenticatedHumanCallerIngress::new(
        HumanActor { actor_id: format!("human_{validity_ms}"), staff_no: "S12345".into() },
        HumanCallerAuthSource::HttpAuth,
        NOW,
        NOW + validity_ms,
        "prod",
    )
    .expect("valid ingress")
}

fn handoff(capacity: usize) -> Arc<HumanCallerDeliveryHandoff> {
    Arc::new(HumanCallerDeliveryHandoff::new(capacity))
}

#[tokio::test]
async fn reserve_publish_claim_happy_path_exactly_once() {
    let h = handoff(8);
    let s = scope("a");
    h.reserve(&s, 10_000, NOW).await.expect("reserve");
    h.publish(&s, ingress(300_000), QueuedAdmissionReceipt::new("d-1", 1, NOW))
        .await
        .expect("publish once");
    // Second publish never overwrites the parked identity.
    assert_eq!(
        h.publish(&s, ingress(60_000), QueuedAdmissionReceipt::new("d-1", 1, NOW)).await.unwrap_err(),
        HandoffError::AlreadyPublished
    );
    // First committed attempt claims and consumes the identity.
    let claimed = h
        .claim(&s, HandoffClaim::new("d-1", 4, Some("run-1".into()), 1, NOW + 1_000))
        .await
        .expect("claim once");
    assert_eq!(claimed.expires_at_ms(), NOW + 300_000);
    // Any later claim — duplicate, stale attempt, mismatched anything —
    // fails; identity never goes back into the slot.
    for claim in [
        HandoffClaim::new("d-1", 4, Some("run-1".into()), 1, NOW + 2_000),
        HandoffClaim::new("d-1", 9, Some("run-1".into()), 2, NOW + 3_000),
        HandoffClaim::new("d-2", 4, None, 1, NOW + 4_000),
    ] {
        assert_eq!(h.claim(&s, claim).await.unwrap_err(), HandoffError::AlreadyClaimed);
    }
}

#[tokio::test]
async fn publish_requires_a_live_reservation_and_fresh_facts() {
    let h = handoff(8);
    // Unreserved scope cannot publish identity out of thin air.
    assert_eq!(
        h.publish(&scope("x"), ingress(60_000), QueuedAdmissionReceipt::new("d-1", 1, NOW))
            .await
            .unwrap_err(),
        HandoffError::NotReserved
    );
    // Empty delivery fact is invalid input, not a silent park.
    let s = scope("y");
    h.reserve(&s, 10_000, NOW).await.expect("reserve");
    assert_eq!(
        h.publish(&s, ingress(60_000), QueuedAdmissionReceipt::new("", 1, NOW))
            .await
            .unwrap_err(),
        HandoffError::InvalidInput
    );
    // Claim with zero attempt or empty delivery is rejected: no attempt-0
    // proofs exist by contract.
    let s = scope("z");
    h.reserve(&s, 10_000, NOW).await.expect("reserve");
    h.publish(&s, ingress(60_000), QueuedAdmissionReceipt::new("d-3", 1, NOW))
        .await
        .expect("publish");
    for claim in [
        HandoffClaim::new("", 4, None, 1, NOW),
        HandoffClaim::new("d-3", 4, None, 0, NOW),
    ] {
        assert_eq!(h.claim(&s, claim).await.unwrap_err(), HandoffError::InvalidInput);
    }
}

#[tokio::test]
async fn identity_expired_parked_is_a_hard_loss() {
    let h = handoff(8);
    let s = scope("e");
    h.reserve(&s, 10_000, NOW).await.expect("reserve");
    h.publish(&s, ingress(300_000), QueuedAdmissionReceipt::new("d-1", 1, NOW))
        .await
        .expect("publish");
    // Claim after the trusted window: identity is refused and stays gone.
    assert_eq!(
        h.claim(&s, HandoffClaim::new("d-1", 4, Some("run-1".into()), 1, NOW + 900_000))
            .await
            .unwrap_err(),
        HandoffError::Expired
    );
    assert_eq!(
        h.claim(&s, HandoffClaim::new("d-1", 4, Some("run-1".into()), 1, NOW + 900_001))
            .await
            .unwrap_err(),
        HandoffError::AlreadyClaimed
    );
}

#[tokio::test]
async fn capacity_counts_all_slots_and_sweeps_expired_before_refusing() {
    let h = handoff(2);
    h.reserve(&scope("c1"), 10_000, NOW).await.expect("r1");
    h.reserve(&scope("c2"), 10_000, NOW).await.expect("r2");
    assert_eq!(
        h.reserve(&scope("c3"), 10_000, NOW).await.unwrap_err(),
        HandoffError::CapacityExhausted
    );
    // After the reserved TTL passes, capacity is reclaimed by the sweep.
    h.reserve(&scope("c3"), 10_000, NOW + 20_000).await.expect("swept and re-reserved");
}

#[tokio::test]
async fn close_is_irreversible_and_forgets_everything() {
    let h = handoff(8);
    let s = scope("k");
    h.reserve(&s, 10_000, NOW).await.expect("reserve");
    h.publish(&s, ingress(300_000), QueuedAdmissionReceipt::new("d-1", 1, NOW))
        .await
        .expect("publish");
    h.close().await;
    // Post-close: no reserve, no publish, no claim, no reopen.
    assert_eq!(h.reserve(&scope("k2"), 10_000, NOW).await.unwrap_err(), HandoffError::Closed);
    assert_eq!(
        h.publish(&s, ingress(60_000), QueuedAdmissionReceipt::new("d-1", 1, NOW))
            .await
            .unwrap_err(),
        HandoffError::Closed
    );
    assert_eq!(
        h.claim(&s, HandoffClaim::new("d-1", 4, None, 1, NOW + 1_000))
            .await
            .unwrap_err(),
        HandoffError::Closed
    );
    // Closing twice stays closed and never reopens into success.
    h.close().await;
    assert_eq!(h.reserve(&scope("k3"), 10_000, NOW).await.unwrap_err(), HandoffError::Closed);
}

#[tokio::test]
async fn scope_keys_are_independent_per_target_and_turn() {
    let h = handoff(8);
    let dm = scope("t1");
    let other_bot = HandoffScope { target_bot_id: "bot-b".into(), ..scope("t1") };
    let other_turn = HandoffScope { source_message_id: "msg-t2".into(), ..scope("t1") };
    for s in [&dm, &other_bot, &other_turn] {
        h.reserve(s, 10_000, NOW).await.expect("reserve");
        h.publish(s, ingress(300_000), QueuedAdmissionReceipt::new("d", 1, NOW))
            .await
            .expect("publish");
    }
    let one = h
        .claim(&dm, HandoffClaim::new("d", 4, None, 1, NOW))
        .await
        .expect("claim first");
    // Consuming one slot exposes nothing about the other scopes.
    let _ = one;
    h.claim(&other_bot, HandoffClaim::new("d", 4, None, 1, NOW))
        .await
        .expect("independent claim per target");
    h.claim(&other_turn, HandoffClaim::new("d", 4, None, 1, NOW))
        .await
        .expect("independent claim per turn");
}

#[tokio::test]
async fn seal_term_wipes_old_term_identity_and_restores_capacity() {
    // Spec §7.5.2 master-term boundary: identity parked under an OLD term
    // is irrevocably gone — even a live, unexpired, correctly-factored
    // claim of the new term reads NotReserved (the row converges through
    // the existing caller rescue/TTL paths), tombstones die with the term,
    // and capacity returns to full for the new term's admissions.
    let h = handoff(2);
    h.reserve(&scope("t1"), 10_000, NOW).await.expect("reserve");
    h.publish(&scope("t1"), ingress(300_000), QueuedAdmissionReceipt::new("d-1", 1, NOW))
        .await
        .expect("publish");
    h.reserve(&scope("t2"), 10_000, NOW).await.expect("reserve t2");
    // Reserved slots crowded the capacity: a third scope would refuse.
    assert_eq!(
        h.reserve(&scope("t3"), 10_000, NOW).await.unwrap_err(),
        HandoffError::CapacityExhausted
    );

    let wiped = h.seal_term();
    assert_eq!(wiped, 2, "the parked identity and the bare reservation both die");
    // Old-term identity never enters the new term, even with valid facts.
    assert_eq!(
        h.claim(&scope("t1"), HandoffClaim::new("d-1", 4, Some("run-1".into()), 1, NOW + 1_000))
            .await
            .unwrap_err(),
        HandoffError::NotReserved
    );
    // The new term admits fresh identity from a clean slate.
    h.reserve(&scope("t3"), 10_000, NOW).await.expect("new term reserve");
    h.publish(&scope("t3"), ingress(300_000), QueuedAdmissionReceipt::new("d-3", 1, NOW))
        .await
        .expect("new term publish");
    let claimed = h
        .claim(&scope("t3"), HandoffClaim::new("d-3", 4, Some("run-1".into()), 1, NOW + 2_000))
        .await
        .expect("new term claim");
    assert_eq!(claimed.expires_at_ms(), NOW + 300_000);
}
