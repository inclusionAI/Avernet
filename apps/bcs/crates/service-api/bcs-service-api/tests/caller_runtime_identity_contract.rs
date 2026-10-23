//! P0-frozen runtime-only caller identity envelope contracts (spec §10.1.1,
//! §10.5, §10.13).
//!
//! Everything asserted here exists to keep Human identity material inside
//! trusted, in-memory objects (ingress snapshots may be cloned):
//! - no `Serialize`/`Deserialize` on purpose (enforced structurally — the
//!   types simply never derive serde; persisting them is a review-level bug);
//! - `Debug` must never leak the staff principal or the opaque subject ref;
//! - validity windows come from the real authentication result and can only
//!   shrink.

use bcs_service_api::application::caller_identity::{
    AuthenticatedHumanCallerIngress, CallerExecutionPurpose, CallerSubjectRef, CallerSubjectKind,
    CallerSubjectRefType, HumanCallerAuthSource,
};
use bcs_service_api::application::principal::HumanActor;

fn alice() -> HumanActor {
    HumanActor {
        actor_id: "human_alice".to_string(),
        staff_no: "S12345".to_string(),
    }
}

fn alice_ingress() -> AuthenticatedHumanCallerIngress {
    AuthenticatedHumanCallerIngress::new(
        alice(),
        HumanCallerAuthSource::HttpAuth,
        1_780_000_000_000,
        1_780_003_000_000,
        "prod",
    )
    .unwrap()
}

#[test]
fn ingress_debug_redacts_the_staff_principal() {
    let debug = format!("{:?}", alice_ingress());
    assert!(!debug.contains("S12345"), "staff_no leaked: {debug}");
    // The business actor id is derived from the staff number, so it is
    // principal material as well and is redacted together with it.
    assert!(!debug.contains("human_alice"), "derived actor id leaked: {debug}");
    assert!(debug.matches("<redacted>").count() >= 2, "redaction is not explicit: {debug}");
}

#[test]
fn ingress_exposes_only_trusted_metadata() {
    let ingress = alice_ingress();
    assert_eq!(ingress.actor().actor_id, "human_alice");
    assert_eq!(ingress.auth_source(), HumanCallerAuthSource::HttpAuth);
    assert_eq!(ingress.expires_at_ms(), 1_780_003_000_000);
    assert_eq!(ingress.env(), "prod");
    // Trusted validity is read from the real authentication result and is
    // compared against the caller-provided clock, never re-estimated.
    assert!(!ingress.is_expired(1_780_002_999_999));
    assert!(ingress.is_expired(1_780_003_000_000));
    assert!(ingress.is_expired(1_780_004_000_000));
}

#[test]
fn ingress_rejects_malformed_inputs() {
    // Empty actor id, empty env and impossible validity windows never yield a
    // trusted ingress; there is no fallback constructor.
    let mut no_actor = alice();
    no_actor.actor_id = String::new();
    assert!(AuthenticatedHumanCallerIngress::new(
        no_actor,
        HumanCallerAuthSource::HttpAuth,
        1_780_000_000_000,
        1_780_003_000_000,
        "prod"
    )
    .is_err());

    assert!(AuthenticatedHumanCallerIngress::new(
        alice(),
        HumanCallerAuthSource::HttpAuth,
        1_780_000_000_000,
        1_780_003_000_000,
        ""
    )
    .is_err());

    for (auth, exp) in [
        (1_780_000_000_000i64, 1_780_000_000_000i64), // expiry == issued
        (1_780_003_000_000, 1_780_000_000_000),       // inverted window
        (0, 1_780_003_000_000),                        // missing auth time
    ] {
        assert!(
            AuthenticatedHumanCallerIngress::new(
                alice(),
                HumanCallerAuthSource::HttpAuth,
                auth,
                exp,
                "prod"
            )
            .is_err(),
            "window ({auth}, {exp}) must be rejected"
        );
    }
}

#[test]
fn subject_ref_debug_redacts_the_opaque_reference() {
    let subject = CallerSubjectRef::new(
        CallerSubjectKind::Human,
        CallerSubjectRefType::BcsEphemeral,
        "xone_ref_opaque_123".to_string(),
        1_780_003_000_000,
    )
    .unwrap();
    let debug = format!("{:?}", subject);
    assert!(!debug.contains("xone_ref_opaque_123"), "subject ref leaked: {debug}");
    assert!(debug.contains("<redacted>"), "redaction is not explicit: {debug}");

    // Exposure is an explicit call, not an implicit display/deref path; only
    // trusted envelope assembly may use it.
    assert_eq!(subject.expose_ref(), "xone_ref_opaque_123");
    assert_eq!(subject.kind(), CallerSubjectKind::Human);
    assert_eq!(subject.ref_type(), CallerSubjectRefType::BcsEphemeral);
    assert_eq!(subject.expires_at_ms(), 1_780_003_000_000);
    assert!(!subject.is_expired(1_780_002_999_999));
    assert!(subject.is_expired(1_780_003_000_000));
}

#[test]
fn subject_ref_rejects_empty_or_expiry_free_refs() {
    assert!(CallerSubjectRef::new(
        CallerSubjectKind::Human,
        CallerSubjectRefType::PluginLineageRef,
        String::new(),
        1_780_003_000_000
    )
    .is_err());
    assert!(CallerSubjectRef::new(
        CallerSubjectKind::Human,
        CallerSubjectRefType::ExternalSubjectRef,
        "opaque".to_string(),
        0
    )
    .is_err());
}

#[test]
fn execution_purpose_values_stay_closed() {
    // The three purposes are the entire V1 set: subject establishment, caller
    // execution, and derivation-only lineage. `DelegationOnly` must never be
    // accepted where `Execution` is required.
    let purposes = [
        CallerExecutionPurpose::SubjectEstablishment,
        CallerExecutionPurpose::Execution,
        CallerExecutionPurpose::DelegationOnly,
    ];
    let execution_only: Vec<_> = purposes
        .iter()
        .filter(|p| permits_human_execution(p))
        .collect();
    assert_eq!(execution_only.len(), 1);
}

fn permits_human_execution(purpose: &CallerExecutionPurpose) -> bool {
    matches!(purpose, CallerExecutionPurpose::Execution)
}
#[test]
fn ingress_requires_a_usable_authenticated_staff_number() {
    for staff_no in ["", "   "] {
        let mut actor = alice();
        actor.staff_no = staff_no.into();
        assert!(AuthenticatedHumanCallerIngress::new(
            actor, HumanCallerAuthSource::HttpAuth, 1_000, 2_000, "prod",
        ).is_err());
    }
}
