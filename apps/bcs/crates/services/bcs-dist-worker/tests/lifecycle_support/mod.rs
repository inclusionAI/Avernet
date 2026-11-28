#![allow(
    dead_code,
    clippy::unwrap_used,
    reason = "shared lifecycle integration fixtures"
)]
use crate::processor_support::{self, ClockAt, Fixture};
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_dist_projection::OutputProjectionService;
use bcs_dist_store::{
    archive::DbArchiveStore,
    inbox::{Clock, StoreAdmissionControl},
    task_feed::StoreTaskFeed,
};
use bcs_dist_worker::*;
use bcs_service_api::{
    core::dist_runner::*,
    port::repo::distributed::{common::*, inbox::*, result::*, task_feed::*},
};
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, Ordering},
};

#[derive(Clone, Copy)]
pub enum Mode {
    Interaction,
    Stream,
    Wait,
    Parallel,
    Finish,
}
pub struct Runner {
    pub mode: Mode,
    pub inputs: Mutex<Vec<RunnerInput>>,
}
impl Runner {
    pub fn new(mode: Mode) -> Arc<Self> {
        Arc::new(Self {
            mode,
            inputs: Mutex::new(vec![]),
        })
    }
    pub fn calls(&self) -> usize {
        self.inputs.lock().unwrap().len()
    }
}
pub fn session() -> SessionSpec {
    let mut s = processor_support::spec();
    s.limits.max_task_steps = 1024;
    s.authorized_agents = vec![AuthorizedAgent {
        agent: AgentId("agent".into()),
        pool: PoolId("pool".into()),
    }];
    s
}
pub fn dispatch_id(tid: &Tid) -> Did {
    Did(format!("{}.dispatch", tid.0))
}
#[async_trait::async_trait]
impl DistRunnerCoreService for Runner {
    fn validate_session(
        &self, _: &SessionSpec, _: &serde_json::Value,
    ) -> Result<Arc<dyn std::any::Any + Send + Sync>, RunnerError> {
        Ok(Arc::new(()))
    }
    async fn init(&self, _: &EvaluationContext<'_>) -> Result<RunnerState, RunnerError> {
        Ok(RunnerState {
            format_version: 1,
            value: serde_json::json!(0),
        })
    }
    async fn reduce(
        &self,
        c: &EvaluationContext<'_>,
        state: &RunnerState,
        inputs: &[RunnerInput],
    ) -> Result<Decision, RunnerError> {
        self.inputs.lock().unwrap().extend_from_slice(inputs);
        let mut actions = vec![];
        match &inputs[0] {
            RunnerInput::Start { tid, .. } => {
                if !matches!(self.mode, Mode::Finish) {
                    let p = &c.spec.authorized_agents[0];
                    actions.push(ActionIntent::StartDispatch(DispatchSpec {
                        schema_version: 1,
                        sid: c.spec.sid.clone(),
                        tid: tid.clone(),
                        did: dispatch_id(tid),
                        source: CorrelationKey("answer".into()),
                        attempt: 1,
                        agent: p.agent.clone(),
                        pool: p.pool.clone(),
                        downstream_context: None,
                        request: Payload::Inline(serde_json::json!("request")),
                        deadline: TimestampMillis(c.now.0 + 60_000),
                    }));
                }
                if matches!(self.mode, Mode::Parallel) {
                    let ActionIntent::StartDispatch(mut second) = actions[0].clone() else {
                        panic!()
                    };
                    second.did = Did(format!("{}.second", tid.0));
                    actions.push(ActionIntent::StartDispatch(second));
                }
                match self.mode {
                    Mode::Interaction => actions.push(ActionIntent::SetStatus {
                        tid: tid.clone(),
                        status: TaskStatus::InputRequired,
                        interactions: vec![Interaction {
                            id: InteractionId("confirm".into()),
                            origin: InteractionOrigin::Dispatch { did: dispatch_id(tid) },
                            prompt: None,
                            kind: InteractionKind::HumanInput,
                        }],
                    }),
                    Mode::Stream => {
                        actions.push(show(tid, Payload::Inline(serde_json::json!("seed"))))
                    }
                    Mode::Finish => actions.push(finish(tid)),
                    Mode::Wait | Mode::Parallel => {}
                }
            }
            RunnerInput::HumanReply { tid, payload, .. } => {
                actions.push(ActionIntent::ContinueDispatch {
                    did: dispatch_id(tid),
                    input: PendingInput {
                        seq: InputSeq(1),
                        input_id: InputId("forwarded-reply".into()),
                        business_digest: bcs_dist_encoding::digest_json(
                            &serde_json::to_value(payload).unwrap(),
                        ),
                        payload: payload.clone(),
                    },
                });
                actions.push(show(tid, payload.clone()));
                actions.push(finish(tid));
            }
            RunnerInput::Agent { tid, event } if matches!(self.mode, Mode::Stream) => {
                actions.push(show(tid, event.payload.clone()))
            }
            RunnerInput::Agent { .. } | RunnerInput::Timeout(_) => {}
            other => panic!("unexpected Runner input: {other:?}"),
        }
        Ok(Decision {
            next_state: RunnerState {
                format_version: 1,
                value: serde_json::json!(state.value.as_u64().unwrap() + 1),
            },
            actions,
            next_deadlines: vec![],
        })
    }
}
fn show(tid: &Tid, delta: Payload) -> ActionIntent {
    ActionIntent::Show {
        tid: tid.clone(),
        source: CorrelationKey("n".into()),
        artifact_id: ArtifactId("answer".into()),
        class: OutputClass::Output,
        update: ArtifactUpdate::AppendText { delta },
    }
}
fn finish(tid: &Tid) -> ActionIntent {
    ActionIntent::FinishTask {
        tid: tid.clone(),
        status: TerminalStatus::Completed,
        result: Payload::Inline(serde_json::json!({"finished": true})),
    }
}
pub async fn admit(f: &Fixture, s: SessionSpec, name: &str, policy: SnapshotPolicy) -> Tid {
    let tid = Tid(format!("{}.{}", s.sid.0, name));
    let mut m = processor_support::metadata();
    m.tid = tid.clone();
    m.sid = s.sid.clone();
    m.scope = s.scope.clone();
    m.output_limits.snapshots = policy;
    assert!(matches!(
        f.inbox
            .admit(AdmitRequest {
                scope: s.scope.clone(),
                input: InputEnvelope {
                    schema_version: 1,
                    sid: s.sid.clone(),
                    tid: tid.clone(),
                    input_id: InputId(name.into()),
                    business_digest: Digest(String::new()),
                    body: InputBody::User {
                        payload: Payload::Inline(serde_json::json!("start"))
                    }
                },
                create_session: Some(s),
                create_task: Some(m)
            })
            .await
            .unwrap(),
        AdmitOutcome::Accepted(_)
    ));
    tid
}
pub fn control(tid: &Tid, id: &str, body: InputBody) -> AdmitRequest {
    AdmitRequest {
        scope: session().scope,
        input: InputEnvelope {
            schema_version: 1,
            sid: session().sid,
            tid: tid.clone(),
            input_id: InputId(id.into()),
            business_digest: Digest(String::new()),
            body,
        },
        create_session: None,
        create_task: None,
    }
}
pub async fn send(f: &Fixture, tid: &Tid, id: &str, body: InputBody) {
    assert!(matches!(
        f.inbox.admit(control(tid, id, body)).await.unwrap(),
        AdmitOutcome::Accepted(_)
    ));
}
pub async fn delta(f: &Fixture, tid: &Tid, seq: u64, text: &str) {
    f.inbox
        .append_stream(AppendAgentEvent {
            sid: session().sid,
            tid: tid.clone(),
            event: AgentEvent {
                did: dispatch_id(tid),
                holder_epoch: HolderEpoch(1),
                seq: InputSeq(seq),
                kind: AgentEventKind::Delta,
                remote_event_id: None,
                payload: Payload::Inline(serde_json::json!(text)),
            },
        })
        .await
        .unwrap();
}
pub async fn drive(f: &Fixture, processor: &WorkerProcessor, n: usize) {
    for _ in 0..n {
        f.runtime.tick().await.unwrap();
        processor.tick().await.unwrap();
        tokio::time::sleep(std::time::Duration::from_millis(5)).await;
    }
}
pub async fn until_status(f: &Fixture, processor: &WorkerProcessor, tid: &Tid, status: TaskStatus) {
    for _ in 0..160 {
        drive(f, processor, 1).await;
        if f.snapshot(tid).await.content.status == status {
            return;
        }
    }
    panic!(
        "expected {status:?}, snapshot {:?}, checkpoint {:?}",
        f.snapshot(tid).await,
        f.checkpoint().await
    );
}
pub async fn until_consumed_changes(
    f: &Fixture,
    processor: &WorkerProcessor,
    before: Option<InboxPos>,
) {
    for _ in 0..200 {
        drive(f, processor, 1).await;
        if f.checkpoint().await.consumed != before {
            return;
        }
    }
    panic!("control input was not durably consumed");
}
pub fn processor(
    f: &Fixture,
    runner: Arc<dyn DistRunnerCoreService>,
    output: Arc<dyn TaskPublisherPort>,
    results: Arc<dyn ResultStorePort>,
) -> WorkerProcessor {
    processor_with_dispatch(f, runner, output, results, f.dispatch.clone())
}
pub fn processor_with_dispatch(
    f: &Fixture,
    runner: Arc<dyn DistRunnerCoreService>,
    output: Arc<dyn TaskPublisherPort>,
    results: Arc<dyn ResultStorePort>,
    dispatch: Arc<dyn bcs_service_api::port::repo::distributed::dispatch::DispatchControlPort>,
) -> WorkerProcessor {
    processor_with_blob(f, runner, output, results, dispatch, f.blobs.clone())
}
pub fn processor_with_blob(
    f: &Fixture,
    runner: Arc<dyn DistRunnerCoreService>,
    output: Arc<dyn TaskPublisherPort>,
    results: Arc<dyn ResultStorePort>,
    dispatch: Arc<dyn bcs_service_api::port::repo::distributed::dispatch::DispatchControlPort>,
    blobs: Arc<dyn BlobStorePort>,
) -> WorkerProcessor {
    WorkerProcessor::new(
        f.runtime.clone(),
        ProcessorPorts {
            scan_budget: f.scan_budget.clone(),
            control: f.control.clone(),
            admission: Arc::new(StoreAdmissionControl::new(
                (*f.inbox).clone(),
                f.feed.clone(),
            )),
            runner,
            projection: Arc::new(OutputProjectionService),
            tasks: f.feed.clone(),
            output,
            blobs,
            results,
            dispatch,
            now: {
                let c: Arc<ClockAt> = f.clock.clone();
                Arc::new(move || c.now())
            },
        },
        ProcessorConfig::default(),
    )
    .unwrap()
}
pub struct Publisher {
    pub inner: Arc<StoreTaskFeed>,
    pub requests: Mutex<Vec<PublishCommittedSnapshot>>,
    pub reject_terminal: AtomicBool,
}
impl Publisher {
    pub fn publications(&self) -> Vec<PublishCommittedSnapshot> {
        let mut cuts: Vec<PublishCommittedSnapshot> = vec![];
        for request in self.requests.lock().unwrap().iter() {
            if let Some(existing) = cuts
                .iter()
                .find(|p| p.snapshot.source_commit == request.snapshot.source_commit)
            {
                assert_eq!(
                    existing.snapshot, request.snapshot,
                    "replay must preserve the complete published cut"
                );
            } else {
                cuts.push(request.clone());
            }
        }
        cuts
    }
    pub fn new(f: &Fixture) -> Arc<Self> {
        Arc::new(Self {
            inner: f.feed.clone(),
            requests: Mutex::new(vec![]),
            reject_terminal: AtomicBool::new(false),
        })
    }
}
#[async_trait]
impl TaskPublisherPort for Publisher {
    async fn append(&self, r: AppendCommittedEvents) -> Result<AppendOutcome, StoreError> {
        self.inner.append(r).await
    }
    async fn publish(&self, r: PublishCommittedSnapshot) -> Result<PublishOutcome, StoreError> {
        self.requests.lock().unwrap().push(r.clone());
        if self.reject_terminal.load(Ordering::SeqCst)
            && matches!(
                r.snapshot.content.status,
                TaskStatus::Completed
                    | TaskStatus::Failed
                    | TaskStatus::Canceled
                    | TaskStatus::Rejected
            )
        {
            return Err(StoreError::NotApplied {
                code: "injected_terminal_publish".into(),
            });
        }
        self.inner.publish(r).await
    }
    async fn trim(&self, r: RetentionRequest) -> Result<TrimOutcome, StoreError> {
        self.inner.trim(r).await
    }
}
pub struct Results {
    pub inner: Arc<DbArchiveStore>,
    pub unavailable: AtomicBool,
    pub lose_reply: AtomicBool,
    pub requests: Mutex<Vec<(EffectSource, FinalTaskRecord)>>,
}
impl Results {
    pub fn new(f: &Fixture) -> Arc<Self> {
        Arc::new(Self {
            inner: f.archive.clone(),
            unavailable: AtomicBool::new(false),
            lose_reply: AtomicBool::new(false),
            requests: Mutex::new(vec![]),
        })
    }
}
#[async_trait]
impl ResultStorePort for Results {
    async fn save_final(
        &self,
        source: EffectSource,
        record: FinalTaskRecord,
    ) -> Result<SavedResult, StoreError> {
        self.requests
            .lock()
            .unwrap()
            .push((source.clone(), record.clone()));
        if self.unavailable.load(Ordering::SeqCst) {
            return Err(StoreError::NotApplied {
                code: "injected_archive".into(),
            });
        }
        let result = self.inner.save_final(source.clone(), record).await?;
        if self.lose_reply.swap(false, Ordering::SeqCst) {
            return Err(StoreError::OutcomeUnknown {
                operation_id: format!("{:?}", source.effect_id),
            });
        }
        Ok(result)
    }
    async fn get_final(
        &self,
        scope: &TaskScope,
        tid: &Tid,
    ) -> Result<Option<FinalTaskRecord>, StoreError> {
        self.inner.get_final(scope, tid).await
    }
}
