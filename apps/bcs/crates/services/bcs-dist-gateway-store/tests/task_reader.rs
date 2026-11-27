//! restore, validate_resume, list and the session store over the store model.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]

mod common;

use bcs_dist_gateway_store::{GatewaySessionStore, WORKER_ONLY};
use bcs_dist_model::*;
use bcs_dist_store::codec::encode_json;
use bcs_service_api::port::repo::distributed::common::{PageRequest, StoreError};
use bcs_service_api::port::repo::distributed::session::SessionStorePort;
use bcs_service_api::port::repo::distributed::task_feed::*;
use bcs_store_api::document::DocumentStorePlugin;
use bcs_store_api::{StoreCapability, StorePlugin};
use bcs_test_support::store_model::Model;
use common::*;
use std::num::NonZeroU32;
use std::sync::Arc;

fn tid() -> Tid {
    Tid(TID.into())
}

fn cursor_at(value: &str) -> TaskCursor {
    TaskCursor {
        sid: Sid(SID.into()),
        tid: tid(),
        token: ReplayToken {
            backend: BackendId("tbase".into()),
            generation: LogGeneration("1".into()),
            value: value.into(),
        },
    }
}

#[tokio::test]
async fn restore_returns_the_stored_snapshot_with_the_cursor_at_its_coverage() {
    let f = fixture();
    f.worker.create(&snapshot(TID, TaskStatus::Submitted, None, 1)).await;
    // Nothing covered yet: the cursor is before the first entry.
    let Some(RestoredTask::Live { view, cursor }) = f.reader.restore(&scope(), &tid()).await.unwrap() else {
        panic!("expected a live task");
    };
    assert_eq!(view.content.status, TaskStatus::Submitted);
    assert_eq!(view.covered_through, None);
    assert_eq!(cursor, cursor_at("0-0"));

    // Later entries are NOT folded into the view (decision 30).
    f.worker.append(&status_event(TID, eid(12, 1), TaskStatus::Working)).await;
    f.worker.publish(&snapshot(TID, TaskStatus::Working, Some(eid(12, 1)), 1)).await;
    f.worker.append(&text_event(TID, eid(13, 1), "later")).await;
    let Some(RestoredTask::Live { view, cursor }) = f.reader.restore(&scope(), &tid()).await.unwrap() else {
        panic!("expected a live task");
    };
    assert_eq!(view.covered_through, Some(eid(12, 1)));
    assert_eq!(cursor, cursor_at("12-1"));
}

#[tokio::test]
async fn restore_of_a_finished_or_expired_task_is_final() {
    let f = fixture();
    f.worker.create(&snapshot(TID, TaskStatus::Completed, Some(eid(9, 1)), 1)).await;
    assert!(matches!(
        f.reader.restore(&scope(), &tid()).await.unwrap(),
        Some(RestoredTask::Final(view)) if view.content.status == TaskStatus::Completed
    ));
    // After the task key expired the final record answers.
    f.worker.expire(TID).await;
    assert_eq!(f.reader.restore(&scope(), &tid()).await.unwrap(), None);
    f.results.put(final_record(TID));
    let Some(RestoredTask::Final(view)) = f.reader.restore(&scope(), &tid()).await.unwrap() else {
        panic!("expected the archived task");
    };
    assert_eq!(view.content.status, TaskStatus::Completed);
    assert_eq!(view.content.snapshot, final_record(TID).snapshot);
}

#[tokio::test]
async fn fresh_group_listing_after_expiry_has_a_usable_cursor() {
    use bcs_service_api::port::repo::distributed::group_listing::{GroupIndexQuery, GroupListingPort};
    use bcs_store_api::zset::{ZAddMode, ZSetEntry, ZSetStorePlugin};
    use std::sync::atomic::{AtomicI64, Ordering};

    let clock = Arc::new(AtomicI64::new(NOW));
    let f = fixture_with(|config| {
        let clock = clock.clone();
        config.now = Arc::new(move || TimestampMillis(clock.load(Ordering::SeqCst)));
    });
    let sid = Sid("s0042.g1111111111111111".into());
    let spec = session_spec(&sid.0, scope());
    f.model.create_document(&layout().spec(&sid).unwrap(), &encode_json(&spec).unwrap()).await.unwrap();
    for suffix in ["one", "two"] {
        let mut task = snapshot(&format!("{}.{suffix}", sid.0), TaskStatus::Working, None, NOW);
        task.sid = sid.clone();
        f.worker.create(&task).await;
        f.model.add(&layout().tasks(&scope()).unwrap(), &[ZSetEntry { member: task.tid.0, score: 0.0 }], ZAddMode::InsertOnly).await.unwrap();
    }
    let query = GroupIndexQuery { statuses: vec![], limit: NonZeroU32::MIN, load_rows: false };
    let first = f.reader.first_page(&scope(), &spec.group, &query, TimestampMillis(NOW)).await.unwrap();
    let old = first.next.unwrap();
    let later = TimestampMillis(NOW + 86_400_000);
    clock.store(later.0, Ordering::SeqCst);
    let fresh = f.reader.first_page(&scope(), &spec.group, &query, later).await.unwrap();
    let page = f.reader.continue_page(&scope(), &spec.group, &fresh.next.unwrap(), false, later).await.unwrap();
    assert_eq!(page.rows.len(), 1);
    assert_ne!(page.rows[0].tid, fresh.rows[0].tid);
    assert!(f.reader.continue_page(&scope(), &spec.group, &old, false, later).await.is_err());
}

#[tokio::test]
async fn another_scope_sees_no_task() {
    let f = fixture();
    f.worker.create(&snapshot(TID, TaskStatus::Working, Some(eid(2, 1)), 1)).await;
    assert_eq!(f.reader.restore(&other_scope(), &tid()).await.unwrap(), None);
    assert_eq!(
        f.reader.validate_resume(&other_scope(), &cursor_at("2-1")).await.unwrap(),
        ResumeCheck::InvalidScope
    );
}

#[tokio::test]
async fn resume_is_valid_only_at_or_after_coverage_of_a_running_task() {
    let f = fixture();
    f.worker.create(&snapshot(TID, TaskStatus::Working, Some(eid(12, 1)), 1)).await;
    f.worker.append(&status_event(TID, eid(12, 1), TaskStatus::Working)).await;
    f.worker.append(&text_event(TID, eid(13, 4), "newest")).await;
    let check = |value: &'static str| {
        let reader = &f.reader;
        async move { reader.validate_resume(&scope(), &cursor_at(value)).await.unwrap() }
    };
    assert_eq!(check("12-1").await, ResumeCheck::Valid);
    assert_eq!(check("13-4").await, ResumeCheck::Valid);
    // Before the coverage: those entries may be trimmed.
    assert_eq!(check("11-9").await, ResumeCheck::Gap);
    assert_eq!(check("not-a-position").await, ResumeCheck::Gap);
    // Beyond the newest entry: never handed out, so never followed.
    assert_eq!(check("13-5").await, ResumeCheck::Gap);
    assert_eq!(check("999999999-1").await, ResumeCheck::Gap);

    let mut other_backend = cursor_at("12-1");
    other_backend.token.backend = BackendId("other".into());
    assert_eq!(f.reader.validate_resume(&scope(), &other_backend).await.unwrap(), ResumeCheck::Gap);
    let mut other_generation = cursor_at("12-1");
    other_generation.token.generation = LogGeneration("2".into());
    assert_eq!(f.reader.validate_resume(&scope(), &other_generation).await.unwrap(), ResumeCheck::Gap);

    let mut foreign = cursor_at("12-1");
    foreign.tid = Tid("s0042.other.b21e".into());
    assert_eq!(f.reader.validate_resume(&scope(), &foreign).await.unwrap(), ResumeCheck::InvalidScope);
}

#[tokio::test]
async fn resume_on_a_finished_or_expired_task_is_a_gap() {
    let f = fixture();
    f.worker.create(&snapshot(TID, TaskStatus::Completed, Some(eid(9, 1)), 1)).await;
    assert_eq!(f.reader.validate_resume(&scope(), &cursor_at("9-1")).await.unwrap(), ResumeCheck::Gap);
    assert_eq!(f.reader.validate_resume(&scope(), &cursor_at("3-1")).await.unwrap(), ResumeCheck::Gap);
    f.worker.expire(TID).await;
    assert_eq!(f.reader.validate_resume(&scope(), &cursor_at("9-1")).await.unwrap(), ResumeCheck::Gap);
}

fn page(after: Option<PageToken>, limit: u32) -> PageRequest {
    PageRequest {
        after,
        limit: NonZeroU32::new(limit).unwrap(),
    }
}

fn session_filter(statuses: Vec<TaskStatus>) -> TaskFilter {
    TaskFilter {
        sid: Some(Sid(SID.into())),
        statuses,
    }
}

#[tokio::test]
async fn list_pages_a_session_in_creation_order() {
    let f = fixture();
    let names = ["a001", "a002", "a003", "a004", "a005"];
    for (n, name) in names.iter().enumerate() {
        let status = if n < 2 { TaskStatus::Completed } else { TaskStatus::Working };
        f.worker
            .create(&snapshot(&format!("{SID}.{name}"), status, None, 100 + n as i64))
            .await;
    }
    // The oldest task expired; its final record is listed instead.
    f.worker.expire(&format!("{SID}.a001")).await;
    f.results.put(final_record(&format!("{SID}.a001")));

    let first = f.reader.list(&scope(), session_filter(vec![]), page(None, 2)).await.unwrap();
    let ids = |p: &[TaskView]| p.iter().map(|v| v.tid.0.clone()).collect::<Vec<_>>();
    assert_eq!(ids(&first.items), [format!("{SID}.a001"), format!("{SID}.a002")]);
    let second = f.reader.list(&scope(), session_filter(vec![]), page(first.next, 2)).await.unwrap();
    assert_eq!(ids(&second.items), [format!("{SID}.a003"), format!("{SID}.a004")]);
    let third = f.reader.list(&scope(), session_filter(vec![]), page(second.next, 2)).await.unwrap();
    assert_eq!(ids(&third.items), [format!("{SID}.a005")]);
    assert_eq!(third.next, None);
}

#[tokio::test]
async fn list_filters_by_status_and_scope_and_requires_a_session() {
    let f = fixture();
    f.worker.create(&snapshot(&format!("{SID}.a001"), TaskStatus::Completed, None, 1)).await;
    f.worker.create(&snapshot(&format!("{SID}.a002"), TaskStatus::Working, None, 2)).await;
    let working = f
        .reader
        .list(&scope(), session_filter(vec![TaskStatus::Working]), page(None, 10))
        .await
        .unwrap();
    assert_eq!(working.items.len(), 1);
    assert_eq!(working.items[0].tid.0, format!("{SID}.a002"));

    let foreign = f.reader.list(&other_scope(), session_filter(vec![]), page(None, 10)).await.unwrap();
    assert!(foreign.items.is_empty());

    let no_session = TaskFilter {
        sid: None,
        statuses: vec![],
    };
    assert_eq!(
        f.reader.list(&scope(), no_session, page(None, 10)).await,
        Err(StoreError::Invalid {
            code: "session_filter_required".into()
        })
    );
    assert_eq!(
        f.reader
            .list(&scope(), session_filter(vec![]), page(Some(PageToken("garbage".into())), 10))
            .await,
        Err(StoreError::Invalid {
            code: "page_token".into()
        })
    );
}

fn spec() -> SessionSpec {
    SessionSpec {
        schema_version: 1,
        sid: Sid(SID.into()),
        shard: ShardId(42),
        scope: scope(),
        kind: SessionKind::FsmGroup,
        group: GroupId("grp-research-01".into()),
        workflow: WorkflowRef {
            definition_id: "wf".into(),
            revision: "7".into(),
            digest: Digest("sha256:ab12".into()),
        },
        participants: vec![],
        limits: SessionLimits {
            max_queued_tasks: 16,
            max_task_steps: 200,
            task_timeout_millis: 1_800_000,
            max_inline_bytes: 65_536,
        },
        digest: Digest("sha256:3f1a".into()),
        created_at: TimestampMillis(1),
    }
}

#[tokio::test]
async fn session_store_reads_the_spec_and_refuses_worker_operations() {
    let model = Arc::new(Model::default());
    let store = GatewaySessionStore::new(model.clone(), layout()).unwrap();
    let sid = Sid(SID.into());
    assert_eq!(store.get_spec(&sid).await.unwrap(), None);
    model
        .create_document(&layout().spec(&sid).unwrap(), &encode_json(&spec()).unwrap())
        .await
        .unwrap();
    assert_eq!(store.get_spec(&sid).await.unwrap(), Some(spec()));

    let refused = StoreError::NotApplied {
        code: WORKER_ONLY.into(),
    };
    assert_eq!(store.load(&sid).await, Err(refused));
}

struct NoJson(Model);
impl StorePlugin for NoJson {
    fn supports(&self, capability: StoreCapability) -> bool {
        capability != StoreCapability::DocumentCas && self.0.supports(capability)
    }
}

#[tokio::test]
async fn a_store_without_a_required_capability_is_refused_at_startup() {
    // `require` only asks `supports`, so a plugin that denies DocumentCas is enough.
    let plugin = NoJson(Model::default());
    let error = bcs_dist_store::capability::require(&[(&plugin, StoreCapability::DocumentCas)]).unwrap_err();
    assert_eq!(
        error,
        StoreError::NotApplied {
            code: "missing_store_capability:DocumentCas".into()
        }
    );
}
