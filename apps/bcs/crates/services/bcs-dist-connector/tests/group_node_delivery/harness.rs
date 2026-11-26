//! Shared fixtures of the T6 vectors: a fixed clock, node-spec builders,
//! the grant-checking agent view, and Worker-side control writes.
#![allow(dead_code, reason = "shared test fixtures")]

#[path = "../rig/mod.rs"]
pub mod rig;

use rig::*;
use bcs_dist_connector::config::{ConnectorConfig, WallClock};
use bcs_dist_connector::rules::DistConnectorRules;
use bcs_dist_connector::{ConnectorEngine, ConnectorPorts};
use bcs_dist_model::*;
use bcs_service_api::core::dist_connector::DistConnectorCoreService;
use bcs_service_api::port::dist_agent::*;
use bcs_service_api::port::repo::distributed::dispatch::{
    AcceptanceEvidence, ClaimOutcome, DispatchClaimRequest, DispatchQueuePort, HeldDispatchPort,
    SendOperation,
};
use bcs_store_api::json::*;
use bcs_store_api::*;
use serde_json::{Value, json};
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

pub const SID: &str = "s0042.7f3a9c";
pub const TID: &str = "s0042.7f3a9c.b21e";
pub const ROUND: &str = "s0042.7f3a9c.b21e.fsm:1";

/// The authoritative dispatch identity (ruling 1):
/// `<tid>.fsm:<generation>:<node>:<attempt>`.
pub fn fsm_did(node: &str, attempt: u32) -> Did {
    Did(format!("{TID}.fsm:1:{node}:{attempt}"))
}

/// A fixed wall clock so deadline and dedupe-window decisions are deterministic.
pub struct FixedClock;
impl WallClock for FixedClock {
    fn now(&self) -> i64 {
        NOW
    }
}

pub fn engine_config() -> ConnectorConfig {
    let mut config = ConnectorConfig::new(instance("conn-03"), pool());
    config.poll_interval = std::time::Duration::from_millis(25);
    config.receive_timeout = std::time::Duration::from_millis(30);
    config.backoff_min = std::time::Duration::from_millis(5);
    config.backoff_max = std::time::Duration::from_millis(20);
    config.membership_renew = std::time::Duration::from_millis(20);
    config.clock = Arc::new(FixedClock);
    config
}

/// A dispatch spec of one node of the round, shareable by the same bot.
pub fn node_spec(node: &str, attempt: u32) -> DispatchSpec {
    let mut spec = dispatch_spec(&fsm_did(node, attempt));
    spec.node = NodeId(node.into());
    spec.attempt = attempt;
    spec.capabilities.isolated_memory = true;
    spec
}

/// Publication-grade capabilities: a queryable, dedupe-negotiated target
/// with verified memory isolation.
pub fn caps() -> DownstreamCapabilities {
    let mut caps = dispatch_spec(&fsm_did("n2", 1)).capabilities;
    // Publication grade and dedupe-negotiated: the strict-source contract
    // of a target that may be resent inside its proof window.
    caps.idempotent_start = true;
    caps.idempotent_input = true;
    caps.query_by_request = true;
    caps.isolated_memory = true;
    caps.persistent_dedupe = true;
    caps
}

/// (round, node, operation, remote_ref) of one call, for grant assertions.
pub type Seen = (String, NodeId, CallOperation, Option<RemoteTaskRef>);

pub fn seen_of(authority: &CallAuthority) -> Seen {
    (
        authority.round.clone(),
        authority.node.clone(),
        authority.operation,
        authority.remote_ref.clone(),
    )
}

/// A granted view of the scripted agent: the checks the adapter's resolver
/// performs per operation (T6: current authorization is re-verified at
/// dispatch time). Grants are per (node, credential): a revoked credential
/// serves no node, an ordinary rebinding serves the node under the new
/// binding. Denied operations never reach the scripted agent.
pub struct GrantedAgent {
    pub inner: Arc<ScriptedAgent>,
    grants: Mutex<HashMap<(String, String), Grants>>,
    seen: Mutex<Vec<Seen>>,
    denies: AtomicU64,
}

#[derive(Clone, Copy, Default)]
pub struct Grants {
    pub start: bool,
    pub cont: bool,
    pub query: bool,
    pub subscribe: bool,
    pub cancel: bool,
}

impl GrantedAgent {
    pub fn all(inner: Arc<ScriptedAgent>) -> Self {
        Self {
            inner,
            grants: Mutex::new(HashMap::new()),
            seen: Mutex::new(Vec::new()),
            denies: AtomicU64::new(0),
        }
    }
    /// Grants absent (node, credential) entries are fully granted.
    pub fn grant(&self, node: &str, credential: &str, grants: Grants) {
        self.grants
            .lock()
            .unwrap()
            .insert((node.into(), credential.into()), grants);
    }
    pub fn seen(&self) -> Vec<Seen> {
        self.seen.lock().unwrap().clone()
    }
    pub fn denies(&self) -> u64 {
        self.denies.load(Ordering::SeqCst)
    }
    fn permit(&self, authority: &CallAuthority, op: CallOperation) -> bool {
        self.seen.lock().unwrap().push(seen_of(authority));
        let allowed = match self.grants.lock().unwrap().get(&(
            authority.node.0.clone(),
            authority.binding.credential_ref.0.clone(),
        )) {
            // Absent entries are fully granted; the resolver has no
            // revocation on file for this node and credential.
            None => true,
            Some(grants) => match op {
                CallOperation::Start | CallOperation::Resend => grants.start,
                CallOperation::Continue => grants.cont,
                CallOperation::Query => grants.query,
                CallOperation::Subscribe => grants.subscribe,
                CallOperation::Cancel => grants.cancel,
            },
        };
        if !allowed {
            self.denies.fetch_add(1, Ordering::SeqCst);
        }
        allowed
    }
}

#[async_trait::async_trait]
impl DistAgentClientPort for GrantedAgent {
    async fn start(&self, authority: &CallAuthority, request: StableSendRequest) -> SendOutcome {
        if !self.permit(authority, authority.operation) {
            return SendOutcome::NotSent { code: "unauthorized_target".into() };
        }
        self.inner.start(authority, request).await
    }
    async fn send_input(
        &self,
        authority: &CallAuthority,
        remote: &RemoteTaskRef,
        request: StableSendRequest,
    ) -> SendOutcome {
        if !self.permit(authority, CallOperation::Continue) {
            return SendOutcome::NotSent { code: "unauthorized_target".into() };
        }
        self.inner.send_input(authority, remote, request).await
    }
    async fn query(&self, authority: &CallAuthority, query: RemoteQuery) -> QueryOutcome {
        if !self.permit(authority, CallOperation::Query) {
            return QueryOutcome::Unavailable { code: "unauthorized_target".into() };
        }
        self.inner.query(authority, query).await
    }
    async fn subscribe(
        &self,
        authority: &CallAuthority,
        remote: &RemoteTaskRef,
        after: Option<ReplayToken>,
    ) -> Result<RemoteEventStream, AgentReadError> {
        if !self.permit(authority, CallOperation::Subscribe) {
            return Err(AgentReadError { code: "unauthorized_target".into() });
        }
        self.inner.subscribe(authority, remote, after).await
    }
    async fn cancel(&self, authority: &CallAuthority, remote: &RemoteTaskRef, request_id: RequestId) -> CancelOutcome {
        if !self.permit(authority, CallOperation::Cancel) {
            return CancelOutcome::Rejected { code: "unauthorized_target".into() };
        }
        self.inner.cancel(authority, remote, request_id).await
    }
}

/// One held store plus an engine whose agent goes through the grants.
pub struct RigWith {
    pub rig: Rig,
    pub agent: Arc<GrantedAgent>,
}

pub async fn rig_with(agent: Arc<GrantedAgent>) -> RigWith {
    let mut rig = Rig::new().await;
    rig.config = engine_config();
    RigWith { rig, agent }
}

impl RigWith {
    pub fn ports(&self) -> ConnectorPorts {
        let mut ports = self.rig.ports();
        ports.agent = self.agent.clone();
        ports
    }
    pub fn start(&self) -> Running {
        let engine = ConnectorEngine::new(self.rig.config.clone(), self.ports()).unwrap();
        let looped = engine.clone();
        let task = tokio::spawn(async move { looped.run().await });
        Running { engine, task }
    }
    pub async fn doc(&self, did: &Did) -> Value {
        stored_at(&self.rig.store, did).await.0
    }
    pub async fn phase(&self, did: &Did) -> String {
        self.doc(did).await["execution"]["phase"].as_str().unwrap_or("?").to_owned()
    }
    /// The inbox's agent events of one dispatch, oldest first.
    pub async fn inbox_of(&self, did: &Did) -> Vec<AgentEvent> {
        self.rig
            .inbox_events()
            .await
            .into_iter()
            .filter(|(_, event)| event.did == *did)
            .map(|(_, event)| event)
            .collect()
    }
}

/// The stored document of an arbitrary dispatch (the shared helper reads
/// only the fixed did).
pub async fn stored_at(store: &Arc<Recorder>, did: &Did) -> (Value, u64) {
    let doc = store
        .model
        .get_json(&layout().disp(did).unwrap())
        .await
        .unwrap()
        .unwrap();
    (doc.value, doc.version.0)
}

/// The Worker writes one control intent on an arbitrary dispatch.
pub async fn write_control(rig: &Rig, did: &Did, change: impl FnOnce(&mut DispatchControlState) -> bool) {
    // Tests are this dispatch's only writer: one read, one change, one write.
    let key = layout().disp(did).unwrap();
    let doc = rig.store.model.get_json(&key).await.unwrap().unwrap();
    let mut control = serde_json::from_value::<DispatchControlState>(doc.value["control"].clone()).unwrap();
    if !change(&mut control) {
        return;
    }
    let outcome = rig
        .store
        .model
        .mutate_json(
            &key,
            WriteCondition::Newer(bcs_dist_store::version::next(doc.version).unwrap()),
            JsonMutation::Patch(vec![JsonEdit::Set {
                pointer: "/control".into(),
                value: serde_json::to_value(&control).unwrap(),
            }]),
            Expiry::Preserve,
        )
        .await
        .unwrap();
    if matches!(outcome, WriteOutcome::Applied { .. }) {
        rig.worker.notify_did(did).await;
    }
}

/// The Worker queues one input on an arbitrary dispatch.
pub async fn push_input(rig: &Rig, did: &Did, seq: u64) {
    write_control(rig, did, |control| {
        if control.pending_input.iter().any(|p| p.seq.0 == seq) {
            return false;
        }
        control.pending_input.push(PendingInput {
            seq: InputSeq(seq),
            input_id: InputId(format!("in-{seq}")),
            business_digest: Digest(format!("sha256:{seq}")),
            payload: Payload::Inline(json!({"parts": [{"text": "new data"}]})),
        });
        true
    })
    .await;
}

/// The Worker cancels an arbitrary dispatch.
pub async fn worker_cancel(rig: &Rig, did: &Did) {
    write_control(rig, did, |control| {
        if control.cancel.is_some() {
            return false;
        }
        control.cancel = Some(CancelIntent { request_id: RequestId(format!("{}:cancel", did.0)), reason: None });
        true
    })
    .await;
}

/// The canonical proof-writing path of the connector, as a former holder
/// performed it before it died.
pub fn written_proof(
    spec: &DispatchSpec,
    rules: &DistConnectorRules,
    config: &ConnectorConfig,
    request_id: &RequestId,
) -> SendProof {
    let Payload::Inline(body) = &spec.request else {
        panic!("fixture request must be inline");
    };
    rules.send_proof(
        spec,
        ProofOperation::Start { request_id: request_id.clone() },
        &serde_json::to_vec(body).unwrap(),
        config.send_fingerprint(&spec.capabilities),
    )
}

/// A former holder (conn-01) takes the call to `phase` and then dies.
pub async fn former_holder(
    rig: &Rig,
    rules: &DistConnectorRules,
    config: &ConnectorConfig,
    spec: DispatchSpec,
    phase: DispatchPhase,
) -> Box<dyn HeldDispatchPort> {
    let request_id = RequestId(format!("{}:start", spec.did.0));
    let proof = written_proof(&spec, rules, config, &request_id);
    let did = spec.did.clone();
    rig.worker.create_spec(spec).await;
    let queue = queue(&rig.store);
    let mut held = match queue
        .claim(DispatchClaimRequest {
            did,
            me: instance("conn-01"),
            request_id: RequestId("fixture-claim-1".into()),
        })
        .await
        .unwrap()
    {
        ClaimOutcome::Acquired(held) => held,
        other => panic!("fixture claim failed: {}", outcome_name(&other)),
    };
    if phase != DispatchPhase::Claimed {
        held.begin_send(SendOperation::Start { request_id: request_id.clone() }, proof).await.unwrap();
    }
    if phase == DispatchPhase::Accepted {
        held.record_acceptance(AcceptanceEvidence {
            operation: SendOperation::Start { request_id },
            remote: remote(),
        })
        .await
        .unwrap();
    }
    held
}

/// A task snapshot the publication rules accept: complete and ordered.
pub fn complete_result() -> Value {
    json!({
        "state": "completed",
        "artifacts": [
            {"artifactId": "a-1", "parts": [{"text": "part one"}]},
            {"artifactId": "a-2", "parts": [{"text": "part two"}, {"text": "part three"}]}
        ]
    })
}

/// The stop fact of a Completed status carrying no product.
pub fn empty_completed() -> Value {
    json!({"state": "completed"})
}

pub fn own_found(status: TaskStatus, snapshot: Value) -> QueryOutcome {
    QueryOutcome::Found {
        remote: remote(),
        status,
        snapshot: Payload::Inline(snapshot),
    }
}

pub fn foreign_remote() -> RemoteTaskRef {
    RemoteTaskRef { task_id: "rt-foreign".into(), context_id: Some("ctx-foreign".into()) }
}

pub fn foreign_found(status: TaskStatus, snapshot: Value) -> QueryOutcome {
    QueryOutcome::Found {
        remote: foreign_remote(),
        status,
        snapshot: Payload::Inline(snapshot),
    }
}

pub fn foreign_event(kind: AgentEventKind, payload: Value) -> Result<RemoteEvent, AgentReadError> {
    Ok(RemoteEvent {
        remote: foreign_remote(),
        event_id: None,
        cursor: None,
        kind,
        payload: Payload::Inline(payload),
    })
}

/// A foreign terminal frame appears on the connector's subscribe stream.
pub fn poison_subscribe(agent: &ScriptedAgent, kind: AgentEventKind, payload: Value) {
    let (tx, rx) = tokio::sync::mpsc::unbounded_channel();
    agent.subscriptions.lock().unwrap().push_back(Ok(rx));
    tx.send(foreign_event(kind, payload)).unwrap();
    drop(tx);
}
