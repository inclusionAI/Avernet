use super::*;
use bcs_db_api::{DbStatement, DbValue};

pub(super) struct Object {
    pub reference: BlobRef,
    pub source: String,
    pub bytes: Vec<u8>,
}
impl DbArchiveStore {
    pub(super) async fn read_object(&self, key: &str) -> Result<Option<Object>, StoreError> {
        let rows = self.db.query(DbStatement::with_params(
            "SELECT namespace_key, object_key, content_digest, source_json, content_bytes FROM bcs_dist_immutable_objects WHERE namespace_hash = ? AND object_hash = ? LIMIT 1",
            vec![DbValue::String(self.namespace_hash.clone()), DbValue::String(object_hash(key))],
        )).await.map_err(|_| not_applied("archive_read"))?;
        let Some(row) = rows.into_iter().next() else {
            return Ok(None);
        };
        let string = |column| {
            row.get_string(column)
                .map_err(|_| invalid("archive_column"))?
                .ok_or_else(|| invalid("archive_column"))
        };
        if string("namespace_key")? != self.namespace || string("object_key")? != key {
            return Err(invalid("archive_hash_collision"));
        }
        let bytes = match row.get("content_bytes") {
            Some(DbValue::Bytes(bytes)) => bytes.clone(),
            _ => return Err(invalid("archive_bytes")),
        };
        let reference = blob_reference(key, &bytes);
        if reference.digest.0 != string("content_digest")? {
            return Err(invalid("archive_corrupt_digest"));
        }
        let source = string("source_json")?;
        if source.len() > 16 * 1024 {
            return Err(invalid("archive_provenance_size"));
        }
        Ok(Some(Object {
            reference,
            source,
            bytes,
        }))
    }

    pub(super) async fn insert_object(&self, row: &Object) -> Result<IdempotentWrite, StoreError> {
        let key = &row.reference.key;
        let outcome = self.db.execute(DbStatement::with_params(
            "INSERT INTO bcs_dist_immutable_objects (namespace_hash, object_hash, namespace_key, object_key, content_digest, source_json, content_bytes) VALUES (?, ?, ?, ?, ?, ?, ?)",
            vec![DbValue::String(self.namespace_hash.clone()), DbValue::String(object_hash(key)), DbValue::String(self.namespace.clone()), DbValue::String(key.clone()), DbValue::String(row.reference.digest.0.clone()), DbValue::String(row.source.clone()), DbValue::Bytes(row.bytes.clone())],
        )).await;
        if outcome
            .as_ref()
            .is_ok_and(|result| result.affected_rows == 1)
        {
            return Ok(IdempotentWrite::Applied);
        }
        // Driver failure and duplicate-key strings are not success evidence.
        // One exact read resolves the immutable row; absence/read failure cannot
        // prove a delayed write failed, and never triggers an automatic INSERT.
        match self.read_object(key).await {
            Ok(Some(existing)) => Ok(compare(&existing, row)),
            Ok(None) | Err(_) => Err(unknown(key)),
        }
    }
}
