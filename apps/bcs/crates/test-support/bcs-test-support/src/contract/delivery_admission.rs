//! `DeliveryAdmissionService` conformance harness
//! (bot-authority spec §8.2/§8.3 — 操作边界准入，不追溯).
//!
//! Delivery authorization happens ONCE per operation per
//! recipient connection, never per frame:
//!
//! - a fully-qualified candidate ⇒ present in the result,
//!   and the returned permit allows its visible frame;
//! - a scope-restricted candidate ⇒ present, but the
//!   permit's pure predicate rejects the restricted frame
//!   while still allowing an in-scope frame (the permit
//!   itself is never an invalidation);
//! - a candidate whose management edge or resource
//!   qualification is gone ⇒ ABSENT from the result
//!   (no permit), never a partial grant;
//! - a cross-resource candidate (mis-keyed
//!   Group/Session) ⇒ ABSENT;
//! - an underlying read failure ⇒ a desensitized
//!   `ApplicationError` — never a raw storage error,
//!   never a fake "no role".
//!
//! The fixture prepares REAL committed states through the
//! implementation under test; the harness asserts the
//! admission directions plus the snapshot sharing and
//! per-call independence (no candidate flips another).
//!
//! Conformance entry:
//! `bcs-app-session/tests/conformance_delivery_admission.rs`.

#![allow(clippy::expect_used, reason = "test harness — panic on failure is the contract")]

use async_trait::async_trait;

use bcs_service_api::application::v1::delivery_authorization::{
    AdmittedRecipient, DeliveryAdmissionService, DeliveryScope, DeliveryVisibility,
    ProtectedDeliveryBinding,
};

/// Prepared REAL states through the implementation
/// under test.
#[async_trait]
pub trait DeliveryAdmissionFixture: Send + Sync {
    /// The service under test (the real composition; the
    /// harness never relaxes it).
    fn service(&self) -> std::sync::Arc<dyn DeliveryAdmissionService>;

    /// The canonical resource every candidate is
    /// evaluated against.
    async fn scope(&self) -> DeliveryScope;

    /// A live, fully-authorized candidate paired with a
    /// frame its admitted view MUST allow.
    async fn authorized_candidate(
        &self,
    ) -> (ProtectedDeliveryBinding, DeliveryVisibility);

    /// A candidate whose committed participant scope
    /// forbids one artifact but allows another: the
    /// permit must exist (admission is not a frame
    /// skip) while its pure predicate rejects the
    /// forbidden frame only.
    async fn scope_restricted_candidate(
        &self,
    ) -> (
        ProtectedDeliveryBinding,
        DeliveryVisibility,
        DeliveryVisibility,
    );

    /// A candidate that LOST its management edge or its
    /// resource qualification (revocation actually
    /// committed through the implementation): it must
    /// receive NO permit.
    async fn revoked_candidate(&self) -> ProtectedDeliveryBinding;

    /// A candidate pinned to a DIFFERENT resource than
    /// the scope (mis-keyed subscription): it must
    /// receive NO permit.
    async fn foreign_candidate(&self) -> ProtectedDeliveryBinding;
}

async fn resolve(
    service: &dyn DeliveryAdmissionService,
    scope: &DeliveryScope,
    candidates: &[ProtectedDeliveryBinding],
) -> Result<Vec<AdmittedRecipient>, bcs_service_api::application::v1::error::ApplicationError>
{
    service.resolve_recipient_views(scope, candidates).await
}

fn permit_for<'a>(
    admitted: &'a [AdmittedRecipient],
    candidate: &ProtectedDeliveryBinding,
) -> Option<&'a AdmittedRecipient> {
    admitted.iter().find(|recipient| {
        recipient.connection_instance_id == candidate.connection_instance_id
            && recipient.view_actor_id == candidate.view_actor_id
    })
}

/// Real operation-boundary admission semantics over
/// prepared state.
pub async fn delivery_admission_service_contract_tests(
    fixture: &dyn DeliveryAdmissionFixture,
) {
    let service = fixture.service();
    let scope = fixture.scope().await;

    let (candidate, visible_frame) = fixture.authorized_candidate().await;
    let (restricted, restricted_frame, still_visible_frame) =
        fixture.scope_restricted_candidate().await;
    let revoked = fixture.revoked_candidate().await;
    let foreign = fixture.foreign_candidate().await;

    // ONE batch over every candidate kind at once: the
    // admission of one must never depend on (or flip)
    // another candidate's qualification.
    let admitted = resolve(
        service.as_ref(),
        &scope,
        &[
            candidate.clone(),
            restricted.clone(),
            revoked.clone(),
            foreign.clone(),
        ],
    )
    .await
    .expect("a batched admission must not be a read failure");

    let permit = permit_for(&admitted, &candidate)
        .expect("a fully-authorized candidate is admitted");
    assert!(
        permit.allows(&visible_frame),
        "the admitted permit allows the visible frame"
    );

    let restricted_permit = permit_for(&admitted, &restricted)
        .expect("a scope-restricted candidate keeps its permit");
    assert!(
        !restricted_permit.allows(&restricted_frame),
        "the pure predicate rejects the restricted frame"
    );
    assert!(
        restricted_permit.allows(&still_visible_frame),
        "the same permit still allows an in-scope frame"
    );

    assert!(
        permit_for(&admitted, &revoked).is_none(),
        "a revoked candidate receives no permit"
    );
    assert!(
        permit_for(&admitted, &foreign).is_none(),
        "a cross-resource candidate receives no permit"
    );

    // Per-call independence: repeated resolution over the
    // live candidate yields the same admitted view, and an
    // interleaved revoked candidate does not poison it.
    let first = resolve(service.as_ref(), &scope, &[candidate.clone()])
        .await
        .expect("repeat resolution of the live candidate");
    let _ = resolve(service.as_ref(), &scope, &[revoked.clone()]).await;
    let second = resolve(service.as_ref(), &scope, &[candidate])
        .await
        .expect("resolution after interleaving");
    assert_eq!(
        first, second,
        "repeated resolution must not drift across calls"
    );
}
