//! Versioned serialization and bounded atomic operations shared by repositories.
use serde::{de::DeserializeOwned, Deserialize, Serialize};
use bcn_runtime_model::*;
use crate::{keys::PARTITION, RuntimeStore};
#[derive(Serialize, Deserialize)]
struct Envelope<T> { schema: u32, data: T }
pub(crate) fn encode<T: Serialize>(data: &T) -> RuntimeResult<Vec<u8>> {
    serde_json::to_vec(&Envelope { schema: 1, data }).map_err(|e| RuntimeError::Storage(e.to_string()))
}
pub(crate) fn decode<T: DeserializeOwned>(row: &VersionedValue) -> RuntimeResult<T> {
    let envelope: Envelope<T> = serde_json::from_slice(&row.value).map_err(|e| RuntimeError::Storage(e.to_string()))?;
    if envelope.schema != 1 { return Err(RuntimeError::IncompatibleSchema(envelope.schema)); }
    Ok(envelope.data)
}
pub(crate) fn condition(key: String, row: &Option<VersionedValue>) -> Condition {
    Condition { key, expected: row.as_ref().map_or(Expected::Absent, |v| Expected::Revision(v.revision)) }
}
pub(crate) fn put<T: Serialize>(key: String, value: &T) -> RuntimeResult<Mutation> { Ok(Mutation::Put { key, value: encode(value)? }) }
pub(crate) fn storage(error: StorageError) -> RuntimeError {
    match error { StorageError::ConditionFailed { .. } => RuntimeError::Conflict("concurrent update; retry from durable state".into()), error => RuntimeError::Storage(error.to_string()) }
}
impl RuntimeStore {
    pub(crate) async fn rows(&self, scope: &Scope, keys: &[String]) -> RuntimeResult<Vec<Option<VersionedValue>>> {
        self.atomic.read_many(scope, PARTITION, keys).await.map_err(storage)
    }
    pub(crate) async fn row(&self, scope: &Scope, key: String) -> RuntimeResult<Option<VersionedValue>> {
        Ok(self.rows(scope, &[key]).await?.remove(0))
    }
    pub(crate) async fn write(&self, scope: &Scope, conditions: Vec<Condition>, mutations: Vec<Mutation>) -> RuntimeResult<TransactionReceipt> {
        self.atomic.transact(scope, PARTITION, AtomicBatch { conditions, mutations }).await.map_err(storage)
    }
}
impl RuntimeStore {
    pub(crate) async fn page(&self, scope: &Scope, prefix: &str, cursor: Option<String>, limit: u32) -> RuntimeResult<ScanPage> {
        let cursor = cursor.map(|s| serde_json::from_str(&s).map_err(|_| RuntimeError::InvalidInput("invalid cursor".into()))).transpose()?;
        self.atomic.scan_page(scope, PARTITION, prefix, cursor, limit as usize).await.map_err(storage)
    }
    pub(crate) async fn fenced_write(&self, fence: &OwnershipFence, mut conditions: Vec<Condition>, mutations: Vec<Mutation>) -> RuntimeResult<TransactionReceipt> {
        conditions.push(self.ownership_condition(fence).await?);
        match self.write(&fence.shard.scope, conditions, mutations).await {
            Err(RuntimeError::Conflict(message)) => { self.ownership_condition(fence).await?; Err(RuntimeError::Conflict(message)) }
            result => result,
        }
    }
}
pub(crate) fn cursor(value: Option<ScanCursor>) -> RuntimeResult<Option<String>> {
    value.map(|c| serde_json::to_string(&c).map_err(|e| RuntimeError::Storage(e.to_string()))).transpose()
}
pub(crate) fn invalid(message: &str) -> RuntimeError { RuntimeError::InvalidInput(message.into()) }
pub(crate) fn conflict(message: &str) -> RuntimeError { RuntimeError::Conflict(message.into()) }
