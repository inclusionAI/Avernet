//! Caller-aware shared-history snapshot manifest (spec §6.8).
//!
//! The manifest is the reconcilable, non-credential business metadata of
//! one shared-history snapshot: the target Bot/session it belongs to, the
//! cutoff sequence fixed at Human-message admission (strictly BEFORE this
//! message), the projection rule version, the explicit message-id list
//! (an immutable, rebuildable index — never "just the highest seq"), and a
//! digest binding it all. Persistable; carrying no identity material.

use sha2::{Digest as _, Sha256};

/// Domain-separated digest of one history snapshot manifest (v1).
/// `manifest_digest = SHA-256(DS || lp(target_bot) || lp(session) ||
/// u64(cutoff) || u64(projection_version) || u64(count) || lp(id)*)`
/// where `DS = b"bcs.caller.history.manifest.v1\0"`, `lp(s)` is
/// u64-BE length-prefixed UTF-8 and numeric fields are u64 big-endian.
pub const MANIFEST_DIGEST_DOMAIN: &[u8] = b"bcs.caller.history.manifest.v1\0";

/// One shared-history snapshot: the explicit message-id list with its
/// cutoff and projection version. The list is ordered (normally ascending
/// session sequence); order is part of the digest.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HistorySnapshotManifest {
    pub history_snapshot_id: String,
    pub target_bot_id: String,
    pub session_id: String,
    /// Session sequence strictly BEFORE the admitting Human message —
    /// the task message itself travels by `chat.send`, never in history.
    pub history_cutoff_seq: u64,
    pub projection_version: u64,
    pub message_ids: Vec<String>,
    pub manifest_digest: String,
}

fn feed_lp(hasher: &mut Sha256, value: &str) {
    hasher.update((value.len() as u64).to_be_bytes());
    hasher.update(value.as_bytes());
}

fn feed_u64(hasher: &mut Sha256, value: u64) {
    hasher.update(value.to_be_bytes());
}

/// Deterministic canonical digest over the manifest facts. Pure; reads no
/// clock and no identity material.
pub fn canonical_manifest_digest(
    target_bot_id: &str,
    session_id: &str,
    history_cutoff_seq: u64,
    projection_version: u64,
    message_ids: &[String],
) -> String {
    let mut hasher = Sha256::new();
    hasher.update(MANIFEST_DIGEST_DOMAIN);
    feed_lp(&mut hasher, target_bot_id);
    feed_lp(&mut hasher, session_id);
    feed_u64(&mut hasher, history_cutoff_seq);
    feed_u64(&mut hasher, projection_version);
    feed_u64(&mut hasher, message_ids.len() as u64);
    for message_id in message_ids {
        feed_lp(&mut hasher, message_id);
    }
    format!("sha256:{}", hex::encode(hasher.finalize()))
}

impl HistorySnapshotManifest {
    /// Build a manifest, deriving its digest from the facts. The id is
    /// caller-supplied (admission's snapshot id); the digest binds it to
    /// the exact list.
    pub fn new(
        history_snapshot_id: impl Into<String>,
        target_bot_id: impl Into<String>,
        session_id: impl Into<String>,
        history_cutoff_seq: u64,
        projection_version: u64,
        message_ids: Vec<String>,
    ) -> Self {
        let target_bot_id = target_bot_id.into();
        let session_id = session_id.into();
        let manifest_digest = canonical_manifest_digest(
            &target_bot_id,
            &session_id,
            history_cutoff_seq,
            projection_version,
            &message_ids,
        );
        Self {
            history_snapshot_id: history_snapshot_id.into(),
            target_bot_id,
            session_id,
            history_cutoff_seq,
            projection_version,
            message_ids,
            manifest_digest,
        }
    }

    /// Verify the manifest's digest against its facts — a rebuilt list
    /// must match the persisted digest or the snapshot is not reconcilable.
    pub fn digest_matches(&self) -> bool {
        canonical_manifest_digest(
            &self.target_bot_id,
            &self.session_id,
            self.history_cutoff_seq,
            self.projection_version,
            &self.message_ids,
        ) == self.manifest_digest
    }

    /// Membership is by explicit id (list), never "seq <= watermark".
    pub fn contains(&self, message_id: &str) -> bool {
        self.message_ids.iter().any(|id| id == message_id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn digest_is_deterministic_and_order_sensitive() {
        let a = canonical_manifest_digest("bot", "s", 41, 1, &["m1".into(), "m2".into()]);
        let b = canonical_manifest_digest("bot", "s", 41, 1, &["m1".into(), "m2".into()]);
        assert_eq!(a, b, "same facts digest identically");
        assert!(a.starts_with("sha256:"));
        assert_eq!(a.len(), "sha256:".len() + 64);

        let swapped = canonical_manifest_digest("bot", "s", 41, 1, &["m2".into(), "m1".into()]);
        assert_ne!(a, swapped, "list order is part of the digest");
        let bumped_cutoff = canonical_manifest_digest("bot", "s", 42, 1, &["m1".into(), "m2".into()]);
        assert_ne!(a, bumped_cutoff, "cutoff is part of the digest");
        let empty = canonical_manifest_digest("bot", "s", 41, 1, &[]);
        assert_ne!(a, empty, "membership count is part of the digest");

        // Independent v1 vector (fixed): verify against a hand-computed
        // reference so the domain separation layout is pinned.
        let fixed = canonical_manifest_digest(
            "bot-driver",
            "session-1",
            41,
            1,
            &["m-1".to_string(), "m-2".to_string()],
        );
        // Re-derive with an explicitly concatenated buffer to pin the layout.
        let mut expected = Sha256::new();
        expected.update(MANIFEST_DIGEST_DOMAIN);
        for part in ["bot-driver", "session-1"] {
            expected.update((part.len() as u64).to_be_bytes());
            expected.update(part.as_bytes());
        }
        expected.update(41u64.to_be_bytes());
        expected.update(1u64.to_be_bytes());
        expected.update(2u64.to_be_bytes());
        for part in ["m-1", "m-2"] {
            expected.update((part.len() as u64).to_be_bytes());
            expected.update(part.as_bytes());
        }
        assert_eq!(
            fixed,
            format!("sha256:{}", hex::encode(expected.finalize())),
            "independent layout vector"
        );
    }

    #[test]
    fn manifest_binds_and_reconciles() {
        let manifest = HistorySnapshotManifest::new("snap-1", "bot", "s", 41, 1,
            vec!["m1".into(), "m2".into()]);
        assert!(manifest.digest_matches(), "fresh manifest reconciles");
        assert!(manifest.contains("m1"));
        assert!(!manifest.contains("m9"), "membership is explicit, not a watermark");
        let mut drifted = manifest.clone();
        drifted.message_ids.push("m3".into());
        assert!(!drifted.digest_matches(), "a drifted list fails reconciliation");
    }
}