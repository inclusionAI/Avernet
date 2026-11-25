use super::{StoreInboxProducer, progress::Progress, state::*, storage::*};
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{common::StoreError, inbox::AdmitOutcome};
use bcs_store_api::stream::{AppendId, StreamId};
use std::{sync::atomic::Ordering, time::Duration};

pub(super) enum Registration {
    Ready(Option<Decision>),
    Full(OccupiedSlot),
}

pub(super) struct OccupiedSlot {
    sid: Sid,
    key: String,
    slot: Slot,
}

impl StoreInboxProducer {
    pub(super) async fn advance(&self, key: &str, new: bool) -> Result<AdmitOutcome, StoreError> {
        let mut budget = EqBudget::new();
        let mut publish_hint = new;
        let mut helped_slot = false;
        loop {
            budget.attempt(key).await?;
            let (v, c) = budget
                .io(key, self.admission_record(key))
                .await?
                .ok_or_else(|| invalid("admission_missing"))?;
            // A retry that helps finish an incomplete admission must make
            // the Session discoverable even if the creator disconnected.
            publish_hint |= c.decision.is_none();
            match budget.io(key, self.progress(v, c, key, new)).await? {
                Progress::Done(outcome) => {
                    if publish_hint
                        && let AdmitOutcome::Accepted(receipt) | AdmitOutcome::Duplicate(receipt) =
                            &outcome
                    {
                        let _ = self.hint(&receipt.sid, Some(self.inner.clock.now())).await;
                    }
                    return Ok(outcome);
                }
                Progress::Full(slot) => {
                    // One foreign admission per call. A retry chooses another slot;
                    // never recursively help a chain of admissions under pressure.
                    if helped_slot {
                        return Err(unknown(key));
                    }
                    helped_slot = true;
                    budget.io(key, self.help_slot(slot)).await?;
                }
                Progress::Changed => budget = EqBudget::new(),
                Progress::Retry => {}
            }
        }
    }
    pub(super) async fn register(
        &self,
        key: &str,
        c: &AdmissionRecord,
    ) -> Result<Registration, StoreError> {
        let sid = c.sid();
        let sk = self.inner.layout.schedule(sid)?;
        let mut budget = EqBudget::new();
        loop {
            budget.attempt(key).await?;
            let (v, mut s) = budget.io(key, self.schedule(sid)).await?;
            if let Some(slot) = s.slots.get(key) {
                if slot.generation != c.generation {
                    return Err(invalid("admission_slot_mismatch"));
                }
                if slot.decision.is_some() {
                    return Ok(Registration::Ready(slot.decision.clone()));
                }
                if slot.start && (s.closed.is_some() || s.generation != slot.generation) {
                    if let Some(slot) = s.slots.get_mut(key) {
                        slot.decision = Some(Decision::Rejected);
                    }
                    if budget.io(key, self.save(&sk, v, &s, key)).await? {
                        return Ok(Registration::Ready(Some(Decision::Rejected)));
                    }
                    continue;
                }
                return Ok(Registration::Ready(None));
            }
            if c.start() && (s.closed.is_some() || s.generation != c.generation) {
                return Ok(Registration::Ready(Some(Decision::Rejected)));
            }
            let starts_full = c.start() && s.slots.values().filter(|s| s.start).count() >= 56;
            if s.slots.len() >= 64 || starts_full {
                return self.occupied_slot(key, sid, &s, starts_full);
            }
            s.slots.insert(
                key.into(),
                Slot {
                    generation: c.generation,
                    start: c.start(),
                    decision: None,
                },
            );
            let size = serde_json::to_vec(&s)
                .map_err(|_| invalid("schedule_encoding"))?
                .len();
            if size > 64 * 1024 - if c.start() { 8 * 1024 } else { 0 } {
                s.slots.remove(key); // The trial insertion was never persisted.
                return self.occupied_slot(key, sid, &s, false);
            }
            if budget.io(key, self.save(&sk, v, &s, key)).await? {
                return Ok(Registration::Ready(None));
            }
        }
    }
    fn occupied_slot(
        &self,
        op: &str,
        sid: &Sid,
        schedule: &Schedule,
        starts_only: bool,
    ) -> Result<Registration, StoreError> {
        let eligible: Vec<_> = schedule
            .slots
            .iter()
            .filter(|(_, slot)| {
                (!starts_only || slot.start) && !(slot.start && schedule.closed.is_some())
            })
            .collect();
        if eligible.is_empty() {
            return Err(unknown(op));
        }
        let index = self.inner.slot_rotation.fetch_add(1, Ordering::Relaxed) % eligible.len();
        let (key, slot) = eligible[index];
        Ok(Registration::Full(OccupiedSlot {
            sid: sid.clone(),
            key: key.clone(),
            slot: slot.clone(),
        }))
    }

    async fn help_slot(&self, occupied: OccupiedSlot) -> Result<(), StoreError> {
        let key = &occupied.key;
        // A normal Start takes at most ten visits from Created to slot release.
        // Every successful phase is durable if the caller's time budget runs out.
        for _ in 0..12 {
            let (v, c) = self
                .admission_record(key)
                .await?
                .ok_or_else(|| invalid("slot_admission_missing"))?;
            if c.sid() != &occupied.sid
                || c.generation != occupied.slot.generation
                || c.start() != occupied.slot.start
            {
                return Err(invalid("admission_slot_mismatch"));
            }
            match self.progress(v, c, key, false).await? {
                Progress::Changed => continue,
                Progress::Done(outcome) => {
                    if matches!(outcome, AdmitOutcome::Duplicate(_)) {
                        let _ = self.hint(&occupied.sid, Some(self.inner.clock.now())).await;
                    }
                    return Ok(());
                }
                // Another writer is progressing this admission; recheck capacity.
                Progress::Retry => return Ok(()),
                Progress::Full(_) => return Err(unknown(key)),
            }
        }
        Err(unknown(key))
    }
    pub(super) async fn append(
        &self,
        sid: &Sid,
        fields: &std::collections::BTreeMap<String, Vec<u8>>,
        op: &str,
    ) -> Result<StreamId, StoreError> {
        let _permit = self.io_permit().await?;
        tokio::time::timeout(
            Duration::from_millis(500),
            self.inner
                .stream
                .append(&self.inner.layout.inbox(sid)?, AppendId::Auto, fields),
        )
        .await
        .map_err(|_| unknown(op))?
        .map_err(|e| crate::error::from_write(&e, op))
    }
    pub(super) async fn decide_slot(
        &self,
        key: &str,
        c: &AdmissionRecord,
        proposed: Decision,
    ) -> Result<Decision, StoreError> {
        let sid = c.sid();
        let sk = self.inner.layout.schedule(sid)?;
        let mut budget = EqBudget::new();
        loop {
            budget.attempt(key).await?;
            let (v, mut s) = budget.io(key, self.schedule(sid)).await?;
            let Some(slot) = s.slots.get_mut(key) else {
                let latest = self
                    .admission_record(key)
                    .await?
                    .ok_or_else(|| invalid("admission_missing"))?
                    .1;
                return latest.decision.ok_or_else(|| unknown(key));
            };
            if let Some(d) = &slot.decision {
                return Ok(d.clone());
            }
            let decision = if slot.start && (s.closed.is_some() || s.generation != slot.generation) {
                Decision::Rejected
            } else {
                proposed.clone()
            };
            slot.decision = Some(decision.clone());
            if decision == Decision::Accepted {
                if c.position.is_none() {
                    return Err(invalid("activation_without_position"));
                }
                s.activation = s
                    .activation
                    .checked_add(1)
                    .ok_or_else(|| invalid("activation_exhausted"))?;
                s.next_check = Some(self.inner.clock.now());
            }
            if budget.io(key, self.save(&sk, v, &s, key)).await? {
                return Ok(decision);
            }
        }
    }
    pub(super) async fn release_slot(&self, key: &str, c: &AdmissionRecord) -> Result<(), StoreError> {
        if c.decision.is_none() {
            return Err(invalid("release_without_decision"));
        }
        let sk = self.inner.layout.schedule(c.sid())?;
        let mut budget = EqBudget::new();
        loop {
            budget.attempt(key).await?;
            let (v, mut s) = budget.io(key, self.schedule(c.sid())).await?;
            if c.start()
                && s.closed
                    .as_ref()
                    .is_some_and(|closed| closed.generation == c.generation)
                && s.slots.contains_key(key)
            {
                // A canonical decision is not a terminal publication proof.
                // The independent closure provider verifies that proof and then
                // releases this closed-generation working slot.
                return Ok(());
            }
            if s.slots.remove(key).is_none() {
                return Ok(());
            }
            if budget.io(key, self.save(&sk, v, &s, key)).await? {
                return Ok(());
            }
        }
    }
}
