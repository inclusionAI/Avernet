//! Public, retained Result/Blob persistence over the host-selected DbPlugin.
mod sql;
use crate::{
    evidence::{EffectVerifier, completion_identity},
    identity::{digest_fields, hex_of},
    layout::KeyLayout,
};
use async_trait::async_trait;
use bcs_db_api::DbPlugin;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{common::*, result::*, session::EffectKind};
use bcs_store_api::document::DocumentStorePlugin;
use std::sync::Arc;

pub struct DbArchiveStore {
    db: Arc<dyn DbPlugin>,
    namespace: String,
    namespace_hash: String,
    verifier: EffectVerifier,
}
impl DbArchiveStore {
    pub fn new(
        db: Arc<dyn DbPlugin>,
        documents: Arc<dyn DocumentStorePlugin>,
        layout: KeyLayout,
    ) -> Result<Self, StoreError> {
        if !documents.supports(bcs_store_api::StoreCapability::DocumentCas) {
            return Err(not_applied("document_cas_required"));
        }
        let namespace = layout.namespace_identity();
        let namespace_hash = object_hash(&namespace);
        Ok(Self {
            db,
            namespace,
            namespace_hash,
            verifier: EffectVerifier::new(documents, layout),
        })
    }

    async fn check_references(&self, record: &FinalTaskRecord) -> Result<(), StoreError> {
        for payload in [&record.snapshot, &record.result] {
            if let Payload::Ref(reference) = payload
                && self.get(reference).await?.is_none()
            {
                return Err(not_applied("archive_reference_missing"));
            }
        }
        Ok(())
    }
}

/// The public provider's byte digest is domain-separated SHA-256 using the same
/// length-prefixed field encoding as other distributed identities. It hashes
/// exact stored bytes; JSON callers must freeze those bytes before publication.
pub use bcs_dist_encoding::blob_reference;

#[async_trait]
impl BlobStorePort for DbArchiveStore {
    async fn put_immutable(
        &self,
        identity: BlobRef,
        bytes: Vec<u8>,
    ) -> Result<IdempotentWrite, StoreError> {
        validate_key(&identity.key)?;
        if identity.key.starts_with("result:") {
            return Err(invalid("blob_identity"));
        }
        if blob_reference(&identity.key, &bytes).digest != identity.digest {
            return Err(invalid("blob_digest"));
        }
        let row = sql::Object {
            reference: identity,
            source: String::new(),
            bytes,
        };
        if let Some(existing) = self.read_object(&row.reference.key).await? {
            return Ok(compare(&existing, &row));
        }
        self.insert_object(&row).await
    }

    async fn get(&self, reference: &BlobRef) -> Result<Option<Vec<u8>>, StoreError> {
        validate_key(&reference.key)?;
        let Some(row) = self.read_object(&reference.key).await? else {
            return Ok(None);
        };
        if row.reference.digest != reference.digest {
            return Err(invalid("blob_digest_conflict"));
        }
        Ok(Some(row.bytes))
    }
}

#[async_trait]
impl ResultStorePort for DbArchiveStore {
    async fn save_final(
        &self,
        source: EffectSource,
        record: FinalTaskRecord,
    ) -> Result<SavedResult, StoreError> {
        validate_record(&record)?;
        if record.sid != source.commit.sid
            || record.source_commit != source.commit.sequence
            || source.effect_id.sid != record.sid
            || source.effect_id.commit != record.source_commit
        {
            return Err(invalid("result_source"));
        }
        let bytes = serde_json::to_vec(&record).map_err(|_| invalid("archive_encoding"))?;
        let reference = blob_reference(&result_key(&record.scope, &record.tid)?, &bytes);
        let provenance = completion_identity(&source)?;
        if provenance.len() > 16 * 1024 {
            return Err(invalid("result_provenance_size"));
        }
        let row = sql::Object {
            reference: reference.clone(),
            source: provenance,
            bytes,
        };
        // Retained completion evidence authorizes late replay after the outbox
        // has advanced. Same task and different content/source is a conflict.
        let outcome = if let Some(existing) = self.read_object(&reference.key).await? {
            compare(&existing, &row)
        } else {
            match self
                .verifier
                .verify(
                    &source,
                    &EffectKind::SaveResult {
                        record: record.clone(),
                    },
                )
                .await
            {
                Ok(()) => {
                    self.check_references(&record).await?;
                    self.insert_object(&row).await?
                }
                Err(verification_error) => {
                    // Another replay may have saved and advanced the outbox
                    // after our first absent-row read. Recheck retained evidence
                    // before interpreting the changed head as invalid authority.
                    match self.read_object(&reference.key).await? {
                        Some(existing) => compare(&existing, &row),
                        None => return Err(verification_error),
                    }
                }
            }
        };
        Ok(match outcome {
            IdempotentWrite::Applied => SavedResult::Saved(reference),
            IdempotentWrite::AlreadyApplied => SavedResult::AlreadySaved(reference),
            IdempotentWrite::ContentConflict => SavedResult::ContentConflict,
        })
    }

    async fn get_final(
        &self,
        scope: &TaskScope,
        tid: &Tid,
    ) -> Result<Option<FinalTaskRecord>, StoreError> {
        let Some(row) = self.read_object(&result_key(scope, tid)?).await? else {
            return Ok(None);
        };
        if row.source.is_empty() {
            return Err(invalid("result_provenance_missing"));
        }
        let record: FinalTaskRecord =
            serde_json::from_slice(&row.bytes).map_err(|_| invalid("result_decode"))?;
        validate_record(&record)?;
        if record.scope != *scope || record.tid != *tid {
            return Err(invalid("result_identity"));
        }
        Ok(Some(record))
    }
}

fn compare(existing: &sql::Object, candidate: &sql::Object) -> IdempotentWrite {
    if existing.reference == candidate.reference
        && existing.source == candidate.source
        && existing.bytes == candidate.bytes
    {
        IdempotentWrite::AlreadyApplied
    } else {
        IdempotentWrite::ContentConflict
    }
}
fn result_key(scope: &TaskScope, tid: &Tid) -> Result<String, StoreError> {
    for value in [&scope.tenant.0, &scope.principal.0, &tid.0] {
        if value.is_empty() || value.len() > 256 {
            return Err(invalid("result_identity"));
        }
    }
    Ok(format!(
        "result:{}",
        hex_of(&digest_fields(&[
            scope.tenant.0.as_bytes(),
            scope.principal.0.as_bytes(),
            tid.0.as_bytes()
        ]))
    ))
}
fn validate_key(key: &str) -> Result<(), StoreError> {
    if key.is_empty() || key.len() > 1024 || key.chars().any(char::is_control) {
        return Err(invalid("blob_key"));
    }
    Ok(())
}
fn validate_record(record: &FinalTaskRecord) -> Result<(), StoreError> {
    if record.schema_version != 1 {
        return Err(StoreError::UnsupportedFormat {
            format: record.schema_version,
        });
    }
    if record.sid.0.len() > 256
        || !record.tid.0.starts_with(&format!("{}.", record.sid.0))
        || record.source_commit.0 == 0
    {
        return Err(invalid("result_identity"));
    }
    KeyLayout::shard_of(&record.sid)?;
    Ok(())
}
fn object_hash(key: &str) -> String {
    hex_of(&digest_fields(&[key.as_bytes()])).into()
}
fn invalid(code: &str) -> StoreError {
    StoreError::Invalid { code: code.into() }
}
fn not_applied(code: &str) -> StoreError {
    StoreError::NotApplied { code: code.into() }
}
fn unknown(key: &str) -> StoreError {
    StoreError::OutcomeUnknown {
        operation_id: object_hash(key),
    }
}
