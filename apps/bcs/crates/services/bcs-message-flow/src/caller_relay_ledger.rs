//! Runtime-only Owner→caller identity relay capture (spec L62 direction).
//!
//! A Human speaking into an ordinary (Owner-executed) session never parks a
//! handoff identity today, so a later task.assign toward a caller-enabled
//! target finds no identity and converges fail-closed. When the deployment
//! opts in via `caller_identity.relay`, the trusted admission seam captures
//! the authenticated ingress HERE — keyed by the session's current human
//! turn — and the trusted task-assign edge redeems it later to park the
//! target's own handoff scope. The middle (Owner) bot never receives,
//! forwards or stores any token; the ledger itself is epoch-scoped memory
//! that dies with the master term, never durable storage.
//!
//! Bounds: entries expire with the caller credential window (fail-closed,
//! never renewed — a relay past its window requires the Human to speak
//! again, per B5); every root turn reserves at most RELAY_PER_TURN redeems
//! (the §8.3 per-turn derivation budget family) so a compromised middle bot
//! cannot amplify one captured identity into unbounded caller dispatches.

use std::collections::HashMap;
use std::sync::Mutex;

use bcs_service_api::application::caller_identity::AuthenticatedHumanCallerIngress;

/// Same per-turn bound as the §8.3 in-group derivation budget: at most
/// this many relay redeems root from one human turn.
pub const RELAY_PER_TURN: u32 = 16;

struct RelayEntry {
    ingress: AuthenticatedHumanCallerIngress,
    root_turn_id: String,
    expires_at_ms: i64,
    redeems_left: u32,
}

/// Bounded, epoch-scoped relay ledger. `record` overwrites the session's
/// previous entry (the session's CURRENT speaker is the deliverable fact);
/// `redeem` refuses expired entries instead of renewing them.
pub struct CallerRelayLedger {
    capacity: usize,
    // (env, session) → the session's current human capture.
    entries: Mutex<HashMap<(String, String), RelayEntry>>,
}

impl CallerRelayLedger {
    pub fn new(capacity: usize) -> Self {
        assert!(capacity > 0, "relay ledger capacity must be positive");
        Self { capacity, entries: Mutex::new(HashMap::new()) }
    }

    /// Capture the session's current human ingress. The caller clock decides
    /// the window exactly like the handoff; nothing here reads wall time.
    pub fn record(
        &self,
        env: &str,
        session_id: &str,
        root_turn_id: &str,
        ingress: AuthenticatedHumanCallerIngress,
        now_ms: i64,
        ttl_ms: i64,
    ) -> bool {
        if now_ms <= 0 || ttl_ms <= 0 {
            return false;
        }
        let mut entries = self.entries.lock().expect("relay ledger lock");
        if !entries.contains_key(&(env.into(), session_id.into()))
            && entries.len() >= self.capacity
        {
            // Evict one expired entry first; a full ledger of LIVE captures
            // refuses the record (fail-closed, no silent drop).
            let victim = entries
                .iter()
                .find(|(_, entry)| entry.expires_at_ms <= now_ms)
                .map(|(key, _)| key.clone());
            match victim {
                Some(victim) => {
                    entries.remove(&victim);
                }
                None => return false,
            }
        }
        entries.insert(
            (env.into(), session_id.into()),
            RelayEntry {
                ingress,
                root_turn_id: root_turn_id.to_string(),
                expires_at_ms: now_ms + ttl_ms,
                redeems_left: RELAY_PER_TURN,
            },
        );
        true
    }

    /// Redemption receipt: the captured turn (for audit), the trusted
    /// ingress itself, and the capture's own expiry — the window the
    /// credential would mint, never a run-budget fiction.
    pub fn redeem_capture(
        &self,
        env: &str,
        session_id: &str,
        now_ms: i64,
    ) -> Option<(String, AuthenticatedHumanCallerIngress, i64)> {
        let mut entries = self.entries.lock().expect("relay ledger lock");
        let key = (env.to_string(), session_id.to_string());
        let entry = entries.get_mut(&key)?;
        if entry.expires_at_ms <= now_ms {
            entries.remove(&key);
            return None;
        }
        if entry.redeems_left == 0 {
            return None;
        }
        entry.redeems_left -= 1;
        Some((entry.root_turn_id.clone(), entry.ingress.clone(), entry.expires_at_ms))
    }

    /// Take one relay redeem for the session's current human identity.
    /// Expired or budget-exhausted turns answer None and stay that way:
    /// an expired capture is REMOVED, not flagged — a later lookup inside
    /// an out-of-order clock window must never resurrect it.
    pub fn redeem(
        &self,
        env: &str,
        session_id: &str,
        now_ms: i64,
    ) -> Option<(String, AuthenticatedHumanCallerIngress)> {
        let mut entries = self.entries.lock().expect("relay ledger lock");
        let key = (env.to_string(), session_id.to_string());
        let entry = entries.get_mut(&key)?;
        if entry.expires_at_ms <= now_ms {
            entries.remove(&key);
            return None;
        }
        if entry.redeems_left == 0 {
            return None;
        }
        entry.redeems_left -= 1;
        Some((entry.root_turn_id.clone(), entry.ingress.clone()))
    }

    /// Master-term boundary (spec §7.5.2): captured identity dies with the
    /// term. Returns how many session captures were wiped.
    pub fn seal_term(&self) -> usize {
        let mut entries = self.entries.lock().expect("relay ledger lock");
        let wiped = entries.len();
        entries.clear();
        wiped
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_service_api::application::caller_identity::HumanCallerAuthSource;
    use bcs_service_api::application::principal::HumanActor;

    const NOW: i64 = 1_780_000_000_000;

    fn ingress(actor: &str) -> AuthenticatedHumanCallerIngress {
        AuthenticatedHumanCallerIngress::new(
            HumanActor { actor_id: actor.into(), staff_no: "S1".into() },
            HumanCallerAuthSource::HttpAuth,
            NOW,
            "prod",
        )
        .expect("valid ingress")
    }

    #[test]
    fn record_and_redeem_within_the_window() {
        let ledger = CallerRelayLedger::new(8);
        assert!(ledger.record("prod", "s-1", "turn-1", ingress("human_a"), NOW, 300_000));
        let (turn, captured) = ledger.redeem("prod", "s-1", NOW + 1).expect("redeem");
        assert_eq!(turn, "turn-1");
        assert_eq!(captured.actor().actor_id, "human_a");
    }

    #[test]
    fn an_expired_capture_fails_closed_and_never_renews() {
        let ledger = CallerRelayLedger::new(8);
        assert!(ledger.record("prod", "s-1", "turn-1", ingress("human_a"), NOW, 300_000));
        assert!(ledger.redeem("prod", "s-1", NOW + 300_000).is_none());
        // Even back inside the clock window the expired entry stays dead.
        assert!(ledger.redeem("prod", "s-1", NOW + 1).is_none());
    }

    #[test]
    fn a_new_turn_overwrites_the_session_speaker() {
        // The session's CURRENT speaker is the deliverable fact: a later
        // human turn replaces the capture instead of piling entries.
        let ledger = CallerRelayLedger::new(8);
        assert!(ledger.record("prod", "s-1", "turn-1", ingress("human_a"), NOW, 300_000));
        assert!(ledger.record("prod", "s-1", "turn-2", ingress("human_b"), NOW, 300_000));
        let (turn, captured) = ledger.redeem("prod", "s-1", NOW).expect("redeem");
        assert_eq!(turn, "turn-2");
        assert_eq!(captured.actor().actor_id, "human_b");
    }

    #[test]
    fn one_turn_reserves_at_most_relay_per_turn_redeems() {
        // The §8.3 per-turn budget family: a compromised middle bot cannot
        // amplify one captured turn into unbounded caller dispatches.
        let ledger = CallerRelayLedger::new(8);
        assert!(ledger.record("prod", "s-1", "turn-1", ingress("human_a"), NOW, 300_000));
        let mut redeemed = 0;
        while ledger.redeem("prod", "s-1", NOW).is_some() {
            redeemed += 1;
        }
        assert_eq!(redeemed, RELAY_PER_TURN);
        // A fresh human turn re-arms the budget — the Human speaks again.
        assert!(ledger.record("prod", "s-1", "turn-2", ingress("human_a"), NOW, 300_000));
        assert!(ledger.redeem("prod", "s-1", NOW).is_some());
    }

    #[test]
    fn seal_term_wipes_every_capture() {
        let ledger = CallerRelayLedger::new(8);
        assert!(ledger.record("prod", "s-1", "turn-1", ingress("human_a"), NOW, 300_000));
        assert_eq!(ledger.seal_term(), 1);
        assert!(ledger.redeem("prod", "s-1", NOW).is_none());
    }

    #[test]
    fn a_full_ledger_of_live_captures_refuses_new_records() {
        let ledger = CallerRelayLedger::new(1);
        assert!(ledger.record("prod", "s-1", "turn-1", ingress("human_a"), NOW, 300_000));
        // Capacity one, entry live: the second session fails closed rather
        // than silently evicting a deliverable identity.
        assert!(!ledger.record("prod", "s-2", "turn-1", ingress("human_b"), NOW, 300_000));
        // An expired entry frees the slot for the next capture.
        assert!(ledger.record("prod", "s-2", "turn-1", ingress("human_b"), NOW + 300_000, 300_000));
    }
}
