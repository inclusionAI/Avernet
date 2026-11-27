//! Only expanded list cuts retain TaskViews. Pack consecutive views into
//! content-addressed TTL blocks, so a page of 1 doesn't mean one write per
//! task. Durable cut documents carry offsets and digests, never artifact data.
use super::group_listing::CutRow;
use crate::task_reader::{Shared, invalid, not_applied};
use bcs_dist_model::{TaskScope, TaskView};
use bcs_dist_store::{
    error::{from_read, from_write},
    identity::{digest_fields, hex_of},
};
use bcs_service_api::port::repo::distributed::{common::StoreError, group_listing::GroupIndexRow};
use bcs_store_api::{Expiry, kv::KvStorePlugin};
use std::{collections::BTreeMap, time::Duration};

const BLOCK_BYTES: usize = 256 * 1024;
const MAX_CUT_BYTES: usize = 32 * 1024 * 1024;

// Bound the allocation during encoding, not after a possibly enormous row
// has already been serialized. TaskView has no fallible custom serializer.
struct LimitedBytes {
    bytes: Vec<u8>,
    limit: usize,
}
impl std::io::Write for LimitedBytes {
    fn write(&mut self, input: &[u8]) -> std::io::Result<usize> {
        if input.len() > self.limit - self.bytes.len() {
            return Err(std::io::Error::other("projection exceeds cut budget"));
        }
        self.bytes.extend_from_slice(input);
        Ok(input.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub(crate) struct ProjectionRef {
    block: String,
    offset: usize,
    len: usize,
}

fn digest(bytes: &[u8]) -> String {
    hex_of(&digest_fields(&[b"group-cut-views:v1", bytes])).to_owned()
}

fn key(shared: &Shared, digest: &str) -> String {
    format!(
        "{}:group-cut-views:{digest}",
        shared.layout.namespace_identity()
    )
}

#[derive(Default)]
pub(crate) struct PendingBlock {
    bytes: Vec<u8>,
    rows: Vec<(usize, usize, usize)>,
    encoded_bytes: usize,
}

impl PendingBlock {
    pub(crate) async fn push(
        &mut self,
        shared: &Shared,
        store: &dyn KvStorePlugin,
        rows: &mut [CutRow],
        view: TaskView,
    ) -> Result<(), StoreError> {
        let mut writer = LimitedBytes {
            bytes: Vec::new(),
            limit: MAX_CUT_BYTES - self.encoded_bytes,
        };
        serde_json::to_writer(&mut writer, &view)
            .map_err(|_| not_applied("group_cut_bytes_budget"))?;
        let bytes = writer.bytes;
        self.encoded_bytes += bytes.len();
        if !self.rows.is_empty() && self.bytes.len().saturating_add(bytes.len()) > BLOCK_BYTES {
            self.flush(shared, store, rows).await?;
        }
        self.rows
            .push((rows.len() - 1, self.bytes.len(), bytes.len()));
        self.bytes.extend_from_slice(&bytes);
        Ok(())
    }

    pub(crate) async fn flush(
        &mut self,
        shared: &Shared,
        store: &dyn KvStorePlugin,
        rows: &mut [CutRow],
    ) -> Result<(), StoreError> {
        if self.rows.is_empty() {
            return Ok(());
        }
        let block = digest(&self.bytes);
        // A later first page may renew the same immutable bytes; it cannot
        // extend the signed expiry of a cursor already handed to a caller.
        let storage_key = key(shared, &block);
        store
            .set(
                &storage_key,
                &self.bytes,
                Expiry::After(Duration::from_millis(crate::CURSOR_LIFETIME_MILLIS)),
            )
            .await
            .map_err(|e| from_write(&e, &storage_key))?;
        for (index, offset, len) in self.rows.drain(..) {
            rows[index].projection = Some(ProjectionRef {
                block: block.clone(),
                offset,
                len,
            });
        }
        self.bytes.clear();
        Ok(())
    }
}

pub(crate) async fn restore(
    shared: &Shared,
    store: &dyn KvStorePlugin,
    scope: &TaskScope,
    frozen: &[CutRow],
    rows: &mut [GroupIndexRow],
) -> Result<(), StoreError> {
    let mut blocks = BTreeMap::new();
    for (i, row) in frozen.iter().enumerate() {
        let reference = row
            .projection
            .as_ref()
            .ok_or_else(|| invalid("cursor_binding"))?;
        blocks
            .entry(&reference.block)
            .or_insert_with(Vec::new)
            .push((i, reference));
    }
    // Read each distinct block once, dropping it before the next read. Never
    // reconstruct a missing version from the hot Task or the current archive.
    for (block, entries) in blocks {
        let bytes = store
            .get(&key(shared, block))
            .await
            .map_err(|e| from_read(&e))?
            .ok_or_else(|| not_applied("cut_released"))?;
        if digest(&bytes) != *block {
            return Err(invalid("cut_encoding"));
        }
        for (i, reference) in entries {
            let end = reference
                .offset
                .checked_add(reference.len)
                .ok_or_else(|| invalid("cut_encoding"))?;
            let slice = bytes
                .get(reference.offset..end)
                .ok_or_else(|| invalid("cut_encoding"))?;
            let view: TaskView =
                serde_json::from_slice(slice).map_err(|_| invalid("cut_encoding"))?;
            let row = &mut rows[i];
            if view.scope != *scope
                || view.sid != row.sid
                || view.tid != row.tid
                || view.content.status != row.status
            {
                return Err(invalid("cut_encoding"));
            }
            row.view = Some(view);
        }
    }
    Ok(())
}
