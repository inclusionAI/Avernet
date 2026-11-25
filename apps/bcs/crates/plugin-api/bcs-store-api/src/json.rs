use crate::*;
use async_trait::async_trait;
use serde_json::Value;

/// RFC 6901 JSON pointers, NOT vendor JSONPath expressions or wildcards.
/// Patch paths must be nonempty. Parents must exist. Set may create an object
/// field or replace an existing array element; Remove requires an existing target.
/// Array indices are canonical nonnegative decimals; append/insert is not implied.
#[derive(Debug, Clone, PartialEq)]
pub enum JsonEdit {
    Set { pointer: String, value: Value },
    Remove { pointer: String },
}

#[derive(Debug, Clone, PartialEq)]
pub enum JsonMutation {
    Replace(Value),
    /// RFC 7396 object patch on a live object. Null members remove fields;
    /// arrays replace as units (including literal nulls in their elements).
    Merge(serde_json::Map<String, Value>),
    /// Nonempty ordered edits on a live document. Any invalid edit aborts ALL.
    Patch(Vec<JsonEdit>),
    /// Delete a live document while retaining its version high-water mark.
    Delete,
}

#[derive(Debug, Clone, PartialEq)]
pub struct JsonProjection {
    pub version: Version,
    /// Same order/length as requested pointers. Missing path is None; JSON null
    /// is Some(Value::Null). All values and version are from the same document.
    pub values: Vec<Option<Value>>,
}

/// AtomicJson includes projection, replacement, pointer edits and delete.
/// PersistentJsonMerge adds the persistent object creation / merge subset. Conditions compare the WHOLE key
/// version, even for path edits. The condition, edits, version and expiry change
/// are one atomic operation. Cross-document transactions are not implied.
#[async_trait]
pub trait JsonStorePlugin: StorePlugin {
    async fn get_json(&self, key: &str) -> StoreResult<Option<Versioned<Value>>>;
    /// Empty pointer selects the root. Invalid pointers fail, not missing paths.
    async fn get_paths(
        &self,
        key: &str,
        pointers: &[String],
    ) -> StoreResult<Option<JsonProjection>>;
    /// Validate request shape first, then test condition. Failed condition does
    /// not modify expiry. Missing Patch/Delete returns ConditionNotMet. Successful
    /// identical Replace/Patch still advances version. Patch failure is InvalidInput.
    async fn mutate_json(
        &self,
        key: &str,
        condition: WriteCondition,
        mutation: JsonMutation,
        expiry: Expiry,
    ) -> StoreResult<WriteOutcome>;
}
