//! Atomic first-write and retry capacity, shared by reliable and intermediate input.
use super::{StoreInboxProducer, state::*, storage::*};
use bcs_dist_model::Sid;
use bcs_service_api::port::repo::distributed::{common::StoreError, inbox::AdmitRequest};
impl StoreInboxProducer {
    pub(super) fn reliable_bytes(&self, request: &AdmitRequest) -> Result<u64, StoreError> {
        let mut fields = crate::codec::encode_inbox_entry(&request.input)?;
        fields.insert(
            "canonical".into(),
            self.inner
                .layout
                .canonical_admission(&request.scope, &request.input.input_id)?
                .into_bytes(),
        );
        Ok(fields.iter().map(|(k, v)| k.len() + v.len()).sum::<usize>() as u64 + 512)
    }
    pub(super) fn charge_inbox(
        &self,
        root: &mut JournalRoot,
        sid: &Sid,
        bytes: u64,
        normal: bool,
        repair: bool,
    ) -> Result<(), StoreError> {
        let cfg = &self.inner.config;
        if !root.inbox.contains_key(sid) && root.inbox.len() as u64 >= cfg.max_sessions {
            return Err(pressure());
        }
        let entry = root.inbox.entry(sid.clone()).or_default();
        if entry.entries >= cfg.max_inbox_entries
            || entry.bytes.saturating_add(bytes) > cfg.max_inbox_bytes
            || (normal
                && (entry.normal_entries
                    >= cfg.max_inbox_entries - 64 - if repair { 0 } else { 64 }
                    || entry.normal_bytes.saturating_add(bytes)
                        > cfg.max_inbox_bytes - 128 * 1024 - if repair { 0 } else { 128 * 1024 }))
        {
            return Err(pressure());
        }
        entry.entries += 1;
        entry.bytes += bytes;
        if normal {
            entry.normal_entries += 1;
            entry.normal_bytes += bytes;
        }
        Ok(())
    }
}
