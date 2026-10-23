//! One-shot identity handoff contract types (spec §7.5.2). These tests pin
//! trait-level invariants that every implementation must keep; the storage
//! semantics (reserve/publish/claim/close) are exercised against the real
//! message-flow implementation in its own crate.

use bcs_service_api::application::caller_execution::{
    HandoffClaim, HandoffError, HandoffScope, QueuedAdmissionReceipt,
};
use bcs_service_api::application::caller_identity::{
    AuthenticatedHumanCallerIngress, HumanCallerAuthSource,
};
use bcs_service_api::application::principal::HumanActor;

fn scope() -> HandoffScope {
    HandoffScope {
        env: "prod".into(),
        session_id: "session-1".into(),
        group_id: Some("group-1".into()),
        source_message_id: "msg-canonical-1".into(),
        target_bot_id: "bot-a".into(),
    }
}

fn ingress(now: i64) -> AuthenticatedHumanCallerIngress {
    AuthenticatedHumanCallerIngress::new(
        HumanActor { actor_id: "human_alice".into(), staff_no: "S12345".into() },
        HumanCallerAuthSource::HttpAuth,
        now,
        now + 300_000,
        "prod",
    )
    .expect("valid ingress")
}

#[test]
fn handoff_scope_debug_carries_no_identity_material() {
    let debug = format!("{:?}", scope());
    assert!(!debug.contains("S12345"));
    assert!(!debug.contains("human_alice"));
    // The directory key is pure coordination fact.
    assert!(debug.contains("msg-canonical-1"));
    assert!(debug.contains("bot-a"));
}

#[test]
fn scope_equality_and_hash_key_the_same_facts() {
    use std::collections::HashSet;
    let mut set = HashSet::new();
    set.insert(scope());
    set.insert(scope());
    set.insert(HandoffScope { target_bot_id: "bot-b".into(), ..scope() });
    assert_eq!(set.len(), 2);
}

#[test]
fn claim_and_receipt_are_plain_non_identity_facts() {
    let claim = HandoffClaim {
        delivery_id: "d-1".into(),
        state_version: 4,
        run_id: Some("run-1".into()),
        attempt: 1,
        now_ms: 1_780_000_000_000,
    };
    let receipt = QueuedAdmissionReceipt {
        delivery_id: "d-1".into(),
        state_version: 3,
        admitted_at_ms: 1_780_000_000_000,
    };
    let debug = format!("{:?}{:?}", claim, receipt);
    assert!(!debug.contains("S12345") && !debug.contains("token"));
}

#[test]
fn handoff_error_is_closed_and_display_sanitized() {
    let errors = [
        HandoffError::CapacityExhausted,
        HandoffError::NotReserved,
        HandoffError::AlreadyPublished,
        HandoffError::AlreadyClaimed,
        HandoffError::Expired,
        HandoffError::Closed,
        HandoffError::InvalidInput,
    ];
    for error in errors {
        let text = error.to_string();
        assert!(!text.contains("alarm"), "error text must stay closed: {text}");
    }
}

// Cloning a trusted connection snapshot never refreshes authentication.
#[test]
fn ingress_snapshot_clone_preserves_identity_window_and_redaction() {
    let original = ingress(1_000);
    let copied = original.clone();
    assert_eq!(original, copied);
    assert_eq!(copied.expires_at_ms(), 301_000);
    assert!(!copied.is_expired(300_999));
    assert!(copied.is_expired(301_000));
    let debug = format!("{copied:?}");
    assert!(!debug.contains("S12345"));
    assert!(!debug.contains("human_alice"));
}
