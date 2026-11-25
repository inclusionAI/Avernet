//! Persistent document CAS, independently injectable from AtomicJson.
use crate::*;
use async_trait::async_trait;
use crate::json::{JsonEdit, JsonProjection};
use serde_json::Value;

/// A namespace of persistent documents. All writers must use this contract;
/// deletion, expiry, eviction and rollback are forbidden for its lifetime.
/// Storage metadata is private, never reserved in the logical business value.
/// No cross-key atomicity is implied. Unsupported providers reject before I/O.
#[async_trait]
pub trait DocumentStorePlugin: StorePlugin {
    /// Consistent value and version from one document. Missing is not proof of
    /// never having existed; lifecycle violations need incident recovery.
    async fn read_document(&self, key: &str) -> StoreResult<Option<Versioned<Value>>>;
    /// Project RFC 6901 pointers from one consistent document/version. Empty
    /// pointer selects the root; missing paths are None, real null is Some(Null).
    /// Invalid pointer syntax fails even if the key is missing. The default
    /// performs one whole-document read, then projects in memory; it does not
    /// reduce stored bytes read. Providers may optimize with equivalent semantics.
    async fn read_document_paths(
        &self,
        key: &str,
        pointers: &[String],
    ) -> StoreResult<Option<JsonProjection>> {
        if !self.supports(StoreCapability::DocumentCas) {
            return Err(StoreError::Unsupported(StoreCapability::DocumentCas));
        }
        for pointer in pointers {
            validate_pointer(pointer)?;
        }
        Ok(self.read_document(key).await?.map(|document| JsonProjection {
            version: document.version,
            values: pointers.iter().map(|pointer| document.value.pointer(pointer).cloned()).collect(),
        }))
    }
    /// Atomically create only if absent, at version 1.
    /// Existing content is never overwritten, even if logically identical.
    async fn create_document(&self, key: &str, value: &Value) -> StoreResult<WriteOutcome>;
    /// Require an existing document at exactly expected, replace its entire
    /// logical value and advance the private version in ONE atomic operation.
    /// Accepted writes advance the version even when content is identical.
    /// Field removal, real JSON null, arrays and scalars preserve normal JSON
    /// semantics. A rejected attempt changes neither value, version nor TTL.
    /// Missing is ConditionNotMet, never creation. Exhaustion cannot wrap.
    async fn replace_document(
        &self,
        key: &str,
        expected: Version,
        value: &Value,
    ) -> StoreResult<WriteOutcome>;
    /// Apply nonempty, ordered JsonEdits under the exact whole-document version.
    /// Validate request shape first, then the condition, then edits against the
    /// matched document. Missing/stale returns ConditionNotMet; an invalid edit
    /// returns InvalidInput without any changes. Identical edits advance version.
    ///
    /// The default performs at most one consistent read and one atomic
    /// replace_document(expected): all edits are computed before that write, and
    /// an intervening mutation rejects the candidate. There is no unconditional
    /// write or automatic retry. Uncertain replacement remains OutcomeUnknown.
    /// Providers can override this with an equally strong native operation.
    async fn patch_document(
        &self,
        key: &str,
        expected: Version,
        edits: &[JsonEdit],
    ) -> StoreResult<WriteOutcome> {
        if !self.supports(StoreCapability::DocumentCas) {
            return Err(StoreError::Unsupported(StoreCapability::DocumentCas));
        }
        if expected.0 == 0 || edits.is_empty() {
            return Err(invalid("positive version and nonempty patch required"));
        }
        for edit in edits {
            let pointer = edit_pointer(edit);
            validate_pointer(pointer)?;
            if pointer.is_empty() {
                return Err(invalid("patch requires a nonempty pointer"));
            }
        }
        let Some(mut document) = self.read_document(key).await? else {
            return Ok(WriteOutcome::ConditionNotMet);
        };
        if document.version != expected {
            return Ok(WriteOutcome::ConditionNotMet);
        }
        for edit in edits {
            apply_edit(&mut document.value, edit)?;
        }
        self.replace_document(key, expected, &document.value).await
    }
}

fn invalid(message: &str) -> StoreError {
    StoreError::InvalidInput(message.into())
}

fn validate_pointer(pointer: &str) -> StoreResult<()> {
    if !pointer.is_empty() && !pointer.starts_with('/') {
        return Err(invalid("invalid JSON pointer"));
    }
    let mut chars = pointer.chars();
    while let Some(c) = chars.next() {
        if c == '~' && !matches!(chars.next(), Some('0' | '1')) {
            return Err(invalid("invalid JSON pointer escape"));
        }
    }
    Ok(())
}

fn edit_pointer(edit: &JsonEdit) -> &str {
    match edit {
        JsonEdit::Set { pointer, .. } | JsonEdit::Remove { pointer } => pointer,
    }
}

fn apply_edit(value: &mut Value, edit: &JsonEdit) -> StoreResult<()> {
    let (parent, token) = edit_pointer(edit).rsplit_once('/')
        .ok_or_else(|| invalid("invalid patch pointer"))?;
    let token = token.replace("~1", "/").replace("~0", "~");
    match value.pointer_mut(parent).ok_or_else(|| invalid("missing patch parent"))? {
        Value::Object(object) => match edit {
            JsonEdit::Set { value, .. } => {
                object.insert(token, value.clone());
            }
            JsonEdit::Remove { .. } => {
                object.remove(&token).ok_or_else(|| invalid("missing patch target"))?;
            }
        },
        Value::Array(array) => {
            let index = token.parse::<usize>().map_err(|_| invalid("invalid array index"))?;
            if index.to_string() != token || index >= array.len() {
                return Err(invalid("invalid array index"));
            }
            match edit {
                JsonEdit::Set { value, .. } => array[index] = value.clone(),
                JsonEdit::Remove { .. } => { array.remove(index); }
            }
        }
        _ => return Err(invalid("patch parent is not a container")),
    }
    Ok(())
}
