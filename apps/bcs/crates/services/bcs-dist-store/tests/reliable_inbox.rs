#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]
mod common;
use bcs_dist_model::*;
use bcs_dist_store::{inbox::*, layout::KeyLayout};
use bcs_service_api::port::repo::distributed::{admission::*, inbox::*};
use common::*;
use std::sync::Arc;

fn provider(store: Arc<Recorder>) -> StoreInboxProducer {
    StoreInboxProducer::new(
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
async fn canonical_identity_is_scope_wide_across_shards() {
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
async fn every_lost_write_repairs_without_a_caller_retry() {
    let baseline = Arc::new(Recorder::default());
    provider(baseline.clone())
        .admit(request("baseline"))
        .await
        .unwrap();
    let writes = baseline
        .calls()
        .iter()
        .filter(|c| !c.starts_with("document.read"))
        .count();
    for failed in 1..=writes {
        let store = Arc::new(Recorder::default());
        store.lose_write(failed);
        let p = provider(store.clone());
        let _ = p.admit(request("crash")).await;
        let restarted = provider(store);
        for _ in 0..8 {
            let _ = restarted.repair(ShardId(42)).await;
        }
        // Only the read here uses the original identity; no repair via admit.
        assert!(
            restarted
                .admission_decision(&scope(), &InputId("crash".into()))
                .await
                .unwrap()
                .is_some(),
            "write {failed}"
        );
    }
}

#[tokio::test]
async fn pending_canonical_retry_never_reports_definite_failure_then_accepts_later() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone());
    store.lose_write(4); // canonical create succeeded; journal remains repairable.
    assert!(p.admit(request("pending-retry")).await.is_err());
    store.fail_write(1); // retry's first missing step definitely fails.
    assert!(matches!(
        p.admit(request("pending-retry")).await,
        Err(bcs_service_api::port::repo::distributed::common::StoreError::OutcomeUnknown { .. })
    ));
    for _ in 0..8 {
        p.repair(ShardId(42)).await.unwrap();
    }
    assert!(matches!(
        p.admission_decision(&scope(), &InputId("pending-retry".into()))
            .await
            .unwrap(),
        Some(AdmitOutcome::Duplicate(_))
    ));
}

#[tokio::test]
async fn precanonical_read_failures_remain_unknown_and_repair_without_retry() {
    use bcs_service_api::port::repo::distributed::common::StoreError;
    for boundary in [":spec:", ":task:", ":canonical:", ":admission-root"] {
        let store = Arc::new(Recorder::default());
        let p = provider(store.clone());
        let pause = store.pause_after("before.document.create", ":canonical:", 1);
        let pending = tokio::spawn({
            let p = p.clone();
            async move { p.admit(request("precanonical")).await }
        });
        pause.reached().await;
        pending.abort();
        let _ = pending.await;
        store.fail_document_read(boundary);
        let cold = provider(store.clone());
        assert!(
            matches!(
                cold.admit(request("precanonical")).await,
                Err(StoreError::OutcomeUnknown { .. })
            ),
            "read {boundary}"
        );
        for _ in 0..8 {
            cold.repair(ShardId(42)).await.unwrap();
        }
        assert!(
            matches!(
                cold.admission_decision(&scope(), &InputId("precanonical".into()))
                    .await
                    .unwrap(),
                Some(AdmitOutcome::Duplicate(_))
            ),
            "repair {boundary}"
        );
    }
}

#[tokio::test]
async fn cold_retry_assists_materialized_intent_before_canonical_selection() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone());
    let pause = store.pause_after("before.document.create", ":canonical:", 1);
    let pending = tokio::spawn({
        let p = p.clone();
        async move { p.admit(request("precanonical")).await }
    });
    pause.reached().await;
    pending.abort();
    let _ = pending.await;
    assert!(matches!(
        provider(store)
            .admit(request("precanonical"))
            .await
            .unwrap(),
        AdmitOutcome::Accepted(_)
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
