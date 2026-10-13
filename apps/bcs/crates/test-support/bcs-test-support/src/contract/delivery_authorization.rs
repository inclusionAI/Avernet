//! `DeliveryAuthorizationService` conformance harness
//! (bot-authority spec §8.2 final-writer decisions).
//!
//! The final writer re-authorizes EVERY protected frame
//! against committed facts: no enqueue-time grant, no
//! positive cache, no connection-time carry-over. The
//! three-state decision is the contract:
//!
//! - an authorized frame ⇒ `Deliver`;
//! - a scope-tightened frame ⇒ `SkipMessage` (binding
//!   kept);
//! - a lost edge / lost resource / failing read ⇒
//!   `InvalidateBinding` or a desensitized
//!   `ApplicationError` — never `Deliver`, never a raw
//!   storage error.
//!
//! The fixture prepares REAL committed
//! states through the implementation under test and
//! pairs them with REAL frames; the harness asserts the
//! decision DIRECTIONS of those states plus the
//! per-frame independence (repeated and interleaved
//! decisions must not drift).
//!
//! Conformance entry:
//! `bcs-app-session/tests/conformance_delivery_authorization.rs`.

#![allow(clippy::expect_used, reason = "test harness — panic on failure is the contract")]

use async_trait::async_trait;

use bcs_service_api::application::v1::delivery_authorization::{
    DeliveryAuthorizationService, DeliveryDecision,
    DeliveryVisibility, ProtectedDeliveryBinding,
};

/// Prepared REAL state + REAL frame pairs.
#[async_trait]
pub trait DeliveryAuthorizationFixture: Send + Sync {
    /// The service under test (the real composition; the
    /// harness never relaxes it).
    fn service(
        &self,
    ) -> std::sync::Arc<dyn DeliveryAuthorizationService>;

    /// A live, fully-authorized view over committed
    /// resource facts, paired with a frame that view
    /// may deliver.
    async fn authorized_frame(
        &self,
    ) -> (ProtectedDeliveryBinding, DeliveryVisibility);

    /// The same authorization facts with a scope that
    /// forbids the paired frame's artifact (scope-only
    /// tightening): the binding survives, the frame
    /// does not.
    async fn scope_restricted_frame(
        &self,
    ) -> (ProtectedDeliveryBinding, DeliveryVisibility);

    /// A view that LOST its management edge or its
    /// resource qualification (revocation actually
    /// committed through the implementation), paired
    /// with a frame.
    async fn revoked_frame(
        &self,
    ) -> (ProtectedDeliveryBinding, DeliveryVisibility);
}

async fn decide(
    service: &dyn DeliveryAuthorizationService,
    binding: &ProtectedDeliveryBinding,
    visibility: &DeliveryVisibility,
) -> Result<DeliveryDecision, bcs_service_api::application::v1::error::ApplicationError>
{
    service.authorize_delivery(binding, visibility).await
}

/// Real final-writer semantics over prepared state.
pub async fn delivery_authorization_service_contract_tests(
    fixture: &dyn DeliveryAuthorizationFixture,
) {
    let service = fixture.service();

    let (binding, visibility) = fixture.authorized_frame().await;
    assert_eq!(
        decide(service.as_ref(), &binding, &visibility)
            .await
            .expect("authorized frame must not be a read failure"),
        DeliveryDecision::Deliver,
        "a fully-authorized frame is delivered"
    );

    let (tight, tight_visibility) =
        fixture.scope_restricted_frame().await;
    assert_eq!(
        decide(service.as_ref(), &tight, &tight_visibility)
            .await
            .expect("scope check must not be a read failure"),
        DeliveryDecision::SkipMessage,
        "a scope-only-tightened frame is skipped, binding kept"
    );

    // The revoked/lost-qualification view is NEVER
    // delivered: fail-closed (InvalidateBinding) or the
    // desensitized read error (which the adapter maps
    // to the invalidation).
    let (revoked, revoked_visibility) = fixture.revoked_frame().await;
    match decide(service.as_ref(), &revoked, &revoked_visibility).await {
        Ok(outcome) => assert_eq!(
            outcome,
            DeliveryDecision::InvalidateBinding,
            "a revoked view must invalidate"
        ),
        Err(_) => {
            // The desensitized failure keeps the raw
            // core/storage error inside the application.
        }
    }

    // Per-frame independence: no positive cache, no
    // cross-frame carry-over — the same queries decide
    // identically when repeated and interleaved.
    let repeat = decide(service.as_ref(), &binding, &visibility)
        .await
        .expect("repeat authorized decision");
    assert_eq!(repeat, DeliveryDecision::Deliver);
    // An interleaved denied view must not flip the live
    // one (decisions are per-frame facts, not state on
    // the service).
    let _ = decide(service.as_ref(), &revoked, &revoked_visibility)
        .await;
    let repeat_after = decide(service.as_ref(), &binding, &visibility)
        .await
        .expect("authorized decision after interleaving");
    assert_eq!(
        repeat_after, repeat,
        "an interleaved denied frame must not flip a live one"
    );
}
