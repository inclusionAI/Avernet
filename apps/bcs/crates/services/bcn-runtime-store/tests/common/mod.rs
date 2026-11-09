#![allow(dead_code)]
use std::{sync::Arc, collections::BTreeMap};
use bcn_runtime_model::*;
use bcn_runtime_storage_api::ServerClock;
use bcn_storage_memory::MemoryAtomicStore;
use bcn_runtime_store::RuntimeStore;
use bcn_runtime_api::{core::SessionRunner, port::repo::*};
use bcn_session_runner::DirectA2aRunner;
pub struct Clock;
impl ServerClock for Clock { fn now_ms(&self) -> u64 { 1000 } }
pub fn scope() -> Scope { Scope::new("test", "tenant", "runtime") }
pub fn node(inc: &str) -> NodeIdentity { NodeIdentity { scope: scope(), node_id: "worker".into(), incarnation: inc.into() } }
pub fn binding() -> FrozenTargetBinding {
 FrozenTargetBinding { target: AgentId::new(scope(), "agent"), binding_version: "v1".into(), binding_id: "binding".into(), credential_ref: None,
 capabilities: ConnectorCapabilities { streaming: true, query: true, subscribe: true, input_continuation: true, cancellation: CancellationSupport::SingleTask, idempotency: IdempotencySupport::Unsupported } }
}
pub fn fixture() -> (RuntimeStore, Arc<MemoryAtomicStore>, AdmissionRequest) {
 let atomic = Arc::new(MemoryAtomicStore::new(RuntimeProfile::Test, Arc::new(Clock)).unwrap());
 let repo = RuntimeStore::new(atomic.clone());
 let session = SessionId::new(scope(), "session/a");
 let task_id = TaskId::new(scope(), "task/a");
 let target = ExecutionTarget::Agent { agent_id: binding().target };
 let spec = SessionSpec { session_id: session.clone(), target: target.clone(), kind: RunnerKind::DirectA2a, definition: None, bindings: BTreeMap::from([("agent".into(), binding())]), policy: ExecutionPolicy { execution_timeout_ms: 10000, human_timeout_ms: 20000, judge_available: false, max_pending_tasks: 16, max_graph_nodes: 32, max_agents: 5 } };
 let task = TaskView { task_id: task_id.clone(), session_id: session.clone(), target, status: TaskStatus::Submitted, output: vec![], error: None, created_at_ms: 1000, updated_at_ms: 1000 };
 let input = SessionInput { event_id: EventId::new(scope(), "submit/a"), at_ms: 1000, kind: SessionInputKind::Submit { task: QueuedTask { task_id, message: Message { message_id: MessageId::new(scope(), "message/a"), context_id: Some(session), task_id: None, parts: vec![MessagePart::Text { text: "hello".into() }] }, admitted_at_ms: 1000, terminal_intent: None } } };
 let request = AdmissionRequest { admission_id: AdmissionId::new(scope(), "admission/a"), caller: CallerContext { scope: scope(), principal: "alice".into(), trace_id: None }, request_digest: "digest-a".into(), spec, task, input };
 (repo, atomic, request)
}
pub async fn activated(repo: &RuntimeStore, req: &AdmissionRequest) -> SessionCheckpoint {
 repo.admit(req.clone()).await.unwrap();
 let fence = repo.claim_shard(session_shard(&req.spec.session_id), node("one"), None).await.unwrap();
 repo.activate(req.spec.clone(), DirectA2aRunner.initialize(&req.spec).unwrap(), fence).await.unwrap()
}
pub async fn submitted(repo: &RuntimeStore, req: &AdmissionRequest) -> SessionCheckpoint {
 let cp = activated(repo, req).await;
 let transition = DirectA2aRunner.reduce(&cp.state, &req.input).unwrap();
 repo.commit(SessionCommit { session_id: req.spec.session_id.clone(), fence: cp.fence, expected_revision: cp.revision, consumed_input: 1, transition }).await.unwrap()
}
