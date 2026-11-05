//! Lifecycle conformance for the real one-shot identity handoff (spec
//! §7.5.2). Runs against the production in-memory implementation; the
//! trusted envelopes move exactly once through it.

use bcs_message_flow::caller_delivery_handoff::HumanCallerDeliveryHandoff;
use bcs_service_api::application::caller_handoff::{HandoffClaim, HandoffError, HandoffIdentityReadiness, HandoffScope, HumanCallerDeliveryHandoffService, QueuedAdmissionReceipt};
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
    assert_eq!(claimed.actor().actor_id, "human_300000");
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
async fn a_late_claim_succeeds_and_consumes_once() {
    // Identity carries no window: a late claim hands the identity out and
    // consumes it; only the already-claimed tombstone answers later.
    let h = handoff(8);
    let s = scope("e");
    h.reserve(&s, 10_000, NOW).await.expect("reserve");
    h.publish(&s, ingress(300_000), QueuedAdmissionReceipt::new("d-1", 1, NOW))
        .await
        .expect("publish");
    let claimed = h
        .claim(&s, HandoffClaim::new("d-1", 4, Some("run-1".into()), 1, NOW + 900_000))
        .await
        .expect("late claim succeeds");
    assert_eq!(claimed.actor().actor_id, "human_300000");
    assert_eq!(
        h.claim(&s, HandoffClaim::new("d-1", 4, Some("run-1".into()), 1, NOW + 900_001))
            .await
            .unwrap_err(),
        HandoffError::AlreadyClaimed
    );
}

#[tokio::test]
async fn a_late_claim_survives_intervening_admission_sweeps_within_the_reap_horizon() {
    // 拥塞场景:消息因前一任务或限流在队列里等待,越过 reserve 的 10s
    // 过期点后另一条 caller 消息入队(每次准入都触发全局 sweep)。
    // Published 身份的回收视角锚定在回执自己的 admitted_at 时刻,不是
    // 准入窗:凭证仍有效、行预算未到,迟到 claim 必须成功。
    let h = handoff(8);
    let s = scope("e2");
    h.reserve(&s, 10_000, NOW).await.expect("reserve");
    h.publish(&s, ingress(300_000), QueuedAdmissionReceipt::new("d-1", 1, NOW))
        .await
        .expect("publish");
    // 另一条消息准入 → reserve 前的全局 sweep 在 reserve 过期点之后运行。
    h.reserve(&scope("other"), 10_000, NOW + 20_000).await.expect("other admission's reserve");
    // 出队前的 readiness 探针必须仍然回答 Published:尚未被任何派发
    // attempt claim 过的身份没有消失的理由。
    assert_eq!(h.identity_readiness(&s).await, HandoffIdentityReadiness::Published);
    let claimed = h
        .claim(&s, HandoffClaim::new("d-1", 4, Some("run-1".into()), 1, NOW + 25_000))
        .await
        .expect("late claim survives other admissions' sweeps");
    assert_eq!(claimed.actor().actor_id, "human_300000");
}

#[tokio::test]
async fn published_identity_is_reaped_only_after_its_own_reap_horizon() {
    // 回收是内存管理,不是身份裁决: published 槽位只在自己的回收视角
    // (admitted_at + horizon)之后才可被 sweep 回收,capacity 不被无限期
    // 占用,但也不被 30 秒的准入窗提前挤掉。
    let h = HumanCallerDeliveryHandoff::with_published_reap_horizon(2, 300_000);
    let s = scope("z");
    h.reserve(&s, 10_000, NOW).await.expect("reserve");
    h.publish(&s, ingress(300_000), QueuedAdmissionReceipt::new("d-1", 1, NOW))
        .await
        .expect("publish");
    // horizon 之内: published 占 capacity,paired reserved 也算满,第三个
    // scope 准入被拒 — sweep 不得提前回收 published。
    h.reserve(&scope("cap"), 10_000, NOW + 100_000).await.expect("reserved within horizon");
    assert_eq!(
        h.reserve(&scope("cap2"), 10_000, NOW + 100_100).await.unwrap_err(),
        HandoffError::CapacityExhausted
    );
    // 越过 horizon 之后,任何准入触发的 sweep 才恢复回收。
    h.reserve(&scope("cap3"), 10_000, NOW + 300_500).await.expect("published reaped past horizon");
    assert_eq!(h.identity_readiness(&s).await, HandoffIdentityReadiness::Gone);
    assert_eq!(
        h.claim(&s, HandoffClaim::new("d-1", 4, Some("run-1".into()), 1, NOW + 300_600))
            .await
            .unwrap_err(),
        HandoffError::NotReserved
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
async fn identity_readiness_reports_the_admission_race_without_consuming() {
    // Plan-A gate contract: the probe answers Reserved while a same-process
    // admission sits between its reserve and its publish, Published once the
    // identity is parked, Gone after a claim consumes it or the slot was
    // never reserved — and never mutates or sweeps anything it observes.
    let h = handoff(8);
    // A scope nobody ever reserved (crash-window residue from the probe's
    // point of view): no identity can ever arrive.
    assert_eq!(h.identity_readiness(&scope("probe-unknown")).await, HandoffIdentityReadiness::Gone);

    let s = scope("probe");
    h.reserve(&s, 10_000, NOW).await.expect("reserve");
    // Admission in flight: repeated probes keep answering Reserved and never
    // disturb the slot the publish below still has to land on.
    for _ in 0..3 {
        assert_eq!(h.identity_readiness(&s).await, HandoffIdentityReadiness::Reserved);
    }
    h.publish(&s, ingress(300_000), QueuedAdmissionReceipt::new("d-1", 1, NOW))
        .await
        .expect("publish still lands after probing");
    assert_eq!(h.identity_readiness(&s).await, HandoffIdentityReadiness::Published);
    // The probe is not a claim: the identity still hands out exactly once.
    h.claim(&s, HandoffClaim::new("d-1", 4, Some("run-1".into()), 1, NOW + 1_000))
        .await
        .expect("claim once after probing");
    assert_eq!(h.identity_readiness(&s).await, HandoffIdentityReadiness::Gone);
    // Term seals and closes read Gone: post-seal/post-close identity never
    // arrives for an old parked scope.
    let w = scope("probe-seal");
    h.reserve(&w, 10_000, NOW).await.expect("reserve w");
    h.publish(&w, ingress(300_000), QueuedAdmissionReceipt::new("d-2", 1, NOW))
        .await
        .expect("publish w");
    assert_eq!(h.identity_readiness(&w).await, HandoffIdentityReadiness::Published);
    h.seal_term();
    assert_eq!(h.identity_readiness(&w).await, HandoffIdentityReadiness::Gone);
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
    assert_eq!(claimed.actor().actor_id, "human_300000");
}
