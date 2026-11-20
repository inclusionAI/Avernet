//! In-memory model of the `bcs-store-api` traits, for tests only.
//!
//! It runs the reusable conformance harness (`tests/conformance_store.rs`) and
//! backs provider tests of `*-store` crates. It is not a production plugin: no
//! durability, no network, no failover. The `broken_*` and `*_disabled` flags
//! inject faults the harness must reject.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test-only fixture")]
mod json;
mod coordination;
mod kv;
mod stream;
mod zset;
use bcs_store_api::stream::{StreamEntry, StreamId};
use bcs_store_api::*;
use serde_json::Value;
use std::{
    collections::{BTreeMap, HashMap},
    sync::{
        Mutex,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    time::{Duration, Instant},
};

#[derive(Default)]
pub struct Model {
    state: Mutex<State>,
    now: AtomicU64,
    lose_response: AtomicBool,
    pub broken_cas: bool,
    pub broken_kv_delete: bool,
    pub broken_kv_ttl: bool,
    pub ordinary_disabled: bool,
    pub monotonic_disabled: bool,
    pub versioned_ttl_disabled: bool,
    pub versioned_delete_disabled: bool,
    pub reset_kv_history: bool,
    pub broken_json: bool,
    pub broken_json_cas: bool,
    pub broken_zset_cas: bool,
    pub broken_zset_noop: bool,
    pub conditional_disabled: bool,
    pub basic_only: bool,
    pub broken_stream_fairness: bool,
}
#[derive(Clone)]
struct Record<T> {
    value: T,
    expires: Option<u64>,
}
#[derive(Default)]
struct State {
    kv: HashMap<String, Record<Vec<u8>>>,
    leases: HashMap<String, coordination::LeaseRecord>,
    json: HashMap<String, Record<Value>>,
    zsets: HashMap<String, BTreeMap<String, f64>>,
    streams: HashMap<String, Log>,
    versions: HashMap<String, Version>,
    read_turn: usize,
    read_turns: HashMap<Vec<String>, usize>,
}
#[derive(Default)]
struct Log {
    entries: BTreeMap<StreamId, StreamEntry>,
    last: Option<StreamId>,
    removed: Option<StreamId>,
    groups: HashMap<String, Group>,
}
struct Group {
    cursor: Option<StreamId>,
    pending: BTreeMap<StreamId, Pending>,
}
struct Pending {
    consumer: String,
    delivered: Instant,
}
impl StorePlugin for Model {
    fn supports(&self, c: StoreCapability) -> bool {
        !(matches!(
            c,
            StoreCapability::ConditionalKv
                | StoreCapability::MonotonicKv
                | StoreCapability::VersionedKvTtl
                | StoreCapability::VersionedKvConditionalDelete
        ) && self.conditional_disabled
            || matches!(
                c,
                StoreCapability::MonotonicKv
                    | StoreCapability::StreamRecovery
                    | StoreCapability::VersionedKvTtl
                    | StoreCapability::VersionedKvConditionalDelete
            ) && self.basic_only
            || c == StoreCapability::Kv && self.ordinary_disabled
            || c == StoreCapability::MonotonicKv && self.monotonic_disabled
            || c == StoreCapability::VersionedKvTtl && self.versioned_ttl_disabled
            || c == StoreCapability::VersionedKvConditionalDelete && self.versioned_delete_disabled)
    }
}
impl Model {
    pub fn advance(&self, by: Duration) {
        self.now.fetch_add(by.as_millis() as u64, Ordering::SeqCst);
    }
    pub fn lose_next_response(&self) {
        self.lose_response.store(true, Ordering::SeqCst);
    }
    fn require(&self, c: StoreCapability) -> StoreResult<()> {
        if self.supports(c) {
            Ok(())
        } else {
            Err(StoreError::Unsupported(c))
        }
    }
    fn finish<T>(&self, result: T) -> StoreResult<T> {
        if self.lose_response.swap(false, Ordering::SeqCst) {
            Err(StoreError::OutcomeUnknown("injected lost response".into()))
        } else {
            Ok(result)
        }
    }
    fn time(&self) -> u64 {
        self.now.load(Ordering::SeqCst)
    }
}
fn invalid(message: &str) -> StoreError {
    StoreError::InvalidInput(message.into())
}
fn expiry(old: Option<u64>, e: Expiry, now: u64) -> StoreResult<Option<u64>> {
    match e {
        Expiry::Preserve => Ok(old),
        Expiry::Persistent => Ok(None),
        Expiry::After(d) if d.is_zero() => Err(invalid("zero TTL")),
        Expiry::After(d) => Ok(Some(
            now.checked_add(
                u64::try_from(d.as_nanos().div_ceil(1_000_000))
                    .map_err(|_| invalid("TTL overflow"))?,
            )
            .ok_or_else(|| invalid("TTL overflow"))?,
        )),
    }
}
impl State {
    fn expire_values(&mut self, key: &str, now: u64) {
        if self
            .kv
            .get(key)
            .is_some_and(|x| x.expires.is_some_and(|t| t <= now))
        {
            self.kv.remove(key);
        }
        if self
            .json
            .get(key)
            .is_some_and(|x| x.expires.is_some_and(|t| t <= now))
        {
            self.json.remove(key);
        }
    }
    fn kind(&self, key: &str, expected: &str) -> StoreResult<()> {
        let actual = if self.kv.contains_key(key) {
            "kv"
        } else if self.json.contains_key(key) {
            "json"
        } else if self.zsets.contains_key(key) {
            "zset"
        } else if self.streams.contains_key(key) {
            "stream"
        } else {
            expected
        };
        if actual == expected {
            Ok(())
        } else {
            Err(StoreError::WrongType)
        }
    }
    fn next(
        &self,
        key: &str,
        condition: WriteCondition,
        exists: bool,
    ) -> StoreResult<Option<Version>> {
        let old = self.versions.get(key).copied().unwrap_or(Version(0));
        let pass = match condition {
            WriteCondition::Any => true,
            WriteCondition::Absent => !exists,
            WriteCondition::Version(v) => {
                if v.0 == 0 {
                    return Err(invalid("zero version"));
                }
                exists && v == old
            }
            WriteCondition::Newer(v) => {
                if v.0 == 0 {
                    return Err(invalid("zero version"));
                }
                v > old
            }
        };
        if !pass {
            return Ok(None);
        }
        Ok(Some(match condition {
            WriteCondition::Newer(v) => v,
            _ => Version(old.0.checked_add(1).ok_or(StoreError::VersionExhausted)?),
        }))
    }
}
