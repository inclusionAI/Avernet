#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "contract assertions"
)]
use bcs_store_api::{document::DocumentStorePlugin, lease::*, *};
use bcs_test_support::store_model::Model;
use serde_json::json;
use std::time::Duration;

#[tokio::test]
async fn document_replace_is_exact_and_stale_or_missing_writes_cannot_create() {
    let p = Model::default();
    let WriteOutcome::Applied { version } = p
        .create_document("doc", &json!({"rev":7,"a":{"x":1,"y":2}}))
        .await
        .unwrap()
    else {
        panic!()
    };
    assert_eq!(
        p.create_document("doc", &json!(0)).await.unwrap(),
        WriteOutcome::ConditionNotMet
    );
    let value = json!({"rev":null,"a":{"x":null},"_store":{"version":"business"}});
    assert!(matches!(
        p.replace_document("doc", version, &value).await.unwrap(),
        WriteOutcome::Applied { .. }
    ));
    assert_eq!(p.read_document("doc").await.unwrap().unwrap().value, value);
    assert_eq!(
        p.replace_document("doc", version, &json!(0)).await.unwrap(),
        WriteOutcome::ConditionNotMet
    );
    assert_eq!(
        p.replace_document("missing", version, &json!(0))
            .await
            .unwrap(),
        WriteOutcome::ConditionNotMet
    );
}

#[tokio::test]
async fn expired_and_retired_registration_never_revive() {
    let p = Model::default();
    let ttl = Duration::from_secs(3);
    let LeaseRegistration::Active(first) =
        p.register_lease("instance", b"worker", ttl).await.unwrap()
    else {
        panic!()
    };
    assert!(matches!(
        p.register_lease("instance", b"other", ttl).await.unwrap(),
        LeaseRegistration::ContentConflict
    ));
    p.advance(ttl);
    assert_eq!(
        p.read_lease("instance").await.unwrap(),
        LeaseState::Terminated
    );
    assert_eq!(
        p.update_lease("instance", first.version, b"worker", Some(ttl))
            .await
            .unwrap(),
        LeaseWrite::Terminated
    );
    assert_eq!(
        p.register_lease("instance", b"worker", ttl).await.unwrap(),
        LeaseRegistration::Terminated
    );
    p.retire_lease("delayed-register").await.unwrap();
    assert_eq!(
        p.register_lease("delayed-register", b"worker", ttl)
            .await
            .unwrap(),
        LeaseRegistration::Terminated
    );
}

#[tokio::test]
async fn lease_update_compares_version_and_never_revives_missing() {
    let p = Model::default();
    let ttl = Duration::from_secs(3);
    let LeaseRegistration::Active(first) = p.register_lease("live", b"running", ttl).await.unwrap()
    else {
        panic!()
    };
    assert!(matches!(
        p.update_lease("live", first.version, b"draining", None)
            .await
            .unwrap(),
        LeaseWrite::Applied(_)
    ));
    assert_eq!(
        p.update_lease("live", first.version, b"running", Some(ttl))
            .await
            .unwrap(),
        LeaseWrite::ConditionNotMet
    );
    assert_eq!(
        p.update_lease("missing", first.version, b"running", Some(ttl))
            .await
            .unwrap(),
        LeaseWrite::ConditionNotMet
    );
    assert_eq!(p.read_lease("missing").await.unwrap(), LeaseState::Absent);
}

#[tokio::test]
async fn document_provider_conformance() {
    bcs_test_support::contract::plugin::store::document::document_store_contract_tests(
        &Model::default(),
        "contract-document",
    )
    .await;
}

#[tokio::test]
async fn registration_retry_does_not_extend_deadline_and_uses_initial_identity() {
    let p = Model::default();
    let ttl = Duration::from_secs(3);
    let LeaseRegistration::Active(first) =
        p.register_lease("retry", b"running", ttl).await.unwrap()
    else {
        panic!()
    };
    p.advance(Duration::from_secs(1));
    assert!(matches!(
        p.update_lease("retry", first.version, b"draining", None)
            .await
            .unwrap(),
        LeaseWrite::Applied(_)
    ));
    let LeaseRegistration::Active(retry) =
        p.register_lease("retry", b"running", ttl).await.unwrap()
    else {
        panic!()
    };
    assert_eq!(retry.value, b"draining");
    assert_eq!(retry.remaining, Duration::from_secs(2));
    p.advance(Duration::from_secs(2));
    assert_eq!(p.read_lease("retry").await.unwrap(), LeaseState::Terminated);
}
#[tokio::test]
async fn lost_retirement_response_is_resolved_by_stable_terminal_state() {
    let p = Model::default();
    p.lose_next_response();
    assert!(matches!(
        p.retire_lease("pending").await,
        Err(StoreError::OutcomeUnknown(_))
    ));
    assert_eq!(
        p.read_lease("pending").await.unwrap(),
        LeaseState::Terminated
    );
    assert_eq!(
        p.register_lease("pending", b"late", Duration::from_secs(3))
            .await
            .unwrap(),
        LeaseRegistration::Terminated
    );
}
#[tokio::test]
async fn missing_update_does_not_claim_stable_termination() {
    let p = Model::default();
    assert_eq!(
        p.update_lease("new", Version(1), b"late", Some(Duration::from_secs(3)))
            .await
            .unwrap(),
        LeaseWrite::ConditionNotMet
    );
    assert!(matches!(
        p.register_lease("new", b"first", Duration::from_secs(3))
            .await
            .unwrap(),
        LeaseRegistration::Active(_)
    ));
}
#[tokio::test]
async fn document_unknown_response_can_be_resolved_by_reading_content() {
    let p = Model::default();
    p.lose_next_response();
    let value = json!({"request_id":"r1","state":null});
    assert!(matches!(
        p.create_document("unknown", &value).await,
        Err(StoreError::OutcomeUnknown(_))
    ));
    assert_eq!(
        p.read_document("unknown").await.unwrap().unwrap().value,
        value
    );
}

#[tokio::test]
async fn lease_provider_conformance() {
    use bcs_test_support::contract::plugin::store::lease::*;
    let model = Model::default();
    lease_store_contract_tests(&model, "contract-lease").await;
    lease_expiry_contract_tests(&model, "contract-lease", |duration| {
        model.advance(duration);
        std::future::ready(())
    }).await;
}

#[tokio::test]
#[should_panic(expected = "same-version replacements have exactly one winner")]
async fn document_contract_rejects_read_then_write_cas() {
    let mut model = Model::default();
    model.broken_json_cas = true;
    bcs_test_support::contract::plugin::store::document::document_concurrency_contract_tests(&model, "broken-document").await;
}

#[tokio::test]
async fn final_document_and_lease_versions_reject_without_mutation() {
    use bcs_test_support::contract::plugin::store::{document::*, lease::*};
    let model = Model::default();
    model.create_document("exhausted-document", &json!({"preserved":null})).await.unwrap();
    model.exhaust_document_version("exhausted-document");
    document_version_exhaustion_contract_tests(&model, "exhausted-document").await;
    model.register_lease("exhausted-lease", b"preserved", Duration::from_secs(3)).await.unwrap();
    model.exhaust_lease_version("exhausted-lease");
    lease_version_exhaustion_contract_tests(&model, "exhausted-lease", |duration| {
        model.advance(duration);
        std::future::ready(())
    }).await;
}
