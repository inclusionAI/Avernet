//! Volatile local/test implementation. Never a production fallback.
use std::{collections::BTreeMap, ops::Bound, sync::{Arc, Mutex}};
use async_trait::async_trait;
use bcn_runtime_model::*;
use bcn_runtime_storage_api::{AtomicStore, ServerClock, validation};

type Partition = BTreeMap<String, VersionedValue>;
#[derive(Default)]
struct State { revision: u64, partitions: BTreeMap<(Scope, String), Partition> }
pub struct MemoryAtomicStore { state: Mutex<State>, clock: Arc<dyn ServerClock> }
impl MemoryAtomicStore {
    /// Bootstrap must pass its validated profile; production is always rejected.
    pub fn new(profile: RuntimeProfile, clock: Arc<dyn ServerClock>) -> StorageResult<Self> {
        if profile == RuntimeProfile::Production { return Err(StorageError::UnsupportedProfile); }
        Ok(Self { state: Mutex::new(State::default()), clock })
    }
    fn lock(&self) -> StorageResult<std::sync::MutexGuard<'_, State>> {
        self.state.lock().map_err(|_| StorageError::Unavailable("memory lock poisoned".into()))
    }
}
#[async_trait]
impl AtomicStore for MemoryAtomicStore {
    async fn read_many(&self, scope: &Scope, partition: &str, keys: &[String]) -> StorageResult<Vec<Option<VersionedValue>>> {
        validation::read(scope, partition, keys)?;
        let state = self.lock()?;
        let rows = state.partitions.get(&(scope.clone(), partition.into()));
        Ok(keys.iter().map(|key| rows.and_then(|rows| rows.get(key)).cloned()).collect())
    }
    async fn transact(&self, scope: &Scope, partition: &str, batch: AtomicBatch) -> StorageResult<TransactionReceipt> {
        validation::batch(scope, partition, &batch)?;
        let mut state = self.lock()?;
        let partition_id = (scope.clone(), partition.to_owned());
        let rows = state.partitions.get(&partition_id);
        for condition in &batch.conditions {
            let current = rows.and_then(|rows| rows.get(&condition.key));
            let matches = match condition.expected {
                Expected::Absent => current.is_none(),
                Expected::Revision(revision) => current.is_some_and(|current| current.revision == revision),
            };
            if !matches { return Err(StorageError::ConditionFailed { key: condition.key.clone() }); }
        }
        // Reserve the whole revision range before writing; overflow is also zero-write.
        let end = state.revision.checked_add(batch.mutations.len() as u64).ok_or(StorageError::RevisionExhausted)?;
        let mut next = state.revision;
        let mut revisions = Vec::with_capacity(batch.mutations.len());
        if !batch.mutations.is_empty() {
            let rows = state.partitions.entry(partition_id).or_default();
            for mutation in batch.mutations {
                next += 1;
                let revision = Revision(next);
                let key = mutation.key().to_owned();
                match mutation {
                    Mutation::Put { key, value } => { rows.insert(key, VersionedValue { revision, value }); }
                    Mutation::Delete { key } => { rows.remove(&key); }
                }
                revisions.push(MutationRevision { key, revision });
            }
        }
        state.revision = end;
        Ok(TransactionReceipt { revisions })
    }
    async fn scan_page(&self, scope: &Scope, partition: &str, prefix: &str, cursor: Option<ScanCursor>, limit: usize) -> StorageResult<ScanPage> {
        validation::scan(scope, partition, prefix, cursor.as_ref(), limit)?;
        let state = self.lock()?;
        let Some(rows) = state.partitions.get(&(scope.clone(), partition.into())) else {
            return Ok(ScanPage { entries: vec![], next_cursor: None });
        };
        let start = match &cursor { Some(cursor) => Bound::Excluded(cursor.after_key.as_str()), None => Bound::Included(prefix) };
        let mut entries = rows.range::<str, _>((start, Bound::Unbounded))
            .take_while(|(key, _)| key.starts_with(prefix)).take(limit + 1)
            .map(|(key, entry)| KeyValue { key: key.clone(), entry: entry.clone() }).collect::<Vec<_>>();
        let next_cursor = if entries.len() > limit {
            entries.pop();
            entries.last().map(|entry| ScanCursor { scope: scope.clone(), partition: partition.into(), prefix: prefix.into(), after_key: entry.key.clone() })
        } else { None };
        Ok(ScanPage { entries, next_cursor })
    }
    async fn server_time_ms(&self) -> StorageResult<u64> { Ok(self.clock.now_ms()) }
}

#[cfg(test)]
mod tests {
    use super::*;
    struct Clock;
    impl ServerClock for Clock { fn now_ms(&self) -> u64 { 0 } }
    #[tokio::test]
    async fn revision_exhaustion_rejects_entire_batch() {
        let store = MemoryAtomicStore::new(RuntimeProfile::Test, Arc::new(Clock)).unwrap();
        store.state.lock().unwrap().revision = u64::MAX - 1;
        let scope = Scope::new("test", "tenant", "overflow");
        let put = |key: &str| Mutation::Put { key: key.into(), value: vec![1] };
        let failed = store.transact(&scope, "p", AtomicBatch { conditions: vec![], mutations: vec![put("a"), put("b")] }).await;
        assert_eq!(failed, Err(StorageError::RevisionExhausted));
        assert_eq!(store.read_many(&scope, "p", &["a".into(), "b".into()]).await.unwrap(), vec![None, None]);
        let last = store.transact(&scope, "p", AtomicBatch { conditions: vec![], mutations: vec![put("a")] }).await.unwrap();
        assert_eq!(last.revisions[0].revision, Revision(u64::MAX));
        let failed = store.transact(&scope, "p", AtomicBatch { conditions: vec![], mutations: vec![Mutation::Delete { key: "a".into() }] }).await;
        assert_eq!(failed, Err(StorageError::RevisionExhausted));
        assert!(store.read_many(&scope, "p", &["a".into()]).await.unwrap()[0].is_some());
    }
}
