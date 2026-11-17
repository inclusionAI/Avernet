//! Test support: the store model behind a stream wrapper that counts blocking
//! reads and can fail them, a fake Worker that writes `ev` then `task` the way
//! the real one will, and an in-memory final-record store.
#![allow(dead_code, clippy::unwrap_used, clippy::expect_used, reason = "test support")]

use async_trait::async_trait;
use bcs_dist_gateway_store::{GatewayStoreConfig, StoreTaskReader};
use bcs_dist_model::*;
use bcs_dist_store::codec::{encode_ev_entry, encode_json};
use bcs_dist_store::layout::KeyLayout;
use bcs_service_api::port::repo::distributed::common::{EffectSource, StoreError};
use bcs_service_api::port::repo::distributed::result::{ResultStorePort, SavedResult};
use bcs_store_api::json::{JsonMutation, JsonStorePlugin};
use bcs_store_api::stream::*;
use bcs_store_api::zset::{ZAddMode, ZSetEntry, ZSetStorePlugin};
use bcs_store_api::*;
use bcs_test_support::store_model::Model;
use std::collections::{BTreeMap, HashMap};
use std::num::NonZeroU32;
use std::ops::Bound;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

pub const SID: &str = "s0042.7f3a9c";
pub const TID: &str = "s0042.7f3a9c.b21e";
pub const OTHER_TID: &str = "s0042.7f3a9c.c33f";

pub fn layout() -> KeyLayout {
    KeyLayout::new("c01", "main").unwrap()
}

pub fn scope() -> TaskScope {
    TaskScope {
        tenant: TenantId("t-ant".into()),
        principal: PrincipalId("user-001".into()),
    }
}

pub fn other_scope() -> TaskScope {
    TaskScope {
        tenant: TenantId("t-ant".into()),
        principal: PrincipalId("user-002".into()),
    }
}

pub fn eid(commit: u64, index: u32) -> EventId {
    EventId {
        commit: CommitSeq(commit),
        index: NonZeroU32::new(index).unwrap(),
    }
}

pub fn snapshot(tid: &str, status: TaskStatus, covered: Option<EventId>, created_at: i64) -> TaskSnapshot {
    TaskSnapshot {
        schema_version: 1,
        scope: scope(),
        sid: Sid(SID.into()),
        tid: Tid(tid.into()),
        content: TaskContent {
            status,
            interactions: vec![],
            snapshot: Payload::Inline(serde_json::json!({ "artifacts": [] })),
            result_ref: None,
        },
        snapshot_event_id: covered,
        source_commit: CommitSeq(covered.map_or(0, |e| e.commit.0)),
        created_at: TimestampMillis(created_at),
        updated_at: TimestampMillis(created_at),
    }
}

pub fn status_event(tid: &str, id: EventId, status: TaskStatus) -> OutputEvent {
    OutputEvent {
        id,
        tid: Tid(tid.into()),
        body: OutputBody::Status {
            status,
            interactions: vec![],
        },
    }
}

pub fn text_event(tid: &str, id: EventId, text: &str) -> OutputEvent {
    OutputEvent {
        id,
        tid: Tid(tid.into()),
        body: OutputBody::Artifact {
            node: NodeId("n1".into()),
            class: OutputClass::Output,
            content: Payload::Inline(serde_json::json!({ "text": text })),
        },
    }
}

/// Writes what the Worker writes, in its order: `ev` entries, then `task`.
pub struct FakeWorker {
    pub model: Arc<Model>,
    version: Mutex<HashMap<String, u64>>,
}

impl FakeWorker {
    pub fn new(model: Arc<Model>) -> Self {
        Self {
            model,
            version: Mutex::new(HashMap::new()),
        }
    }

    /// What Gateway admission does for a new task: create it and list it.
    pub async fn create(&self, task: &TaskSnapshot) {
        self.publish(task).await;
        ZSetStorePlugin::add(
            self.model.as_ref(),
            &layout().tasks(&task.sid).unwrap(),
            &[ZSetEntry {
                member: task.tid.0.clone(),
                score: task.created_at.0 as f64,
            }],
            ZAddMode::InsertOnly,
        )
        .await
        .unwrap();
    }

    pub async fn append(&self, event: &OutputEvent) {
        let (id, fields) = encode_ev_entry(event).unwrap();
        let key = layout().ev(&Sid(SID.into())).unwrap();
        StreamStorePlugin::append(self.model.as_ref(), &key, AppendId::Explicit(id), &fields)
            .await
            .unwrap();
    }

    pub async fn append_raw(&self, id: StreamId, fields: BTreeMap<String, Vec<u8>>) {
        let key = layout().ev(&Sid(SID.into())).unwrap();
        StreamStorePlugin::append(self.model.as_ref(), &key, AppendId::Explicit(id), &fields)
            .await
            .unwrap();
    }

    pub async fn publish(&self, task: &TaskSnapshot) {
        let key = layout().task(&task.tid).unwrap();
        let version = {
            let mut versions = self.version.lock().unwrap();
            let v = versions.entry(key.clone()).or_insert(0);
            *v += 1;
            *v
        };
        let outcome = self
            .model
            .mutate_json(
                &key,
                WriteCondition::Newer(Version(version)),
                JsonMutation::Replace(encode_json(task).unwrap()),
                Expiry::Persistent,
            )
            .await
            .unwrap();
        assert!(matches!(outcome, WriteOutcome::Applied { .. }));
    }

    /// The task key expired (7 days after the task ended).
    pub async fn expire(&self, tid: &str) {
        let key = layout().task(&Tid(tid.into())).unwrap();
        self.model
            .mutate_json(&key, WriteCondition::Any, JsonMutation::Delete, Expiry::Persistent)
            .await
            .unwrap();
    }
}

/// The stream store with counted and failable blocking reads.
pub struct CountingStream {
    pub model: Arc<Model>,
    pub blocking_reads: AtomicUsize,
    pub ranges: AtomicUsize,
    pub fail_reads: AtomicBool,
    pub multi_key_reads: AtomicUsize,
}

impl CountingStream {
    pub fn new(model: Arc<Model>) -> Self {
        Self {
            model,
            blocking_reads: AtomicUsize::new(0),
            ranges: AtomicUsize::new(0),
            fail_reads: AtomicBool::new(false),
            multi_key_reads: AtomicUsize::new(0),
        }
    }
}

impl StorePlugin for CountingStream {
    fn supports(&self, capability: StoreCapability) -> bool {
        self.model.supports(capability)
    }
}

#[async_trait]
impl StreamStorePlugin for CountingStream {
    async fn append(&self, key: &str, id: AppendId, fields: &BTreeMap<String, Vec<u8>>) -> StoreResult<StreamId> {
        self.model.append(key, id, fields).await
    }
    async fn read(&self, streams: &[StreamRead], options: ReadOptions) -> StoreResult<Vec<StreamBatch>> {
        self.blocking_reads.fetch_add(1, Ordering::SeqCst);
        if streams.len() != 1 {
            self.multi_key_reads.fetch_add(1, Ordering::SeqCst);
        }
        if self.fail_reads.load(Ordering::SeqCst) {
            return Err(bcs_store_api::StoreError::NotApplied("injected".into()));
        }
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
        self.ranges.fetch_add(1, Ordering::SeqCst);
        StreamStorePlugin::range(self.model.as_ref(), key, start, end, limit, reverse).await
    }
    async fn trim(&self, key: &str, policy: TrimPolicy) -> StoreResult<TrimResult> {
        self.model.trim(key, policy).await
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

/// Final records, keyed by task id.
#[derive(Default)]
pub struct MemoryResults {
    pub records: Mutex<HashMap<String, FinalTaskRecord>>,
}

impl MemoryResults {
    pub fn put(&self, record: FinalTaskRecord) {
        self.records.lock().unwrap().insert(record.tid.0.clone(), record);
    }
}

pub fn final_record(tid: &str) -> FinalTaskRecord {
    FinalTaskRecord {
        schema_version: 1,
        scope: scope(),
        sid: Sid(SID.into()),
        tid: Tid(tid.into()),
        status: TerminalStatus::Completed,
        snapshot: Payload::Inline(serde_json::json!({ "artifacts": ["final"] })),
        result: Payload::Inline(serde_json::json!("done")),
        source_commit: CommitSeq(9),
        finalized_at: TimestampMillis(1_794_371_300_000),
    }
}

#[async_trait]
impl ResultStorePort for MemoryResults {
    async fn save_final(&self, _source: EffectSource, _record: FinalTaskRecord) -> Result<SavedResult, StoreError> {
        Err(StoreError::NotApplied {
            code: "not_used_by_gateway".into(),
        })
    }
    async fn get_final(&self, scope: &TaskScope, tid: &Tid) -> Result<Option<FinalTaskRecord>, StoreError> {
        Ok(self
            .records
            .lock()
            .unwrap()
            .get(&tid.0)
            .filter(|record| &record.scope == scope)
            .cloned())
    }
}

pub struct Fixture {
    pub model: Arc<Model>,
    pub stream: Arc<CountingStream>,
    pub results: Arc<MemoryResults>,
    pub worker: FakeWorker,
    pub reader: StoreTaskReader,
}

pub fn fixture() -> Fixture {
    fixture_with(|_| {})
}

pub fn fixture_with(tune: impl FnOnce(&mut GatewayStoreConfig)) -> Fixture {
    let model = Arc::new(Model::default());
    let stream = Arc::new(CountingStream::new(model.clone()));
    let results = Arc::new(MemoryResults::default());
    let mut config = GatewayStoreConfig::new(BackendId("tbase".into()));
    config.read_block = Duration::from_millis(40);
    tune(&mut config);
    let reader = StoreTaskReader::new(
        model.clone(),
        model.clone(),
        stream.clone(),
        results.clone(),
        layout(),
        config,
    )
    .unwrap();
    Fixture {
        worker: FakeWorker::new(model.clone()),
        model,
        stream,
        results,
        reader,
    }
}
