use std::{collections::HashMap, sync::Mutex};
use sha2::{Digest, Sha256};

/// Idempotency decision returned by [`IdempotencyLedger::begin`].
///
/// `Replay` carries the `status` + `body` of the originally-completed response
/// (or the default in-flight ack `200 {"ok":true}` for an `InProgress` entry),
/// so a caller that wants to surface non-200 responses (e.g. `chat.abort`'s 410
/// `run_terminated`) can be replayed with the exact same status on retry.
pub enum IdemDecision {
    Proceed,
    Replay { status: u16, body: serde_json::Value },
    Conflict,
}

enum Entry {
    InProgress { fingerprint: String },
    Completed { fingerprint: String, status: u16, response: serde_json::Value },
}

#[derive(Default)]
pub struct IdempotencyLedger { map: Mutex<HashMap<String, Entry>> }

impl IdempotencyLedger {
    pub fn new() -> Self { Self::default() }

    /// Look up `id` and decide between Proceed (new entry — caller drives the
    /// operation), Replay (matching fingerprint — replay the prior response,
    /// or the default in-flight ack `200 {"ok":true}` while still in flight), or
    /// Conflict (same id with a different fingerprint). The default in-flight
    /// ack preserves the historical inject behavior: a same-id retry of an
    /// already-running request receives `200 {"ok":true}` immediately.
    pub fn begin(&self, id: &str, fingerprint: &str) -> IdemDecision {
        let mut map = self.map.lock().unwrap_or_else(|p| p.into_inner());
        match map.get(id) {
            Some(Entry::InProgress { fingerprint: f }) if f == fingerprint =>
                IdemDecision::Replay { status: 200, body: serde_json::json!({"ok": true}) },
            Some(Entry::Completed { fingerprint: f, status, response }) if f == fingerprint =>
                IdemDecision::Replay { status: *status, body: response.clone() },
            Some(_) => IdemDecision::Conflict,
            None => {
                map.insert(id.to_string(), Entry::InProgress { fingerprint: fingerprint.to_string() });
                IdemDecision::Proceed
            }
        }
    }

    /// Complete an in-progress entry in the default 200 OK shape (the original
    /// inject path — kept for callers that only ever respond 200).
    pub fn complete(&self, id: &str, response: serde_json::Value) {
        self.complete_with_status(id, 200, response);
    }

    /// Complete an in-progress entry, recording the response's `status` and
    /// `body`. A subsequent same-id, same-fingerprint retry replays this exact
    /// `(status, body)` pair via [`IdemDecision::Replay`]. Used by `chat.abort`
    /// to make a 410 `run_terminated` retry replay as 410 (not the default
    /// in-flight ack). Only an in-progress entry is advanced — an
    /// already-completed entry is left untouched (no overwrite).
    pub fn complete_with_status(&self, id: &str, status: u16, response: serde_json::Value) {
        let mut map = self.map.lock().unwrap_or_else(|p| p.into_inner());
        if let Some(Entry::InProgress { fingerprint }) = map.get(id) {
            let fingerprint = fingerprint.clone();
            map.insert(id.to_string(), Entry::Completed { fingerprint, status, response });
        }
    }
}

pub fn fingerprint(parts: &[&str]) -> String {
    // Length-prefix every UTF-8 part so user text cannot mimic boundaries.
    // Only the fixed-size digest survives in a run's idempotency tombstone.
    let mut digest = Sha256::new();
    for part in parts {
        digest.update((part.len() as u64).to_be_bytes());
        digest.update(part.as_bytes());
    }
    format!("{:x}", digest.finalize())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fingerprint_preserves_part_boundaries() {
        assert_ne!(fingerprint(&["a\u{1f}b", "c"]), fingerprint(&["a", "b\u{1f}c"]));
        assert_ne!(fingerprint(&["ab", "c"]), fingerprint(&["a", "bc"]));
        assert_ne!(fingerprint(&[]), fingerprint(&[""]));
        assert_ne!(fingerprint(&["", "a"]), fingerprint(&["a", ""]));
    }

    #[test]
    fn fingerprint_has_fixed_size_for_large_payloads_and_drives_replay_conflict() {
        let payload = "x".repeat(8 * 1024 * 1024);
        let fp = fingerprint(&[&payload, "session", "bot"]);
        assert_eq!(fp.len(), 64);
        assert_eq!(fp.len(), fingerprint(&["small"]).len());
        let ledger = IdempotencyLedger::new();
        assert!(matches!(ledger.begin("same-id", &fp), IdemDecision::Proceed));
        ledger.complete("same-id", serde_json::json!({"ok": true}));
        assert!(matches!(ledger.begin("same-id", &fingerprint(&[&payload, "session", "bot"])), IdemDecision::Replay { status: 200, .. }));
        assert!(matches!(ledger.begin("same-id", &fingerprint(&[&payload, "different-session", "bot"])), IdemDecision::Conflict));
    }

    #[test]
    fn dedupes_same_id_same_body_and_conflicts_different_body() {
        let ledger = IdempotencyLedger::new();
        assert!(matches!(ledger.begin("id-1", "fp-a"), IdemDecision::Proceed));
        ledger.complete("id-1", serde_json::json!({"ok": true}));
        match ledger.begin("id-1", "fp-a") {
            IdemDecision::Replay { status, body } => {
                assert_eq!(status, 200);
                assert_eq!(body["ok"], serde_json::json!(true));
            }
            _ => panic!("expected replay"),
        }
        assert!(matches!(ledger.begin("id-1", "fp-b"), IdemDecision::Conflict));
    }

    #[test]
    fn in_progress_same_body_replays_ok_ack() {
        let ledger = IdempotencyLedger::new();
        assert!(matches!(ledger.begin("id-2", "fp-a"), IdemDecision::Proceed));
        match ledger.begin("id-2", "fp-a") {
            IdemDecision::Replay { status, body } => {
                assert_eq!(status, 200);
                assert_eq!(body["ok"], serde_json::json!(true));
            }
            _ => panic!("expected replay"),
        }
    }

    #[test]
    fn complete_with_status_replays_non_200_status_on_retry() {
        // chat.abort's 410 run_terminated must replay as 410 (not the default
        // in-flight 200 ack) so a same-id retry stably returns the same status.
        let ledger = IdempotencyLedger::new();
        assert!(matches!(ledger.begin("id-3", "fp-a"), IdemDecision::Proceed));
        let body = serde_json::json!({
            "ok": false,
            "error": { "code": "run_terminated", "message": "run is already terminal", "retryable": false }
        });
        ledger.complete_with_status("id-3", 410, body.clone());
        match ledger.begin("id-3", "fp-a") {
            IdemDecision::Replay { status, body: b } => {
                assert_eq!(status, 410);
                assert_eq!(b["error"]["code"], serde_json::json!("run_terminated"));
            }
            _ => panic!("expected replay"),
        }
    }
}
