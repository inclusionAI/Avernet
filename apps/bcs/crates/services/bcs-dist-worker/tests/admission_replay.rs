#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]
#[path = "../../bcs-dist-store/tests/common/mod.rs"]
mod common;
use bcs_dist_model::*;
use bcs_dist_worker::WorkerTaskAdmission;
use bcs_service_api::core::dist_admission::{TaskAdmissionCommand, TaskAdmissionCoreService};
use bcs_service_api::port::{dist_agent::FrozenGroupDefinition, repo::distributed::inbox::*};
use common::*;
use std::sync::{
    Arc,
    atomic::{AtomicI64, Ordering},
};

fn command() -> TaskAdmissionCommand {
    let r = AdmitRequest {
        scope: scope(),
        input: user_input("same-input", "hello"),
        create_session: Some(spec("g")),
        create_task: Some(task()),
    };
    let s = r.create_session.unwrap();
    TaskAdmissionCommand {
        input: r.input,
        start_task: true,
        create_session: Some(FrozenGroupDefinition {
            group: s.group,
            workflow: s.workflow,
            participants: s.participants,
            limits: s.limits,
        }),
    }
}

#[tokio::test]
async fn retry_reuses_initial_request_after_time_and_definition_change() {
    let store = Arc::new(Recorder::default());
    let inbox = Arc::new(producer(&store));
    let now = Arc::new(AtomicI64::new(NOW));
    let clock = now.clone();
    let service = WorkerTaskAdmission::new(
        inbox.clone(),
        Arc::new(move || TimestampMillis(clock.load(Ordering::SeqCst))),
    );
    let original = command();
    let first = service.admit(&scope(), original.clone()).await.unwrap();
    let AdmitOutcome::Accepted(receipt) = first else {
        panic!("{first:?}")
    };
    now.fetch_add(10000, Ordering::SeqCst);
    let mut retry = original.clone();
    retry.create_session.as_mut().unwrap().workflow.revision = "new-config".into();
    assert_eq!(
        service.admit(&scope(), retry).await.unwrap(),
        AdmitOutcome::Duplicate(receipt)
    );
    let mut changed = original;
    changed.input.body = InputBody::User {
        payload: Payload::Inline(serde_json::json!("changed")),
    };
    assert_eq!(
        service.admit(&scope(), changed).await.unwrap(),
        AdmitOutcome::ContentConflict
    );
    assert_eq!(inbox_len(&store).await, 1);
}

#[tokio::test]
async fn concurrent_first_admits_reuse_the_record_that_wins_create_cas() {
    let store = Arc::new(Recorder::default());
    let inbox = Arc::new(producer(&store));
    let losing = WorkerTaskAdmission::new(inbox.clone(), Arc::new(|| TimestampMillis(1)));
    let winning = WorkerTaskAdmission::new(inbox, Arc::new(|| TimestampMillis(2)));
    let pause = store.pause_after("before.document.create", ":admission:", 1);
    let losing = tokio::spawn(async move { losing.admit(&scope(), command()).await });
    pause.reached().await;
    let winner = winning.admit(&scope(), command()).await;
    pause.resume();
    let AdmitOutcome::Accepted(receipt) = winner.unwrap() else {
        panic!("the winning create must accept the request");
    };
    assert_eq!(losing.await.unwrap(), Ok(AdmitOutcome::Duplicate(receipt)));
    assert_eq!(inbox_len(&store).await, 1);
}

#[tokio::test]
async fn concurrent_admit_resumes_an_interrupted_creator_and_wakes_worker() {
    let store = Arc::new(Recorder::default());
    let inbox = Arc::new(producer(&store));
    let losing = WorkerTaskAdmission::new(inbox.clone(), Arc::new(|| TimestampMillis(NOW)));
    let winning = WorkerTaskAdmission::new(inbox.clone(), Arc::new(|| TimestampMillis(NOW)));
    let before_create = store.pause_after("before.document.create", ":admission:", 1);
    let losing = tokio::spawn(async move { losing.admit(&scope(), command()).await });
    before_create.reached().await;
    let after_create = store.pause_after("document.create", ":admission:", 1);
    let winning = tokio::spawn(async move { winning.admit(&scope(), command()).await });
    after_create.reached().await;
    winning.abort();
    assert!(winning.await.unwrap_err().is_cancelled());
    before_create.resume();
    let AdmitOutcome::Duplicate(receipt) = losing.await.unwrap().unwrap() else {
        panic!("the retry must finish the existing admission");
    };
    let pending = inbox.enumerate_pending(
        ShardId(42),
        bcs_service_api::port::repo::distributed::common::PageRequest {
            limit: std::num::NonZeroU32::new(1).unwrap(),
            after: None,
        },
    ).await.unwrap();
    assert_eq!(pending.items, vec![receipt.sid]);
    assert_eq!(inbox_len(&store).await, 1);
}

#[tokio::test]
async fn concurrent_changed_content_cannot_recover_as_a_duplicate() {
    let store = Arc::new(Recorder::default());
    let inbox = Arc::new(producer(&store));
    let raced = Arc::new(RacingInbox {
        inner: inbox.clone(),
        barrier: Arc::new(tokio::sync::Barrier::new(2)),
    });
    let one = WorkerTaskAdmission::new(raced.clone(), Arc::new(|| TimestampMillis(1)));
    let two = WorkerTaskAdmission::new(raced, Arc::new(|| TimestampMillis(2)));
    let original = command();
    let mut changed = command();
    changed.input.body = InputBody::User {
        payload: Payload::Inline(serde_json::json!("different competing request")),
    };
    let scope = scope();
    let (a, b) = tokio::join!(
        one.admit(&scope, original.clone()),
        two.admit(&scope, changed.clone()),
    );
    let outcomes = [a.unwrap(), b.unwrap()];
    assert_eq!(outcomes.iter().filter(|o| matches!(o, AdmitOutcome::Accepted(_))).count(), 1);
    assert_eq!(outcomes.iter().filter(|o| **o == AdmitOutcome::ContentConflict).count(), 1);
    let canonical = inbox.lookup(&scope, &original.input.input_id).await.unwrap().unwrap();
    let winning_body = if matches!(outcomes[0], AdmitOutcome::Accepted(_)) {
        original.input.body
    } else {
        changed.input.body
    };
    assert_eq!(canonical.input.body, winning_body);
    assert_eq!(inbox_len(&store).await, 1);
}

struct RacingInbox {
    inner: Arc<bcs_dist_store::inbox::StoreInboxProducer>,
    barrier: Arc<tokio::sync::Barrier>,
}
#[async_trait::async_trait]
impl InboxProducerPort for RacingInbox {
    async fn lookup(
        &self,
        s: &TaskScope,
        id: &InputId,
    ) -> Result<Option<AdmitRequest>, bcs_service_api::port::repo::distributed::common::StoreError>
    {
        let r = self.inner.lookup(s, id).await?;
        if r.is_none() {
            self.barrier.wait().await;
        }
        Ok(r)
    }
    async fn admit(
        &self,
        r: AdmitRequest,
    ) -> Result<AdmitOutcome, bcs_service_api::port::repo::distributed::common::StoreError> {
        self.inner.admit(r).await
    }
    async fn append_stream(
        &self,
        r: AppendAgentEvent,
    ) -> Result<InboxPos, bcs_service_api::port::repo::distributed::common::StoreError> {
        self.inner.append_stream(r).await
    }
}

const NOW: i64 = 100;

#[tokio::test]
async fn stable_admission_conformance() {
    let store = Arc::new(Recorder::default());
    let service = WorkerTaskAdmission::new(
        Arc::new(producer(&store)),
        Arc::new(|| TimestampMillis(NOW)),
    );
    bcs_test_support::contract::distributed::admission::task_admission_core_service_contract_tests(
        &service,
        &scope(),
        command(),
        InputBody::User {
            payload: Payload::Inline(serde_json::json!("different")),
        },
    )
    .await
    .unwrap();
}

fn producer(store: &Arc<Recorder>) -> bcs_dist_store::inbox::StoreInboxProducer {
    use bcs_dist_store::{budget::*, inbox::*, layout::KeyLayout};
    StoreInboxProducer::new(
        Arc::new(SharedScanBudget::new(ScanBudgetConfig::default()).unwrap()),
        Arc::new(bcs_dist_projection::OutputProjectionService),
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        KeyLayout::new("c01", "main").unwrap(),
        Arc::new(FixedClock(NOW)),
        InboxProducerConfig::new(BackendId("tbase".into())),
    )
    .unwrap()
}
async fn inbox_len(store: &Recorder) -> usize {
    use bcs_store_api::stream::StreamStorePlugin;
    store
        .model
        .range(
            "bcn:v1:c01:main:s0042:inbox:s0042.7f3a9c",
            std::ops::Bound::Unbounded,
            std::ops::Bound::Unbounded,
            std::num::NonZeroU32::new(100).unwrap(),
            false,
        )
        .await
        .unwrap()
        .entries
        .len()
}

#[tokio::test]
async fn partial_admission_retries_preserve_repair_and_original_content() {
    for step in 1..=16 {
        let store = Arc::new(Recorder::default());
        let inbox = Arc::new(producer(&store));
        let now = Arc::new(AtomicI64::new(10));
        let clock = now.clone();
        let service = WorkerTaskAdmission::new(
            inbox.clone(),
            Arc::new(move || TimestampMillis(clock.load(Ordering::SeqCst))),
        );
        store.lose_write(step);
        let _first = service.admit(&scope(), command()).await;
        now.store(20, Ordering::SeqCst);
        let retry = service
            .admit(&scope(), command())
            .await
            .unwrap_or_else(|e| panic!("step {step}: {e:?}"));
        assert!(
            matches!(
                retry,
                AdmitOutcome::Accepted(_) | AdmitOutcome::Duplicate(_)
            ),
            "step {step}: {retry:?}"
        );
        let canonical = inbox
            .lookup(&scope(), &InputId("same-input".into()))
            .await
            .unwrap()
            .unwrap();
        assert_eq!(canonical.input.body, command().input.body);
        let mut changed = command();
        changed.input.tid = Tid(format!("{}.other", changed.input.sid.0));
        assert_eq!(
            service.admit(&scope(), changed).await.unwrap(),
            AdmitOutcome::ContentConflict
        );
    }
}
