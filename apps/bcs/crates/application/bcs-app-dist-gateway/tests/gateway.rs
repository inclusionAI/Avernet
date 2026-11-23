//! The Gateway use cases over the real core and store providers on the
//! in-memory store model; a fake Worker writes `ev` and `task`.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]

#[path = "../../../services/bcs-dist-gateway-store/tests/common/mod.rs"]
mod common;

use async_trait::async_trait;
use bcs_app_dist_gateway::{Clock, DistGateway, DistGatewayConfig};
use bcs_dist_gateway::DistGatewayCore;
use bcs_dist_model::*;
use bcs_service_api::application::dist_gateway::*;
use bcs_service_api::application::principal::{BotActor, CallerContext};
use bcs_service_api::port::dist_agent::*;
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_service_api::port::repo::distributed::inbox::*;
use bcs_test_support::contract::distributed::services::dist_gateway_service_contract_tests;
use common::{FakeWorker, Ports, eid, ports, question, snapshot, status_event, text_event};
use futures::StreamExt;
use std::num::NonZeroU32;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

#[derive(Default)]
struct Groups {
    /// The directory cannot answer: every resolve fails as unavailable.
    down: AtomicBool,
}
#[async_trait]
impl DistGroupDirectoryPort for Groups {
    async fn resolve(&self, _scope: &TaskScope, group: &GroupId) -> Result<FrozenGroupDefinition, GroupDirectoryError> {
        if self.down.load(Ordering::SeqCst) {
            return Err(GroupDirectoryError::Unavailable);
        }
        if group.0 == "grp-missing" {
            return Err(GroupDirectoryError::NotFound);
        }
        Ok(FrozenGroupDefinition {
            // A directory that answers an alias with its canonical id.
            group: GroupId(group.0.to_lowercase()),
            workflow: WorkflowRef {
                definition_id: "wf-research".into(),
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
        })
    }
    async fn load_workflow(
        &self,
        _scope: &TaskScope,
        _reference: &WorkflowRef,
    ) -> Result<bcs_domain::collaboration::StateMachineDefinition, GroupDirectoryError> {
        Err(GroupDirectoryError::Unavailable)
    }
}

struct TestClock;
impl Clock for TestClock {
    fn now(&self) -> TimestampMillis {
        TimestampMillis(common::NOW)
    }
}

fn caller(bot: &str) -> GatewayCaller {
    GatewayCaller {
        tenant: TenantId("t-ant".into()),
        caller: CallerContext::Bot(BotActor {
            bot_uuid: bot.into(),
        }),
    }
}

fn me() -> GatewayCaller {
    caller("pa-001")
}

fn gateway_over(p: &Ports, inbox: Arc<dyn InboxProducerPort>) -> DistGateway {
    gateway_with(p, inbox, Arc::new(Groups::default()))
}

fn gateway_with(p: &Ports, inbox: Arc<dyn InboxProducerPort>, groups: Arc<Groups>) -> DistGateway {
    let core = DistGatewayCore::new(Arc::new(bcs_dist_worker::WorkerTaskAdmission::new(inbox, Arc::new(|| TimestampMillis(common::NOW)))), Arc::new(bcs_dist_worker::WorkerOutputReader::new(p.tasks.clone(), p.blobs.clone(), Arc::new(bcs_dist_a2a_output::A2aOutputAdapter))), p.sessions.clone());
    let mut config = DistGatewayConfig::new(64);
    config.snapshot_cover_retry = Duration::from_millis(10);
    config.snapshot_cover_limit = Duration::from_millis(300);
    DistGateway::new(Arc::new(core), groups, Arc::new(TestClock), config).unwrap()
}

fn gateway(p: &Ports) -> DistGateway {
    gateway_over(p, p.inbox.clone())
}

fn grp() -> GroupId {
    GroupId("grp-research-01".into())
}

fn message(id: &str, text: &str) -> SubmitInput {
    SubmitInput {
        group: grp(),
        message_id: RequestId(id.into()),
        session: None,
        task: None,
        interaction: None,
        content: Payload::Inline(serde_json::json!({ "parts": [{ "text": text }] })),
    }
}

/// What the Worker publishes for a task: the given status at the given coverage.
async fn publish(worker: &FakeWorker, tid: &Tid, status: TaskStatus, covered: Option<EventId>, questions: &[&str]) {
    let mut task = snapshot(&tid.0, status, covered, common::NOW);
    task.sid = Sid(tid.0.rsplit_once('.').unwrap().0.to_owned());
    task.scope = TaskScope {
        tenant: TenantId("t-ant".into()),
        principal: PrincipalId("bot:pa-001".into()),
    };
    task.content.interactions = questions.iter().map(|id| question(id)).collect();
    worker.publish(&task).await;
}

async fn next(stream: &mut TaskSubscription) -> TaskStreamItem {
    tokio::time::timeout(Duration::from_secs(5), stream.next())
        .await
        .expect("an item in time")
        .expect("stream open")
        .expect("no error")
}

fn event_ids(item: TaskStreamItem) -> (Vec<EventId>, String) {
    match item {
        TaskStreamItem::Events { events, cursor } => (events.iter().map(|e| e.id).collect(), cursor.token.value),
        other => panic!("expected events, got {other:?}"),
    }
}

#[tokio::test]
async fn the_application_contract_harness_passes() {
    let p = ports();
    dist_gateway_service_contract_tests(&gateway(&p), &me(), &caller("pa-002"), message("msg-1", "hello"))
        .await
        .unwrap();
}

#[tokio::test]
async fn the_same_message_selects_the_same_session_task_and_input() {
    let p = ports();
    let g = gateway(&p);
    let first = g.submit(&me(), message("msg-1", "hello")).await.unwrap();
    let again = g.submit(&me(), message("msg-1", "hello")).await.unwrap();
    assert_eq!(first, again);
    assert!(first.tid.0.starts_with(&format!("{}.", first.sid.0)));
    // 64 bits of the digest name the task within its session.
    assert_eq!(first.tid.0.rsplit_once('.').unwrap().1.len(), 16);
    assert!(first.sid.0.starts_with('s') && first.sid.0.contains('.'));

    // Changed content under the same message id is a conflict.
    assert_eq!(g.submit(&me(), message("msg-1", "bye")).await, Err(GatewayError::Conflict));
    // Another message, or another caller, gets another session.
    let other = g.submit(&me(), message("msg-2", "hello")).await.unwrap();
    assert_ne!(other.sid, first.sid);
    let theirs = g.submit(&caller("pa-002"), message("msg-1", "hello")).await.unwrap();
    assert_ne!(theirs.sid, first.sid);

    // A second task in the same session.
    let mut follow_up = message("msg-3", "next");
    follow_up.session = Some(first.sid.clone());
    let second = g.submit(&me(), follow_up.clone()).await.unwrap();
    assert_eq!(second.sid, first.sid);
    assert_ne!(second.tid, first.tid);
    assert_eq!(g.submit(&me(), follow_up).await.unwrap(), second);
}

/// Applies the admission, then reports its first outcome as lost.
struct LosesFirstResponse {
    inner: Arc<dyn InboxProducerPort>,
    lost: AtomicBool,
}
#[async_trait]
impl InboxProducerPort for LosesFirstResponse {
    async fn lookup(&self, scope: &TaskScope, id: &InputId) -> Result<Option<AdmitRequest>, StoreError> {
        self.inner.lookup(scope, id).await
    }

    async fn admit(&self, request: AdmitRequest) -> Result<AdmitOutcome, StoreError> {
        let id = request.input.input_id.0.clone();
        let outcome = self.inner.admit(request).await;
        if !self.lost.swap(true, Ordering::SeqCst) {
            return Err(StoreError::OutcomeUnknown { operation_id: id });
        }
        outcome
    }
    async fn append_stream(&self, request: AppendAgentEvent) -> Result<InboxPos, StoreError> {
        self.inner.append_stream(request).await
    }
}

#[tokio::test]
async fn an_unknown_outcome_names_the_request_and_the_retry_completes() {
    let p = ports();
    let g = gateway_over(
        &p,
        Arc::new(LosesFirstResponse {
            inner: p.inbox.clone(),
            lost: AtomicBool::new(false),
        }),
    );
    assert_eq!(
        g.submit(&me(), message("msg-1", "hello")).await,
        Err(GatewayError::OutcomeUnknown {
            request_id: RequestId("msg-1".into())
        })
    );
    let receipt = g.submit(&me(), message("msg-1", "hello")).await.unwrap();
    assert_eq!(g.get_task(&me(), &grp(), &receipt.tid).await.unwrap().content.status, TaskStatus::Submitted);
}

#[tokio::test]
async fn callers_only_reach_their_own_sessions_and_tasks() {
    let p = ports();
    let g = gateway(&p);
    let receipt = g.submit(&me(), message("msg-1", "hello")).await.unwrap();
    let other = caller("pa-002");
    assert_eq!(g.get_task(&other, &grp(), &receipt.tid).await, Err(GatewayError::NotFound));
    let mut intrude = message("msg-9", "hi");
    intrude.session = Some(receipt.sid.clone());
    // Another caller's session is reported as absent, not as forbidden.
    assert_eq!(g.submit(&other, intrude).await, Err(GatewayError::NotFound));
    let public = GatewayCaller {
        tenant: TenantId("t-ant".into()),
        caller: CallerContext::Public,
    };
    assert_eq!(g.submit(&public, message("msg-1", "hello")).await, Err(GatewayError::AccessDenied));
    // Malformed ids are rejected before any read.
    assert!(matches!(
        g.get_task(&me(), &grp(), &Tid("not-a-task".into())).await,
        Err(GatewayError::Invalid { .. })
    ));
    assert_eq!(
        g.submit(&me(), SubmitInput { group: GroupId("grp-missing".into()), ..message("msg-5", "x") }).await,
        Err(GatewayError::NotFound)
    );
}

#[tokio::test]
async fn a_new_subscriber_gets_the_snapshot_then_events_and_ends_on_input_required() {
    let p = ports();
    let g = gateway(&p);
    let receipt = g.submit(&me(), message("msg-1", "hello")).await.unwrap();
    let tid = receipt.tid.clone();
    let mut stream = g
        .subscribe(&me(), SubscribeTask { group: grp(), tid: tid.clone(), mode: SubscribeMode::Snapshot })
        .await
        .unwrap();
    let TaskStreamItem::Snapshot { view, cursor } = next(&mut stream).await else {
        panic!("the snapshot comes first");
    };
    assert_eq!(view.content.status, TaskStatus::Submitted);
    assert!(cursor.is_some());

    p.worker.append(&status_event(&tid.0, eid(1, 1), TaskStatus::Working)).await;
    p.worker.append(&text_event(&tid.0, eid(2, 1), "a")).await;
    p.worker.append(&status_event(&tid.0, eid(3, 1), TaskStatus::InputRequired)).await;
    assert_eq!(event_ids(next(&mut stream).await), (vec![eid(1, 1)], "1-1".into()));
    assert_eq!(event_ids(next(&mut stream).await), (vec![eid(2, 1)], "2-1".into()));
    assert_eq!(event_ids(next(&mut stream).await), (vec![eid(3, 1)], "3-1".into()));
    assert!(stream.next().await.is_none(), "the stream ends when the task asks for input");
}

#[tokio::test]
async fn a_resume_gets_only_later_events_and_an_unusable_cursor_gets_the_snapshot() {
    let p = ports();
    let g = gateway(&p);
    let tid = g.submit(&me(), message("msg-1", "hello")).await.unwrap().tid;
    for n in 1..=3 {
        p.worker.append(&text_event(&tid.0, eid(n, 1), "x")).await;
    }
    publish(&p.worker, &tid, TaskStatus::Working, Some(eid(2, 1)), &[]).await;

    let mut first = g
        .subscribe(&me(), SubscribeTask { group: grp(), tid: tid.clone(), mode: SubscribeMode::Snapshot })
        .await
        .unwrap();
    let TaskStreamItem::Snapshot { cursor, .. } = next(&mut first).await else {
        panic!("snapshot first");
    };
    let at_snapshot = cursor.unwrap();
    assert_eq!(at_snapshot.token.value, "2-1");
    assert_eq!(event_ids(next(&mut first).await).1, "3-1");

    // Resume right after the snapshot's coverage: no snapshot, only what follows.
    let mut resumed = g
        .subscribe(&me(), SubscribeTask { group: grp(), tid: tid.clone(), mode: SubscribeMode::Resume(at_snapshot.clone()) })
        .await
        .unwrap();
    assert_eq!(next(&mut resumed).await, TaskStreamItem::ResumeAccepted { cursor: at_snapshot.clone() });
    assert_eq!(event_ids(next(&mut resumed).await).1, "3-1");

    // A position before the coverage may be trimmed: snapshot first.
    let mut stale = at_snapshot.clone();
    stale.token.value = "1-1".into();
    let mut restarted = g
        .subscribe(&me(), SubscribeTask { group: grp(), tid: tid.clone(), mode: SubscribeMode::Resume(stale) })
        .await
        .unwrap();
    assert!(matches!(next(&mut restarted).await, TaskStreamItem::Snapshot { .. }));

    // A cursor of another task or caller is refused.
    let mut foreign = at_snapshot.clone();
    foreign.tid = Tid(format!("{}.ffff", foreign.sid.0));
    assert!(matches!(
        g.subscribe(&me(), SubscribeTask { group: grp(), tid: tid.clone(), mode: SubscribeMode::Resume(foreign) }).await,
        Err(GatewayError::AccessDenied)
    ));
    assert!(matches!(
        g.subscribe(&caller("pa-002"), SubscribeTask { group: grp(), tid: tid.clone(), mode: SubscribeMode::Snapshot }).await,
        Err(GatewayError::NotFound)
    ));
}

#[tokio::test]
async fn subscribing_to_a_finished_task_yields_its_final_view_and_ends() {
    let p = ports();
    let g = gateway(&p);
    let tid = g.submit(&me(), message("msg-1", "hello")).await.unwrap().tid;
    publish(&p.worker, &tid, TaskStatus::Completed, Some(eid(9, 1)), &[]).await;
    let mut stream = g
        .subscribe(&me(), SubscribeTask { group: grp(), tid: tid.clone(), mode: SubscribeMode::Snapshot })
        .await
        .unwrap();
    let TaskStreamItem::Snapshot { view, cursor } = next(&mut stream).await else {
        panic!("snapshot first");
    };
    assert_eq!(view.content.status, TaskStatus::Completed);
    assert_eq!(cursor, None);
    assert!(stream.next().await.is_none());
}

fn wait(tid: &Tid, until: WaitUntil, after: Option<EventId>, millis: u64) -> WaitTask {
    WaitTask {
        group: grp(),
        tid: tid.clone(),
        until,
        after,
        timeout: Duration::from_millis(millis),
    }
}

#[tokio::test]
async fn wait_returns_a_view_that_covers_the_awaited_event() {
    let p = Arc::new(ports());
    let g = gateway(&p);
    let tid = g.submit(&me(), message("msg-1", "hello")).await.unwrap().tid;
    let worker = {
        let p = p.clone();
        let tid = tid.clone();
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(50)).await;
            p.worker.append(&status_event(&tid.0, eid(1, 1), TaskStatus::Working)).await;
            p.worker.append(&status_event(&tid.0, eid(2, 1), TaskStatus::InputRequired)).await;
            // The view is published a little after the event (ev first, then task).
            tokio::time::sleep(Duration::from_millis(60)).await;
            publish(&p.worker, &tid, TaskStatus::InputRequired, Some(eid(2, 1)), &["i-1"]).await;
        })
    };
    let view = g.wait_task(&me(), wait(&tid, WaitUntil::InputOrFinal, None, 5_000)).await.unwrap();
    assert_eq!(view.content.status, TaskStatus::InputRequired);
    assert_eq!(view.covered_through, Some(eid(2, 1)));
    worker.await.unwrap();
}

#[tokio::test]
async fn wait_returns_the_current_view_at_its_timeout_or_when_coverage_never_comes() {
    let p = ports();
    let g = gateway(&p);
    let tid = g.submit(&me(), message("msg-1", "hello")).await.unwrap().tid;
    let view = g.wait_task(&me(), wait(&tid, WaitUntil::InputOrFinal, None, 80)).await.unwrap();
    assert_eq!(view.content.status, TaskStatus::Submitted);

    // The final event is in the stream but the view is never published: the
    // bounded cover window ends and the stored view is returned.
    p.worker
        .append(&OutputEvent {
            id: eid(4, 1),
            tid: tid.clone(),
            body: OutputBody::Terminal {
                status: TerminalStatus::Completed,
                result_ref: BlobRef { key: "res/final".into(), digest: Digest("sha256:r".into()) },
            },
        })
        .await;
    let started = std::time::Instant::now();
    let view = g.wait_task(&me(), wait(&tid, WaitUntil::Final, None, 5_000)).await.unwrap();
    assert_eq!(view.content.status, TaskStatus::Submitted);
    assert!(started.elapsed() < Duration::from_secs(2), "the cover window is bounded");
}

#[tokio::test]
async fn a_reply_is_admitted_and_its_wait_ignores_the_question_it_answers() {
    let p = Arc::new(ports());
    let g = gateway(&p);
    let tid = g.submit(&me(), message("msg-1", "hello")).await.unwrap().tid;
    p.worker.append(&status_event(&tid.0, eid(7, 1), TaskStatus::InputRequired)).await;
    publish(&p.worker, &tid, TaskStatus::InputRequired, Some(eid(7, 1)), &["i-3"]).await;

    // An immediate answer for a plain wait: the task waits for input.
    let view = g.wait_task(&me(), wait(&tid, WaitUntil::InputOrFinal, None, 5_000)).await.unwrap();
    assert_eq!(view.content.status, TaskStatus::InputRequired);
    let before = view.covered_through;

    // The reply names no interaction: the single open one is answered.
    let mut answer = message("msg-2", "B");
    answer.task = Some(tid.clone());
    let receipt = g.submit(&me(), answer.clone()).await.unwrap();
    assert_eq!(receipt.tid, tid);
    assert_eq!(g.submit(&me(), answer).await.unwrap(), receipt);

    let worker = {
        let p = p.clone();
        let tid = tid.clone();
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(60)).await;
            p.worker.append(&status_event(&tid.0, eid(8, 1), TaskStatus::Working)).await;
            p.worker
                .append(&OutputEvent {
                    id: eid(9, 1),
                    tid: tid.clone(),
                    body: OutputBody::Terminal {
                        status: TerminalStatus::Completed,
                        result_ref: BlobRef { key: "res/final".into(), digest: Digest("sha256:r".into()) },
                    },
                })
                .await;
            publish(&p.worker, &tid, TaskStatus::Completed, Some(eid(9, 1)), &[]).await;
        })
    };
    // With the coverage seen before the reply, the old question does not end the wait.
    let view = g.wait_task(&me(), wait(&tid, WaitUntil::InputOrFinal, before, 5_000)).await.unwrap();
    assert_eq!(view.content.status, TaskStatus::Completed);
    worker.await.unwrap();
}

#[tokio::test]
async fn a_reply_needs_exactly_one_open_question_unless_it_names_one() {
    let p = ports();
    let g = gateway(&p);
    let tid = g.submit(&me(), message("msg-1", "hello")).await.unwrap().tid;
    let mut answer = message("msg-2", "B");
    answer.task = Some(tid.clone());
    assert_eq!(
        g.submit(&me(), answer.clone()).await,
        Err(GatewayError::Invalid { code: "interaction_not_open".into() })
    );
    publish(&p.worker, &tid, TaskStatus::InputRequired, Some(eid(7, 1)), &["i-1", "i-2"]).await;
    assert_eq!(
        g.submit(&me(), answer.clone()).await,
        Err(GatewayError::Invalid { code: "interaction_ambiguous".into() })
    );
    answer.interaction = Some(InteractionId("i-2".into()));
    assert!(g.submit(&me(), answer.clone()).await.is_ok());
    answer.message_id = RequestId("msg-3".into());
    answer.interaction = Some(InteractionId("i-closed".into()));
    assert_eq!(
        g.submit(&me(), answer.clone()).await,
        Err(GatewayError::Invalid { code: "invalid_task_state".into() })
    );
    answer.session = Some(Sid("s0001.other".into()));
    assert_eq!(
        g.submit(&me(), answer).await,
        Err(GatewayError::Invalid { code: "task_not_in_session".into() })
    );
}

#[tokio::test]
async fn cancel_is_admitted_once_and_refused_for_a_finished_task() {
    let p = ports();
    let g = gateway(&p);
    let tid = g.submit(&me(), message("msg-1", "hello")).await.unwrap().tid;
    let request = CancelTask {
        group: grp(),
        tid: tid.clone(),
        request_id: RequestId("req-55".into()),
        reason: None,
    };
    let receipt = g.cancel(&me(), request.clone()).await.unwrap();
    assert_eq!(g.cancel(&me(), request.clone()).await.unwrap(), receipt);
    assert_eq!(g.cancel(&caller("pa-002"), request.clone()).await, Err(GatewayError::NotFound));

    // The wait of a cancel ends only on a final state, not on a question.
    publish(&p.worker, &tid, TaskStatus::InputRequired, Some(eid(3, 1)), &["i-1"]).await;
    let view = g.wait_task(&me(), wait(&tid, WaitUntil::Final, None, 80)).await.unwrap();
    assert_eq!(view.content.status, TaskStatus::InputRequired);

    publish(&p.worker, &tid, TaskStatus::Canceled, Some(eid(4, 1)), &[]).await;
    let view = g.wait_task(&me(), wait(&tid, WaitUntil::Final, None, 5_000)).await.unwrap();
    assert_eq!(view.content.status, TaskStatus::Canceled);
    let late = CancelTask { request_id: RequestId("req-56".into()), ..request };
    assert_eq!(g.cancel(&me(), late).await, Err(GatewayError::NotCancelable));
}

#[tokio::test]
async fn listing_needs_a_session_and_pages_its_tasks() {
    let p = ports();
    let g = gateway(&p);
    let first = g.submit(&me(), message("msg-1", "hello")).await.unwrap();
    for n in 2..=4 {
        let mut next = message(&format!("msg-{n}"), "more");
        next.session = Some(first.sid.clone());
        g.submit(&me(), next).await.unwrap();
    }
    let list = |session: Option<Sid>, after: Option<PageToken>, limit: u32| ListTasks {
        group: grp(),
        session,
        statuses: vec![],
        after,
        limit: NonZeroU32::new(limit).unwrap(),
    };
    assert_eq!(
        g.list_tasks(&me(), list(None, None, 10)).await,
        Err(GatewayError::Invalid { code: "session_filter_required".into() })
    );
    let page = g.list_tasks(&me(), list(Some(first.sid.clone()), None, 3)).await.unwrap();
    assert_eq!(page.tasks.len(), 3);
    let rest = g.list_tasks(&me(), list(Some(first.sid.clone()), page.next, 3)).await.unwrap();
    assert_eq!(rest.tasks.len(), 1);
    assert_eq!(rest.next, None);
    assert_eq!(
        g.list_tasks(&caller("pa-002"), list(Some(first.sid.clone()), None, 3)).await,
        Err(GatewayError::NotFound)
    );
    // A malformed page token is invalid input, not a dependency to retry.
    assert_eq!(
        g.list_tasks(&me(), list(Some(first.sid.clone()), Some(PageToken("garbage".into())), 3)).await,
        Err(GatewayError::Invalid { code: "page_token".into() })
    );
}

async fn snapshot_stream(g: &DistGateway, tid: &Tid) -> TaskSubscription {
    g.subscribe(&me(), SubscribeTask { group: grp(), tid: tid.clone(), mode: SubscribeMode::Snapshot })
        .await
        .unwrap()
}

#[tokio::test]
async fn a_task_that_advances_right_after_the_first_snapshot_gets_a_fresh_snapshot() {
    let p = ports();
    let g = gateway(&p);
    let tid = g.submit(&me(), message("msg-1", "hello")).await.unwrap().tid;
    let mut stream = snapshot_stream(&g, &tid).await;
    assert!(matches!(next(&mut stream).await, TaskStreamItem::Snapshot { .. }));
    // The Worker picks the input up before the stream is polled again.
    p.worker.append(&status_event(&tid.0, eid(1, 1), TaskStatus::Working)).await;
    publish(&p.worker, &tid, TaskStatus::Working, Some(eid(1, 1)), &[]).await;
    let TaskStreamItem::Snapshot { view, cursor } = next(&mut stream).await else {
        panic!("an ordinary advance re-sends the snapshot, it does not end the stream");
    };
    assert_eq!(view.content.status, TaskStatus::Working);
    assert_eq!(cursor.unwrap().token.value, "1-1");
    // And the stream goes on from there.
    p.worker.append(&status_event(&tid.0, eid(2, 1), TaskStatus::InputRequired)).await;
    assert_eq!(event_ids(next(&mut stream).await).1, "2-1");
    assert!(stream.next().await.is_none());
}

#[tokio::test]
async fn a_task_that_finishes_right_after_the_first_snapshot_ends_with_its_final_view() {
    let p = ports();
    let g = gateway(&p);
    let tid = g.submit(&me(), message("msg-1", "hello")).await.unwrap().tid;
    let mut stream = snapshot_stream(&g, &tid).await;
    assert!(matches!(next(&mut stream).await, TaskStreamItem::Snapshot { .. }));
    publish(&p.worker, &tid, TaskStatus::Completed, Some(eid(3, 1)), &[]).await;
    let TaskStreamItem::Snapshot { view, cursor } = next(&mut stream).await else {
        panic!("the final view is delivered");
    };
    assert_eq!(view.content.status, TaskStatus::Completed);
    assert_eq!(cursor, None);
    assert!(stream.next().await.is_none());
}

#[tokio::test]
async fn a_second_gap_in_a_row_ends_the_stream_with_a_retryable_error() {
    let p = ports();
    let g = gateway(&p);
    let tid = g.submit(&me(), message("msg-1", "hello")).await.unwrap().tid;
    let mut stream = snapshot_stream(&g, &tid).await;
    assert!(matches!(next(&mut stream).await, TaskStreamItem::Snapshot { .. }));
    publish(&p.worker, &tid, TaskStatus::Working, Some(eid(1, 1)), &[]).await;
    assert!(matches!(next(&mut stream).await, TaskStreamItem::Snapshot { .. }));
    // The task moves again before the re-sent snapshot could be followed.
    publish(&p.worker, &tid, TaskStatus::Working, Some(eid(2, 1)), &[]).await;
    let item = tokio::time::timeout(Duration::from_secs(5), stream.next()).await.unwrap();
    assert_eq!(
        item,
        Some(Err(GatewayError::Unavailable { code: "task_position_unavailable".into() }))
    );
    assert!(stream.next().await.is_none());
}

#[tokio::test]
async fn a_wait_returns_the_current_view_when_the_event_stream_cannot_be_read() {
    let p = ports();
    let g = gateway(&p);
    let tid = g.submit(&me(), message("msg-1", "hello")).await.unwrap().tid;
    p.stream.fail_reads.store(true, Ordering::SeqCst);
    let started = std::time::Instant::now();
    let view = g.wait_task(&me(), wait(&tid, WaitUntil::InputOrFinal, None, 30_000)).await.unwrap();
    assert_eq!(view.content.status, TaskStatus::Submitted);
    assert!(started.elapsed() < Duration::from_secs(10), "ends with the failed reads, not the timeout");
}

#[tokio::test]
async fn a_wait_keeps_up_with_a_task_that_advances_under_it() {
    let p = Arc::new(ports());
    let g = gateway(&p);
    let tid = g.submit(&me(), message("msg-1", "hello")).await.unwrap().tid;
    let worker = {
        let p = p.clone();
        let tid = tid.clone();
        tokio::spawn(async move {
            // Each publish moves the coverage past the position the wait holds.
            for n in 1..=3u64 {
                tokio::time::sleep(Duration::from_millis(15)).await;
                p.worker.append(&status_event(&tid.0, eid(n, 1), TaskStatus::Working)).await;
                publish(&p.worker, &tid, TaskStatus::Working, Some(eid(n, 1)), &[]).await;
            }
            tokio::time::sleep(Duration::from_millis(15)).await;
            p.worker.append(&status_event(&tid.0, eid(4, 1), TaskStatus::Completed)).await;
            publish(&p.worker, &tid, TaskStatus::Completed, Some(eid(4, 1)), &[]).await;
        })
    };
    let view = g.wait_task(&me(), wait(&tid, WaitUntil::Final, None, 5_000)).await.unwrap();
    worker.await.unwrap();
    assert_eq!(view.content.status, TaskStatus::Completed);
}

#[tokio::test]
async fn a_session_is_reached_only_through_the_group_it_was_created_for() {
    let p = ports();
    let g = gateway(&p);
    let receipt = g.submit(&me(), message("msg-1", "hello")).await.unwrap();
    let (sid, tid) = (receipt.sid.clone(), receipt.tid.clone());
    publish(&p.worker, &tid, TaskStatus::InputRequired, Some(eid(7, 1)), &["i-3"]).await;
    let other = GroupId("grp-legal-01".into());

    // A new task in the session.
    let mut next_task = message("msg-2", "next");
    next_task.group = other.clone();
    next_task.session = Some(sid.clone());
    assert_eq!(g.submit(&me(), next_task).await, Err(GatewayError::NotFound));
    // A reply to its open question.
    let mut answer = message("msg-3", "B");
    answer.group = other.clone();
    answer.task = Some(tid.clone());
    assert_eq!(g.submit(&me(), answer).await, Err(GatewayError::NotFound));
    // Get, subscribe, wait, cancel and list.
    assert_eq!(g.get_task(&me(), &other, &tid).await, Err(GatewayError::NotFound));
    let subscribe = SubscribeTask { group: other.clone(), tid: tid.clone(), mode: SubscribeMode::Snapshot };
    assert!(matches!(g.subscribe(&me(), subscribe).await, Err(GatewayError::NotFound)));
    let waiting = WaitTask { group: other.clone(), ..wait(&tid, WaitUntil::Final, None, 50) };
    assert_eq!(g.wait_task(&me(), waiting).await, Err(GatewayError::NotFound));
    let cancel = CancelTask {
        group: other.clone(),
        tid: tid.clone(),
        request_id: RequestId("req-1".into()),
        reason: None,
    };
    assert_eq!(g.cancel(&me(), cancel).await, Err(GatewayError::NotFound));
    let list = ListTasks {
        group: other,
        session: Some(sid.clone()),
        statuses: vec![],
        after: None,
        limit: NonZeroU32::new(10).unwrap(),
    };
    assert_eq!(g.list_tasks(&me(), list).await, Err(GatewayError::NotFound));

    // Nothing was admitted through the wrong group; the right one still works.
    let listed = ListTasks {
        group: grp(),
        session: Some(sid),
        statuses: vec![],
        after: None,
        limit: NonZeroU32::new(10).unwrap(),
    };
    assert_eq!(g.list_tasks(&me(), listed).await.unwrap().tasks.len(), 1);
    assert_eq!(g.get_task(&me(), &grp(), &tid).await.unwrap().content.status, TaskStatus::InputRequired);
}

#[tokio::test]
async fn a_directory_answer_for_another_group_id_creates_nothing() {
    let p = ports();
    let g = gateway(&p);
    let mut aliased = message("msg-1", "hello");
    aliased.group = GroupId("Grp-Research-01".into());
    assert_eq!(
        g.submit(&me(), aliased).await,
        Err(GatewayError::Unavailable { code: "group_directory_mismatch".into() })
    );
    // The group's own id still works, and creates the session.
    let canonical = g.submit(&me(), message("msg-1", "hello")).await.unwrap();
    assert_eq!(g.get_task(&me(), &grp(), &canonical.tid).await.unwrap().content.status, TaskStatus::Submitted);
}

#[tokio::test]
async fn a_retried_first_message_needs_no_directory_once_its_session_is_frozen() {
    let p = ports();
    let groups = Arc::new(Groups::default());
    // The first attempt is admitted, but its response is lost.
    let inbox = Arc::new(LosesFirstResponse {
        inner: p.inbox.clone(),
        lost: AtomicBool::new(false),
    });
    let g = gateway_with(&p, inbox, groups.clone());
    assert!(matches!(
        g.submit(&me(), message("msg-1", "hello")).await,
        Err(GatewayError::OutcomeUnknown { .. })
    ));
    // The directory is down when the client retries: the frozen session answers.
    groups.down.store(true, Ordering::SeqCst);
    let receipt = g.submit(&me(), message("msg-1", "hello")).await.unwrap();
    assert_eq!(g.get_task(&me(), &grp(), &receipt.tid).await.unwrap().content.status, TaskStatus::Submitted);
    // A genuinely new session still needs the directory.
    assert_eq!(
        g.submit(&me(), message("msg-2", "hello")).await,
        Err(GatewayError::Unavailable { code: "group_directory_unavailable".into() })
    );
}
