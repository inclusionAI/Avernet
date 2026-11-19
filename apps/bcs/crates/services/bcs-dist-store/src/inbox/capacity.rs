//! Atomic first-write and retry capacity, shared by reliable and intermediate input.
use super::{StoreInboxProducer, state::*, storage::*};
use bcs_dist_model::Sid;
use bcs_service_api::port::repo::distributed::{common::StoreError, inbox::AdmitRequest};

fn maximum_empty_scan() -> Scan {
    Scan {
        cut: u64::MAX,
        cursor: u64::MAX,
        completed: u64::MAX,
        pending: Default::default(),
        started_at: i64::MIN,
        round: u64::MAX,
        retry_after: u64::MAX,
        retry_first: false,
    }
}

/// Every retained intent may remain pending. A pending sequence is at most 20
/// decimal bytes plus a comma; all other fields use their longest encodings.
/// The extra comma on the first item is conservative. No history is allocated.
pub(super) fn maximum_scan_bytes(history_entries: u64) -> Option<u64> {
    let metadata = u64::try_from(serde_json::to_vec(&maximum_empty_scan()).ok()?.len()).ok()?;
    history_entries.checked_mul(21)?.checked_add(metadata)
}

/// Remaining lifetime charge units, not free disk or proof that an input fits.
/// Pending slots, encoded document size and maintenance health are independent.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RetentionCapacityObservation {
    /// Intent reservations plus physical append reservations; not `root.next`.
    pub charged_history_entries: u64,
    pub history_entries_remaining: u64,
    pub history_bytes_remaining: u64,
    pub normal_entries_remaining: u64,
    pub normal_bytes_remaining: u64,
    pub session_reservations_remaining: u64,
    pub distinct_sessions_remaining: u64,
    /// Minimum across retained Session ledgers; no Session identities exposed.
    pub minimum_inbox_entries_remaining: u64,
    pub minimum_inbox_bytes_remaining: u64,
    pub minimum_normal_inbox_entries_remaining: u64,
    pub minimum_normal_inbox_bytes_remaining: u64,
    pub near_limit: bool,
}

impl RetentionCapacityObservation {
    pub(super) fn from_root(root: &JournalRoot, cfg: &super::InboxProducerConfig) -> Self {
        let normal_entries = cfg.max_history_entries - cfg.reserved_entries - 64;
        let normal_bytes = cfg.max_history_bytes - cfg.reserved_bytes - 128 * 1024;
        let history_entries_remaining = cfg.max_history_entries.saturating_sub(root.entries);
        let history_bytes_remaining = cfg.max_history_bytes.saturating_sub(root.bytes);
        let mut result = Self {
            charged_history_entries: root.entries,
            history_entries_remaining,
            history_bytes_remaining,
            normal_entries_remaining: normal_entries
                .saturating_sub(root.normal_entries)
                .min(history_entries_remaining),
            normal_bytes_remaining: normal_bytes
                .saturating_sub(root.normal_bytes)
                .min(history_bytes_remaining),
            session_reservations_remaining: cfg.max_sessions.saturating_sub(root.sessions),
            distinct_sessions_remaining: cfg.max_sessions.saturating_sub(root.inbox.len() as u64),
            minimum_inbox_entries_remaining: cfg.max_inbox_entries,
            minimum_inbox_bytes_remaining: cfg.max_inbox_bytes,
            minimum_normal_inbox_entries_remaining: cfg.max_inbox_entries - 128,
            minimum_normal_inbox_bytes_remaining: cfg.max_inbox_bytes - 256 * 1024,
            near_limit: shard_near_capacity(root, cfg),
        };
        // This on-demand observation reuses the already-loaded bounded root;
        // admission warnings below do not scan this map or issue extra reads.
        for entry in root.inbox.values() {
            let entries = cfg.max_inbox_entries.saturating_sub(entry.entries);
            let bytes = cfg.max_inbox_bytes.saturating_sub(entry.bytes);
            result.minimum_inbox_entries_remaining =
                result.minimum_inbox_entries_remaining.min(entries);
            result.minimum_inbox_bytes_remaining = result.minimum_inbox_bytes_remaining.min(bytes);
            result.minimum_normal_inbox_entries_remaining =
                result.minimum_normal_inbox_entries_remaining.min(
                    (cfg.max_inbox_entries - 128)
                        .saturating_sub(entry.normal_entries)
                        .min(entries),
                );
            result.minimum_normal_inbox_bytes_remaining =
                result.minimum_normal_inbox_bytes_remaining.min(
                    (cfg.max_inbox_bytes - 256 * 1024)
                        .saturating_sub(entry.normal_bytes)
                        .min(bytes),
                );
        }
        result.near_limit |= result.minimum_normal_inbox_entries_remaining
            <= (cfg.max_inbox_entries - 128) / 5
            || result.minimum_normal_inbox_bytes_remaining
                <= (cfg.max_inbox_bytes - 256 * 1024) / 5;
        result
    }
}

pub(super) fn shard_near_capacity(root: &JournalRoot, cfg: &super::InboxProducerConfig) -> bool {
    let normal_entries = cfg.max_history_entries - cfg.reserved_entries - 64;
    let normal_bytes = cfg.max_history_bytes - cfg.reserved_bytes - 128 * 1024;
    cfg.max_history_entries.saturating_sub(root.entries) <= cfg.max_history_entries / 5
        || cfg.max_history_bytes.saturating_sub(root.bytes) <= cfg.max_history_bytes / 5
        || normal_entries.saturating_sub(root.normal_entries) <= normal_entries / 5
        || normal_bytes.saturating_sub(root.normal_bytes) <= normal_bytes / 5
        || cfg.max_sessions.saturating_sub(root.sessions) <= cfg.max_sessions / 5
        || cfg.max_sessions.saturating_sub(root.inbox.len() as u64) <= cfg.max_sessions / 5
}

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
        let entries = entry.entries.checked_add(1).ok_or_else(pressure)?;
        let total_bytes = entry.bytes.checked_add(bytes).ok_or_else(pressure)?;
        if entries > cfg.max_inbox_entries
            || total_bytes > cfg.max_inbox_bytes
            || (normal
                && (entry.normal_entries
                    >= cfg.max_inbox_entries - 64 - if repair { 0 } else { 64 }
                    || entry.normal_bytes.saturating_add(bytes)
                        > cfg.max_inbox_bytes - 128 * 1024 - if repair { 0 } else { 128 * 1024 }))
        {
            return Err(pressure());
        }
        entry.entries = entries;
        entry.bytes = total_bytes;
        if normal {
            entry.normal_entries += 1;
            entry.normal_bytes += bytes;
        }
        Ok(())
    }
}

#[cfg(test)]
#[allow(clippy::expect_used, reason = "test assertions")]
mod tests {
    use super::*;

    #[test]
    fn pending_scan_bound_includes_maximum_metadata_and_numeric_widths() {
        let mut scan = maximum_empty_scan();
        let metadata = serde_json::to_vec(&scan).expect("encode").len() as u64;
        assert_eq!(metadata, 222);
        assert_eq!(maximum_scan_bytes(0), Some(metadata));
        scan.pending.extend([u64::MAX - 1, u64::MAX]);
        let encoded = serde_json::to_vec(&scan).expect("encode").len() as u64;
        assert_eq!(maximum_scan_bytes(2), Some(encoded + 1));
        assert!(maximum_scan_bytes(u64::MAX).is_none());
    }
}
