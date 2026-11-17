//! Test support: the exported store model behind a wrapper that records every
//! call and can lose the response of the n-th write (the write is applied, the
//! caller sees `OutcomeUnknown`), plus fixtures of the merged model types.
#![allow(dead_code, clippy::unwrap_used, clippy::expect_used, reason = "test support")]

use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_dist_store::inbox::Clock;
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
    fn read(&self, op: &str, key: &str) {
        self.calls.lock().unwrap().push(format!("{op} {key}"));
    }
    /// Records a write; true when its response must be lost.
    fn write(&self, op: &str, key: &str) -> bool {
        self.calls.lock().unwrap().push(format!("{op} {key}"));
        let mut writes = self.writes.lock().unwrap();
        *writes += 1;
        let mut lose = self.lose_write.lock().unwrap();
        if *lose == Some(*writes) {
            *lose = None;
            return true;
        }
        false
    }
    fn finish<T>(lose: bool, result: StoreResult<T>) -> StoreResult<T> {
        match result {
            Ok(_) if lose => Err(StoreError::OutcomeUnknown("lost".into())),
            other => other,
        }
    }
}

impl StorePlugin for Recorder {
    fn supports(&self, capability: StoreCapability) -> bool {
        self.model.supports(capability)
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
        let lose = self.write("kv.set", key);
        Self::finish(lose, self.model.set(key, value, expiry).await)
    }
    async fn put_if_absent(&self, key: &str, value: &[u8], expiry: Expiry) -> StoreResult<bool> {
        let lose = self.write("kv.put_if_absent", key);
        Self::finish(lose, self.model.put_if_absent(key, value, expiry).await)
    }
    async fn delete(&self, key: &str) -> StoreResult<bool> {
        let lose = self.write("kv.delete", key);
        Self::finish(lose, KvStorePlugin::delete(&self.model, key).await)
    }
    async fn expire(&self, key: &str, ttl: Duration) -> StoreResult<bool> {
        let lose = self.write("kv.expire", key);
        Self::finish(lose, self.model.expire(key, ttl).await)
    }
    async fn ttl(&self, key: &str) -> StoreResult<KeyTtl> {
        KvStorePlugin::ttl(&self.model, key).await
    }
}

#[async_trait]
impl JsonStorePlugin for Recorder {
    async fn get_json(&self, key: &str) -> StoreResult<Option<Versioned<Value>>> {
        self.read("json.get", key);
        self.model.get_json(key).await
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
        let lose = self.write("json.mutate", key);
        Self::finish(lose, self.model.mutate_json(key, condition, mutation, expiry).await)
    }
}

#[async_trait]
impl ZSetStorePlugin for Recorder {
    async fn add(&self, key: &str, entries: &[ZSetEntry], mode: ZAddMode) -> StoreResult<u64> {
        let lose = self.write("zset.add", key);
        Self::finish(lose, self.model.add(key, entries, mode).await)
    }
    async fn remove(&self, key: &str, members: &[String]) -> StoreResult<u64> {
        let lose = self.write("zset.remove", key);
        Self::finish(lose, self.model.remove(key, members).await)
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
        let lose = self.write("zset.update", key);
        Self::finish(lose, self.model.update_if_version(key, expected, mutations).await)
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
        let lose = self.write("stream.append", key);
        Self::finish(lose, self.model.append(key, id, fields).await)
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
        let lose = self.write("stream.trim", key);
        Self::finish(lose, self.model.trim(key, policy).await)
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

pub struct FixedClock(pub i64);
impl Clock for FixedClock {
    fn now(&self) -> TimestampMillis {
        TimestampMillis(self.0)
    }
}

pub const SID: &str = "s0042.7f3a9c";
pub const TID: &str = "s0042.7f3a9c.b21e";

pub fn scope() -> TaskScope {
    TaskScope {
        tenant: TenantId("t-ant".into()),
        principal: PrincipalId("user-001".into()),
    }
}

pub fn spec(group: &str) -> SessionSpec {
    SessionSpec {
        schema_version: 1,
        sid: Sid(SID.into()),
        shard: ShardId(42),
        scope: scope(),
        kind: SessionKind::FsmGroup,
        group: GroupId(group.into()),
        workflow: WorkflowRef {
            definition_id: "wf-research".into(),
            revision: "3".into(),
            digest: Digest("sha256:6b1e".into()),
        },
        participants: vec![],
        limits: SessionLimits {
            max_queued_tasks: 4,
            max_task_steps: 32,
            task_timeout_millis: 600_000,
            max_inline_bytes: 65_536,
        },
        digest: Digest("sha256:3f1a".into()),
        created_at: TimestampMillis(1_794_371_200_000),
    }
}

pub fn task() -> TaskSnapshot {
    TaskSnapshot {
        schema_version: 1,
        scope: scope(),
        sid: Sid(SID.into()),
        tid: Tid(TID.into()),
        content: TaskContent {
            status: TaskStatus::Submitted,
            interactions: vec![],
            snapshot: Payload::Inline(Value::Null),
            result_ref: None,
        },
        snapshot_event_id: None,
        source_commit: CommitSeq(0),
        created_at: TimestampMillis(1_794_371_200_000),
        updated_at: TimestampMillis(1_794_371_200_000),
    }
}

pub fn user_input(input_id: &str, text: &str) -> InputEnvelope {
    let body = InputBody::User {
        payload: Payload::Inline(serde_json::json!({ "text": text })),
    };
    let digest = bcs_dist_store::identity::digest_json(&serde_json::to_value(&body).unwrap());
    InputEnvelope {
        schema_version: 1,
        sid: Sid(SID.into()),
        tid: Tid(TID.into()),
        input_id: InputId(input_id.into()),
        business_digest: digest,
        body,
    }
}
