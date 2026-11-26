//! Test support: the exported store model behind a wrapper that records every
//! call and can lose, fail or reject the n-th write (same pattern as the
//! wrapper in `bcs-dist-store/tests/common`), plus a test-only Worker side of
//! the dispatch document and fixtures.
#![allow(
    dead_code,
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "test support"
)]

use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_dist_store::inbox::Clock;
use bcs_dist_connector_store::membership::PendingMembership;
use bcs_dist_connector_store::{DispatchBoardConfig, StoreDispatchQueue};
use bcs_dist_store::command::{CommandStreams, DID_FIELD};
use bcs_dist_store::error::need_contract;
use bcs_dist_store::layout::KeyLayout;
use bcs_dist_store::version;
use bcs_service_api::port::repo::distributed::common::{
    CommitReceipt, EffectSource, IdempotentWrite, Page, PageRequest, Revision, ShardOwner,
    StoreError as PortError,
};
use bcs_service_api::port::repo::distributed::dispatch::*;
use bcs_service_api::port::repo::distributed::ownership::{
    Liveness, MembershipLease, MembershipPort, Renewal, RuntimeRole,
};
use std::sync::Arc;
use bcs_store_api::json::*;
use bcs_store_api::kv::KvStorePlugin;
use bcs_store_api::stream::*;
use bcs_store_api::zset::*;
use bcs_store_api::*;
use bcs_test_support::store_model::Model;
use serde_json::Value;
use std::collections::BTreeMap;
use std::num::NonZeroU32;
use std::ops::Bound;
use std::sync::Mutex;
use std::time::Duration;

#[derive(Default)]
pub struct Recorder {
    pub model: Model,
    calls: Mutex<Vec<String>>,
    writes: Mutex<usize>,
    lose_write: Mutex<Option<usize>>,
    fail_write: Mutex<Option<usize>>,
    reject_json: Mutex<usize>,
    drop_write: Mutex<Option<usize>>,
    interleave: Mutex<bool>,
    gets: Mutex<usize>,
    fail_get: Mutex<Option<usize>>,
    before_mutate: Mutex<Option<(String, WriteCondition, JsonMutation)>>,
    deny: Mutex<Option<StoreCapability>>,
}

impl Recorder {
    pub fn with_model(model: Model) -> Self {
        Self {
            model,
            ..Self::default()
        }
    }
    pub fn calls(&self) -> Vec<String> {
        self.calls.lock().unwrap().clone()
    }
    pub fn clear(&self) {
        self.calls.lock().unwrap().clear();
    }
    /// Lose the response of the n-th write from now (1-based).
    pub fn lose_write(&self, n: usize) {
        *self.writes.lock().unwrap() = 0;
        *self.lose_write.lock().unwrap() = Some(n);
    }
    /// Fail the n-th write from now (1-based) with a definite error, without
    /// applying it.
    pub fn fail_write(&self, n: usize) {
        *self.writes.lock().unwrap() = 0;
        *self.fail_write.lock().unwrap() = Some(n);
    }
    /// Answer the next `count` JSON writes with `ConditionNotMet` without
    /// applying them, as if someone else had written since the read.
    pub fn reject_json(&self, count: usize) {
        *self.reject_json.lock().unwrap() = count;
    }
    /// Drop the n-th write from now (1-based): it is NOT applied and the
    /// caller sees `OutcomeUnknown`.
    pub fn drop_write(&self, n: usize) {
        *self.writes.lock().unwrap() = 0;
        *self.drop_write.lock().unwrap() = Some(n);
    }
    /// Fail the n-th JSON read from now (1-based).
    pub fn fail_get(&self, n: usize) {
        *self.gets.lock().unwrap() = 0;
        *self.fail_get.lock().unwrap() = Some(n);
    }
    /// Yield at every JSON read, so concurrent callers interleave.
    pub fn interleave(&self) {
        *self.interleave.lock().unwrap() = true;
    }
    /// Apply this raw write to the model right before the next JSON write,
    /// as another writer landing between a caller's read and its write.
    pub fn before_next_mutate(&self, key: String, condition: WriteCondition, mutation: JsonMutation) {
        *self.before_mutate.lock().unwrap() = Some((key, condition, mutation));
    }
    pub fn count(&self, op: &str) -> usize {
        self.calls().iter().filter(|c| c.starts_with(op)).count()
    }
    /// Report one capability as unsupported.
    pub fn deny(&self, capability: StoreCapability) {
        *self.deny.lock().unwrap() = Some(capability);
    }
    pub fn writes_to(&self, key_part: &str) -> usize {
        self.calls()
            .iter()
            .filter(|c| !c.starts_with("json.get") && !c.starts_with("kv.get") && c.contains(key_part))
            .count()
    }
    fn read(&self, op: &str, key: &str) {
        self.calls.lock().unwrap().push(format!("{op} {key}"));
    }
    /// Records a write and decides what happens to it.
    fn write(&self, op: &str, key: &str) -> Effect {
        self.calls.lock().unwrap().push(format!("{op} {key}"));
        let mut writes = self.writes.lock().unwrap();
        *writes += 1;
        let mut dropped = self.drop_write.lock().unwrap();
        if *dropped == Some(*writes) {
            *dropped = None;
            return Effect::Drop;
        }
        let mut lose = self.lose_write.lock().unwrap();
        if *lose == Some(*writes) {
            *lose = None;
            return Effect::Lose;
        }
        let mut fail = self.fail_write.lock().unwrap();
        if *fail == Some(*writes) {
            *fail = None;
            return Effect::Fail;
        }
        Effect::Apply
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Effect {
    Apply,
    Lose,
    Fail,
    Drop,
}

/// Runs a write unless it must fail definitely; loses its response if asked.
macro_rules! run {
    ($effect:expr, $call:expr) => {{
        let effect = $effect;
        if effect == Effect::Fail {
            Err(StoreError::NotApplied("injected".into()))
        } else if effect == Effect::Drop {
            Err(StoreError::OutcomeUnknown("dropped".into()))
        } else {
            match $call {
                Ok(_) if effect == Effect::Lose => Err(StoreError::OutcomeUnknown("lost".into())),
                other => other,
            }
        }
    }};
}

impl StorePlugin for Recorder {
    fn pool_observation(&self) -> Option<StorePoolObservation> {
        self.model.pool_observation()
    }
    fn supports(&self, capability: StoreCapability) -> bool {
        *self.deny.lock().unwrap() != Some(capability) && self.model.supports(capability)
    }
}

#[async_trait]
impl KvStorePlugin for Recorder {
    async fn get(&self, key: &str) -> StoreResult<Option<Vec<u8>>> {
        self.read("kv.get", key);
        self.model.get(key).await
    }
    async fn mget(&self, keys: &[String]) -> StoreResult<Vec<Option<Vec<u8>>>> {
        self.model.mget(keys).await
    }
    async fn set(&self, key: &str, value: &[u8], expiry: Expiry) -> StoreResult<()> {
        run!(self.write("kv.set", key), self.model.set(key, value, expiry).await)
    }
    async fn put_if_absent(&self, key: &str, value: &[u8], expiry: Expiry) -> StoreResult<bool> {
        run!(self.write("kv.put_if_absent", key), self.model.put_if_absent(key, value, expiry).await)
    }
    async fn delete(&self, key: &str) -> StoreResult<bool> {
        run!(self.write("kv.delete", key), KvStorePlugin::delete(&self.model, key).await)
    }
    async fn expire(&self, key: &str, ttl: Duration) -> StoreResult<bool> {
        run!(self.write("kv.expire", key), self.model.expire(key, ttl).await)
    }
    async fn ttl(&self, key: &str) -> StoreResult<KeyTtl> {
        KvStorePlugin::ttl(&self.model, key).await
    }
}

#[async_trait]
impl JsonStorePlugin for Recorder {
    async fn get_json(&self, key: &str) -> StoreResult<Option<Versioned<Value>>> {
        self.read("json.get", key);
        {
            let mut gets = self.gets.lock().unwrap();
            *gets += 1;
            let mut fail = self.fail_get.lock().unwrap();
            if *fail == Some(*gets) {
                *fail = None;
                return Err(StoreError::NotApplied("injected read failure".into()));
            }
        }
        let read = self.model.get_json(key).await;
        let interleave = *self.interleave.lock().unwrap();
        if interleave {
            // The caller holds this read while others run.
            tokio::task::yield_now().await;
        }
        read
    }
    async fn get_paths(&self, key: &str, pointers: &[String]) -> StoreResult<Option<JsonProjection>> {
        self.read("json.get_paths", key);
        self.model.get_paths(key, pointers).await
    }
    async fn mutate_json(
        &self,
        key: &str,
        condition: WriteCondition,
        mutation: JsonMutation,
        expiry: Expiry,
    ) -> StoreResult<WriteOutcome> {
        let injected = self.before_mutate.lock().unwrap().take();
        if let Some((other_key, other_condition, other_mutation)) = injected {
            self.model
                .mutate_json(&other_key, other_condition, other_mutation, Expiry::Preserve)
                .await?;
        }
        {
            let mut reject = self.reject_json.lock().unwrap();
            if *reject > 0 {
                *reject -= 1;
                self.calls.lock().unwrap().push(format!("json.rejected {key}"));
                return Ok(WriteOutcome::ConditionNotMet);
            }
        }
        run!(self.write("json.mutate", key), self.model.mutate_json(key, condition, mutation, expiry).await)
    }
}

#[async_trait]
impl document::DocumentStorePlugin for Recorder {
    async fn read_document(&self, key: &str) -> StoreResult<Option<Versioned<Value>>> {
        self.model.read_document(key).await
    }
    async fn create_document(&self, key: &str, value: &Value) -> StoreResult<WriteOutcome> {
        run!(self.write("document.create", key), self.model.create_document(key, value).await)
    }
    async fn replace_document(&self, key: &str, expected: Version, value: &Value) -> StoreResult<WriteOutcome> {
        run!(self.write("document.replace", key), self.model.replace_document(key, expected, value).await)
    }
}

#[async_trait]
impl ZSetStorePlugin for Recorder {
    async fn add(&self, key: &str, entries: &[ZSetEntry], mode: ZAddMode) -> StoreResult<u64> {
        run!(self.write("zset.add", key), self.model.add(key, entries, mode).await)
    }
    async fn remove(&self, key: &str, members: &[String]) -> StoreResult<u64> {
        run!(self.write("zset.remove", key), self.model.remove(key, members).await)
    }
    async fn range(&self, key: &str, range: &ZSetRange) -> StoreResult<Vec<ZSetEntry>> {
        ZSetStorePlugin::range(&self.model, key, range).await
    }
    async fn snapshot(&self, key: &str, range: &ZSetRange) -> StoreResult<ZSetSnapshot> {
        self.model.snapshot(key, range).await
    }
    async fn update_if_version(
        &self,
        key: &str,
        expected: Option<Version>,
        mutations: &[ZSetMutation],
    ) -> StoreResult<WriteOutcome> {
        run!(self.write("zset.update", key), self.model.update_if_version(key, expected, mutations).await)
    }
}

#[async_trait]
impl StreamStorePlugin for Recorder {
    async fn append(
        &self,
        key: &str,
        id: AppendId,
        fields: &BTreeMap<String, Vec<u8>>,
    ) -> StoreResult<StreamId> {
        run!(self.write("stream.append", key), self.model.append(key, id, fields).await)
    }
    async fn read(&self, streams: &[StreamRead], options: ReadOptions) -> StoreResult<Vec<StreamBatch>> {
        self.model.read(streams, options).await
    }
    async fn range(
        &self,
        key: &str,
        start: Bound<StreamId>,
        end: Bound<StreamId>,
        limit: NonZeroU32,
        reverse: bool,
    ) -> StoreResult<StreamPage> {
        StreamStorePlugin::range(&self.model, key, start, end, limit, reverse).await
    }
    async fn trim(&self, key: &str, policy: TrimPolicy) -> StoreResult<TrimResult> {
        run!(self.write("stream.trim", key), self.model.trim(key, policy).await)
    }
    async fn create_group(&self, key: &str, group: &str, start: GroupStart) -> StoreResult<GroupCreateOutcome> {
        self.model.create_group(key, group, start).await
    }
    async fn read_group(
        &self,
        key: &str,
        group: &str,
        consumer: &str,
        options: ReadOptions,
    ) -> StoreResult<Vec<StreamEntry>> {
        self.read("stream.read_group", key);
        self.model.read_group(key, group, consumer, options).await
    }
    async fn ack(&self, key: &str, group: &str, ids: &[StreamId]) -> StoreResult<u64> {
        self.model.ack(key, group, ids).await
    }
    async fn claim_idle(
        &self,
        key: &str,
        group: &str,
        consumer: &str,
        min_idle: Duration,
        start: Option<StreamId>,
        limit: NonZeroU32,
    ) -> StoreResult<ClaimPage> {
        self.model.claim_idle(key, group, consumer, min_idle, start, limit).await
    }
}

pub struct FixedClock(pub i64);
impl Clock for FixedClock {
    fn now(&self) -> TimestampMillis {
        TimestampMillis(self.0)
    }
}

pub const DID: &str = "s0042.7f3a9c.b21e.n2.a1";
pub const NOW: i64 = 1_794_371_205_200;

pub fn did() -> Did {
    Did(DID.into())
}

pub fn instance(node: &str) -> Instance {
    Instance {
        node: node.into(),
        incarnation: "1794370950000".into(),
    }
}

pub fn layout() -> KeyLayout {
    KeyLayout::new("c01", "main").unwrap()
}

pub fn disp_key() -> String {
    layout().disp(&did()).unwrap()
}

pub fn pool() -> PoolId {
    PoolId("a2a".into())
}

pub fn dispatch_spec(did: &Did) -> DispatchSpec {
    DispatchSpec {
        schema_version: 2,
        sid: Sid("s0042.7f3a9c".into()),
        tid: Tid("s0042.7f3a9c.b21e".into()),
        did: did.clone(),
        source: CorrelationKey("n2".into()),
        attempt: 1,
        agent: AgentId("agent-analyst".into()),
        pool: pool(),
        downstream_context: None,
        request: Payload::Inline(serde_json::json!({"parts": [{"text": "compare"}]})),
        deadline: TimestampMillis(NOW + 300_000),
    }
}

pub fn remote() -> RemoteTaskRef {
    RemoteTaskRef {
        task_id: "rt-77c2".into(),
        context_id: Some("ctx-91".into()),
    }
}

pub fn pending(seq: u64) -> PendingInput {
    PendingInput {
        seq: InputSeq(seq),
        input_id: InputId(format!("in-{seq}")),
        business_digest: Digest(format!("sha256:{seq}")),
        payload: Payload::Inline(serde_json::json!({"parts": [{"text": "A"}]})),
    }
}

pub fn cancel_intent() -> CancelIntent {
    CancelIntent {
        request_id: RequestId("cancel-1".into()),
        reason: None,
    }
}

pub fn start_op() -> SendOperation {
    SendOperation::Start {
        request_id: RequestId(format!("{DID}:start")),
    }
}

pub fn input_op(seq: u64) -> SendOperation {
    SendOperation::Input {
        seq: InputSeq(seq),
        request_id: RequestId(format!("{DID}:in:{seq}")),
    }
}

pub fn accepted(operation: SendOperation) -> AcceptanceEvidence {
    AcceptanceEvidence {
        operation,
        remote: remote(),
    }
}

/// A membership provider with one fixed answer for every instance.
pub struct FixedLiveness(pub Liveness);

#[async_trait]
impl MembershipPort for FixedLiveness {
    async fn register(&self, instance: Instance, _: RuntimeRole, _: String, _: Duration) -> Result<MembershipLease, PortError> {
        Ok(MembershipLease { instance })
    }
    async fn renew(&self, _: &MembershipLease, _: Duration) -> Result<Renewal, PortError> {
        Ok(Renewal::Renewed)
    }
    async fn set_draining(&self, _: &MembershipLease) -> Result<Renewal, PortError> {
        Ok(Renewal::Renewed)
    }
    async fn retire(&self, _: &MembershipLease) -> Result<(), PortError> {
        Ok(())
    }
    async fn liveness(&self, _: &Instance) -> Result<Liveness, PortError> {
        Ok(self.0)
    }
    async fn members(
        &self,
        _: RuntimeRole,
        _: PageRequest,
    ) -> Result<Page<bcs_service_api::port::repo::distributed::ownership::MemberRecord>, PortError> {
        Ok(Page { items: Vec::new(), next: None })
    }
}

pub fn config(partitions: u32) -> DispatchBoardConfig {
    let mut config = DispatchBoardConfig::new(
        BackendId("model".into()),
        CommandStreams::new(NonZeroU32::new(partitions).unwrap()),
    );
    config.read_block = Duration::from_millis(20);
    config
}

pub fn queue_with(
    store: &Arc<Recorder>,
    membership: Arc<dyn MembershipPort>,
    config: DispatchBoardConfig,
) -> StoreDispatchQueue {
    StoreDispatchQueue::new(
        store.clone(),
        store.clone(),
        store.clone(),
        membership,
        layout(),
        Arc::new(FixedClock(NOW)),
        config,
    )
    .unwrap()
}

/// A queue with the pending membership stub, as it runs today.
pub fn queue(store: &Arc<Recorder>) -> StoreDispatchQueue {
    queue_with(store, Arc::new(PendingMembership), config(1))
}

pub fn claim_request(me: &Instance) -> DispatchClaimRequest {
    DispatchClaimRequest {
        did: did(),
        me: me.clone(),
        request_id: RequestId("claim-1".into()),
    }
}

pub async fn acquire(queue: &StoreDispatchQueue, me: &Instance) -> Box<dyn HeldDispatchPort> {
    match queue.claim(claim_request(me)).await.unwrap() {
        ClaimOutcome::Acquired(held) => held,
        other => panic!("not acquired: {}", outcome_name(&other)),
    }
}

pub fn outcome_name(outcome: &ClaimOutcome) -> &'static str {
    match outcome {
        ClaimOutcome::Acquired(_) => "acquired",
        ClaimOutcome::OwnedByMe(_) => "owned_by_me",
        ClaimOutcome::HeldByOther(_) => "held_by_other",
        ClaimOutcome::Ended => "ended",
        ClaimOutcome::NotFound => "not_found",
    }
}

/// The stored document and its version.
pub async fn stored(store: &Recorder) -> (Value, u64) {
    let doc = store.model.get_json(&disp_key()).await.unwrap().unwrap();
    (doc.value, doc.version.0)
}

pub async fn held_members(store: &Recorder, me: &Instance) -> Vec<String> {
    let range = ZSetRange {
        min: Bound::Unbounded,
        max: Bound::Unbounded,
        after: None,
        limit: NonZeroU32::new(100).unwrap(),
    };
    ZSetStorePlugin::range(&store.model, &layout().held(me).unwrap(), &range)
        .await
        .unwrap()
        .into_iter()
        .map(|e| e.member)
        .collect()
}

/// Test-only Worker side of the dispatch document: it writes `spec` at
/// creation and `control` afterwards, with the design's version rules, and
/// posts hints. The real provider belongs to the executor team.
pub struct WorkerFixture {
    pub store: Arc<Recorder>,
    pub streams: CommandStreams,
}

impl WorkerFixture {
    pub fn new(store: &Arc<Recorder>) -> Self {
        Self {
            store: store.clone(),
            streams: CommandStreams::new(NonZeroU32::MIN),
        }
    }

    pub async fn create_spec(&self, spec: DispatchSpec) -> bool {
        let key = layout().disp(&spec.did).unwrap();
        let view = DispatchView {
            spec,
            control: DispatchControlState { cancel: None, pending_input: Vec::new() },
            execution: DispatchExecution {
                holder: None,
                phase: DispatchPhase::Created,
                remote: None,
                confirmed_input_through: None,
                cancellation: CancelObservation::NotSent,
                outcome: None,
            },
        };
        let outcome = self
            .store
            .model
            .mutate_json(
                &key,
                WriteCondition::Newer(version::CREATE),
                JsonMutation::Replace(serde_json::to_value(&view).unwrap()),
                Expiry::Persistent,
            )
            .await
            .unwrap();
        matches!(outcome, WriteOutcome::Applied { .. })
    }

    pub async fn create(&self) -> bool {
        self.create_spec(dispatch_spec(&did())).await
    }

    /// Read, change `control`, write at read version + 1, retry on rejection.
    async fn write_control(&self, did: &Did, change: impl Fn(&mut DispatchControlState, &DispatchExecution) -> bool) -> bool {
        let key = layout().disp(did).unwrap();
        loop {
            let doc = self.store.model.get_json(&key).await.unwrap().unwrap();
            let mut control: DispatchControlState = serde_json::from_value(doc.value["control"].clone()).unwrap();
            let execution: DispatchExecution = serde_json::from_value(doc.value["execution"].clone()).unwrap();
            if !change(&mut control, &execution) {
                return false;
            }
            let outcome = self
                .store
                .model
                .mutate_json(
                    &key,
                    WriteCondition::Newer(version::next(doc.version).unwrap()),
                    JsonMutation::Patch(vec![JsonEdit::Set {
                        pointer: "/control".into(),
                        value: serde_json::to_value(&control).unwrap(),
                    }]),
                    Expiry::Preserve,
                )
                .await
                .unwrap();
            if matches!(outcome, WriteOutcome::Applied { .. }) {
                return true;
            }
        }
    }

    pub async fn cancel(&self) -> bool {
        self.write_control(&did(), |control, _| {
            if control.cancel.is_some() {
                return false;
            }
            control.cancel = Some(cancel_intent());
            true
        })
        .await
    }

    /// Queues an input unless it was already confirmed or queued; also drops
    /// entries the Connector confirmed (the Worker's removal job).
    pub async fn push(&self, input: PendingInput) -> bool {
        self.write_control(&did(), |control, execution| {
            let confirmed = execution.confirmed_input_through.map_or(0, |s| s.0);
            let before = control.pending_input.len();
            control.pending_input.retain(|p| p.seq.0 > confirmed);
            let known = input.seq.0 <= confirmed || control.pending_input.iter().any(|p| p.seq == input.seq);
            if !known {
                control.pending_input.push(input.clone());
            }
            !known || control.pending_input.len() != before
        })
        .await
    }

    pub async fn notify_did(&self, did: &Did) -> StreamId {
        let key = layout().command(&pool(), self.streams.partition_of(did)).unwrap();
        let mut fields = BTreeMap::new();
        fields.insert(DID_FIELD.to_owned(), did.0.clone().into_bytes());
        self.store.model.append(&key, AppendId::Auto, &fields).await.unwrap()
    }

    pub async fn notify(&self) -> StreamId {
        self.notify_did(&did()).await
    }
}

fn source() -> EffectSource {
    let sid = Sid("s0042.7f3a9c".into());
    EffectSource {
        commit: CommitReceipt {
            sid: sid.clone(),
            owner: ShardOwner { instance: instance("worker-07"), epoch: WorkerEpoch(13) },
            commit_id: CommitId("c-12".into()),
            content_digest: Digest("sha256:c12".into()),
            sequence: CommitSeq(12),
            consumed: None,
            proof: "proof".into(),
        },
        effect_id: EffectId { sid, commit: CommitSeq(12), index: NonZeroU32::MIN },
        content_digest: Digest("sha256:e1".into()),
    }
}

pub fn committed_input(seq: u64) -> CommittedDispatchInput {
    CommittedDispatchInput { source: source(), did: did(), input: pending(seq) }
}

/// Just enough of the Worker's port for the shared port harnesses.
#[async_trait]
impl DispatchControlPort for WorkerFixture {
    async fn create(&self, request: CreateCommittedDispatch) -> Result<IdempotentWrite, PortError> {
        Ok(if self.create_spec(request.spec).await {
            IdempotentWrite::Applied
        } else {
            IdempotentWrite::AlreadyApplied
        })
    }
    async fn request_cancel(&self, _: CommittedCancelIntent) -> Result<ControlOutcome, PortError> {
        Ok(if self.cancel().await { ControlOutcome::Applied } else { ControlOutcome::AlreadyApplied })
    }
    async fn push_input(&self, request: CommittedDispatchInput) -> Result<ControlOutcome, PortError> {
        let key = disp_key();
        let before = self.store.model.get_json(&key).await.unwrap().unwrap();
        let execution: DispatchExecution = serde_json::from_value(before.value["execution"].clone()).unwrap();
        let control: DispatchControlState = serde_json::from_value(before.value["control"].clone()).unwrap();
        let confirmed = execution.confirmed_input_through.map_or(0, |s| s.0);
        let known = request.input.seq.0 <= confirmed || control.pending_input.iter().any(|p| p.seq == request.input.seq);
        self.push(request.input).await;
        Ok(if known { ControlOutcome::AlreadyApplied } else { ControlOutcome::Applied })
    }
    async fn notify(&self, _: &PoolId, did: &Did) -> Result<(), PortError> {
        self.notify_did(did).await;
        Ok(())
    }
    async fn get(&self, _: &Did) -> Result<Option<DispatchRecord>, PortError> {
        Err(need_contract())
    }
    async fn scan_due(&self, _: ShardId, _: TimestampMillis, _: PageRequest) -> Result<Page<Did>, PortError> {
        Err(need_contract())
    }
    async fn reschedule(&self, _: &Did, _: Revision, _: TimestampMillis) -> Result<RecheckOutcome, PortError> {
        Err(need_contract())
    }
}
