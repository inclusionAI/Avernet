#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]
mod common;
use bcs_dist_model::*;
use bcs_dist_store::{inbox::*, layout::KeyLayout};
use bcs_service_api::port::repo::distributed::{admission::*, inbox::*};
use common::*;
use std::sync::Arc;

fn provider(store: Arc<Recorder>) -> StoreInboxProducer {
    StoreInboxProducer::new(
        Arc::new(
            bcs_dist_store::budget::SharedScanBudget::new(
                bcs_dist_store::budget::ScanBudgetConfig::default(),
            )
            .unwrap(),
        ),
        Arc::new(bcs_dist_projection::OutputProjectionService),
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        KeyLayout::new("test", "reliable").unwrap(),
        Arc::new(FixedClock(100)),
        InboxProducerConfig::new(BackendId("model".into())),
    )
    .unwrap()
}
fn request(id: &str) -> AdmitRequest {
    AdmitRequest {
        scope: scope(),
        input: user_input(id, "hello"),
        create_session: Some(spec("g")),
        create_task: Some(task()),
    }
}
#[tokio::test]
async fn caller_digest_cannot_hide_changed_business_content() {
    let store = Arc::new(Recorder::default());
    let p = provider(store);
    let a = request("same");
    assert!(matches!(
        p.admit(a.clone()).await.unwrap(),
        AdmitOutcome::Accepted(_)
    ));
    let mut b = a;
    b.input.body = InputBody::User {
        payload: Payload::Inline(serde_json::json!("changed")),
    };
    assert_eq!(p.admit(b).await.unwrap(), AdmitOutcome::ContentConflict);
}
#[tokio::test]
async fn admission_identity_is_scope_wide_across_shards() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone());
    assert!(matches!(
        p.admit(request("same")).await.unwrap(),
        AdmitOutcome::Accepted(_)
    ));
    let mut b = request("same");
    let sid = Sid("s0007.other".into());
    let tid = Tid("s0007.other.t".into());
    b.input.sid = sid.clone();
    b.input.tid = tid.clone();
    let s = b.create_session.as_mut().unwrap();
    s.sid = sid.clone();
    s.shard = ShardId(7);
    let t = b.create_task.as_mut().unwrap();
    t.sid = sid;
    t.tid = tid.clone();
    assert_eq!(p.admit(b).await.unwrap(), AdmitOutcome::ContentConflict);
    use bcs_store_api::document::DocumentStorePlugin;
    assert!(
        store
            .read_document(
                &KeyLayout::new("test", "reliable")
                    .unwrap()
                    .task(&tid)
                    .unwrap()
            )
            .await
            .unwrap()
            .is_none()
    );
}
#[tokio::test]
async fn retry_repairs_after_the_admission_document_was_created() {
    let store = Arc::new(Recorder::default());
    store.lose_write(1); // the admission create is applied, but its reply is lost.
    let p = provider(store.clone());
    assert!(matches!(
        p.admit(request("created-before-reply")).await,
        Err(bcs_service_api::port::repo::distributed::common::StoreError::OutcomeUnknown { .. })
    ));

    let retry = provider(store.clone());
    assert!(matches!(
        retry.admit(request("created-before-reply")).await,
        Ok(AdmitOutcome::Duplicate(_))
    ));
    assert!(retry
        .admission_decision(&scope(), &InputId("created-before-reply".into()))
        .await
        .unwrap()
        .is_some());
}

#[tokio::test]
async fn a_failure_before_admission_creation_is_recovered_only_by_caller_retry() {
    let store = Arc::new(Recorder::default());
    store.fail_write(1); // no admission document is created.
    let p = provider(store.clone());
    assert!(p.admit(request("before-create")).await.is_err());
    assert!(p
        .admission_decision(&scope(), &InputId("before-create".into()))
        .await
        .unwrap()
        .is_none());
    let maintenance = p.repair(ShardId(42)).await.unwrap();
    assert!(maintenance.round_complete);
    assert_eq!(maintenance.pending, 0);

    assert!(matches!(
        provider(store)
            .admit(request("before-create"))
            .await
            .unwrap(),
        AdmitOutcome::Accepted(_)
    ));
}

#[tokio::test]
async fn an_existing_admission_record_is_repaired_by_same_input_id_after_a_read_failure() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone());
    p.admit(request("read-retry")).await.unwrap();
    store.fail_document_read(":admission:");
    assert!(matches!(
        provider(store.clone())
            .admit(request("read-retry"))
            .await,
        Err(bcs_service_api::port::repo::distributed::common::StoreError::OutcomeUnknown { .. })
    ));
    assert!(matches!(
        provider(store)
            .admit(request("read-retry"))
            .await
            .unwrap(),
        AdmitOutcome::Duplicate(_)
    ));
}

fn other_target(mut r: AdmitRequest) -> AdmitRequest {
    let sid = Sid("s0007.other".into());
    let tid = Tid("s0007.other.task".into());
    r.input.sid = sid.clone();
    r.input.tid = tid.clone();
    let spec = r.create_session.as_mut().unwrap();
    spec.sid = sid.clone();
    spec.shard = ShardId(7);
    let task = r.create_task.as_mut().unwrap();
    task.sid = sid;
    task.tid = tid;
    r
}

#[tokio::test]
async fn concurrent_cross_shard_identity_has_one_winner_and_no_losing_task() {
    use bcs_store_api::document::DocumentStorePlugin;
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone());
    let a = request("race");
    let b = other_target(request("race"));
    let results = tokio::join!(p.admit(a.clone()), p.admit(b.clone()));
    let accepted = [&results.0, &results.1]
        .iter()
        .filter(|r| matches!(r, Ok(AdmitOutcome::Accepted(_))))
        .count();
    assert_eq!(accepted, 1);
    let loser = if matches!(results.0, Ok(AdmitOutcome::ContentConflict)) {
        a
    } else {
        assert!(matches!(results.1, Ok(AdmitOutcome::ContentConflict)));
        b
    };
    let key = KeyLayout::new("test", "reliable")
        .unwrap()
        .task(&loser.input.tid)
        .unwrap();
    assert!(store.model.read_document(&key).await.unwrap().is_none());
}

#[tokio::test]
async fn different_scope_can_independently_admit_the_same_input_id() {
    let store = Arc::new(Recorder::default());
    let p = provider(store);
    p.admit(request("same-scoped-id")).await.unwrap();
    let mut other = other_target(request("same-scoped-id"));
    other.scope.principal = PrincipalId("another-principal".into());
    other.create_session.as_mut().unwrap().scope = other.scope.clone();
    other.create_task.as_mut().unwrap().scope = other.scope.clone();
    assert!(matches!(
        p.admit(other).await.unwrap(),
        AdmitOutcome::Accepted(_)
    ));
}

#[tokio::test]
async fn invalid_frozen_output_limits_are_rejected_before_admission_creation() {
    use bcs_service_api::port::repo::distributed::common::StoreError;
    let store = Arc::new(Recorder::default());
    let p = provider(store);
    let mut r = request("invalid-profile");
    r.create_task
        .as_mut()
        .unwrap()
        .output_limits
        .max_checkpoint_bytes = 1024;
    assert!(matches!(p.admit(r).await, Err(StoreError::Invalid { .. })));
    assert!(
        p.admission_decision(&scope(), &InputId("invalid-profile".into()))
            .await
            .unwrap()
            .is_none()
    );
}
