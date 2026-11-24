use super::{StoreInboxProducer, state::*, storage::*};
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{common::StoreError, inbox::AdmitOutcome};
use bcs_store_api::stream::{AppendId, StreamId};
use std::time::Duration;

impl StoreInboxProducer {
    pub(super) async fn advance(&self, key: &str, new: bool) -> Result<AdmitOutcome, StoreError> {
        let mut budget = EqBudget::new();
        let mut publish_hint = new;
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
                super::progress::Progress::Done(outcome) => {
                    if publish_hint
                        && let AdmitOutcome::Accepted(receipt) | AdmitOutcome::Duplicate(receipt) =
                            &outcome
                    {
                        let _ = self.hint(&receipt.sid, Some(self.inner.clock.now())).await;
                    }
                    return Ok(outcome);
                }
                super::progress::Progress::Changed => budget = EqBudget::new(),
                super::progress::Progress::Retry => {}
            }
        }
    }
    pub(super) async fn register(
        &self,
        key: &str,
        c: &AdmissionRecord,
    ) -> Result<Option<Decision>, StoreError> {
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
                    return Ok(slot.decision.clone());
                }
                if slot.start && (s.closed.is_some() || s.generation != slot.generation) {
                    if let Some(slot) = s.slots.get_mut(key) {
                        slot.decision = Some(Decision::Rejected);
                    }
                    if budget.io(key, self.save(&sk, v, &s, key)).await? {
                        return Ok(Some(Decision::Rejected));
                    }
                    continue;
                }
                return Ok(None);
            }
            if c.start() && (s.closed.is_some() || s.generation != c.generation) {
                return Ok(Some(Decision::Rejected));
            }
            if s.slots.len() >= 64
                || (c.start() && s.slots.values().filter(|s| s.start).count() >= 56)
            {
                return Err(unknown(key));
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
                return Err(unknown(key));
            }
            if budget.io(key, self.save(&sk, v, &s, key)).await? {
                return Ok(None);
            }
        }
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
    pub(super) async fn activate_slot(
        &self,
        key: &str,
        c: &AdmissionRecord,
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
            let rejected = slot.start && (s.closed.is_some() || s.generation != slot.generation);
            let decision = if rejected {
                Decision::Rejected
            } else {
                Decision::Accepted
            };
            slot.decision = Some(decision.clone());
            if !rejected {
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
