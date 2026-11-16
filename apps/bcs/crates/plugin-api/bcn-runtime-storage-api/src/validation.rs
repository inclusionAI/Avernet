//! Shared fail-closed request validation, used by every backend before touching storage.
use std::collections::BTreeSet;
use bcn_runtime_model::*;

pub fn location(scope: &Scope, partition: &str) -> StorageResult<()> {
    scope.validate()?;
    validate_component(partition)
}
pub fn key(key: &str, allow_empty: bool) -> StorageResult<()> {
    if (!allow_empty && key.is_empty()) || key.len() > MAX_KEY_BYTES ||
        !key.bytes().all(|b| b.is_ascii_alphanumeric() || b"_-/.:".contains(&b)) {
        return Err(StorageError::InvalidKey("invalid storage key".into()));
    }
    Ok(())
}
pub fn read(scope: &Scope, partition: &str, keys: &[String]) -> StorageResult<()> {
    location(scope, partition)?;
    if keys.len() > MAX_READ_KEYS { return Err(StorageError::LimitExceeded("read keys".into())); }
    for item in keys { key(item, false)?; }
    Ok(())
}
pub fn batch(scope: &Scope, partition: &str, batch: &AtomicBatch) -> StorageResult<()> {
    location(scope, partition)?;
    if batch.conditions.len() > MAX_BATCH_CONDITIONS || batch.mutations.len() > MAX_BATCH_MUTATIONS {
        return Err(StorageError::LimitExceeded("batch entries".into()));
    }
    for condition in &batch.conditions { key(&condition.key, false)?; }
    let mut bytes = batch.conditions.iter().map(|condition| condition.key.len()).sum::<usize>();
    let mut seen = BTreeSet::new();
    for mutation in &batch.mutations {
        key(mutation.key(), false)?;
        bytes += mutation.key().len();
        if !seen.insert(mutation.key()) { return Err(StorageError::DuplicateMutation(mutation.key().into())); }
        if let Mutation::Put { value, .. } = mutation {
            bytes += value.len();
            if value.len() > MAX_VALUE_BYTES { return Err(StorageError::LimitExceeded("value bytes".into())); }
        }
        if bytes > MAX_BATCH_BYTES { return Err(StorageError::LimitExceeded("batch bytes".into())); }
    }
    Ok(())
}
pub fn scan(scope: &Scope, partition: &str, prefix: &str, cursor: Option<&ScanCursor>, limit: usize) -> StorageResult<()> {
    location(scope, partition)?;
    key(prefix, true)?;
    if limit == 0 || limit > MAX_SCAN_LIMIT { return Err(StorageError::LimitExceeded("scan limit".into())); }
    if let Some(cursor) = cursor {
        key(&cursor.after_key, false)?;
        if cursor.scope != *scope || cursor.partition != partition || cursor.prefix != prefix || !cursor.after_key.starts_with(prefix) {
            return Err(StorageError::InvalidCursor);
        }
    }
    Ok(())
}
