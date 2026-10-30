//! Runtime-only observe-target index (spec §6.9/§10.8): the in-memory,
//! never-persisted routing table between ONE caller CONTAINER
//! (Human×Bot×session, keyed by the dispatching caller subject) and its
//! trusted observe destination (subject plus optional Gateway reference).
//! Capacity-bounded, expiry-swept, and the
//! single place a container's sync reference lives: an expired or evicted
//! entry pauses that container's observe inject — never borrowing another
//! Human's identity to recover, never triggering ensure from inject, and
//! never conflating two Humans' containers of the same Bot.
use std::collections::HashMap;
use std::sync::Mutex;

use bcs_service_api::port::{CallerContainerDescriptor, CallerObserveTarget};

/// Bounded observe-target index. Entries sweep on access; the directory is
/// capped so idle containers cannot grow it without bound.
pub struct ObserveTargetIndex {
    capacity: usize,
    inner: Mutex<HashMap<(String, String, String), CallerObserveTarget>>,
    tombstones: Mutex<HashMap<(String, String, String), ()>>,
}

impl ObserveTargetIndex {
    /// Capacity must be positive; a zero-capacity index is a misassembly.
    pub fn new(capacity: usize) -> Self {
        assert!(capacity > 0, "observe index capacity must be positive");
        Self {
            capacity,
            inner: Mutex::new(HashMap::new()),
            tombstones: Mutex::new(HashMap::new()),
        }
    }

    fn key(caller_subject: &str, bot_id: &str, session_id: &str) -> (String, String, String) {
        (
            caller_subject.to_string(),
            bot_id.to_string(),
            session_id.to_string(),
        )
    }

    /// Atomically replace (or insert) the observe reference for ONE caller
    /// container after trusted preparation (ensure or explicit subject routing). A new reference unconditionally
    /// overrides the previous generation AND clears the container's
    /// tombstone (a fresh ensure legitimately reopens sync; §10.8
    /// replacement rule). Two Humans' containers of the same Bot are
    /// separate entries: one Human's ensure never touches the other's.
    /// Over-capacity inserts evict OTHER expired entries first, then refuse
    /// rather than silently dropping the fresh material.
    pub fn replace_from_descriptor(
        &self,
        caller_subject: &str,
        bot_id: &str,
        session_id: &str,
        descriptor: &CallerContainerDescriptor,
        now_ms: i64,
    ) -> Result<bool, ()> {
        let Some(target) = descriptor.observe_target.as_ref() else {
            return Ok(false);
        };
        // The descriptor's observe target must belong to the requested
        // bot/session — a readback mismatch is corrupted material.
        if caller_subject.trim().is_empty()
            || target.caller_ref.is_some() != target.target_epoch.is_some()
            || target.target_bot_id != bot_id || target.session_id != session_id {
            return Err(());
        }
        // An already-expired reference never enters the index.
        if target.expires_at_ms <= now_ms {
            return Ok(false);
        }
        let mut inner = self.inner.lock().expect("observe index");
        // Sweep expired entries on the write path.
        inner.retain(|_, entry| entry.expires_at_ms > now_ms);
        let key = Self::key(caller_subject, bot_id, session_id);
        if !inner.contains_key(&key) && inner.len() >= self.capacity {
            // At capacity with no reclaimable slot: refuse the fresh entry
            // rather than silently dropping older live routing material.
            return Err(());
        }
        let mut target = target.clone();
        // The trusted dispatch association, not an optional gateway echo,
        // is the authority for the destination Human.
        target.caller_subject = bcs_service_api::port::CallerOpaqueRef::new(caller_subject);
        inner.insert(key.clone(), target);
        self.tombstones
            .lock()
            .expect("observe tombstones")
            .remove(&key);
        Ok(true)
    }

    /// Live observe lookup for ONE container. Expired or evicted entries
    /// read as absent — the caller pauses that container's sync until its
    /// own next trusted preparation; no identity borrowing, no ensure side effect.
    pub fn current(
        &self,
        caller_subject: &str,
        bot_id: &str,
        session_id: &str,
        now_ms: i64,
    ) -> Option<CallerObserveTarget> {
        let mut inner = self.inner.lock().expect("observe index");
        inner.retain(|_, entry| entry.expires_at_ms > now_ms);
        inner
            .get(&Self::key(caller_subject, bot_id, session_id))
            .cloned()
    }

    /// The OTHER live caller containers of ONE Bot×session (spec §6.4:
    /// `同Bot其他Caller容器`), excluding the dispatching caller's own entry.
    /// Another Bot's containers are NEVER returned: whether a different Bot
    /// receives content is that Bot's own delivery/shared-projection fact,
    /// not this fan-out's (L225/L226).
    pub fn others_for(
        &self,
        bot_id: &str,
        session_id: &str,
        current_subject: &str,
        now_ms: i64,
    ) -> Vec<CallerObserveTarget> {
        let mut inner = self.inner.lock().expect("observe index");
        inner.retain(|_, entry| entry.expires_at_ms > now_ms);
        inner
            .iter()
            .filter(|(key, _)| key.1 == bot_id && key.2 == session_id && key.0 != current_subject)
            .map(|(_, entry)| entry.clone())
            .collect()
    }

    /// ALL live caller containers of one Bot×session — the reply-sync
    /// companion of `others_for` (spec §6.4 "Bot 共享回复也同步到源容器
    /// 及其他容器"): a bot's own reply is part of the shared stream for
    /// EVERY container of that Bot, the executing one included (its live
    /// generation each deduplicated by message id on the receiving side).
    pub fn all_for(
        &self,
        bot_id: &str,
        session_id: &str,
        now_ms: i64,
    ) -> Vec<CallerObserveTarget> {
        let mut inner = self.inner.lock().expect("observe index");
        inner.retain(|_, entry| entry.expires_at_ms > now_ms);
        inner
            .iter()
            .filter(|(key, _)| key.1 == bot_id && key.2 == session_id)
            .map(|(_, entry)| entry.clone())
            .collect()
    }

    /// Tomb-stone one container (member leave / unbind / config revoke): the
    /// paused state is observable per container; only a fresh trusted ensure
    /// of THAT container clears it.
    pub fn invalidate(&self, caller_subject: &str, bot_id: &str, session_id: &str) {
        let key = Self::key(caller_subject, bot_id, session_id);
        self.inner.lock().expect("observe index").remove(&key);
        self.tombstones
            .lock()
            .expect("observe tombstones")
            .insert(key, ());
    }

    /// Whether a container is explicitly invalidated (paused) rather than
    /// merely absent — callers distinguish "pause" from "never synced".
    pub fn is_invalidated(&self, caller_subject: &str, bot_id: &str, session_id: &str) -> bool {
        self.tombstones
            .lock()
            .expect("observe tombstones")
            .contains_key(&Self::key(caller_subject, bot_id, session_id))
    }

    /// Membership-failure sweep (review-VI P1): one HUMAN's entries across
    /// EVERY Bot of one session are paused at once — a removed Human stops
    /// receiving NEW group context immediately, in every container they had
    /// with any Bot of the session. The tombstone is per (subject, bot,
    /// session) so a fresh trusted ensure of a re-admitted member re-opens
    /// that exact container (the §10.8 reopening rule), and nothing is
    /// ever inferred from absence of a tombstone elsewhere.
    pub fn invalidate_subject_in_session(&self, caller_subject: &str, session_id: &str) {
        let subject = caller_subject.to_string();
        let session = session_id.to_string();
        let mut inner = self.inner.lock().expect("observe index");
        // Capture FIRST, remove second — the captured keys become the
        // tombstones; iterating after retain would see an already-swept map.
        let affected: Vec<(String, String, String)> = inner
            .keys()
            .filter(|(subject_key, _, session_key)| {
                subject_key == &subject && session_key == &session
            })
            .cloned()
            .collect();
        inner.retain(|(subject_key, _, session_key), _| !(subject_key == &subject && session_key == &session));
        drop(inner);
        let mut tombstones = self.tombstones.lock().expect("observe tombstones");
        for key in affected {
            tombstones.insert(key, ());
        }
    }

    /// Membership-convergence prune (review-VI P1): every LIVE entry of
    /// this Bot×session whose caller subject is NOT in the CURRENT roster
    /// pauses — the review's departed-member gap (a Human who left the
    /// group kept receiving new group context until reference expiry) is
    /// closed at every fan-out site that has the live roster at hand. The
    /// tombstone lives per (subject, bot, session); a re-admitted member
    /// re-opens by their own next trusted ensure.
    pub fn pause_departed_subjects(
        &self,
        bot_id: &str,
        session_id: &str,
        current_roster: &[String],
    ) {
        let mut inner = self.inner.lock().expect("observe index");
        let removed: Vec<(String, String, String)> = inner
            .keys()
            .filter(|(subject, bot, session)| {
                bot == bot_id
                    && session == session_id
                    && !current_roster.iter().any(|member| member == subject)
            })
            .cloned()
            .collect();
        for key in &removed {
            inner.remove(key);
        }
        drop(inner);
        if !removed.is_empty() {
            let mut tombstones = self.tombstones.lock().expect("observe tombstones");
            for key in removed {
                tombstones.insert(key, ());
            }
        }
    }

    /// Membership-failure sweep for one BOT (review-VI P1): every Human
    /// container of ONE Bot in one session pauses — the removed/unbound
    /// Bot receives no further observe syncs until it is re-invited and
    /// its own next trusted ensure re-opens it.
    pub fn invalidate_bot_in_session(&self, bot_id: &str, session_id: &str) {
        let bot = bot_id.to_string();
        let session = session_id.to_string();
        let mut inner = self.inner.lock().expect("observe index");
        // Capture FIRST, remove second — same ordering discipline as the
        // subject sweep.
        let affected: Vec<(String, String, String)> = inner
            .keys()
            .filter(|(_, bot_key, session_key)| bot_key == &bot && session_key == &session)
            .cloned()
            .collect();
        inner.retain(|(_, bot_key, session_key), _| !(bot_key == &bot && session_key == &session));
        drop(inner);
        let mut tombstones = self.tombstones.lock().expect("observe tombstones");
        for key in affected {
            tombstones.insert(key, ());
        }
    }

    /// Bounded directory size (diagnostics/tests).
    pub fn len(&self) -> usize {
        self.inner.lock().expect("observe index").len()
    }

    /// Seal the current term and start the next one clean (spec §7.5.2):
    /// the caller master's epoch boundary wipes the whole runtime-only
    /// directory — observe references of an OLD term never inject in the
    /// new term (their sync pauses until their own next trusted ensure,
    /// the same as an expired entry), and tombstones die with the
    /// directory because the new term has no member-leave state to
    /// preserve. Returns how many live references were wiped.
    pub fn seal_term(&self) -> usize {
        let wiped = self.inner.lock().expect("observe index").len();
        self.inner.lock().expect("observe index").clear();
        self.tombstones.lock().expect("observe tombstones").clear();
        wiped
    }
}

/// Shared fixture builder for sibling contract modules (observe_sync).
#[cfg(any(test, doc))]
pub mod tests_support {
    use super::*;
    use bcs_service_api::port::{CallerContainerIsolation, CallerOpaqueRef};

    pub fn descriptor(bot: &str, session: &str, epoch: &str, expires: i64) -> CallerContainerDescriptor {
        CallerContainerDescriptor {
            sandbox_container_id: None,
            target_bot_id: bot.to_string(),
            caller_subject_ref: CallerOpaqueRef::new(format!("observer-{epoch}")),
            ready: true,
            isolation: CallerContainerIsolation::ContainerBound,
            binding_ref: None,
            runtime_generation: Some(1),
            expires_at_ms: expires,
            observe_target: Some(CallerObserveTarget {
                caller_subject: CallerOpaqueRef::new(format!("observer-{epoch}")),
                caller_ref: Some(CallerOpaqueRef::new(format!("ref-{epoch}"))),
                target_bot_id: bot.to_string(),
                session_id: session.to_string(),
                target_epoch: Some(CallerOpaqueRef::new(format!("epoch-{epoch}"))),
                expires_at_ms: expires,
            }),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_service_api::port::caller_container::{
        CallerContainerDescriptor, ContainerRunAssociation, EnsureCallerContainer,
    };
    use bcs_service_api::port::{CallerContainerMinimum, CallerOpaqueRef};

    fn descriptor(bot: &str, session: &str, epoch: &str, expires: i64) -> CallerContainerDescriptor {
        tests_support::descriptor(bot, session, epoch, expires)
    }

    fn ensure_request(bot: &str, session: &str) -> EnsureCallerContainer {
        EnsureCallerContainer {
            caller_subject_ref: CallerOpaqueRef::new("human"),
            target_bot_id: bot.into(),
            session_id: session.into(),
            environment: "local".into(),
            minimum_isolation: CallerContainerMinimum::CallerContainer,
            idempotency_key: "key".into(),
            run: ContainerRunAssociation {
                run_id: "run".into(),
                root_run_id: "root".into(),
                parent_run_id: None,
                message_turn_id: "turn".into(),
                history_cutoff_seq: 0,
            },
            create_mode: bcs_service_api::port::caller_container::EnsureCreateMode::Lazy,
            container_ttl_ms: 60_000,
            deadline_ms: 0,
            now_ms: 0,
        }
    }

    const NOW: i64 = 1_780_000_000_000;

    #[test]
    fn replaces_generations_and_clears_tombstones_per_container() {
        let index = ObserveTargetIndex::new(8);
        assert!(
            index
                .replace_from_descriptor("alice", "bot", "s", &descriptor("bot", "s", "1", NOW + 10_000), NOW)
                .unwrap()
        );
        assert!(
            index
                .replace_from_descriptor("alice", "bot", "s", &descriptor("bot", "s", "2", NOW + 20_000), NOW)
                .unwrap(),
            "a fresh trusted ensure unconditionally overrides the generation"
        );
        let target = index.current("alice", "bot", "s", NOW).unwrap();
        assert_eq!(target.target_epoch.as_ref().unwrap().expose(), "epoch-2");
        assert_eq!(target.caller_subject.expose(), "alice", "trusted association overrides descriptor subject");

        // Invalidate pauses the container; only a fresh ensure clears it.
        index.invalidate("alice", "bot", "s");
        assert!(index.current("alice", "bot", "s", NOW).is_none());
        assert!(index.is_invalidated("alice", "bot", "s"));
        assert!(
            index
                .replace_from_descriptor("alice", "bot", "s", &descriptor("bot", "s", "3", NOW + 30_000), NOW)
                .unwrap()
        );
        assert!(!index.is_invalidated("alice", "bot", "s"), "fresh ensure reopens sync");
    }

    #[test]
    fn two_humans_containers_of_one_bot_are_independent_entries() {
        let index = ObserveTargetIndex::new(8);
        assert!(
            index
                .replace_from_descriptor("human-alice", "bot-a", "s", &descriptor("bot-a", "s", "a1", NOW + 10_000), NOW)
                .unwrap()
        );
        assert!(
            index
                .replace_from_descriptor("human-bob", "bot-a", "s", &descriptor("bot-a", "s", "b1", NOW + 10_000), NOW)
                .unwrap()
        );
        // Bob's ensure for HIS container never overwrote Alice's entry.
        assert_eq!(
            index.current("human-alice", "bot-a", "s", NOW).unwrap().target_epoch.as_ref().unwrap().expose(),
            "epoch-a1"
        );

        // The same-Bot fan-out sees exactly the OTHER container.
        let others = index.others_for("bot-a", "s", "human-alice", NOW);
        assert_eq!(others.len(), 1);
        assert_eq!(others[0].target_epoch.as_ref().unwrap().expose(), "epoch-b1");
        // And symmetrically from Bob's side.
        let others = index.others_for("bot-a", "s", "human-bob", NOW);
        assert_eq!(others.len(), 1);
        assert_eq!(others[0].target_epoch.as_ref().unwrap().expose(), "epoch-a1");
    }

    #[test]
    fn membership_failure_sweeps_pause_only_the_named_scope() {
        // Review-VI P1: a removed HUMAN stops receiving new group context in
        // every container of the session; an unrelated Human and cross-
        // session entries stay live; a fresh trusted ensure of the same
        // container re-opens it while OTHER tombstoned containers stay
        // paused. The BOT sweep behaves symmetrically.
        let index = ObserveTargetIndex::new(16);
        index
            .replace_from_descriptor("human-a", "bot-1", "s1", &descriptor("bot-1", "s1", "a1", NOW + 60_000), NOW)
            .unwrap();
        index
            .replace_from_descriptor("human-a", "bot-2", "s1", &descriptor("bot-2", "s1", "a2", NOW + 60_000), NOW)
            .unwrap();
        index
            .replace_from_descriptor("human-a", "bot-1", "s2", &descriptor("bot-1", "s2", "a3", NOW + 60_000), NOW)
            .unwrap();
        index
            .replace_from_descriptor("human-b", "bot-1", "s1", &descriptor("bot-1", "s1", "b1", NOW + 60_000), NOW)
            .unwrap();

        index.invalidate_subject_in_session("human-a", "s1");
        assert!(index.current("human-a", "bot-1", "s1", NOW).is_none());
        assert!(index.current("human-a", "bot-2", "s1", NOW).is_none());
        assert!(index.is_invalidated("human-a", "bot-1", "s1"));
        assert!(index.is_invalidated("human-a", "bot-2", "s1"));
        // Cross-session survival: another session's containers keep sync
        // until a fresh sweep of THAT session.
        assert!(index.current("human-a", "bot-1", "s2", NOW).is_some(), "cross-session human entry survives");
        // Unrelated human stays live.
        assert!(index.current("human-b", "bot-1", "s1", NOW).is_some());
        // A fresh trusted ensure re-opens exactly the re-invited container;
        // the sibling container stays paused.
        index
            .replace_from_descriptor("human-a", "bot-1", "s1", &descriptor("bot-1", "s1", "a4", NOW + 60_000), NOW)
            .unwrap();
        assert!(index.current("human-a", "bot-1", "s1", NOW).is_some(), "fresh ensure re-opens");
        assert!(!index.is_invalidated("human-a", "bot-1", "s1"));
        assert!(index.current("human-a", "bot-2", "s1", NOW).is_none(), "sibling container stays paused");

        index.invalidate_bot_in_session("bot-1", "s1");
        assert!(index.current("human-b", "bot-1", "s1", NOW).is_none(), "bot sweep pauses its humans");
        assert!(index.is_invalidated("human-b", "bot-1", "s1"));
        assert!(index.current("human-a", "bot-1", "s2", NOW).is_some(), "bot sweep is session-scoped");
    }

    #[test]
    fn seal_term_wipes_references_and_tombstones_for_the_new_master_epoch() {
        // Spec §7.5.2: an old term's observe references never inject in a
        // new term (their containers re-seed on their own next trusted
        // ensure, the same as an expired entry), and member-leave
        // tombstones die with the directory — the new term has no paused
        // state to preserve.
        let index = ObserveTargetIndex::new(8);
        assert!(
            index
                .replace_from_descriptor("human-alice", "bot-a", "s", &descriptor("bot-a", "s", "a1", NOW + 10_000), NOW)
                .unwrap()
        );
        assert!(
            index
                .replace_from_descriptor("human-bob", "bot-a", "s", &descriptor("bot-a", "s", "b1", NOW + 10_000), NOW)
                .unwrap()
        );
        index.invalidate("human-alice", "bot-a", "s");
        assert!(index.is_invalidated("human-alice", "bot-a", "s"));

        let wiped = index.seal_term();
        assert_eq!(wiped, 1, "only the live reference counts as wiped");
        assert!(index.current("human-bob", "bot-a", "s", NOW).is_none(), "old-term reference is gone");
        assert!(index.others_for("bot-a", "s", "human-bob", NOW).is_empty());
        assert!(!index.is_invalidated("human-alice", "bot-a", "s"), "tombstones die with the term");
        assert_eq!(index.len(), 0);
        // The new term re-seeds cleanly from the next trusted ensure.
        assert!(
            index
                .replace_from_descriptor("human-alice", "bot-a", "s", &descriptor("bot-a", "s", "a2", NOW + 10_000), NOW)
                .unwrap()
        );
        assert_eq!(index.current("human-alice", "bot-a", "s", NOW).unwrap().target_epoch.as_ref().unwrap().expose(), "epoch-a2");
    }

    #[test]
    fn others_never_cross_bots_or_sessions() {
        let index = ObserveTargetIndex::new(8);
        for (subject, bot, session) in [
            ("human-bob", "bot-a", "s-1"),
            ("human-bob", "bot-b", "s-1"),
            ("human-bob", "bot-a", "s-2"),
        ] {
            assert!(
                index
                    .replace_from_descriptor(subject, bot, session, &descriptor(bot, session, "g", NOW + 10_000), NOW)
                    .unwrap()
            );
        }
        // Dispatching bot-a in session s-1 as alice: only Bob's bot-a/s-1
        // container is observable — never another Bot, never another session
        // (a different session is a different shared stream, spec §6.4).
        let others = index.others_for("bot-a", "s-1", "human-alice", NOW);
        assert_eq!(others.len(), 1);
        assert_eq!(others[0].target_bot_id, "bot-a");
        assert_eq!(others[0].session_id, "s-1");
    }

    #[test]
    fn expired_or_mismatched_or_absent_material_never_enters() {
        let index = ObserveTargetIndex::new(8);
        // Expired reference: refused silently.
        assert!(!index
            .replace_from_descriptor("human-alice", "bot", "s", &descriptor("bot", "s", "1", NOW), NOW)
            .unwrap());
        // Readback mismatch (bot/session): corrupted material.
        assert!(
            index
                .replace_from_descriptor("human-alice", "bot", "s", &descriptor("OTHER", "s", "2", NOW + 10_000), NOW)
                .is_err()
        );
        assert!(
            index
                .replace_from_descriptor("human-alice", "bot", "s", &descriptor("bot", "OTHER", "3", NOW + 10_000), NOW)
                .is_err()
        );
        // Descriptor without an observe target (pure H2A): absent, not an error.
        let mut h2a = descriptor("bot", "s", "4", NOW + 10_000);
        h2a.observe_target = None;
        assert!(!index
            .replace_from_descriptor("human-alice", "bot", "s", &h2a, NOW)
            .unwrap());
        assert!(index.len() == 0);
        let _ = ensure_request("bot", "s");
    }

    #[test]
    fn expiry_sweeps_and_capacity_refuses_instead_of_dropping_live() {
        let index = ObserveTargetIndex::new(2);
        assert!(index
            .replace_from_descriptor("h1", "a", "s", &descriptor("a", "s", "1", NOW + 1_000), NOW)
            .unwrap());
        assert!(index
            .replace_from_descriptor("h2", "b", "s", &descriptor("b", "s", "2", NOW + 1_000), NOW)
            .unwrap());
        // BOTH entries expire by NOW+2_000 — the third insert reclaims a slot.
        assert!(index
            .replace_from_descriptor("h3", "c", "s", &descriptor("c", "s", "3", NOW + 9_000), NOW + 2_000)
            .unwrap());
        assert!(index.current("h1", "a", "s", NOW + 2_000).is_none(), "h1 expired");
        assert!(index.current("h2", "b", "s", NOW + 2_000).is_none(), "h2 expired");
        assert_eq!(index.len(), 1, "only the fresh live entry remains");
        // Fill to capacity with live entries, then refuse the fresh one
        // instead of evicting a live container.
        assert!(index
            .replace_from_descriptor("h1", "a", "s", &descriptor("a", "s", "5", NOW + 9_000), NOW + 2_000)
            .unwrap());
        assert!(
            index
                .replace_from_descriptor("h4", "d", "s", &descriptor("d", "s", "4", NOW + 9_000), NOW + 2_000)
                .is_err(),
            "capacity refuses; live entries are never silently dropped"
        );
        assert_eq!(index.len(), 2);
    }
}
