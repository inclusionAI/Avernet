//! Test rig of the engine: the real store providers and admission on the
//! in-memory store model, a test-only Worker, and a scripted agent.
#![allow(
    dead_code,
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "test support"
)]

#[path = "../../../bcs-dist-connector-store/tests/common/mod.rs"]
pub mod store;

use async_trait::async_trait;
use bcs_dist_connector::rules::DistConnectorRules;
use bcs_dist_connector::{ConnectorConfig, ConnectorEngine, ConnectorPorts};
use bcs_dist_connector_store::membership::PendingMembership;
use bcs_dist_model::*;
use bcs_dist_store::codec::decode_inbox_entry;
use bcs_dist_store::inbox::{InboxProducerConfig, StoreInboxProducer};
use bcs_service_api::port::dist_agent::*;
use bcs_service_api::port::dist_runtime::DistRuntimeControlPort;
use bcs_service_api::port::repo::distributed::common::{IdempotentWrite, StoreError};
use bcs_service_api::port::repo::distributed::ownership::MembershipPort;
use bcs_service_api::port::repo::distributed::result::BlobStorePort;
use bcs_store_api::json::JsonStorePlugin;
use bcs_store_api::stream::StreamStorePlugin;
use futures::StreamExt;
use serde_json::{Value, json};
use std::collections::{HashMap, VecDeque};
use std::num::NonZeroU32;
use std::ops::Bound;
use std::sync::{Arc, Mutex};
use std::time::Duration;
pub use store::*;
use tokio::sync::mpsc;

pub type EventTx = mpsc::UnboundedSender<Result<RemoteEvent, AgentReadError>>;

/// What the scripted agent answers to one send.
pub enum Reply {
    /// Accepted with an event stream fed through the returned sender.
    Stream(mpsc::UnboundedReceiver<Result<RemoteEvent, AgentReadError>>),
    /// Accepted without a stream.
    Accepted,
    Completed(Payload),
    Rejected(&'static str),
    NotSent,
    Unknown,
    /// The agent client itself fails hard (kills the actor).
    Panic,
}

#[derive(Default)]
pub struct ScriptedAgent {
    store: Option<Arc<Recorder>>,
    /// Every call, e.g. `start <request id> phase=<stored phase>`.
    pub calls: Mutex<Vec<String>>,
    pub starts: Mutex<VecDeque<Reply>>,
    pub inputs: Mutex<VecDeque<Reply>>,
    /// Answers to `query(ByTask)`; the last one repeats.
    pub tasks: Mutex<VecDeque<QueryOutcome>>,
    pub by_request: Mutex<VecDeque<QueryOutcome>>,
    pub cancels: Mutex<VecDeque<CancelOutcome>>,
    pub subscriptions: Mutex<VecDeque<Result<mpsc::UnboundedReceiver<Result<RemoteEvent, AgentReadError>>, AgentReadError>>>,
    /// When set, the next `start` waits for it before answering.
    start_gate: Mutex<Option<tokio::sync::oneshot::Receiver<()>>>,
}

impl ScriptedAgent {
    pub fn new(store: &Arc<Recorder>) -> Self {
        Self {
            store: Some(store.clone()),
            ..Self::default()
        }
    }
    pub fn calls(&self) -> Vec<String> {
        self.calls.lock().unwrap().clone()
    }
    pub fn count(&self, prefix: &str) -> usize {
        self.calls().iter().filter(|c| c.starts_with(prefix)).count()
    }
    /// Holds the next `start` on the wire until the returned sender fires.
    pub fn gate_start(&self) -> tokio::sync::oneshot::Sender<()> {
        let (tx, rx) = tokio::sync::oneshot::channel();
        *self.start_gate.lock().unwrap() = Some(rx);
        tx
    }
    pub fn on_start(&self, reply: Reply) {
        self.starts.lock().unwrap().push_back(reply);
    }
    pub fn on_input(&self, reply: Reply) {
        self.inputs.lock().unwrap().push_back(reply);
    }
    pub fn on_task(&self, outcome: QueryOutcome) {
        self.tasks.lock().unwrap().push_back(outcome);
    }
    pub fn on_request(&self, outcome: QueryOutcome) {
        self.by_request.lock().unwrap().push_back(outcome);
    }
    pub fn on_cancel(&self, outcome: CancelOutcome) {
        self.cancels.lock().unwrap().push_back(outcome);
    }
    /// A stream the next `start` returns; events are pushed through the sender.
    pub fn start_stream(&self) -> EventTx {
        let (tx, rx) = mpsc::unbounded_channel();
        self.on_start(Reply::Stream(rx));
        tx
    }
    pub fn input_stream(&self) -> EventTx {
        let (tx, rx) = mpsc::unbounded_channel();
        self.on_input(Reply::Stream(rx));
        tx
    }
    pub fn subscribe_stream(&self) -> EventTx {
        let (tx, rx) = mpsc::unbounded_channel();
        self.subscriptions.lock().unwrap().push_back(Ok(rx));
        tx
    }
    async fn log(&self, what: String) {
        // What is durable in the dispatch at the moment the agent is called.
        let durable = match &self.store {
            Some(store) => {
                let doc = stored(store).await.0;
                format!(
                    "phase={} cancel={} input_sending={}",
                    doc["execution"]["phase"].as_str().unwrap_or("?"),
                    doc["execution"]["cancellation"]["kind"].as_str().unwrap_or("?"),
                    doc["connector"]["input_sending"]
                )
            }
            None => "?".to_owned(),
        };
        self.calls.lock().unwrap().push(format!("{what} {durable}"));
    }
    fn outcome(reply: Option<Reply>) -> SendOutcome {
        match reply {
            Some(Reply::Stream(rx)) => SendOutcome::Accepted {
                remote: remote(),
                events: Some(stream_of(rx)),
            },
            Some(Reply::Accepted) => SendOutcome::Accepted {
                remote: remote(),
                events: None,
            },
            Some(Reply::Completed(result)) => SendOutcome::Completed { result },
            Some(Reply::Rejected(code)) => SendOutcome::Rejected { code: code.into() },
            Some(Reply::NotSent) => SendOutcome::NotSent { code: "connect_refused".into() },
            Some(Reply::Panic) => panic!("scripted agent client failure"),
            Some(Reply::Unknown) | None => SendOutcome::OutcomeUnknown { code: "timeout".into() },
        }
    }
}

fn stream_of(rx: mpsc::UnboundedReceiver<Result<RemoteEvent, AgentReadError>>) -> RemoteEventStream {
    futures::stream::unfold(rx, |mut rx| async move { rx.recv().await.map(|item| (item, rx)) }).boxed()
}

fn payload_text(payload: &Payload) -> String {
    match payload {
        Payload::Inline(value) => value.to_string(),
        Payload::Ref(reference) => format!("ref:{}", reference.key),
    }
}

#[async_trait]
impl DistAgentClientPort for ScriptedAgent {
    async fn start(&self, target: &AgentBinding, request: StableSendRequest) -> SendOutcome {
        self.log(format!("start {} agent={} payload={}", request.request_id.0, target.agent.0, payload_text(&request.payload)))
            .await;
        let gate = self.start_gate.lock().unwrap().take();
        if let Some(gate) = gate {
            let _ = gate.await;
        }
        let reply = self.starts.lock().unwrap().pop_front();
        Self::outcome(reply)
    }
    async fn send_input(&self, _: &AgentBinding, remote: &RemoteTaskRef, request: StableSendRequest) -> SendOutcome {
        self.log(format!(
            "input {} task={} context={:?} payload={}",
            request.request_id.0,
            remote.task_id,
            request.context_id,
            payload_text(&request.payload)
        ))
        .await;
        let reply = self.inputs.lock().unwrap().pop_front();
        Self::outcome(reply)
    }
    async fn query(&self, _: &AgentBinding, query: RemoteQuery) -> QueryOutcome {
        let (queue, what) = match &query {
            RemoteQuery::ByRequest(id) => (&self.by_request, format!("query_request {}", id.0)),
            RemoteQuery::ByTask(remote) => (&self.tasks, format!("query_task {}", remote.task_id)),
        };
        self.log(what).await;
        let mut queue = queue.lock().unwrap();
        match queue.len() {
            0 => QueryOutcome::Unavailable { code: "unscripted".into() },
            1 => queue.front().cloned().unwrap(),
            _ => queue.pop_front().unwrap(),
        }
    }
    async fn subscribe(
        &self,
        _: &AgentBinding,
        remote: &RemoteTaskRef,
        after: Option<ReplayToken>,
    ) -> Result<RemoteEventStream, AgentReadError> {
        self.log(format!("subscribe {} after={}", remote.task_id, after.is_some())).await;
        match self.subscriptions.lock().unwrap().pop_front() {
            Some(Ok(rx)) => Ok(stream_of(rx)),
            Some(Err(error)) => Err(error),
            None => Err(AgentReadError { code: "unscripted".into() }),
        }
    }
    async fn cancel(&self, _: &AgentBinding, remote: &RemoteTaskRef, request_id: RequestId) -> CancelOutcome {
        self.log(format!("cancel {} task={}", request_id.0, remote.task_id)).await;
        self.cancels
            .lock()
            .unwrap()
            .pop_front()
            .unwrap_or(CancelOutcome::OutcomeUnknown { code: "unscripted".into() })
    }
}

/// An event as the adapter would hand it over.
pub fn event(kind: AgentEventKind, payload: Value) -> Result<RemoteEvent, AgentReadError> {
    Ok(RemoteEvent {
        remote: remote(),
        event_id: None,
        cursor: None,
        kind,
        payload: Payload::Inline(payload),
    })
}

pub fn delta(text: &str) -> Result<RemoteEvent, AgentReadError> {
    event(AgentEventKind::Delta, json!({"artifact": {"artifactId": "a-1", "parts": [{"text": text}]}, "append": true}))
}

pub fn final_event() -> Result<RemoteEvent, AgentReadError> {
    event(AgentEventKind::Final, json!({"state": "completed"}))
}

pub fn input_required() -> Result<RemoteEvent, AgentReadError> {
    event(
        AgentEventKind::InputRequired { interaction: InteractionId("from-adapter".into()) },
        json!({"state": "input_required", "message": {"parts": [{"text": "A or B?"}]}}),
    )
}

pub fn found(status: TaskStatus, payload: Value) -> QueryOutcome {
    QueryOutcome::Found {
        remote: remote(),
        status,
        snapshot: Payload::Inline(payload),
    }
}

#[derive(Default)]
pub struct MemoryBlobs {
    pub blobs: Mutex<HashMap<String, Vec<u8>>>,
}

#[async_trait]
impl BlobStorePort for MemoryBlobs {
    async fn put_immutable(&self, identity: BlobRef, bytes: Vec<u8>) -> Result<IdempotentWrite, StoreError> {
        let mut blobs = self.blobs.lock().unwrap();
        Ok(match blobs.get(&identity.key) {
            Some(existing) if *existing == bytes => IdempotentWrite::AlreadyApplied,
            Some(_) => IdempotentWrite::ContentConflict,
            None => {
                blobs.insert(identity.key, bytes);
                IdempotentWrite::Applied
            }
        })
    }
    async fn get(&self, reference: &BlobRef) -> Result<Option<Vec<u8>>, StoreError> {
        Ok(self.blobs.lock().unwrap().get(&reference.key).cloned())
    }
}

/// In-process peer channel: a handoff goes straight to the target engine's
/// control port.
#[derive(Default)]
pub struct LocalPeers {
    pub engines: Mutex<HashMap<String, ConnectorEngine>>,
    /// Every handoff offered, as `<partition> -> <target node>`.
    pub offers: Mutex<Vec<String>>,
    /// While set, an offer never gets an answer.
    pub hang: std::sync::atomic::AtomicBool,
}

impl LocalPeers {
    pub fn add(&self, engine: &ConnectorEngine, node: &str) {
        self.engines.lock().unwrap().insert(node.to_owned(), engine.clone());
    }
}

#[async_trait]
impl bcs_service_api::port::dist_runtime::DistWorkerPeerPort for LocalPeers {
    async fn handoff(
        &self,
        target: &bcs_service_api::port::repo::distributed::ownership::MemberRecord,
        request: bcs_service_api::port::repo::distributed::ownership::HandoffRequest,
    ) -> Result<bcs_service_api::port::repo::distributed::ownership::HandoffOutcome, bcs_service_api::types::dist_runtime::RuntimeError> {
        self.offers
            .lock()
            .unwrap()
            .push(format!("{} -> {}", request.from.shard.0, target.instance.node));
        if self.hang.load(std::sync::atomic::Ordering::SeqCst) {
            std::future::pending::<()>().await;
        }
        let engine = self.engines.lock().unwrap().get(&target.instance.node).cloned();
        match engine {
            Some(engine) => engine.accept_handoff(request).await,
            None => Err(bcs_service_api::types::dist_runtime::RuntimeError { code: "peer_offline".into() }),
        }
    }
    async fn source_done(
        &self,
        _: &bcs_service_api::port::repo::distributed::ownership::MemberRecord,
        _: Instance,
        _: bcs_service_api::port::repo::distributed::ownership::ShardClaim,
        _: RequestId,
    ) -> Result<bool, bcs_service_api::types::dist_runtime::RuntimeError> {
        Ok(false)
    }
}

pub struct Rig {
    pub store: Arc<Recorder>,
    pub worker: WorkerFixture,
    pub agent: Arc<ScriptedAgent>,
    pub blobs: Arc<MemoryBlobs>,
    pub membership: Arc<dyn MembershipPort>,
    /// Partition owner records, shared by every engine of the rig.
    pub owners: MemoryOwners,
    pub peers: Arc<LocalPeers>,
    pub config: ConnectorConfig,
}

impl Rig {
    pub async fn new() -> Self {
        let store = Arc::new(Recorder::default());
        // A real dispatch always refers to an admitted Session and Task.
        use bcs_store_api::document::DocumentStorePlugin;
        let dispatch = dispatch_spec(&did());
        let mut spec = bcs_dist_test_runner::specification(dispatch.sid.clone());
        spec.shard = bcs_dist_store::layout::KeyLayout::shard_of(&spec.sid).unwrap();
        let task = bcs_dist_test_runner::metadata(&spec, dispatch.tid);
        store
            .model
            .create_document(
                &layout().spec(&spec.sid).unwrap(),
                &serde_json::to_value(&spec).unwrap(),
            )
            .await
            .unwrap();
        store
            .model
            .create_document(
                &layout().task(&task.tid).unwrap(),
                &serde_json::to_value(&task).unwrap(),
            )
            .await
            .unwrap();
        let mut config = ConnectorConfig::new(instance("conn-03"), pool());
        config.poll_interval = Duration::from_millis(25);
        config.receive_timeout = Duration::from_millis(30);
        config.backoff_min = Duration::from_millis(5);
        config.backoff_max = Duration::from_millis(20);
        config.membership_renew = Duration::from_millis(20);
        config.ownership_interval = Duration::from_millis(20);
        config.candidate_backoff = Duration::from_millis(20);
        config.peer_timeout = Duration::from_millis(200);
        config.handoff_reserve = Duration::from_millis(100);
        config.control_address = "http://conn-03.example.test:8080".into();
        Self {
            worker: WorkerFixture::new(&store),
            agent: Arc::new(ScriptedAgent::new(&store)),
            blobs: Arc::new(MemoryBlobs::default()),
            membership: Arc::new(PendingMembership),
            owners: MemoryOwners::default(),
            peers: Arc::new(LocalPeers::default()),
            config,
            store,
        }
    }

    pub fn board_config(&self) -> bcs_dist_connector_store::DispatchBoardConfig {
        let mut board = config(1);
        // This Connector also takes over its own unacked hints quickly.
        board.reclaim_every = Duration::from_millis(20);
        board.reclaim_min_idle = Duration::from_millis(20);
        board
    }

    pub fn inbox(&self) -> Arc<StoreInboxProducer> {
        Arc::new(
            StoreInboxProducer::new(
                Arc::new(bcs_dist_store::budget::SharedScanBudget::new(Default::default()).unwrap()),
                Arc::new(bcs_dist_projection::OutputProjectionService),
                self.store.clone(),
                self.store.clone(),
                self.store.clone(),
                self.store.clone(),
                layout(),
                Arc::new(FixedClock(NOW)),
                InboxProducerConfig::new(BackendId("model".into())),
            )
            .unwrap(),
        )
    }

    pub fn ports(&self) -> ConnectorPorts {
        ConnectorPorts {
            queue: Arc::new(queue_owned(
                &self.store,
                self.membership.clone(),
                Arc::new(self.owners.clone()),
                self.board_config(),
            )),
            inbox: self.inbox(),
            agent: self.agent.clone(),
            membership: self.membership.clone(),
            partitions: Arc::new(self.owners.clone()),
            peers: self.peers.clone(),
            blobs: self.blobs.clone(),
            rules: Arc::new(DistConnectorRules),
        }
    }

    pub fn engine(&self) -> ConnectorEngine {
        ConnectorEngine::new(self.config.clone(), self.ports()).unwrap()
    }

    /// Another Connector process on the same stores, owner records and peer
    /// channel, reachable through `peers`.
    pub fn start_as(&self, node: &str) -> Running {
        let mut config = self.config.clone();
        config.me = instance(node);
        config.control_address = format!("http://{node}.example.test:8080");
        let engine = ConnectorEngine::new(config, self.ports()).unwrap();
        self.peers.add(&engine, node);
        let looped = engine.clone();
        let task = tokio::spawn(async move { looped.run().await });
        Running { engine, task }
    }

    /// Builds an engine and runs its loop in the background.
    pub fn start(&self) -> Running {
        let engine = self.engine();
        self.peers.add(&engine, &self.config.me.node);
        let looped = engine.clone();
        let task = tokio::spawn(async move { looped.run().await });
        Running { engine, task }
    }

    pub async fn doc(&self) -> Value {
        stored(&self.store).await.0
    }

    pub async fn phase(&self) -> String {
        match self.store.model.get_json(&disp_key()).await.unwrap() {
            Some(doc) => doc.value["execution"]["phase"].as_str().unwrap_or("?").to_owned(),
            None => "missing".to_owned(),
        }
    }

    /// The session's inbox as decoded agent events, oldest first.
    pub async fn inbox_events(&self) -> Vec<(InputEnvelope, AgentEvent)> {
        let sid = Sid("s0042.7f3a9c".into());
        let key = layout().inbox(&sid).unwrap();
        let page = match StreamStorePlugin::range(
            &self.store.model,
            &key,
            Bound::Unbounded,
            Bound::Unbounded,
            NonZeroU32::new(500).unwrap(),
            false,
        )
        .await
        {
            Ok(page) => page,
            Err(_) => return Vec::new(),
        };
        page.entries
            .iter()
            .map(|entry| {
                let envelope = decode_inbox_entry(&sid, &entry.fields).unwrap();
                let InputBody::Agent { event } = envelope.body.clone() else {
                    panic!("not an agent input");
                };
                (envelope, event)
            })
            .collect()
    }

    pub async fn kinds(&self) -> Vec<String> {
        self.inbox_events()
            .await
            .iter()
            .map(|(_, event)| kind_name(&event.kind).to_owned())
            .collect()
    }
}

pub fn kind_name(kind: &AgentEventKind) -> &'static str {
    match kind {
        AgentEventKind::Delta => "delta",
        AgentEventKind::Thinking => "thinking",
        AgentEventKind::Tool => "tool",
        AgentEventKind::Snapshot => "snapshot",
        AgentEventKind::InputRequired { .. } => "input_required",
        AgentEventKind::AuthRequired { .. } => "auth_required",
        AgentEventKind::Final => "final",
        AgentEventKind::Error => "error",
    }
}

pub fn state_of(event: &AgentEvent) -> String {
    match &event.payload {
        Payload::Inline(value) => value["state"].as_str().unwrap_or("").to_owned(),
        Payload::Ref(_) => "ref".to_owned(),
    }
}

pub struct Running {
    pub engine: ConnectorEngine,
    pub task: tokio::task::JoinHandle<Result<(), bcs_service_api::types::dist_runtime::RuntimeError>>,
}

impl Running {
    pub async fn active(&self) -> u64 {
        self.engine.snapshot().await.unwrap().active_work
    }
    pub async fn pending_effects(&self) -> u64 {
        self.engine.snapshot().await.unwrap().pending_effects
    }
}

/// Waits until `check` holds; panics with `what` after two seconds.
pub async fn until<F, Fut>(what: &str, mut check: F)
where
    F: FnMut() -> Fut,
    Fut: Future<Output = bool>,
{
    let deadline = tokio::time::Instant::now() + Duration::from_secs(2);
    loop {
        if check().await {
            return;
        }
        assert!(tokio::time::Instant::now() < deadline, "timed out waiting for: {what}");
        tokio::time::sleep(Duration::from_millis(3)).await;
    }
}

/// Lets the engine run for a few poll intervals.
pub async fn settle() {
    tokio::time::sleep(Duration::from_millis(120)).await;
}

/// Membership with a live member list: registered processes are `Alive`
/// and listed until retired (then `Dead`); draining ones are not listed.
#[derive(Default)]
pub struct Members {
    pub records: Mutex<Vec<bcs_service_api::port::repo::distributed::ownership::MemberRecord>>,
    pub dead: Mutex<Vec<String>>,
    pub renewals: std::sync::atomic::AtomicUsize,
    /// While set, `members` does not answer.
    pub stall_members: std::sync::atomic::AtomicBool,
    /// Extra time every `members` answer takes, in ms.
    pub members_delay_ms: std::sync::atomic::AtomicU64,
}

impl Members {
    pub fn kill(&self, node: &str) {
        self.dead.lock().unwrap().push(node.to_owned());
        self.records.lock().unwrap().retain(|r| r.instance.node != node);
    }
}

#[async_trait]
impl MembershipPort for Members {
    async fn register(
        &self,
        instance: Instance,
        role: bcs_service_api::port::repo::distributed::ownership::RuntimeRole,
        control_address: String,
        _: Duration,
    ) -> Result<bcs_service_api::port::repo::distributed::ownership::MembershipLease, StoreError> {
        let mut records = self.records.lock().unwrap();
        if !records.iter().any(|r| r.instance == instance) {
            records.push(bcs_service_api::port::repo::distributed::ownership::MemberRecord {
                instance: instance.clone(),
                role,
                control_address,
                draining: false,
            });
        }
        Ok(bcs_service_api::port::repo::distributed::ownership::MembershipLease { instance })
    }
    async fn renew(
        &self,
        _: &bcs_service_api::port::repo::distributed::ownership::MembershipLease,
        _: Duration,
    ) -> Result<bcs_service_api::port::repo::distributed::ownership::Renewal, StoreError> {
        self.renewals.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        Ok(bcs_service_api::port::repo::distributed::ownership::Renewal::Renewed)
    }
    async fn set_draining(
        &self,
        lease: &bcs_service_api::port::repo::distributed::ownership::MembershipLease,
    ) -> Result<bcs_service_api::port::repo::distributed::ownership::Renewal, StoreError> {
        for record in self.records.lock().unwrap().iter_mut() {
            if record.instance == lease.instance {
                record.draining = true;
            }
        }
        Ok(bcs_service_api::port::repo::distributed::ownership::Renewal::Renewed)
    }
    async fn retire(&self, lease: &bcs_service_api::port::repo::distributed::ownership::MembershipLease) -> Result<(), StoreError> {
        self.kill(&lease.instance.node);
        Ok(())
    }
    async fn liveness(&self, instance: &Instance) -> Result<bcs_service_api::port::repo::distributed::ownership::Liveness, StoreError> {
        use bcs_service_api::port::repo::distributed::ownership::Liveness;
        if self.dead.lock().unwrap().contains(&instance.node) {
            return Ok(Liveness::Dead);
        }
        let listed = self.records.lock().unwrap().iter().any(|r| r.instance == *instance);
        Ok(if listed { Liveness::Alive } else { Liveness::Unknown })
    }
    async fn members(
        &self,
        _: bcs_service_api::port::repo::distributed::ownership::RuntimeRole,
        _: bcs_service_api::port::repo::distributed::common::PageRequest,
    ) -> Result<bcs_service_api::port::repo::distributed::common::Page<bcs_service_api::port::repo::distributed::ownership::MemberRecord>, StoreError> {
        let delay = self.members_delay_ms.load(std::sync::atomic::Ordering::SeqCst);
        tokio::time::sleep(Duration::from_millis(delay)).await;
        while self.stall_members.load(std::sync::atomic::Ordering::SeqCst) {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
        let items = self.records.lock().unwrap().iter().filter(|r| !r.draining).cloned().collect();
        Ok(bcs_service_api::port::repo::distributed::common::Page { items, next: None })
    }
}
