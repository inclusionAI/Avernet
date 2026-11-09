#![allow(dead_code)]
use async_trait::async_trait;
use bcn_runtime_api::{
    core::SessionRunner,
    port::{repo::*, *},
};
use bcn_runtime_app::*;
use bcn_runtime_model::*;
use bcn_runtime_storage_api::ServerClock;
use bcn_runtime_store::{LivenessAdapter, LocalResultStore, RuntimeStore};
use bcn_session_runner::{DirectA2aRunner, StructuredGroupRunner};
use bcn_storage_memory::MemoryAtomicStore;
use std::{
    collections::BTreeMap,
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
};
pub struct TestClock(pub AtomicU64);
impl Clock for TestClock {
    fn now_ms(&self) -> u64 {
        self.0.load(Ordering::SeqCst)
    }
}
impl ServerClock for TestClock {
    fn now_ms(&self) -> u64 {
        Clock::now_ms(self)
    }
}
impl TestClock {
    pub fn set(&self, n: u64) {
        self.0.store(n, Ordering::SeqCst);
    }
}
pub fn scope() -> Scope {
    Scope::new("test", "tenant", "new-runtime")
}
pub fn caller() -> CallerContext {
    CallerContext {
        scope: scope(),
        principal: "alice".into(),
        trace_id: None,
    }
}
pub fn node(id: &str) -> NodeIdentity {
    NodeIdentity {
        scope: scope(),
        node_id: id.into(),
        incarnation: id.into(),
    }
}
pub fn binding() -> FrozenTargetBinding {
    FrozenTargetBinding {
        target: AgentId::new(scope(), "agent"),
        binding_version: "v1".into(),
        binding_id: "local".into(),
        credential_ref: None,
        capabilities: ConnectorCapabilities {
            streaming: true,
            query: true,
            subscribe: true,
            input_continuation: true,
            cancellation: CancellationSupport::SingleTask,
            idempotency: IdempotencySupport::Unsupported,
        },
    }
}
pub fn request(id: &str) -> SubmitRequest {
    SubmitRequest {
        target: ExecutionTarget::Agent {
            agent_id: binding().target,
        },
        message: Message {
            message_id: MessageId::new(scope(), id),
            context_id: None,
            task_id: None,
            parts: text("hello"),
        },
        interaction_id: None,
    }
}
pub fn text(s: &str) -> Vec<MessagePart> {
    vec![MessagePart::Text { text: s.into() }]
}
pub fn handle() -> ExecutionHandle {
    ExecutionHandle {
        target: binding().target,
        binding_version: "v1".into(),
        remote_task_id: Some("remote".into()),
        remote_context_id: Some("conversation".into()),
    }
}
pub struct Catalog;
#[async_trait]
impl AgentCatalog for Catalog {
    async fn resolve(
        &self,
        _: &CallerContext,
        target: &ExecutionTarget,
        session: &SessionId,
    ) -> RuntimeResult<SessionSpec> {
        Ok(SessionSpec {
            session_id: session.clone(),
            target: target.clone(),
            kind: RunnerKind::DirectA2a,
            definition: None,
            bindings: BTreeMap::from([("agent".into(), binding())]),
            policy: ExecutionPolicy {
                execution_timeout_ms: 60000,
                human_timeout_ms: 60000,
                judge_available: false,
                max_pending_tasks: 16,
                max_graph_nodes: 32,
                max_agents: 5,
            },
        })
    }
}
pub struct Allow;
#[async_trait]
impl AccessPolicy for Allow {
    async fn authorize(
        &self,
        _: &CallerContext,
        _: AccessAction,
        _: &ExecutionTarget,
        _: Option<&SessionId>,
        _: Option<&TaskId>,
    ) -> RuntimeResult<()> {
        Ok(())
    }
}
pub struct NoJudge;
#[async_trait]
impl JudgeExecutor for NoJudge {
    async fn execute(&self, _: JudgeRequest) -> RuntimeResult<String> {
        Err(RuntimeError::Unsupported("judge".into()))
    }
}
pub struct Results {
    pub inner: LocalResultStore,
    pub down: AtomicBool,
    pub hang: AtomicBool,
}
#[async_trait]
impl ResultStore for Results {
    async fn put_if_absent(&self, intent: ResultIntent) -> RuntimeResult<SavedResult> {
        if self.hang.load(Ordering::SeqCst) {
            return futures::future::pending().await;
        }
        if self.down.load(Ordering::SeqCst) {
            Err(RuntimeError::Unavailable("results offline".into()))
        } else {
            self.inner.put_if_absent(intent).await
        }
    }
    async fn get(&self, id: &ResultId) -> RuntimeResult<Option<SavedResult>> {
        self.inner.get(id).await
    }
    async fn list_tasks(&self, c: &CallerContext, q: TaskQuery) -> RuntimeResult<Page<TaskView>> {
        self.inner.list_tasks(c, q).await
    }
    async fn read_group_records(
        &self,
        t: &TaskId,
        c: Option<String>,
        l: u32,
    ) -> RuntimeResult<Page<SavedResult>> {
        self.inner.read_group_records(t, c, l).await
    }
}
pub struct Transport {
    pub events: std::sync::Mutex<Vec<ConnectorResult<AgentEvent>>>,
    pub sends: AtomicU64,
    pub queries: AtomicU64,
    pub query: std::sync::Mutex<ExecutionSnapshot>,
    pub cancel: std::sync::Mutex<CancelOutcome>,
}
impl Transport {
    pub fn new(events: Vec<ConnectorResult<AgentEvent>>) -> Self {
        Self {
            events: std::sync::Mutex::new(events),
            sends: AtomicU64::new(0),
            queries: AtomicU64::new(0),
            query: std::sync::Mutex::new(ExecutionSnapshot { observation_error: None,
                handle: handle(),
                status: TaskStatus::Completed,
                output: text("queried"),
                error: None,
            }),
            cancel: std::sync::Mutex::new(CancelOutcome::Unknown),
        }
    }
}
#[async_trait]
impl AgentConnector for Transport {
    fn capabilities(&self) -> ConnectorCapabilities {
        binding().capabilities
    }
    async fn send_streaming_message(
        &self,
        _: AgentSendRequest,
    ) -> ConnectorResult<AgentEventStream> {
        self.sends.fetch_add(1, Ordering::SeqCst);
        let events = std::mem::take(&mut *self.events.lock().unwrap());
        Ok(Box::pin(
            futures::stream::iter(events).chain(futures::stream::pending()),
        ))
    }
    async fn get_task(&self, _: ExecutionHandle) -> ConnectorResult<ExecutionSnapshot> {
        self.queries.fetch_add(1, Ordering::SeqCst);
        Ok(self.query.lock().unwrap().clone())
    }
    async fn subscribe_to_task(&self, _: ExecutionHandle) -> ConnectorResult<AgentEventStream> {
        Ok(Box::pin(futures::stream::pending()))
    }
    async fn cancel_task(&self, _: AgentCancelRequest) -> ConnectorResult<CancelOutcome> {
        Ok(self.cancel.lock().unwrap().clone())
    }
}
use futures::StreamExt;
pub struct Fixture {
    pub repo: Arc<RuntimeStore>,
    pub ports: RuntimePorts,
    pub clock: Arc<TestClock>,
    pub results: Arc<Results>,
}
impl Fixture {
    pub fn new() -> Self {
        let clock = Arc::new(TestClock(AtomicU64::new(1000)));
        let atomic = Arc::new(MemoryAtomicStore::new(RuntimeProfile::Test, clock.clone()).unwrap());
        let repo = Arc::new(RuntimeStore::new(atomic));
        let results = Arc::new(Results {
            inner: LocalResultStore::new((*repo).clone(), repo.clone(), RuntimeProfile::Test)
                .unwrap(),
            down: AtomicBool::new(false),
            hang: AtomicBool::new(false),
        });
        let ports = RuntimePorts {
            sessions: repo.clone(),
            ownership: repo.clone(),
            admissions: repo.clone(),
            inbox: repo.clone(),
            commands: repo.clone(),
            dispatches: repo.clone(),
            events: repo.clone(),
            queries: repo.clone(),
            results: results.clone(),
            liveness: Arc::new(LivenessAdapter::new(repo.clone())),
        };
        Self {
            repo,
            ports,
            clock,
            results,
        }
    }
    pub fn runners(&self) -> Vec<Arc<dyn SessionRunner>> {
        vec![Arc::new(DirectA2aRunner), Arc::new(StructuredGroupRunner)]
    }
    pub fn gateway(&self) -> DefaultGateway {
        DefaultGateway::new(
            self.ports.clone(),
            self.runners(),
            Arc::new(Catalog),
            vec![Arc::new(Allow)],
            self.clock.clone(),
            true,
        )
        .unwrap()
    }
    pub fn worker(&self, id: &str) -> DefaultWorker {
        DefaultWorker::new(
            node(id),
            self.ports.clone(),
            self.runners(),
            Arc::new(NoJudge),
            self.clock.clone(),
        )
        .unwrap()
    }
    pub fn connector(&self, id: &str, t: Arc<dyn AgentConnector>) -> DefaultConnector {
        DefaultConnector::new(
            node(id),
            self.ports.clone(),
            vec![(binding(), t)],
            self.clock.clone(),
        )
        .unwrap()
    }
    pub async fn pump(&self, w: &DefaultWorker, c: &DefaultConnector, n: usize) {
        for _ in 0..n {
            w.tick().await.unwrap();
            c.tick().await.unwrap();
            self.clock.set(Clock::now_ms(self.clock.as_ref()) + 250);
        }
    }
}
pub mod faults;
pub mod group;
